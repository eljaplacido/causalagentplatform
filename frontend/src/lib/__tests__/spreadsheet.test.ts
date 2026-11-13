/**
 * The analysis page's file reader: what a user's CSV or .xlsx becomes is what the kernel
 * analyses. The workbook fixture was written by LibreOffice Calc (headless --convert-to xlsx)
 * from the table below, so it carries a real writer's shared strings, booleans, an empty
 * cell and a relationship id other than rId1:
 *
 *   treated,spend,region,returned
 *   1,3.5,Pohjoinen,TRUE
 *   0,2.25,Etelä,FALSE
 *   1,,"Itä, ranta",TRUE
 *   0,-1.5,Länsi,FALSE
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { parseCsv } from "../analysis";
import { decodeText, readTableBytes, readTableFile, xlsxRows } from "../spreadsheet";

const fixture = (name: string) =>
  new Uint8Array(readFileSync(join(__dirname, "..", "..", "test-fixtures", name)));

// jsdom's File has no arrayBuffer(); the reader needs only that method of a File.
const file = (bytes: Uint8Array) => ({ arrayBuffer: async () => bytes.slice().buffer });

/** A zip of stored (uncompressed) entries: enough for a hand-written workbook. */
function storedZip(files: Record<string, string>): Uint8Array {
  const enc = new TextEncoder();
  const locals: Uint8Array[] = [];
  const centrals: Uint8Array[] = [];
  let offset = 0;
  for (const [name, text] of Object.entries(files)) {
    const n = enc.encode(name);
    const data = enc.encode(text);
    const local = new Uint8Array(30 + n.length + data.length);
    const lv = new DataView(local.buffer);
    lv.setUint32(0, 0x04034b50, true);
    lv.setUint32(18, data.length, true);
    lv.setUint32(22, data.length, true);
    lv.setUint16(26, n.length, true);
    local.set(n, 30);
    local.set(data, 30 + n.length);
    const central = new Uint8Array(46 + n.length);
    const cv = new DataView(central.buffer);
    cv.setUint32(0, 0x02014b50, true);
    cv.setUint32(20, data.length, true);
    cv.setUint32(24, data.length, true);
    cv.setUint16(28, n.length, true);
    cv.setUint32(42, offset, true);
    central.set(n, 46);
    locals.push(local);
    centrals.push(central);
    offset += local.length;
  }
  const cdSize = centrals.reduce((a, c) => a + c.length, 0);
  const eocd = new Uint8Array(22);
  const ev = new DataView(eocd.buffer);
  ev.setUint32(0, 0x06054b50, true);
  ev.setUint16(8, centrals.length, true);
  ev.setUint16(10, centrals.length, true);
  ev.setUint32(12, cdSize, true);
  ev.setUint32(16, offset, true);
  const out = new Uint8Array(offset + cdSize + 22);
  let p = 0;
  for (const part of [...locals, ...centrals, eocd]) {
    out.set(part, p);
    p += part.length;
  }
  return out;
}

const NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"';
const RNS = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"';

describe("readTableFile: .xlsx", () => {
  it("reads a LibreOffice-written workbook's first sheet as the same table its CSV gives", async () => {
    const { table, sheet } = await readTableFile(file(fixture("analyze-libreoffice.xlsx")));
    expect(sheet).toBe("demo");
    expect(table.names).toEqual(["treated", "spend", "region", "returned"]);
    expect(table.nRows).toBe(4);
    expect(table.columns).toEqual({
      treated: [1, 0, 1, 0],
      spend: [3.5, 2.25, null, -1.5],
      region: ["Pohjoinen", "Etelä", "Itä, ranta", "Länsi"],
      returned: [1, 0, 1, 0],
    });
    const csv = parseCsv(
      'treated,spend,region,returned\n1,3.5,Pohjoinen,TRUE\n0,2.25,Etelä,FALSE\n1,,"Itä, ranta",TRUE\n0,-1.5,Länsi,FALSE\n'
    );
    expect(table.columns).toEqual(csv.columns);
  });

  it("reads inline strings, rich text runs, error cells as missing, gaps and an absolute sheet path", async () => {
    const bytes = storedZip({
      "xl/workbook.xml": `<workbook ${NS} ${RNS}><sheets><sheet name="Data" sheetId="1" r:id="rId7"/></sheets></workbook>`,
      "xl/_rels/workbook.xml.rels": `<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId7" Type="worksheet" Target="/xl/worksheets/data.xml"/></Relationships>`,
      "xl/sharedStrings.xml": `<sst ${NS}><si><r><t>out</t></r><r><t>come</t></r><rPh><t>x</t></rPh></si></sst>`,
      "xl/worksheets/data.xml":
        `<worksheet ${NS}><sheetData>` +
        `<row r="1"><c r="A1" t="inlineStr"><is><t>arm</t></is></c><c r="C1" t="s"><v>0</v></c><c r="E1" s="3"/></row>` +
        `<row r="2"><c r="A2"><v>1</v></c><c r="B2"><v>9</v></c><c r="C2" t="e"><v>#N/A</v></c></row>` +
        `<row r="3"/>` +
        `<row r="4"><c r="A4"><v>0</v></c><c r="C4" t="str"><v>2.5</v></c></row>` +
        `</sheetData></worksheet>`,
    });
    const { rows, sheet } = await xlsxRows(bytes);
    expect(sheet).toBe("Data");
    // B1 is empty (header named column_2 later), E1 is formatting only, row 3 is blank.
    expect(rows).toEqual([
      ["arm", "", "outcome"],
      ["1", "9", ""],
      ["0", "", "2.5"],
    ]);
    const { table } = await readTableFile(file(bytes));
    expect(table.names).toEqual(["arm", "column_2", "outcome"]);
    expect(table.columns.outcome).toEqual([null, 2.5]);
  });

  it("refuses an old binary .xls with what to do instead", async () => {
    const xls = new Uint8Array([0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1, 0, 0]);
    await expect(readTableFile(file(xls))).rejects.toThrow(/save it as \.xlsx or CSV/);
  });

  it("refuses a zip that is not a workbook", async () => {
    const zip = storedZip({ "readme.txt": "hello" });
    await expect(readTableFile(file(zip))).rejects.toThrow(/no worksheet/);
  });
});

describe("readTableFile: CSV as Excel writes it", () => {
  it("reads a Finnish-locale Excel CSV: Windows-1252, semicolons, decimal commas", async () => {
    // "kohde;tulos;alue\r\n1;3,5;Etelä\r\n0;-0,25;Itä\r\n" in Windows-1252 (ä = 0xE4).
    const text = "kohde;tulos;alue\r\n1;3,5;Etelä\r\n0;-0,25;Itä\r\n";
    const bytes = Uint8Array.from(text, (c) => c.charCodeAt(0));
    const { table } = await readTableFile(file(bytes));
    expect(table.columns).toEqual({ kohde: [1, 0], tulos: [3.5, -0.25], alue: ["Etelä", "Itä"] });
  });

  it("keeps UTF-8 (with a BOM) as UTF-8", async () => {
    const bytes = new TextEncoder().encode("\uFEFFalue;y\nEtel\u00e4;1,5\n");
    expect(decodeText(bytes)).toBe("alue;y\nEtel\u00e4;1,5\n");
    expect((await readTableBytes(bytes)).table.columns).toEqual({ alue: ["Etel\u00e4"], y: [1.5] });
  });

  it("does not read a comma as a decimal point in a comma-separated file", () => {
    // Quoted "3,5" in a comma file is text, not 3.5: the comma is that file's delimiter.
    expect(parseCsv('a,b\n1,"3,5"\n2,"4,5"').columns.b).toEqual(["3,5", "4,5"]);
  });

  it("leaves a semicolon file's text with commas as text", () => {
    expect(parseCsv("a;b\n1;Smith, J.\n2;x").columns.b).toEqual(["Smith, J.", "x"]);
  });
});
