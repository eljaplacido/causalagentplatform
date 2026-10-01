"use client";

/**
 * Root landing page.
 * Shows a persona selector for first-time visitors;
 * otherwise redirects OPERATOR persona directly to /incidents.
 */

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { usePersonaStore } from "@/lib/store";
import { PersonaSelector } from "@/components/shared/PersonaSelector";
import type { Persona } from "@/lib/types";
import { PERSONA_LANDING as PERSONA_ROUTES } from "@/lib/navigation";

export default function RootPage() {
  const router = useRouter();
  const { currentPersona, setPersona } = usePersonaStore();
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  // After hydration, redirect OPERATOR immediately — fastest path for on-call SREs
  useEffect(() => {
    if (!mounted) return;
    const savedPersona = localStorage.getItem("causal-platform-persona");
    if (savedPersona) {
      // Already selected a persona before — redirect directly
      router.replace(PERSONA_ROUTES[currentPersona] ?? "/incidents");
    }
  }, [mounted, currentPersona, router]);

  function handlePersonaSelect(persona: Persona) {
    setPersona(persona);
    router.push(PERSONA_ROUTES[persona] ?? "/incidents");
  }

  if (!mounted) {
    // Avoid hydration mismatch — render nothing on server
    return null;
  }

  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-background px-4">
      <div className="w-full max-w-lg space-y-8">
        {/* Platform wordmark */}
        <div className="text-center">
          <h1 className="text-3xl font-bold tracking-tight text-foreground">
            Causal Platform
          </h1>
          <p className="mt-2 text-sm text-muted-foreground">
            Governed causal AI — epistemically aware, human-in-the-loop
          </p>
        </div>

        {/* Persona selection */}
        <div className="rounded-xl border border-border bg-card p-8 shadow-sm">
          <h2 className="mb-1 text-lg font-semibold text-foreground">
            Select your role
          </h2>
          <p className="mb-6 text-sm text-muted-foreground">
            Your role determines which view and data surfaces are shown by
            default. You can switch at any time.
          </p>

          <PersonaSelector
            layout="card-grid"
            onSelect={handlePersonaSelect}
          />
        </div>

        <p className="text-center text-xs text-muted-foreground">
          All decisions are governed by the Guardian policy engine and recorded
          in the immutable audit trail.
        </p>
      </div>
    </main>
  );
}
