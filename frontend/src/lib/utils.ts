import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/**
 * Format an ISO8601 timestamp to a locale-aware display string.
 */
export function formatTimestamp(
  iso: string,
  opts: Intl.DateTimeFormatOptions = {
    dateStyle: "short",
    timeStyle: "medium",
  }
): string {
  try {
    return new Intl.DateTimeFormat(undefined, opts).format(new Date(iso));
  } catch {
    return iso;
  }
}

/**
 * Format elapsed milliseconds to a human-readable string.
 * e.g. 1234 → "1.2s"
 */
export function formatElapsed(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

/**
 * Clamp a number between min and max.
 */
export function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

/**
 * Format a confidence score (0.0–1.0) as a percentage string.
 */
export function formatConfidence(score: number): string {
  return `${Math.round(clamp(score, 0, 1) * 100)}%`;
}

/**
 * Truncate a string to maxLength, appending an ellipsis if truncated.
 */
export function truncate(str: string, maxLength: number): string {
  if (str.length <= maxLength) return str;
  return `${str.slice(0, maxLength - 1)}…`;
}
