/**
 * One navigation for every page.
 *
 * The tour found two unconnected nav systems: the persona pages (/incidents,
 * /analyst, /governance, /engineer, /executive) linked to nothing but a persona
 * dropdown that relabelled without navigating, and the analysis views
 * (/workbench, /decisions, /registry) linked only to each other, with no
 * brand/home link on /decisions or /registry.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const { nav } = vi.hoisted(() => ({
  nav: { push: vi.fn(), pathname: "/decisions" },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: nav.push, replace: vi.fn() }),
  usePathname: () => nav.pathname,
}));

import { SiteNav } from "../SiteNav";
import { PersonaSelector } from "../PersonaSelector";
import { usePersonaStore } from "@/lib/store";

beforeEach(() => {
  nav.push.mockReset();
  nav.pathname = "/decisions";
  usePersonaStore.setState({ currentPersona: "OPERATOR" });
});

describe("SiteNav", () => {
  it("links home and to every persona page and analysis view", () => {
    render(<SiteNav />);
    const bar = screen.getByRole("navigation", { name: /site/i });
    expect(within(bar).getByRole("link", { name: /causal platform/i })).toHaveAttribute("href", "/");
    const hrefs = within(bar)
      .getAllByRole("link")
      .map((a) => a.getAttribute("href"));
    for (const href of [
      "/incidents",
      "/analyst",
      "/governance",
      "/engineer",
      "/executive",
      "/workbench",
      "/decisions",
      "/registry",
    ]) {
      expect(hrefs).toContain(href);
    }
  });

  it("marks the current page", () => {
    nav.pathname = "/registry";
    render(<SiteNav />);
    const current = screen
      .getAllByRole("link")
      .filter((a) => a.getAttribute("aria-current") === "page");
    expect(current.map((a) => a.getAttribute("href"))).toEqual(["/registry"]);
  });
});

describe("PersonaSelector dropdown", () => {
  it("navigates to the chosen persona's page, not only relabels", async () => {
    const user = userEvent.setup();
    render(<PersonaSelector layout="dropdown" />);
    await user.selectOptions(screen.getByLabelText(/switch persona/i), "EXECUTIVE");
    expect(usePersonaStore.getState().currentPersona).toBe("EXECUTIVE");
    expect(nav.push).toHaveBeenCalledWith("/executive");
  });
});
