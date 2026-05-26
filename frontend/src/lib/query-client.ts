"use client";

import { QueryClient } from "@tanstack/react-query";

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // 60 second stale time — operational data refreshes frequently
        staleTime: 60 * 1000,
        // Do not retry on 4xx errors — those are client errors
        retry: (failureCount, error) => {
          if (
            error instanceof Error &&
            "status" in error &&
            typeof (error as { status?: number }).status === "number"
          ) {
            const status = (error as { status: number }).status;
            if (status >= 400 && status < 500) return false;
          }
          return failureCount < 2;
        },
      },
      mutations: {
        retry: false,
      },
    },
  });
}
