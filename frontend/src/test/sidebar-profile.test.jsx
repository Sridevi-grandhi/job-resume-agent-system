import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation } from "react-router-dom";
import { renderRoute } from "./render.jsx";
import { makeApp } from "./mockApp";
import Sidebar from "../components/Sidebar";
import Topbar from "../components/Topbar";
import * as api from "../api";

vi.mock("../api", () => ({ getEmailLogs: vi.fn() }));

let app;
vi.mock("../context/AppContext", () => ({ useApp: () => app }));

function CurrentPath() {
  return <output data-testid="current-path">{useLocation().pathname}</output>;
}

function renderShell() {
  return renderRoute(<><Sidebar open={false} onClose={vi.fn()} /><Topbar onMenu={vi.fn()} /><CurrentPath /></>);
}

function profileButton() {
  return screen.getByRole("button", { name: /Test Candidate/i });
}

beforeEach(() => {
  localStorage.clear();
  api.getEmailLogs.mockReset().mockRejectedValue(new Error("no logs"));
  app = makeApp();
});

describe("shared sidebar profile menu", () => {
  it("removes both top-right controls and keeps the user row in the sidebar", () => {
    renderShell();
    const header = screen.getByRole("banner");
    expect(within(header).queryByRole("button", { name: /notifications/i })).toBeNull();
    expect(header.querySelector(".top-avatar, .top-actions")).toBeNull();
    expect(within(screen.getByRole("complementary")).getByText("candidate@example.test")).toBeInTheDocument();
    expect(profileButton()).toHaveAttribute("aria-expanded", "false");
    expect(app.logout).not.toHaveBeenCalled();
  });

  it("opens one in-sidebar menu above the user row with the requested item order and unread count", async () => {
    app.jobEmailEvents = [{ id: 101, subject: "Interview Invitation", resolution: "NEEDS_CONFIRMATION" }];
    renderShell();
    await userEvent.click(profileButton());
    const sidebar = screen.getByRole("complementary");
    const actions = sidebar.querySelector(".sidebar-profile-actions");
    expect(actions).toBeInTheDocument();
    expect(actions.parentElement).toHaveClass("sidebar-profile");
    expect(actions.compareDocumentPosition(profileButton()) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect([...actions.querySelectorAll(".sidebar-profile-item")].map((item) => item.textContent.trim())).toEqual([
      "Notifications1", "Settings", "Logout",
    ]);
    expect(screen.getAllByRole("button", { name: /notifications/i })).toHaveLength(1);
    await waitFor(() => expect(within(actions).getByText("1")).toBeInTheDocument());
    expect(app.logout).not.toHaveBeenCalled();
  });

  it("toggles with click and keyboard, and closes on Escape and outside click", async () => {
    renderShell();
    profileButton().focus();
    await userEvent.keyboard("{Enter}");
    expect(profileButton()).toHaveAttribute("aria-expanded", "true");
    await userEvent.click(profileButton());
    expect(profileButton()).toHaveAttribute("aria-expanded", "false");
    profileButton().focus();
    await userEvent.keyboard(" ");
    expect(profileButton()).toHaveAttribute("aria-expanded", "true");
    await userEvent.keyboard("{Escape}");
    expect(profileButton()).toHaveAttribute("aria-expanded", "false");
    expect(profileButton()).toHaveFocus();
    await userEvent.click(profileButton());
    fireEvent.mouseDown(document.body);
    expect(profileButton()).toHaveAttribute("aria-expanded", "false");
  });

  it("keeps notifications interactive inside the sidebar", async () => {
    renderShell();
    await userEvent.click(profileButton());
    const notificationButton = screen.getByRole("button", { name: /notifications/i });
    await userEvent.click(notificationButton);
    expect(within(screen.getByRole("complementary")).getByRole("menu")).toHaveClass("sidebar-notification-dropdown");
    expect(screen.getByText(/you're all caught up/i)).toBeInTheDocument();
    await userEvent.click(notificationButton);
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("routes Settings to its own page and closes the menu", async () => {
    renderShell();
    await userEvent.click(profileButton());
    await userEvent.click(screen.getByRole("link", { name: "Settings" }));
    expect(screen.getByTestId("current-path")).toHaveTextContent("/settings");
    expect(profileButton()).toHaveAttribute("aria-expanded", "false");
  });

  it("uses the existing logout handler only when Logout is chosen", async () => {
    renderShell();
    await userEvent.click(profileButton());
    await userEvent.click(screen.getByRole("button", { name: "Logout" }));
    expect(app.logout).toHaveBeenCalledTimes(1);
    expect(profileButton()).toHaveAttribute("aria-expanded", "false");
  });
});
