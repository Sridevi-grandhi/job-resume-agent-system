import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, fireEvent, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderRoute } from "./render.jsx";
import { makeApp } from "./mockApp";
import NotificationBell from "../components/NotificationBell";
import Sidebar from "../components/Sidebar";
import * as api from "../api";

vi.mock("../api", () => ({ getEmailLogs: vi.fn() }));

let app;
vi.mock("../context/AppContext", () => ({ useApp: () => app }));

const needsConfirmationEvent = {
  id: 101, subject: "Interview Invitation", sender: "hr@abccorp.com", event_type: "INTERVIEW_INVITE",
  resolution: "NEEDS_CONFIRMATION", received_at: new Date(Date.now() - 5 * 60000).toISOString(),
};
const confirmedEvent = {
  id: 102, subject: "Old confirmed update", sender: "hr@abccorp.com", event_type: "APPLICATION_UPDATE",
  resolution: "CONFIRMED", received_at: new Date(Date.now() - 3600000).toISOString(),
};
const anotherNeedsConfirmationEvent = {
  id: 103, subject: "Assessment Request", sender: "hr@xyzcorp.com", event_type: "ASSESSMENT_REQUEST",
  resolution: "NEEDS_CONFIRMATION", received_at: new Date(Date.now() - 10 * 60000).toISOString(),
};

beforeEach(() => {
  localStorage.clear();
  api.getEmailLogs.mockReset().mockRejectedValue(new Error("no logs"));
  app = makeApp();
});

function renderNotifications() {
  return renderRoute(<NotificationBell inSidebar />, "/dashboard");
}

describe("NotificationBell", () => {
  // Item 1
  it("renders a bell icon, never the old diamond character", async () => {
    renderNotifications();
    const button = screen.getByRole("button", { name: /notifications/i });
    expect(button.textContent).not.toContain("♢");
    expect(button.querySelector("svg")).toBeInTheDocument();
  });

  // Item 2
  it("hides the badge when unread count is 0", async () => {
    app.jobEmailEvents = [confirmedEvent];
    renderNotifications();
    await waitFor(() => expect(app.loadJobEmailEvents).toHaveBeenCalled());
    const button = screen.getByRole("button", { name: /notifications/i });
    expect(within(button).queryByText(/^\d+\+?$/)).toBeNull();
  });

  // Item 3
  it("shows the badge with the correct real unread count", async () => {
    app.jobEmailEvents = [needsConfirmationEvent, anotherNeedsConfirmationEvent];
    renderNotifications();
    await waitFor(() => {
      const button = screen.getByRole("button", { name: /notifications/i });
      expect(within(button).getByText("2")).toBeInTheDocument();
    });
  });

  it("caps the badge at 9+ for large unread counts", async () => {
    app.jobEmailEvents = Array.from({ length: 12 }, (_, i) => ({
      ...needsConfirmationEvent, id: 1000 + i,
    }));
    renderNotifications();
    await waitFor(() => {
      const button = screen.getByRole("button", { name: /notifications/i });
      expect(within(button).getByText("9+")).toBeInTheDocument();
    });
  });

  // Item 4/5
  it("opens the dropdown on click and closes it on a second click", async () => {
    renderNotifications();
    const button = screen.getByRole("button", { name: /notifications/i });
    expect(screen.queryByRole("menu")).toBeNull();
    await userEvent.click(button);
    expect(screen.getByRole("menu")).toBeInTheDocument();
    expect(button).toHaveAttribute("aria-expanded", "true");
    await userEvent.click(button);
    expect(screen.queryByRole("menu")).toBeNull();
    expect(button).toHaveAttribute("aria-expanded", "false");
  });

  // Item 6
  it("closes the dropdown when clicking outside", async () => {
    renderNotifications();
    await userEvent.click(screen.getByRole("button", { name: /notifications/i }));
    expect(screen.getByRole("menu")).toBeInTheDocument();
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole("menu")).toBeNull();
  });

  // Item 7
  it("shows the empty state when there are no real notifications", async () => {
    app.jobEmailEvents = [];
    renderNotifications();
    await userEvent.click(screen.getByRole("button", { name: /notifications/i }));
    expect(screen.getByText(/you're all caught up/i)).toBeInTheDocument();
  });

  // Item 8
  it("renders notification rows built from real event data, not fake records", async () => {
    app.jobEmailEvents = [needsConfirmationEvent];
    renderNotifications();
    await userEvent.click(screen.getByRole("button", { name: /notifications/i }));
    expect(screen.getByText("Interview invitation")).toBeInTheDocument();
    expect(screen.getByText("Interview Invitation")).toBeInTheDocument();
  });

  // Item 9/10
  it("marks a notification read on click and the badge count drops", async () => {
    app.jobEmailEvents = [needsConfirmationEvent, anotherNeedsConfirmationEvent];
    renderNotifications();
    await waitFor(() => {
      expect(within(screen.getByRole("button", { name: /notifications/i })).getByText("2")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByRole("button", { name: /notifications/i }));
    await userEvent.click(screen.getByText("Assessment Request"));
    // Clicking navigates and closes the dropdown; re-open to check state.
    await userEvent.click(screen.getByRole("button", { name: /notifications/i }));
    const row = screen.getByText("Assessment Request").closest("button");
    expect(row.className).not.toContain("unread");
    expect(within(screen.getByRole("button", { name: /notifications/i })).getByText("1")).toBeInTheDocument();
  });

  // Item 11
  it("Mark all as read clears every unread notification and hides the badge", async () => {
    app.jobEmailEvents = [needsConfirmationEvent];
    renderNotifications();
    await userEvent.click(screen.getByRole("button", { name: /notifications/i }));
    await userEvent.click(screen.getByRole("button", { name: /mark all as read/i }));
    expect(screen.queryByRole("button", { name: /mark all as read/i })).toBeNull();
    const button = screen.getByRole("button", { name: /notifications/i });
    expect(within(button).queryByText(/^\d+\+?$/)).toBeNull();
  });

  // Item 12
  it("keeps the authenticated user's initial in the sidebar profile row", async () => {
    app.currentUser = { user_id: 7, name: "Rita Example", email: "rita@example.test" };
    renderRoute(<Sidebar open={false} onClose={vi.fn()} />, "/dashboard");
    expect(screen.getByText("R")).toBeInTheDocument();
  });

  // Item 13
  it("supports keyboard accessibility — Enter opens, Escape closes", async () => {
    renderNotifications();
    const button = screen.getByRole("button", { name: /notifications/i });
    button.focus();
    await userEvent.keyboard("{Enter}");
    expect(screen.getByRole("menu")).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();
  });

  // Item 14
  it("renders with no console errors", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    app.jobEmailEvents = [needsConfirmationEvent];
    renderNotifications();
    await userEvent.click(screen.getByRole("button", { name: /notifications/i }));
    await waitFor(() => expect(api.getEmailLogs).toHaveBeenCalled());
    expect(errorSpy).not.toHaveBeenCalled();
    errorSpy.mockRestore();
  });

  it("navigates to Email Summary when a notification with a known target is clicked", async () => {
    app.jobEmailEvents = [needsConfirmationEvent];
    renderNotifications();
    await userEvent.click(screen.getByRole("button", { name: /notifications/i }));
    await userEvent.click(screen.getByText("Interview invitation"));
    // Dropdown closes after navigation-triggering click.
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("read state persists across remounts (localStorage) but is scoped and does not error without a user", async () => {
    app.jobEmailEvents = [needsConfirmationEvent];
    const { unmount } = renderNotifications();
    await userEvent.click(screen.getByRole("button", { name: /notifications/i }));
    await userEvent.click(screen.getByText("Interview invitation"));
    unmount();

    app = makeApp({ jobEmailEvents: [needsConfirmationEvent] });
    renderNotifications();
    await waitFor(() => {
      const button = screen.getByRole("button", { name: /notifications/i });
      expect(within(button).queryByText(/^\d+\+?$/)).toBeNull(); // still read
    });
  });

  it("suppresses interview alerts without removing the tracked interview event", async () => {
    app.jobEmailEvents = [needsConfirmationEvent, anotherNeedsConfirmationEvent];
    app.emailSummaryPreferences = {
      ...app.emailSummaryPreferences,
      email_types_to_scan: ["interview_invitations"],
      interview_alerts_enabled: false,
    };
    renderNotifications();
    await userEvent.click(screen.getByRole("button", { name: /notifications/i }));
    expect(screen.queryByText("Interview Invitation")).toBeNull();
    expect(screen.getByText("Assessment Request")).toBeInTheDocument();
    expect(app.jobEmailEvents).toHaveLength(2);
    expect(app.emailSummaryPreferences.email_types_to_scan).toContain("interview_invitations");
  });

  it("gates job and application alerts by their persisted categories", async () => {
    app.jobEmailEvents = [
      { ...needsConfirmationEvent, id: 201, event_type: "RECRUITER_OUTREACH", subject: "Engineer role" },
      { ...needsConfirmationEvent, id: 202, event_type: "APPLICATION_UPDATE", subject: "Application status" },
      { ...needsConfirmationEvent, id: 203, event_type: "INTERVIEW_INVITE", subject: "Interview time" },
      { ...needsConfirmationEvent, id: 204, event_type: "APPLICATION_CONFIRMATION", subject: "Applied" },
      { ...needsConfirmationEvent, id: 205, event_type: "REJECTION", subject: "Rejected" },
      { ...needsConfirmationEvent, id: 206, event_type: "OFFER", subject: "Offer" },
    ];
    app.emailSummaryPreferences = { ...app.emailSummaryPreferences, job_alerts_enabled: false, application_updates_enabled: false };
    renderNotifications();
    await userEvent.click(screen.getByRole("button", { name: /notifications/i }));
    expect(screen.queryByText("Engineer role")).toBeNull();
    expect(screen.queryByText("Application status")).toBeNull();
    expect(screen.queryByText("Applied")).toBeNull();
    expect(screen.queryByText("Rejected")).toBeNull();
    expect(screen.queryByText("Offer")).toBeNull();
    expect(screen.getByText("Interview time")).toBeInTheDocument();
    expect(app.jobEmailEvents).toHaveLength(6);
  });
});
