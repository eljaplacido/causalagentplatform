import "@testing-library/jest-dom";
import { vi } from "vitest";

// The App Router's hooks need a mounted router; page tests render pages
// outside one. PersonaSelector and SiteNav navigate with them, so every test
// gets a stand-in (a test that asserts navigation mocks the module itself).
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(),
}));
