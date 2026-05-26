/**
 * ThinkingIndicator tests
 *
 * Critical UX contract: must appear within 150ms of agent submission.
 * Tests:
 *   1. Appears within 150ms of agent submission (synchronous render, not delayed)
 *   2. Shows stage text
 *   3. Shows cynefin badge once domain classified
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act, cleanup } from "@testing-library/react";
import { ThinkingIndicator } from "../ThinkingIndicator";
import type { CynefinDomain } from "@/lib/types";

describe("ThinkingIndicator", () => {
  beforeEach(() => {
    // Only fake setInterval/clearInterval — leave setTimeout real so React's
    // act() (which uses setTimeout(resolve, 0) internally) can flush effects.
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
  });

  afterEach(() => {
    cleanup();           // unmount → fires useEffect cleanup → clearInterval
    vi.clearAllTimers(); // clear any remaining fake timers
    vi.useRealTimers();  // restore real timers
  });

  it("renders immediately — within 150ms of being mounted", () => {
    // The 150ms contract means the component must be rendered without any
    // internal delay. We verify this by asserting the data-testid is present
    // synchronously after render() — no timers needed.
    const startTime = performance.now();

    render(
      <ThinkingIndicator
        stage="Classifying domain..."
        elapsed={0}
      />
    );

    const indicator = screen.getByTestId("thinking-indicator");
    const renderTime = performance.now() - startTime;

    expect(indicator).toBeDefined();
    // Render should be effectively synchronous (well under 150ms in JSDOM)
    expect(renderTime).toBeLessThan(150);
  });

  it("shows the current stage text", () => {
    render(
      <ThinkingIndicator
        stage="Analyzing causal paths..."
        elapsed={1200}
      />
    );

    expect(screen.getByText("Analyzing causal paths...")).toBeDefined();
  });

  it("shows elapsed time", () => {
    render(
      <ThinkingIndicator
        stage="Checking policy..."
        elapsed={2500}
      />
    );

    // formatElapsed(2500) → "2.5s"
    expect(screen.getByText("2.5s")).toBeDefined();
  });

  it("does NOT show cynefin badge when domain is not yet classified", () => {
    render(
      <ThinkingIndicator
        stage="Classifying domain..."
        elapsed={0}
      />
    );

    expect(screen.queryByTestId("thinking-cynefin-badge")).toBeNull();
  });

  it("shows cynefin badge once domain is classified (prop provided)", () => {
    const domain: CynefinDomain = "COMPLICATED";

    render(
      <ThinkingIndicator
        stage="Analyzing causal paths..."
        elapsed={800}
        cynefinDomain={domain}
        cynefinConfidence={0.82}
      />
    );

    const badge = screen.getByTestId("thinking-cynefin-badge");
    expect(badge).toBeDefined();

    // The inner CynefinBadge should show the domain label
    expect(screen.getByText("Complicated")).toBeDefined();
  });

  it("shows CHAOTIC domain badge with correct color context", () => {
    const domain: CynefinDomain = "CHAOTIC";

    render(
      <ThinkingIndicator
        stage="Consulting Guardian..."
        elapsed={400}
        cynefinDomain={domain}
        cynefinConfidence={0.91}
      />
    );

    const badge = screen.getByTestId("thinking-cynefin-badge");
    expect(badge).toBeDefined();
    expect(screen.getByText("Chaotic")).toBeDefined();
  });

  it("increments elapsed time via internal ticker", () => {
    render(
      <ThinkingIndicator
        stage="Classifying domain..."
        elapsed={0}
      />
    );

    // Initially shows 0ms
    expect(screen.getByText("0ms")).toBeDefined();

    // Advance timer by 1 second
    act(() => {
      vi.advanceTimersByTime(1000);
    });

    // After 1s of ticking, should show ~1.0s
    expect(screen.getByText("1.0s")).toBeDefined();
  });

  it("has correct ARIA attributes for screen reader accessibility", () => {
    render(
      <ThinkingIndicator
        stage="Running simulation..."
        elapsed={0}
      />
    );

    const indicator = screen.getByRole("status");
    expect(indicator.getAttribute("aria-live")).toBe("polite");
    expect(indicator.getAttribute("aria-label")).toContain("Running simulation...");
  });

  it("updates stage text when prop changes", () => {
    const { rerender } = render(
      <ThinkingIndicator
        stage="Classifying domain..."
        elapsed={0}
      />
    );

    expect(screen.getByText("Classifying domain...")).toBeDefined();

    rerender(
      <ThinkingIndicator
        stage="Checking policy..."
        elapsed={900}
      />
    );

    expect(screen.getByText("Checking policy...")).toBeDefined();
    expect(screen.queryByText("Classifying domain...")).toBeNull();
  });
});
