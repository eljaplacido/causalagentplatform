/**
 * Engineer page -- hydrates without a mismatch.
 *
 * The tour saw React error #418 (hydration text mismatch) on every load of
 * /engineer: "Last checked" rendered new Date() during the prerender, so the
 * server's minute and the browser's differed. The page is rendered to HTML at
 * one time and hydrated at another, as a static export is; hydration must not
 * report a recoverable error.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { act } from "react";
import { renderToString } from "react-dom/server";
import { hydrateRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const { platformApiMock, semanticApiMock, governanceApiMock } = vi.hoisted(() => ({
  platformApiMock: { connectors: vi.fn(), drift: vi.fn(), metrics: vi.fn() },
  semanticApiMock: { fragments: vi.fn() },
  governanceApiMock: { auditLog: vi.fn() },
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    platformApi: platformApiMock,
    semanticApi: semanticApiMock,
    governanceApi: governanceApiMock,
  };
});

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), usePathname: () => "/engineer" }));

import EngineerPage from "../page";

function tree() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={queryClient}>
      <EngineerPage />
    </QueryClientProvider>
  );
}

afterEach(() => {
  vi.useRealTimers();
});

describe("EngineerPage — hydration", () => {
  it("hydrates prerendered HTML made at another time without a recoverable error", async () => {
    const never = () => new Promise(() => {});
    platformApiMock.connectors.mockImplementation(never);
    platformApiMock.drift.mockImplementation(never);
    semanticApiMock.fragments.mockImplementation(never);
    governanceApiMock.auditLog.mockImplementation(never);

    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-09-26T08:00:00Z"));
    const html = renderToString(tree());

    vi.setSystemTime(new Date("2026-09-26T15:47:00Z"));
    const container = document.createElement("div");
    container.innerHTML = html;
    document.body.appendChild(container);
    const errors: unknown[] = [];
    await act(async () => {
      hydrateRoot(container, tree(), { onRecoverableError: (e) => errors.push(e) });
    });
    expect(errors.map((e) => String(e))).toEqual([]);
    container.remove();
  });
});
