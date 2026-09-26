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

/**
 * A number, interval or flag from a backend or kernel response, as sent.
 * Causal numbers are shown verbatim -- never rounded, rescaled or recomputed in
 * the UI -- and an absent value is said to be absent (`null`), not shown as 0.
 */
export function verbatim(
  value: number | boolean | string | readonly (number | null)[] | null | undefined
): string {
  if (value === null || value === undefined) return "null";
  if (Array.isArray(value)) return `[${value.map((v) => (v === null ? "null" : String(v))).join(", ")}]`;
  return String(value);
}

/**
 * A number as the kernel writes it in a rendered narrative:
 * causal_core.claims.format_number (4 significant digits, "%.4g", integers
 * whole, "e+4" rather than "e+04"). Rounding for display only; the value
 * itself is the backend's.
 */
export function formatKernelNumber(x: number, sig = 4): string {
  if (!Number.isFinite(x)) return String(x);
  if (x === 0) return "0";
  if (Number.isInteger(x) && Math.abs(x) < 1e15) return String(x);
  const [mantissa, expText] = x.toExponential(sig - 1).split("e");
  const exp = Number(expText);
  const trim = (s: string) => (s.includes(".") ? s.replace(/\.?0+$/, "") : s);
  if (exp < -4 || exp >= sig) {
    return `${trim(mantissa!)}e${exp < 0 ? "-" : "+"}${Math.abs(exp)}`;
  }
  return trim(x.toFixed(sig - 1 - exp));
}

/**
 * A figure from a backend response for reading: numbers in the kernel's format
 * (formatKernelNumber), an interval as "[lo, hi]", an absent value as "—".
 * Pair it with `verbatim` (e.g. in a title) where the exact value matters.
 */
export function formatFigure(
  value: number | boolean | string | readonly (number | null)[] | null | undefined
): string {
  if (value === null || value === undefined) return "—";
  if (Array.isArray(value)) {
    return `[${value.map((v) => (v === null ? "—" : formatKernelNumber(v))).join(", ")}]`;
  }
  if (typeof value === "number") return formatKernelNumber(value);
  return String(value);
}
