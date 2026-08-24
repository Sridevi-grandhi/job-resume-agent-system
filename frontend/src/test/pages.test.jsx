import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderRoute } from "./render.jsx";
import { makeApp } from "./mockApp";
import AuthPage from "../pages/AuthPage";
import PreferencesPage from "../pages/PreferencesPage";
import ResumePage from "../pages/ResumePage";
import JobSearchPage from "../pages/JobSearchPage";
import SelectedJobsPage from "../pages/SelectedJobsPage";
import EmailSummaryPage from "../pages/EmailSummaryPage";
import WorkflowPage from "../pages/WorkflowPage";

let app;
vi.mock("../context/AppContext", () => ({ useApp: () => app }));

beforeEach(() => { app = makeApp(); });

describe("authentication", () => {
  it("submits login credentials and redirects after success", async () => {
    app.currentUser = null;
    app.authenticate.mockResolvedValue(true);
    renderRoute(<AuthPage mode="login" />, "/login");
    await userEvent.type(screen.getByLabelText(/email address/i), "person@example.test");
    await userEvent.type(screen.getByLabelText(/password/i), "secret-value");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));
    expect(app.authenticate).toHaveBeenCalledWith("login", { name: "", email: "person@example.test", password: "secret-value", confirmPassword: "" });
  });

  it("shows Forgot password link on login and opens forgot-password screen", async () => {
    app.currentUser = null;
    app.clearAuthMessages = vi.fn();
    renderRoute(<AuthPage mode="login" />, "/login");
    const link = screen.getByRole("link", { name: /forgot password/i });
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute("href", "/forgot-password");
  });

  it("renders forgot-password form, submits email, and shows generic success", async () => {
    app.currentUser = null;
    app.clearAuthMessages = vi.fn();
    app.requestPasswordReset = vi.fn().mockResolvedValue({
      message: "If an account exists for this email, password reset instructions have been sent.",
    });
    app.messages = {};
    renderRoute(<AuthPage mode="forgot" />, "/forgot-password");
    expect(screen.getByRole("heading", { name: /forgot your password/i })).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/email address/i), "person@example.test");
    await userEvent.click(screen.getByRole("button", { name: /send reset link/i }));
    expect(app.requestPasswordReset).toHaveBeenCalledWith("person@example.test");
    await waitFor(() => {
      expect(screen.getByText(/if an account exists for this email/i)).toBeInTheDocument();
    });
    expect(screen.getByRole("link", { name: /back to sign in/i })).toHaveAttribute("href", "/login");
  });

  it("validates mismatched passwords on reset screen", async () => {
    app.currentUser = null;
    app.clearAuthMessages = vi.fn();
    app.submitPasswordReset = vi.fn();
    app.messages = {};
    renderRoute(<AuthPage mode="reset" />, "/reset-password?token=valid-token-value");
    await userEvent.type(screen.getByLabelText(/^new password$/i), "newpass12");
    await userEvent.type(screen.getByLabelText(/confirm new password/i), "different1");
    await userEvent.click(screen.getByRole("button", { name: /reset password/i }));
    expect(screen.getByText(/passwords do not match/i)).toBeInTheDocument();
    expect(app.submitPasswordReset).not.toHaveBeenCalled();
  });

  it("shows invalid token UI when token is missing", () => {
    app.currentUser = null;
    app.clearAuthMessages = vi.fn();
    app.messages = {};
    renderRoute(<AuthPage mode="reset" />, "/reset-password");
    expect(screen.getByText(/invalid or has expired/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /request new reset link/i })).toBeInTheDocument();
  });

  it("shows successful reset UI and continue to sign in", async () => {
    app.currentUser = null;
    app.clearAuthMessages = vi.fn();
    app.submitPasswordReset = vi.fn().mockResolvedValue({ message: "Password updated successfully." });
    app.messages = { authReset: "Password updated successfully." };
    renderRoute(<AuthPage mode="reset" />, "/reset-password?token=valid-token-value");
    await userEvent.type(screen.getByLabelText(/^new password$/i), "newpass12");
    await userEvent.type(screen.getByLabelText(/confirm new password/i), "newpass12");
    await userEvent.click(screen.getByRole("button", { name: /reset password/i }));
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /password updated/i })).toBeInTheDocument();
    });
    expect(screen.getByRole("link", { name: /continue to sign in/i })).toHaveAttribute("href", "/login");
  });

  it("clears stale auth errors when switching screens", () => {
    app.currentUser = null;
    app.clearAuthMessages = vi.fn();
    app.messages = { authSignup: "User with this email already exists" };
    renderRoute(<AuthPage mode="login" />, "/login");
    expect(app.clearAuthMessages).toHaveBeenCalled();
    expect(screen.queryByText(/already exists/i)).not.toBeInTheDocument();
  });

  it("renders signup and displays signup API errors without navigating", () => {
    app.currentUser = null;
    app.clearAuthMessages = vi.fn();
    app.messages = { authSignup: "Email already registered" };
    renderRoute(<AuthPage mode="signup" />, "/signup");
    expect(screen.getByLabelText(/full name/i)).toBeInTheDocument();
    expect(screen.getByText("Email already registered")).toBeInTheDocument();
  });

  it("prevents duplicate auth submission while busy", () => {
    app.currentUser = null; app.busy = "authLogin";
    renderRoute(<AuthPage mode="login" />, "/login");
    expect(screen.getByRole("button", { name: /please wait/i })).toBeDisabled();
  });
});

describe("preferences", () => {
  it("renders every field and delegates changes and save", async () => {
    app.preferences = { ...app.preferences, job_title: "Engineer", location: "Chicago", work_mode: "Remote", job_type: "Full-time", skills: "Java", timezone: "America/Chicago" };
    renderRoute(<PreferencesPage />, "/preferences");
    expect(screen.getByLabelText(/job title/i)).toHaveValue("Engineer");
    expect(screen.getByLabelText(/schedule time/i)).toHaveValue("08:00");
    await userEvent.selectOptions(screen.getByLabelText(/work mode/i), "Hybrid");
    await userEvent.click(screen.getByRole("button", { name: /save preferences/i }));
    expect(app.changePreferences).toHaveBeenCalled();
    expect(app.savePreferences).toHaveBeenCalled();
  });

  it("loads saved preferences and exposes loading state", async () => {
    app.busy = "preferences";
    renderRoute(<PreferencesPage />, "/preferences");
    expect(screen.getByRole("button", { name: /load saved/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /save preferences/i })).toBeDisabled();
  });
});

describe("resume", () => {
  it.each(["resume.pdf", "resume.docx", "resume.txt"])("accepts %s and uploads it", async (name) => {
    renderRoute(<ResumePage />, "/resume");
    const input = document.querySelector('input[type="file"]');
    const file = new File(["safe resume"], name, { type: "text/plain" });
    fireEvent.change(input, { target: { files: [file] } });
    expect(app.setSelectedResumeFile).toHaveBeenCalledWith(file);
    await userEvent.click(screen.getByRole("button", { name: /^upload resume$/i }));
    expect(app.uploadResume).toHaveBeenCalled();
  });

  it("renders latest resume text as inert text", () => {
    app.latestResume = { id: 9, file_name: "safe.txt", parsed_text: '<img src=x onerror="alert(1)">' };
    renderRoute(<ResumePage />, "/resume");
    expect(screen.getByText(/<img src=x/)).toBeInTheDocument();
    expect(document.querySelector(".text-preview img")).toBeNull();
  });
});

describe("job search", () => {
  it("keeps existing and updated searches separate", async () => {
    renderRoute(<JobSearchPage />, "/jobs");
    await userEvent.click(screen.getByRole("button", { name: /search job links/i }));
    await userEvent.click(screen.getByRole("button", { name: /search updated jobs/i }));
    expect(app.search).toHaveBeenNthCalledWith(1, false);
    expect(app.search).toHaveBeenNthCalledWith(2, true);
  });

  it("renders a Remotive direct link in a new tab", () => {
    app.latestResume = { id: 1 };
    app.jobs = [{ id: 12, title: "Java Engineer", company: "Example Co", location: "Remote", source: "Remotive API", description: "A sufficiently detailed job description for direct optimization testing.", job_url: "https://jobs.example.test/12" }];
    renderRoute(<JobSearchPage />, "/jobs");
    const link = screen.getByRole("link", { name: /view job/i });
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("href", "https://jobs.example.test/12");
  });
});

describe("selected jobs", () => {
  const job = { id: 42, title: "Backend Engineer", company: "Acme", location: "Remote", source: "User Selected Job", description: "Detailed backend engineering role requiring APIs and cloud delivery.", job_url: "https://jobs.example.test/42" };
  it("loads automatically and deletes only after confirmation", async () => {
    app.selectedJobs = [job]; app.latestResume = { id: 2 };
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderRoute(<SelectedJobsPage />, "/selected-jobs");
    await waitFor(() => expect(app.loadSelectedJobs).toHaveBeenCalled());
    await userEvent.click(screen.getByRole("button", { name: /remove job/i }));
    expect(app.removeSelectedJob).toHaveBeenCalledWith(42);
  });

  it("keeps a job when deletion is cancelled and disables optimization without resume", async () => {
    app.selectedJobs = [job];
    vi.spyOn(window, "confirm").mockReturnValue(false);
    renderRoute(<SelectedJobsPage />, "/selected-jobs");
    expect(screen.getByRole("button", { name: /optimize and compare/i })).toBeDisabled();
    expect(screen.getByText("Upload a resume first")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /remove job/i }));
    expect(app.removeSelectedJob).not.toHaveBeenCalled();
  });
});

describe("email and workflow", () => {
  it("renders email content safely and generates a summary", async () => {
    app.emailLogs = [{ id: 1, recipient: "candidate@example.test", subject: "Daily jobs", body: "<script>bad()</script>", status: "Sent" }];
    renderRoute(<EmailSummaryPage />, "/emails");
    expect(screen.getByText("<script>bad()</script>")).toBeInTheDocument();
    expect(document.querySelector(".email-preview script")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /generate email summary/i }));
    expect(app.generateEmailSummary).toHaveBeenCalled();
  });

  it("runs workflow once and disables while active", async () => {
    app.busy = "workflow";
    renderRoute(<WorkflowPage />, "/workflow");
    expect(screen.getByRole("button", { name: /workflow running/i })).toBeDisabled();
  });
});
