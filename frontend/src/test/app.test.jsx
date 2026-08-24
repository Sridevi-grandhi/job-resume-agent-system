import { render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import App from "../App";

beforeEach(() => {
  history.pushState({}, "", "/login");
  globalThis.fetch = vi.fn(() => Promise.resolve(new Response(JSON.stringify({ status: "healthy" }), { status: 200 })));
});

it("renders the application login route", () => {
  render(<App />);
  expect(screen.getByRole("heading", { name: /welcome back/i })).toBeInTheDocument();
});
