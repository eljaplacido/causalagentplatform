/**
 * The 404 page says the page does not exist and links home.
 * The tour's /does-not-exist showed Next's bare default 404, with no way back.
 */

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import NotFound from "../not-found";

describe("not-found page", () => {
  it("says the page was not found and links home", () => {
    render(<NotFound />);
    expect(screen.getByRole("heading", { name: /page not found/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /home/i })).toHaveAttribute("href", "/");
  });
});
