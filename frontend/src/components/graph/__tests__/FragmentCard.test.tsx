/**
 * FragmentCard: the footer badge follows the fragment's approvalStatus.
 *
 * The tour found a green "Approved" Guardian badge on DRAFT fragments: the
 * card showed guardianVerdict (derived from a verdict ref) without regard to
 * the status. A fragment that is not APPROVED must never read as approved.
 */

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { FragmentCard, type FragmentCardData } from "../FragmentCard";
import type { FragmentApprovalStatus } from "@causalplatform/semantic-types";
import type { GuardianVerdict } from "@/lib/types";

function fragment(
  approvalStatus: FragmentApprovalStatus,
  guardianVerdict: GuardianVerdict
): FragmentCardData {
  return {
    id: "frag-1",
    name: "Deployment → Latency",
    domain: "devops",
    description: "",
    edgeCount: 1,
    usageCount: 0,
    version: "1.0.0",
    approvalStatus,
    guardianVerdict,
    tags: [],
    createdAt: "2026-09-26T12:00:00Z",
  };
}

describe("FragmentCard approval badge", () => {
  it.each<FragmentApprovalStatus>(["DRAFT", "DEPRECATED"])(
    "a %s fragment carrying an APPROVED verdict ref does not show Approved",
    (status) => {
      render(<FragmentCard fragment={fragment(status, "APPROVED")} />);
      const badge = screen.getByTestId("fragment-approval-badge");
      expect(badge).toHaveAttribute("data-status", status);
      expect(badge).not.toHaveTextContent(/approved/i);
      expect(screen.queryByTestId("guardian-verdict-badge")).toBeNull();
    }
  );

  it("an APPROVED fragment shows the Guardian's approval", () => {
    render(<FragmentCard fragment={fragment("APPROVED", "APPROVED")} />);
    expect(screen.getByTestId("fragment-approval-badge")).toHaveTextContent(/approved/i);
    expect(screen.getByTestId("guardian-verdict-badge")).toHaveAttribute("data-verdict", "APPROVED");
  });

  it("a DRAFT fragment says it is a draft awaiting review", () => {
    render(<FragmentCard fragment={fragment("DRAFT", "PENDING")} />);
    expect(screen.getByTestId("fragment-approval-badge")).toHaveTextContent(/draft/i);
  });
});
