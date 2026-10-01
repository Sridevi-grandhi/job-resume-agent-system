import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderRoute } from "./render.jsx";
import { makeApp } from "./mockApp";
import SettingsPage from "../pages/SettingsPage";
import AuthPage from "../pages/AuthPage";

let app;
vi.mock("../context/AppContext", () => ({ useApp: () => app }));

beforeEach(() => {
  app = makeApp();
  app.loadEmailSummaryPreferences.mockResolvedValue(app.emailSummaryPreferences);
});

describe("Settings page", () => {
  it("shows the authenticated account and the requested page header without a welcome banner", () => {
    app.currentUser = { user_id: 7, name: "Tanishka", email: "tanishka@example.test" };
    renderRoute(<SettingsPage />, "/settings");
    expect(screen.getByRole("heading", { name: "Settings" })).toBeInTheDocument();
    expect(screen.getByText("Manage your account, integrations, notifications, and security.")).toBeInTheDocument();
    expect(screen.getByText("Tanishka")).toBeInTheDocument();
    expect(screen.getByText("tanishka@example.test")).toBeInTheDocument();
    expect(screen.queryByText(/welcome back/i)).toBeNull();
    expect(screen.getByRole("button", { name: /edit profile/i })).toBeInTheDocument();
  });

  it("shows the disconnected state and only the existing Connect Gmail action", () => {
    renderRoute(<SettingsPage />, "/settings");
    expect(screen.getByText("Disconnected")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Connect Gmail" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Scan Now" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Disconnect Gmail" })).toBeNull();
    expect(screen.queryByRole("checkbox", { name: /automatic gmail scan/i })).toBeNull();
    expect(screen.queryByText(/next scan/i)).toBeNull();
    expect(screen.getByText("Email Types to Scan")).toBeInTheDocument();
    expect(screen.getByText("Connect Gmail to configure automatic email scanning.")).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "Recruiter Messages" })).toBeNull();
  });

  it("validates and saves a changed name while keeping email read-only", async () => {
    const user = userEvent.setup();
    app.currentUser = { user_id: 7, name: "Tanishka", email: "tanishka@example.test" };
    app.saveProfileName.mockResolvedValue({ user_id: 7, name: "Tanishka Test", email: "tanishka@example.test" });
    renderRoute(<SettingsPage />, "/settings");
    await user.click(screen.getByRole("button", { name: "Edit Profile" }));
    expect(screen.getByText("tanishka@example.test")).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Email" })).toBeNull();
    expect(screen.getByRole("button", { name: "Save Profile" })).toBeDisabled();
    await user.clear(screen.getByRole("textbox", { name: "Name" }));
    await user.type(screen.getByRole("textbox", { name: "Name" }), "   ");
    await user.click(screen.getByRole("button", { name: "Save Profile" }));
    expect(screen.getByText("Name must be between 1 and 100 characters.")).toBeInTheDocument();
    expect(app.saveProfileName).not.toHaveBeenCalled();
    await user.clear(screen.getByRole("textbox", { name: "Name" }));
    await user.type(screen.getByRole("textbox", { name: "Name" }), "  Tanishka Test  ");
    await user.click(screen.getByRole("button", { name: "Save Profile" }));
    expect(screen.getByText("Enter your current password to save your profile.")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Current password"), "secret123");
    await user.click(screen.getByRole("button", { name: "Save Profile" }));
    expect(app.saveProfileName).toHaveBeenCalledWith({ name: "Tanishka Test", current_password: "secret123" });
    expect(await screen.findByText("Profile saved.")).toBeInTheDocument();
  });

  it("keeps profile editing open on a rejected save without exposing backend details", async () => {
    const user = userEvent.setup();
    app.saveProfileName.mockResolvedValue(null);
    renderRoute(<SettingsPage />, "/settings");
    await user.click(screen.getByRole("button", { name: "Edit Profile" }));
    await user.clear(screen.getByRole("textbox", { name: "Name" }));
    await user.type(screen.getByRole("textbox", { name: "Name" }), "New Name");
    await user.type(screen.getByLabelText("Current password"), "wrong");
    await user.click(screen.getByRole("button", { name: "Save Profile" }));
    expect(await screen.findByText("Unable to save profile. Check your password and try again.")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("New Name");
  });

  it("saves interval and email types together with clean, changed, undo, and saved states", async () => {
    const user = userEvent.setup();
    app.gmailStatus = { connected: true, email_address: "candidate@gmail.com" };
    app.saveEmailSummaryPreferences.mockImplementation(async (patch) => {
      app.emailSummaryPreferences = { ...app.emailSummaryPreferences, ...patch };
      return app.emailSummaryPreferences;
    });
    renderRoute(<SettingsPage />, "/settings");
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "Recruiter Messages" })).toBeEnabled());
    expect([...screen.getByLabelText("Scan interval").options].map((option) => option.value)).toEqual([
      "every_1_hour", "every_6_hours", "every_12_hours", "daily",
    ]);
    expect(screen.getByRole("button", { name: "Save Email Scan Settings" })).toBeDisabled();
    await user.selectOptions(screen.getByLabelText("Scan interval"), "every_1_hour");
    expect(screen.getByRole("button", { name: "Save Email Scan Settings" })).toBeEnabled();
    await user.selectOptions(screen.getByLabelText("Scan interval"), "daily");
    expect(screen.getByRole("button", { name: "Save Email Scan Settings" })).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: "Recruiter Messages" }));
    expect(screen.getByRole("button", { name: "Save Email Scan Settings" })).toBeEnabled();
    await user.click(screen.getByRole("checkbox", { name: "Recruiter Messages" }));
    expect(screen.getByRole("button", { name: "Save Email Scan Settings" })).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: "Recruiter Messages" }));
    await user.selectOptions(screen.getByLabelText("Scan interval"), "every_1_hour");
    await user.click(screen.getByRole("button", { name: "Save Email Scan Settings" }));
    expect(app.saveEmailSummaryPreferences).toHaveBeenCalledWith({
      gmail_scan_frequency: "every_1_hour",
      email_types_to_scan: ["job_opportunities", "application_updates", "interview_invitations"],
    });
    expect(await screen.findByText("Email scan settings saved.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save Email Scan Settings" })).toBeDisabled();
  });

  it("keeps failed email scan edits available for retry", async () => {
    const user = userEvent.setup();
    app.gmailStatus = { connected: true, email_address: "candidate@gmail.com" };
    app.saveEmailSummaryPreferences.mockResolvedValue(null);
    renderRoute(<SettingsPage />, "/settings");
    await waitFor(() => expect(screen.getByLabelText("Scan interval")).toBeEnabled());
    await user.selectOptions(screen.getByLabelText("Scan interval"), "every_1_hour");
    await user.click(screen.getByRole("button", { name: "Save Email Scan Settings" }));
    expect(await screen.findByText("Unable to save email scan settings. Please try again.")).toBeInTheDocument();
    expect(screen.getByLabelText("Scan interval")).toHaveValue("every_1_hour");
    expect(screen.getByRole("button", { name: "Save Email Scan Settings" })).toBeEnabled();
  });

  it("shows Saving while the email scan settings request is in flight", async () => {
    const user = userEvent.setup();
    app.gmailStatus = { connected: true, email_address: "candidate@gmail.com" };
    let finishSave;
    app.saveEmailSummaryPreferences.mockImplementation(() => new Promise((resolve) => {
      finishSave = () => {
        app.emailSummaryPreferences = { ...app.emailSummaryPreferences, gmail_scan_frequency: "every_1_hour" };
        resolve(app.emailSummaryPreferences);
      };
    }));
    renderRoute(<SettingsPage />, "/settings");
    await waitFor(() => expect(screen.getByLabelText("Scan interval")).toBeEnabled());
    await user.selectOptions(screen.getByLabelText("Scan interval"), "every_1_hour");
    await user.click(screen.getByRole("button", { name: "Save Email Scan Settings" }));
    expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();
    finishSave();
    expect(await screen.findByText("Email scan settings saved.")).toBeInTheDocument();
  });

  it("saves real notification preferences and opens the existing panel", async () => {
    const user = userEvent.setup();
    app.saveEmailSummaryPreferences.mockImplementation(async (patch) => {
      app.emailSummaryPreferences = { ...app.emailSummaryPreferences, ...patch };
      return app.emailSummaryPreferences;
    });
    renderRoute(<SettingsPage />, "/settings");
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "Job alerts" })).toBeEnabled());
    expect(screen.getByRole("checkbox", { name: "Job alerts" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Application updates" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Interview alerts" })).toBeChecked();
    expect(screen.getByRole("button", { name: "Save Notification Settings" })).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: "Job alerts" }));
    expect(screen.getByRole("button", { name: "Save Notification Settings" })).toBeEnabled();
    await user.click(screen.getByRole("checkbox", { name: "Job alerts" }));
    expect(screen.getByRole("button", { name: "Save Notification Settings" })).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: "Interview alerts" }));
    await user.click(screen.getByRole("button", { name: "Save Notification Settings" }));
    expect(app.saveEmailSummaryPreferences).toHaveBeenCalledWith({
      job_alerts_enabled: true, application_updates_enabled: true, interview_alerts_enabled: false,
    });
    expect(await screen.findByText("Notification settings saved.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save Notification Settings" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "View Notifications" }));
    expect(screen.getByRole("menu")).toBeInTheDocument();
    expect(screen.getByText("You're all caught up.")).toBeInTheDocument();
  });

  it("keeps failed notification edits for retry", async () => {
    const user = userEvent.setup();
    app.saveEmailSummaryPreferences.mockResolvedValue(null);
    renderRoute(<SettingsPage />, "/settings");
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "Job alerts" })).toBeEnabled());
    await user.click(screen.getByRole("checkbox", { name: "Job alerts" }));
    await user.click(screen.getByRole("button", { name: "Save Notification Settings" }));
    expect(await screen.findByText("Unable to save notification settings. Please try again.")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Job alerts" })).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Save Notification Settings" })).toBeEnabled();
  });

  it("shows Saving while notification settings are being persisted", async () => {
    const user = userEvent.setup();
    let finishSave;
    app.saveEmailSummaryPreferences.mockImplementation(() => new Promise((resolve) => {
      finishSave = () => {
        app.emailSummaryPreferences = { ...app.emailSummaryPreferences, job_alerts_enabled: false };
        resolve(app.emailSummaryPreferences);
      };
    }));
    renderRoute(<SettingsPage />, "/settings");
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "Job alerts" })).toBeEnabled());
    await user.click(screen.getByRole("checkbox", { name: "Job alerts" }));
    await user.click(screen.getByRole("button", { name: "Save Notification Settings" }));
    expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();
    finishSave();
    expect(await screen.findByText("Notification settings saved.")).toBeInTheDocument();
  });

  it("shows connected Gmail data and saves the automatic toggle independently", async () => {
    const user = userEvent.setup();
    app.gmailStatus = {
      connected: true, email_address: "candidate@gmail.com", last_scanned_at: "2026-09-28T19:56:17Z",
    };
    app.emailSummaryPreferences = { ...app.emailSummaryPreferences, gmail_scan_frequency: "daily" };
    renderRoute(<SettingsPage />, "/settings");
    await waitFor(() => expect(screen.getByRole("checkbox", { name: /automatic gmail scan/i })).toBeEnabled());
    expect(screen.getByText("candidate@gmail.com")).toBeInTheDocument();
    expect(screen.getByText("Last scan")).toBeInTheDocument();
    expect(screen.queryByText("Not yet scanned")).toBeNull();
    expect(screen.queryByText(/next scan/i)).toBeNull();
    await user.click(screen.getByRole("checkbox", { name: /automatic gmail scan/i }));
    expect(app.saveEmailSummaryPreferences).toHaveBeenNthCalledWith(1, { automatic_gmail_scan_enabled: true });
    expect(app.saveEmailSummaryPreferences).toHaveBeenCalledTimes(1);
  });

  it("uses the shared scan and disconnect actions and shows a scanning state", async () => {
    const user = userEvent.setup();
    app.gmailStatus = { connected: true, email_address: "candidate@gmail.com" };
    let finishScan;
    app.scanGmailForJobs.mockImplementation(() => new Promise((resolve) => { finishScan = resolve; }));
    renderRoute(<SettingsPage />, "/settings");
    await user.click(screen.getByRole("button", { name: "Scan Now" }));
    expect(app.scanGmailForJobs).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Scanning…" })).toBeDisabled();
    finishScan();
    await waitFor(() => expect(screen.getByRole("button", { name: "Scan Now" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "Disconnect Gmail" }));
    expect(app.disconnectGmailAccount).toHaveBeenCalledTimes(1);
  });

  it("shows safe inline errors without raw backend details", () => {
    app.gmailStatus = { connected: true, email_address: "candidate@gmail.com" };
    app.messages.gmail = "HTTP 500 internal detail";
    app.messages["email-settings"] = "raw backend validation";
    renderRoute(<SettingsPage />, "/settings");
    expect(screen.getByText("Unable to complete the Gmail action. Please try again.")).toBeInTheDocument();
    expect(screen.getByText("Unable to load Gmail scan settings. Please try again.")).toBeInTheDocument();
    expect(screen.queryByText(/HTTP 500|raw backend validation/)).toBeNull();
    expect(screen.getByRole("checkbox", { name: "Job alerts" })).toBeInTheDocument();
  });

  it("offers a status retry when the Gmail connection cannot be loaded", async () => {
    const user = userEvent.setup();
    app.gmailStatus = null;
    app.messages.gmail = "HTTP 500 internal detail";
    renderRoute(<SettingsPage />, "/settings");
    expect(screen.getByText("Connection unavailable")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry connection status" }));
    expect(app.loadGmailStatus).toHaveBeenCalledTimes(2);
  });

  it("links Security to the existing reset workflow, which works while signed in", () => {
    renderRoute(<SettingsPage />, "/settings");
    expect(screen.getByRole("link", { name: "Change Password" })).toHaveAttribute("href", "/forgot-password");
    const { unmount } = renderRoute(<AuthPage mode="forgot" />, "/forgot-password");
    expect(screen.getByRole("heading", { name: "Forgot your password?" })).toBeInTheDocument();
    unmount();
  });
});
