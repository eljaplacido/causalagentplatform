"use client";

/**
 * PersonaSelector — Switches between platform personas.
 *
 * Persists selection in localStorage via Zustand persist middleware.
 * Supports two layouts:
 *   "dropdown"  — compact dropdown for nav bars
 *   "card-grid" — full card grid for the landing page
 */

import { usePersonaStore } from "@/lib/store";
import { cn } from "@/lib/utils";
import type { Persona } from "@/lib/types";
import {
  Shield,
  BarChart3,
  Briefcase,
  GanttChartSquare,
  Wrench,
} from "lucide-react";

export interface PersonaSelectorProps {
  layout?: "dropdown" | "card-grid";
  /** Called after persona is selected and persisted */
  onSelect?: (persona: Persona) => void;
  className?: string;
}

const PERSONA_META: Record<
  Persona,
  {
    label: string;
    description: string;
    icon: typeof Shield;
    primarySurface: string;
  }
> = {
  OPERATOR: {
    label: "Operator / SRE",
    description: "Incident response, intervention approval, real-time triage",
    icon: Shield,
    primarySurface: "/incidents",
  },
  ANALYST: {
    label: "Causal Analyst",
    description: "DAG workbench, simulation, assumption validation",
    icon: BarChart3,
    primarySurface: "/analyst",
  },
  EXECUTIVE: {
    label: "Executive",
    description: "Decision summaries, outcome tracking, intervention comparison",
    icon: Briefcase,
    primarySurface: "/executive",
  },
  GOVERNANCE: {
    label: "Governance Officer",
    description: "Review queue, audit trail, compliance reporting",
    icon: GanttChartSquare,
    primarySurface: "/governance",
  },
  ENGINEER: {
    label: "Platform Engineer",
    description: "Connector health, graph fragments, drift alerts",
    icon: Wrench,
    primarySurface: "/engineer",
  },
};

const PERSONA_ORDER: Persona[] = [
  "OPERATOR",
  "ANALYST",
  "EXECUTIVE",
  "GOVERNANCE",
  "ENGINEER",
];

export function PersonaSelector({
  layout = "dropdown",
  onSelect,
  className,
}: PersonaSelectorProps) {
  const { currentPersona, setPersona } = usePersonaStore();

  function handleSelect(persona: Persona) {
    setPersona(persona);
    onSelect?.(persona);
  }

  if (layout === "card-grid") {
    return (
      <div
        data-testid="persona-selector"
        role="radiogroup"
        aria-label="Select your persona"
        className={cn(
          "grid grid-cols-1 gap-3 sm:grid-cols-2",
          className
        )}
      >
        {PERSONA_ORDER.map((persona) => {
          const meta = PERSONA_META[persona];
          const Icon = meta.icon;
          const isSelected = persona === currentPersona;

          return (
            <button
              key={persona}
              role="radio"
              aria-checked={isSelected}
              data-testid={`persona-option-${persona.toLowerCase()}`}
              onClick={() => handleSelect(persona)}
              className={cn(
                "group flex w-full items-start gap-3 rounded-lg border p-4 text-left transition-all",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                isSelected
                  ? "border-primary bg-primary/5 ring-1 ring-primary"
                  : "border-border bg-card hover:border-primary/50 hover:bg-muted/50"
              )}
            >
              <Icon
                className={cn(
                  "mt-0.5 h-5 w-5 shrink-0",
                  isSelected
                    ? "text-primary"
                    : "text-muted-foreground group-hover:text-foreground"
                )}
                aria-hidden="true"
              />
              <div>
                <p
                  className={cn(
                    "text-sm font-semibold",
                    isSelected ? "text-foreground" : "text-foreground"
                  )}
                >
                  {meta.label}
                </p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {meta.description}
                </p>
              </div>
            </button>
          );
        })}
      </div>
    );
  }

  // Dropdown layout for nav bar
  const currentMeta = PERSONA_META[currentPersona];
  const CurrentIcon = currentMeta.icon;

  return (
    <div data-testid="persona-selector" className={cn("relative", className)}>
      <select
        value={currentPersona}
        onChange={(e) => handleSelect(e.target.value as Persona)}
        aria-label="Switch persona view"
        className={cn(
          "flex h-9 w-full cursor-pointer appearance-none items-center rounded-md border border-input",
          "bg-background pl-8 pr-8 text-sm shadow-sm",
          "focus:outline-none focus:ring-2 focus:ring-ring",
          "text-foreground"
        )}
      >
        {PERSONA_ORDER.map((persona) => (
          <option key={persona} value={persona}>
            {PERSONA_META[persona]?.label ?? persona}
          </option>
        ))}
      </select>
      <CurrentIcon
        className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
        aria-hidden="true"
      />
    </div>
  );
}
