/**
 * Reading a table file in the browser: CSV text in any encoding Excel writes, and .xlsx
 * workbooks (first sheet). Nothing is uploaded; the cells become the same rows of strings a
 * CSV gives, so `tableFromRows` types them by one set of rules whatever the file was.
 *
 * An .xlsx is a zip of XML parts. The zip is read here (central directory, stored or
 * deflated entries, inflated with the browser's DecompressionStream), so the page needs no
 * spreadsheet dependency.
 */

import { parseCsv, tableFromRows, type ParsedTable } from "./analysis";

export interface ReadTable {
  table: ParsedTable;
  /** The sheet read, for a workbook. */
  sheet?: string;
}

/** CSV bytes as text: UTF-8 when they are valid UTF-8, else Windows-1252 (Excel's "CSV" on Windows). */
export function decodeText(bytes: Uint8Array): string {
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    return new TextDecoder("windows-1252").decode(bytes);
  }
}

const ZIP_MAGIC = [0x50, 0x4b, 0x03, 0x04];
const OLE_MAGIC = [0xd0, 0xcf, 0x11, 0xe0];

function startsWith(bytes: Uint8Array, magic: number[]): boolean {
  return magic.every((b, i) => bytes[i] === b);
}

/** Read a CSV/TSV or .xlsx file into a typed table. */
export async function readTableFile(file: Pick<File, "arrayBuffer">): Promise<ReadTable> {
  return readTableBytes(new Uint8Array(await file.arrayBuffer()));
}

/** A file's bytes to a typed table: .xlsx by its zip signature, anything else as CSV text. */
export async function readTableBytes(bytes: Uint8Array): Promise<ReadTable> {
  if (startsWith(bytes, ZIP_MAGIC)) return readXlsx(bytes);
  if (startsWith(bytes, OLE_MAGIC)) {
    throw new Error("this is an old-format Excel file (.xls); save it as .xlsx or CSV and try again");
  }
  return { table: parseCsv(decodeText(bytes)) };
}

// ── zip ──────────────────────────────────────────────────────────────────────

async function inflateRaw(data: Uint8Array): Promise<Uint8Array> {
  const inflate = new DecompressionStream("deflate-raw");
  const writer = inflate.writable.getWriter();
  void writer.write(data as Uint8Array<ArrayBuffer>);
  void writer.close();
  return new Uint8Array(await new Response(inflate.readable).arrayBuffer());
}

/** The entries of a zip archive, by name, read lazily. */
export function zipEntries(bytes: Uint8Array): Map<string, () => Promise<Uint8Array>> {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let eocd = -1;
  for (let i = bytes.length - 22; i >= Math.max(0, bytes.length - 22 - 0xffff); i--) {
    if (view.getUint32(i, true) === 0x06054b50) {
      eocd = i;
      break;
    }
  }
  if (eocd < 0) throw new Error("the file is not a readable .xlsx (no zip directory)");
  const count = view.getUint16(eocd + 10, true);
  let p = view.getUint32(eocd + 16, true);
  const names = new TextDecoder();
  const entries = new Map<string, () => Promise<Uint8Array>>();
  for (let n = 0; n < count; n++) {
    if (view.getUint32(p, true) !== 0x02014b50) throw new Error("the .xlsx zip directory is damaged");
    const method = view.getUint16(p + 10, true);
    const size = view.getUint32(p + 20, true);
    const nameLen = view.getUint16(p + 28, true);
    const extraLen = view.getUint16(p + 30, true);
    const commentLen = view.getUint16(p + 32, true);
    const local = view.getUint32(p + 42, true);
    const name = names.decode(bytes.subarray(p + 46, p + 46 + nameLen));
    p += 46 + nameLen + extraLen + commentLen;
    entries.set(name, async () => {
      const start = local + 30 + view.getUint16(local + 26, true) + view.getUint16(local + 28, true);
      const data = bytes.subarray(start, start + size);
      if (method === 0) return data;
      if (method === 8) return inflateRaw(data);
      throw new Error(`the .xlsx uses zip compression method ${method}, which this reader does not support`);
    });
  }
  return entries;
}

// ── xlsx ─────────────────────────────────────────────────────────────────────

function xml(bytes: Uint8Array): Document {
  return new DOMParser().parseFromString(new TextDecoder().decode(bytes), "application/xml");
}

function byName(node: Document | Element, local: string): Element[] {
  return Array.from(node.getElementsByTagNameNS("*", local));
}

/** Text of an <si> or <is>: its runs' <t>, without phonetic (<rPh>) text. */
function richText(el: Element): string {
  return byName(el, "t")
    .filter((t) => t.parentElement?.localName !== "rPh")
    .map((t) => t.textContent ?? "")
    .join("");
}

/** "AB12" -> 27 (zero-based column). */
function columnIndex(ref: string): number {
  let n = 0;
  for (const ch of ref.toUpperCase()) {
    const c = ch.charCodeAt(0);
    if (c < 65 || c > 90) break;
    n = n * 26 + (c - 64);
  }
  return n - 1;
}

async function part(entries: Map<string, () => Promise<Uint8Array>>, name: string): Promise<Document | null> {
  const read = entries.get(name);
  return read ? xml(await read()) : null;
}

/** The first worksheet of an .xlsx as rows of cell strings. */
export async function xlsxRows(bytes: Uint8Array): Promise<{ rows: string[][]; sheet: string }> {
  const entries = zipEntries(bytes);
  const workbook = await part(entries, "xl/workbook.xml");
  const first = workbook ? byName(workbook, "sheet")[0] : undefined;
  if (!workbook || !first) throw new Error("the .xlsx has no worksheet");
  const sheet = first.getAttribute("name") ?? "Sheet1";
  const rid =
    first.getAttributeNS("http://schemas.openxmlformats.org/officeDocument/2006/relationships", "id") ??
    first.getAttribute("r:id");
  const rels = await part(entries, "xl/_rels/workbook.xml.rels");
  const target = rels
    ? byName(rels, "Relationship").find((r) => r.getAttribute("Id") === rid)?.getAttribute("Target")
    : undefined;
  const path = target
    ? target.startsWith("/")
      ? target.slice(1)
      : `xl/${target}`
    : "xl/worksheets/sheet1.xml";
  const ws = await part(entries, path);
  if (!ws) throw new Error(`the .xlsx's first sheet (${sheet}) is missing`);

  const sharedDoc = await part(entries, "xl/sharedStrings.xml");
  const shared = sharedDoc ? byName(sharedDoc, "si").map(richText) : [];

  const rows: string[][] = [];
  for (const row of byName(ws, "row")) {
    const cells: string[] = [];
    let next = 0;
    for (const c of Array.from(row.children).filter((e) => e.localName === "c")) {
      const ref = c.getAttribute("r");
      const j = ref ? columnIndex(ref) : next;
      next = j + 1;
      const type = c.getAttribute("t");
      const v = Array.from(c.children).find((e) => e.localName === "v")?.textContent ?? "";
      let value: string;
      if (type === "s") value = shared[Number(v)] ?? "";
      else if (type === "inlineStr") {
        const is = Array.from(c.children).find((e) => e.localName === "is");
        value = is ? richText(is) : "";
      } else if (type === "b") value = v === "1" ? "true" : "false";
      else if (type === "e") value = ""; // #N/A, #DIV/0! ...: a missing value
      else value = v;
      while (cells.length < j) cells.push("");
      cells[j] = value;
    }
    if (cells.some((x) => x.trim() !== "")) rows.push(cells);
  }
  // Width is the header's; trailing empty cells beyond it are formatting, not data.
  let width = rows[0]?.length ?? 0;
  while (width > 0 && (rows[0]?.[width - 1] ?? "").trim() === "") width--;
  const trimmed = rows.map((r) => {
    let last = r.length - 1;
    while (last >= 0 && (r[last] ?? "").trim() === "") last--;
    const cut = r.slice(0, Math.max(width, last + 1));
    while (cut.length < width) cut.push("");
    return cut;
  });
  return { rows: trimmed, sheet };
}

async function readXlsx(bytes: Uint8Array): Promise<ReadTable> {
  const { rows, sheet } = await xlsxRows(bytes);
  return { table: tableFromRows(rows), sheet };
}
