/**
 * CynefinBadge on a value outside the Cynefin vocabulary.
 *
 * Preconditions: `domain` is typed CynefinDomain, but at runtime it comes from
 * backend strings, which a caller may pass through unchecked.
 * Postconditions: a known domain renders its label; an unknown value renders
 * without throwing, shows the value it was given, and does not claim a Cynefin
 * classification it does not have.
 * Undefined, not asserted: the styling of the fallback.
 */

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { CynefinBadge } from "../CynefinBadge";
import type { CynefinDomain } from "@/lib/types";

describe("CynefinBadge", () => {
  it("renders a known domain's label", () => {
    render(<CynefinBadge domain="COMPLICATED" />);
    expect(screen.getByTestId("cynefin-badge")).toHaveTextContent("Complicated");
  });

  it("renders an unknown key without throwing and shows the raw value", () => {
    render(<CynefinBadge domain={"DEVOPS" as CynefinDomain} />);
    const badge = screen.getByTestId("cynefin-badge");
    expect(badge).toHaveTextContent("DEVOPS");
    expect(badge.getAttribute("aria-label")).toMatch(/unrecognised/i);
  });
});
