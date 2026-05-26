"use client";

import { useEffect, useState } from "react";
import { QueryClientProvider } from "@tanstack/react-query";
import { createQueryClient } from "@/lib/query-client";
import { useAuthStore } from "@/lib/store";

// ─── Local-dev auth token injection ───────────────────────────────────────────

/**
 * DevAuthInit — sets the dev bypass token in the Zustand auth store when
 * NEXT_PUBLIC_ENVIRONMENT=local.
 *
 * The backend accepts "dev-local-token" only when:
 *   ENV=local AND DEV_AUTH_BYPASS=true
 *
 * This component renders nothing; it only performs a side-effect once.
 */
function DevAuthInit() {
  const { token, setToken } = useAuthStore();

  useEffect(() => {
    if (
      process.env["NEXT_PUBLIC_ENVIRONMENT"] === "local" &&
      !token
    ) {
      setToken("dev-local-token", "dev-user-001");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return null;
}

// ─── Providers ────────────────────────────────────────────────────────────────

interface ProvidersProps {
  children: React.ReactNode;
}

export function Providers({ children }: ProvidersProps) {
  // Stable QueryClient instance per component tree
  const [queryClient] = useState(() => createQueryClient());

  return (
    <QueryClientProvider client={queryClient}>
      <DevAuthInit />
      {children}
    </QueryClientProvider>
  );
}
