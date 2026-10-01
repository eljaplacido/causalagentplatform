"use client";

/**
 * New decision — start a causal analysis from the UI (UIX §2).
 *
 * Personas: Causal Analyst and Executive (a question about an action, answered
 * with a verdict), Governance Officer (every answer is sealed in the ledger).
 *
 * Data: POST /api/v1/decisions (analyse, rule, record, in one call) and
 * GET /api/v1/models for the registered graphs. The form and the result are
 * NewDecisionForm and DecideResult; nothing here computes a number.
 */

import Link from "next/link";
import { FilePlus2 } from "lucide-react";

import { NewDecisionForm } from "@/components/decisions/NewDecisionForm";

export default function NewDecisionPage() {
  return (
    <div className="flex min-h-screen flex-col bg-background">
      <header className="flex flex-wrap items-center gap-3 border-b border-border bg-background px-4 py-3">
        <h1 className="flex items-center gap-2 text-sm font-bold text-foreground">
          <FilePlus2 className="h-4 w-4 text-muted-foreground" aria-hidden />
          New decision
        </h1>
        <p className="text-xs text-muted-foreground">
          Data, a causal graph and what the action is worth: the platform analyses, rules and
          seals the result in the{" "}
          <Link href="/decisions" className="underline hover:text-foreground">
            decision ledger
          </Link>
          .
        </p>
      </header>
      <main className="flex-1 p-4 lg:p-6">
        <NewDecisionForm />
      </main>
    </div>
  );
}
