"use client";

/**
 * AuditLogPanel — Collapsible panel showing recent audit entries.
 * Progressive disclosure Level 3 component.
 *
 * Columns: timestamp, agent, action, verdict (colored), policy rules.
 */

import { useState } from "react";
import { ChevronDown, ChevronRight, Shield } from "lucide-react";
import { cn, formatTimestamp } from "@/lib/utils";
import { GuardianVerdictBadge } from "./GuardianVerdictBadge";
import type { AuditLogRow } from "@/lib/types";

export interface AuditLogPanelProps {
  entries: AuditLogRow[];
  /** Initially expanded */
  defaultOpen?: boolean;
  className?: string;
}

export function AuditLogPanel({
  entries,
  defaultOpen = false,
  className,
}: AuditLogPanelProps) {
  const [isOpen, setIsOpen] = useState(defaultOpen);

  return (
    <div
      data-testid="audit-log-panel"
      className={cn("rounded-lg border border-border", className)}
    >
      {/* Collapsible header */}
      <button
        onClick={() => setIsOpen((prev) => !prev)}
        aria-expanded={isOpen}
        aria-controls="audit-log-content"
        className={cn(
          "flex w-full items-center gap-2 px-4 py-3 text-left",
          "hover:bg-muted/50 transition-colors",
          isOpen ? "border-b border-border" : ""
        )}
      >
        {isOpen ? (
          <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        ) : (
          <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        )}
        <Shield className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
        <span className="text-sm font-semibold text-foreground">
          Audit Trail
        </span>
        <span className="ml-auto rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
          {entries.length}
        </span>
      </button>

      {/* Table content */}
      {isOpen && (
        <div id="audit-log-content">
          {entries.length === 0 ? (
            <p className="px-4 py-6 text-center text-sm text-muted-foreground">
              No audit entries yet.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border bg-muted/30">
                    <th className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                      Timestamp
                    </th>
                    <th className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                      Agent
                    </th>
                    <th className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                      Action
                    </th>
                    <th className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                      Verdict
                    </th>
                    <th className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                      Policy Rules
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {entries.map((entry, i) => (
                    <tr
                      key={entry.id}
                      data-testid="audit-entry"
                      className={cn(
                        "border-b border-border last:border-0",
                        i % 2 === 0 ? "bg-background" : "bg-muted/20"
                      )}
                    >
                      <td className="whitespace-nowrap px-4 py-2 font-mono text-xs text-muted-foreground">
                        {formatTimestamp(entry.timestamp)}
                      </td>
                      <td className="px-4 py-2 text-xs font-medium text-foreground">
                        {entry.agent}
                      </td>
                      <td className="px-4 py-2 text-xs text-foreground">
                        {entry.action}
                      </td>
                      <td className="px-4 py-2">
                        <GuardianVerdictBadge
                          verdict={entry.verdict}
                          size="sm"
                        />
                      </td>
                      <td className="px-4 py-2">
                        {entry.policyRulesCited.length > 0 ? (
                          <div className="flex flex-wrap gap-1">
                            {entry.policyRulesCited.map((rule) => (
                              <span
                                key={rule}
                                className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs text-muted-foreground"
                              >
                                {rule}
                              </span>
                            ))}
                          </div>
                        ) : (
                          <span className="text-xs text-muted-foreground">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
