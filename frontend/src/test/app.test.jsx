import { render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import App from "../App";

beforeEach(() => {
  localStorage.clear();
  history.pushState({}, "", "/login");
  globalThis.fetch = vi.fn(() => Promise.resolve(new Response(JSON.stringify({ status: "healthy" }), { status: 200 })));
});

it("renders the application login route", () => {
  render(<App />);
  expect(screen.getByRole("heading", { name: /welcome back/i })).toBeInTheDocument();
});

it("loads Settings at its protected route for a signed-in user", async () => {
  localStorage.setItem("currentUser", JSON.stringify({ user_id: 7, name: "Tanishka", email: "tanishka@example.test" }));
  history.pushState({}, "", "/settings");
  globalThis.fetch = vi.fn((input) => {
    const path = String(input);
    const payload = path.includes("/gmail/status/")
      ? { connected: false, status: "DISCONNECTED" }
      : path.includes("/email/preferences/")
        ? { automatic_gmail_scan_enabled: false, gmail_scan_frequency: "daily" }
        : { status: "healthy" };
    return Promise.resolve(new Response(JSON.stringify(payload), { status: 200 }));
  });
  render(<App />);
  expect(await screen.findByRole("heading", { name: "Settings" })).toBeInTheDocument();
  expect(screen.getAllByText("tanishka@example.test").length).toBeGreaterThan(0);
});

it("keeps job Preferences on its separate route", async () => {
  localStorage.setItem("currentUser", JSON.stringify({ user_id: 7, name: "Tanishka", email: "tanishka@example.test" }));
  history.pushState({}, "", "/preferences");
  globalThis.fetch = vi.fn((input) => {
    const payload = String(input).includes("/api/preferences/")
      ? { job_title: "Python Developer", schedule_time: "09:00:00" }
      : { status: "healthy" };
    return Promise.resolve(new Response(JSON.stringify(payload), { status: 200 }));
  });
  render(<App />);
  expect(await screen.findByRole("heading", { name: "Preferences" })).toBeInTheDocument();
  expect(await screen.findByDisplayValue("Python Developer")).toBeInTheDocument();
});
