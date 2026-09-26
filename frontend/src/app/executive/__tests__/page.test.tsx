/**
 * Executive page -- each figure says which rows and which window it is over.
 *
 * Preconditions: GET /api/v1/platform/metrics (backend/src/api/platform.py)
 * returns weekly_sessions (the four weeks ending this one, oldest first; each
 * week's approval_rate and escalation_count count every audit_log row that
 * week) and overall_approval_rate (every audit_log row, all time).
 *
 * The tour saw a tile "Approval rate 76%" and a footer "Platform-wide approval
 * rate: 74%" with nothing saying one is this week's and the other all time.
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const { platformApiMock } = vi.hoisted(() => ({
  platformApiMock: { metrics: vi.fn(), connectors: vi.fn(), drift: vi.fn() },
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, platformApi: platformApiMock };
});

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), usePathname: () => "/executive" }));

import ExecutivePage from "../page";

const week = (label: string, sessions: number, rate: number, escalations: number) => ({
  week: label,
  week_start: "2026-09-21T00:00:00+00:00",
  session_count: sessions,
  approval_rate: rate,
  escalation_count: escalations,
});

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ExecutivePage />
    </QueryClientProvider>
  );
}

describe("ExecutivePage — approval rates and escalations", () => {
  it("labels this week's rate and the all-time rate, and sums the four weeks' escalations", async () => {
    platformApiMock.metrics.mockResolvedValue({
      weekly_sessions: [
        week("Aug 31 – Sep 6", 56, 0.75, 1),
        week("Sep 7 – Sep 13", 56, 0.75, 0),
        week("Sep 14 – Sep 20", 56, 0.75, 2),
        week("Sep 21 – Sep 27", 52, 0.76, 1),
      ],
      policy_patterns: [],
      fragment_stats: { total: 0, approved: 0, under_review: 0, draft: 0, deprecated: 0 },
      total_audit_entries: 267,
      total_sessions_all_time: 247,
      overall_approval_rate: 0.74,
      last_activity_at: "2026-09-26T12:42:50+00:00",
    });

    renderPage();

    const rate = await screen.findByTestId("kpi-approval-rate");
    expect(rate).toHaveTextContent(/this week/i);
    expect(rate).toHaveTextContent("76%");
    const overall = screen.getByTestId("overall-approval-rate");
    expect(overall).toHaveTextContent(/all time/i);
    expect(overall).toHaveTextContent("74%");
    const escalations = screen.getByTestId("kpi-escalations");
    expect(escalations).toHaveTextContent("4");
  });
});
