/**
 * Zustand stores for global platform state.
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { Persona } from "@/lib/types";

// ─── Persona store ─────────────────────────────────────────────────────────────

interface PersonaState {
  currentPersona: Persona;
  setPersona: (persona: Persona) => void;
}

export const usePersonaStore = create<PersonaState>()(
  persist(
    (set) => ({
      currentPersona: "OPERATOR",
      setPersona: (persona) => set({ currentPersona: persona }),
    }),
    {
      name: "causal-platform-persona",
    }
  )
);

// ─── Auth store ────────────────────────────────────────────────────────────────

interface AuthState {
  token: string | null;
  userId: string | null;
  setToken: (token: string, userId: string) => void;
  clearAuth: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      userId: null,
      setToken: (token, userId) => set({ token, userId }),
      clearAuth: () => set({ token: null, userId: null }),
    }),
    {
      name: "causal-platform-auth",
    }
  )
);

// ─── ThinkingIndicator store ───────────────────────────────────────────────────

import type { ThinkingStage } from "@/lib/types";

interface ThinkingState {
  isThinking: boolean;
  stage: ThinkingStage | string;
  startedAt: number | null;
  cynefinDomain: string | null;
  startThinking: (initialStage?: ThinkingStage | string) => void;
  updateStage: (stage: ThinkingStage | string, domain?: string) => void;
  stopThinking: () => void;
}

export const useThinkingStore = create<ThinkingState>()((set) => ({
  isThinking: false,
  stage: "Classifying domain...",
  startedAt: null,
  cynefinDomain: null,

  startThinking: (initialStage = "Classifying domain...") =>
    set({
      isThinking: true,
      stage: initialStage,
      startedAt: Date.now(),
      cynefinDomain: null,
    }),

  updateStage: (stage, domain) =>
    set((state) => ({
      stage,
      cynefinDomain: domain ?? state.cynefinDomain,
    })),

  stopThinking: () =>
    set({
      isThinking: false,
      stage: "Classifying domain...",
      startedAt: null,
      cynefinDomain: null,
    }),
}));
