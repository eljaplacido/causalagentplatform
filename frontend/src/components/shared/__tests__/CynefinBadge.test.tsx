/**
 * CynefinBadge tests.
 *
 * The badge renders values that arrive from the backend as strings, so the
 * type annotation is not a guarantee: a non-Cynefin value (a business domain
 * such as "devops" once reached it from the governance page) must render a
 * neutral "Unclassified" badge, not throw and take the page down with it, and
 * must never be relabelled as a real Cynefin domain.
 */

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { CynefinBadge } from "../CynefinBadge";
import type { CynefinDomain } from "@/lib/types";

describe("CynefinBadge", () => {
  it("renders a known domain with its label", () => {
    render(<CynefinBadge domain="COMPLEX" />);
    const badge = screen.getByTestId("cynefin-badge");
    expect(badge).toHaveAttribute("data-domain", "COMPLEX");
    expect(badge).toHaveTextContent("Complex");
  });

  it("accepts a known domain in any case", () => {
    render(<CynefinBadge domain={"complicated" as CynefinDomain} />);
    const badge = screen.getByTestId("cynefin-badge");
    expect(badge).toHaveAttribute("data-domain", "COMPLICATED");
    expect(badge).toHaveTextContent("Complicated");
  });

  it("renders an unknown value as Unclassified instead of crashing", () => {
    render(<CynefinBadge domain={"devops" as CynefinDomain} />);
    const badge = screen.getByTestId("cynefin-badge");
    expect(badge).toHaveAttribute("data-domain", "UNKNOWN");
    expect(badge).toHaveTextContent("Unclassified");
    expect(badge).not.toHaveTextContent(/Disorder|Clear|Complicated|Complex|Chaotic/);
    expect(badge.getAttribute("title")).toContain("devops");
  });

  it("renders a missing value as Unclassified instead of crashing", () => {
    render(<CynefinBadge domain={undefined as unknown as CynefinDomain} />);
    expect(screen.getByTestId("cynefin-badge")).toHaveTextContent("Unclassified");
  });
});
