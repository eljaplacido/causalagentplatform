import Link from "next/link";

/**
 * 404 — rendered for any URL no page matches, below the site navigation
 * (the root layout's SiteNav), with a link home.
 */
export default function NotFound() {
  return (
    <main className="flex min-h-[60vh] flex-col items-center justify-center gap-3 px-4 text-center">
      <p className="font-mono text-xs text-muted-foreground">404</p>
      <h1 className="text-lg font-bold text-foreground">Page not found</h1>
      <p className="max-w-sm text-sm text-muted-foreground">
        No page lives at this address. Use the navigation above, or go back home.
      </p>
      <Link
        href="/"
        className="rounded-md border border-border px-3 py-1.5 text-sm font-medium text-foreground hover:bg-muted focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        Go home
      </Link>
    </main>
  );
}
