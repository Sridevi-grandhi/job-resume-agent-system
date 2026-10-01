import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
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
    expect(screen.getByText(/check your inbox/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /back to sign in/i })).toHaveAttribute("href", "/login");
  });

  it("never shows a development reset link in normal mode, even after a successful submit", async () => {
    app.currentUser = null;
    app.clearAuthMessages = vi.fn();
    // Matches what the backend now always returns outside the explicit
    // APP_ENV=development + SHOW_DEV_RESET_LINK=true opt-in.
    app.requestPasswordReset = vi.fn().mockResolvedValue({
      message: "If an account exists for this email, password reset instructions have been sent.",
      dev_reset_url: null,
    });
    app.messages = {};
    renderRoute(<AuthPage mode="forgot" />, "/forgot-password");
    await userEvent.type(screen.getByLabelText(/email address/i), "person@example.test");
    await userEvent.click(screen.getByRole("button", { name: /send reset link/i }));
    await waitFor(() => {
      expect(screen.getByText(/if an account exists for this email/i)).toBeInTheDocument();
    });
    expect(screen.queryByText(/development reset link/i)).toBeNull();
    expect(screen.queryByRole("link", { name: /reset-password\?token=/i })).toBeNull();
  });

  it("shows the explicit dev reset link only when the backend opts in", async () => {
    app.currentUser = null;
    app.clearAuthMessages = vi.fn();
    app.requestPasswordReset = vi.fn().mockResolvedValue({
      message: "If an account exists for this email, password reset instructions have been sent.",
      dev_reset_url: "http://localhost:5173/reset-password?token=dev-only-token",
    });
    app.messages = {};
    renderRoute(<AuthPage mode="forgot" />, "/forgot-password");
    await userEvent.type(screen.getByLabelText(/email address/i), "person@example.test");
    await userEvent.click(screen.getByRole("button", { name: /send reset link/i }));
    await waitFor(() => {
      expect(screen.getByText(/development reset link/i)).toBeInTheDocument();
    });
  });

  it("shows a safe generic error when the forgot-password request itself fails", async () => {
    app.currentUser = null;
    app.clearAuthMessages = vi.fn();
    app.requestPasswordReset = vi.fn().mockResolvedValue(undefined); // task() swallows the error, resolves falsy
    app.messages = { authForgot: "TypeError: Failed to fetch at internal/module.js:42" };
    renderRoute(<AuthPage mode="forgot" />, "/forgot-password");
    await userEvent.type(screen.getByLabelText(/email address/i), "person@example.test");
    await userEvent.click(screen.getByRole("button", { name: /send reset link/i }));
    await waitFor(() => {
      expect(screen.getByText(/unable to process the request right now/i)).toBeInTheDocument();
    });
    expect(screen.queryByText(/typeerror|failed to fetch|internal\/module/i)).toBeNull();
  });

  it("disables Send Reset Link and shows a loading state while the request is in flight", () => {
    app.currentUser = null;
    app.clearAuthMessages = vi.fn();
    app.busy = "authForgot";
    app.messages = {};
    renderRoute(<AuthPage mode="forgot" />, "/forgot-password");
    const button = screen.getByRole("button", { name: /please wait/i });
    expect(button).toBeDisabled();
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

  it("requires confirmation before signup and sends only the existing signup payload", async () => {
    app.currentUser = null;
    app.authenticate.mockResolvedValue(false);
    renderRoute(<AuthPage mode="signup" />, "/signup");

    const confirmPassword = screen.getByLabelText(/^confirm password$/i);
    expect(confirmPassword).toHaveAttribute("type", "password");
    expect(confirmPassword).toHaveAttribute("placeholder", "Re-enter your password");
    await userEvent.type(screen.getByLabelText(/full name/i), "Test Candidate");
    await userEvent.type(screen.getByLabelText(/email address/i), "candidate@example.test");
    await userEvent.type(screen.getByLabelText(/^password$/i), "CareerBoost@123");
    await userEvent.click(screen.getByRole("button", { name: /create account/i }));
    expect(screen.getByText("Please confirm your password.")).toBeInTheDocument();
    expect(app.authenticate).not.toHaveBeenCalled();

    await userEvent.type(confirmPassword, "CareerBoost@123");
    expect(screen.queryByText("Please confirm your password.")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /create account/i }));
    expect(app.authenticate).toHaveBeenCalledWith("signup", {
      name: "Test Candidate",
      email: "candidate@example.test",
      password: "CareerBoost@123",
    });
  });

  it("blocks mismatched signup passwords and clears the inline error once corrected", async () => {
    app.currentUser = null;
    renderRoute(<AuthPage mode="signup" />, "/signup");
    await userEvent.type(screen.getByLabelText(/full name/i), "Test Candidate");
    await userEvent.type(screen.getByLabelText(/email address/i), "candidate@example.test");
    await userEvent.type(screen.getByLabelText(/^password$/i), "CareerBoost@123");
    const confirmPassword = screen.getByLabelText(/^confirm password$/i);
    await userEvent.type(confirmPassword, "CareerBoost@124");
    await userEvent.click(screen.getByRole("button", { name: /create account/i }));

    expect(screen.getByRole("alert")).toHaveTextContent("Passwords do not match.");
    expect(app.authenticate).not.toHaveBeenCalled();
    await userEvent.clear(confirmPassword);
    await userEvent.type(confirmPassword, "CareerBoost@123");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows and hides each signup password independently without changing its value", async () => {
    app.currentUser = null;
    renderRoute(<AuthPage mode="signup" />, "/signup");
    const password = screen.getByLabelText(/^password$/i);
    const confirmPassword = screen.getByLabelText(/^confirm password$/i);
    await userEvent.type(password, "CareerBoost@123");
    await userEvent.type(confirmPassword, "CareerBoost@123");

    expect(password).toHaveAttribute("type", "password");
    expect(confirmPassword).toHaveAttribute("type", "password");
    await userEvent.click(screen.getByRole("button", { name: "Show password" }));
    expect(password).toHaveAttribute("type", "text");
    expect(password).toHaveValue("CareerBoost@123");
    expect(confirmPassword).toHaveAttribute("type", "password");
    await userEvent.click(screen.getByRole("button", { name: "Hide password" }));
    expect(password).toHaveAttribute("type", "password");

    await userEvent.click(screen.getByRole("button", { name: "Show confirm password" }));
    expect(confirmPassword).toHaveAttribute("type", "text");
    expect(confirmPassword).toHaveValue("CareerBoost@123");
    expect(password).toHaveAttribute("type", "password");
  });

  it("prevents duplicate auth submission while busy", () => {
    app.currentUser = null; app.busy = "authLogin";
    renderRoute(<AuthPage mode="login" />, "/login");
    expect(screen.getByRole("button", { name: /please wait/i })).toBeDisabled();
  });
});

describe("preferences", () => {
  it("hydrates saved preferences on mount so refresh does not require visiting Dashboard first", async () => {
    renderRoute(<PreferencesPage />, "/preferences");
    await waitFor(() => expect(app.loadPreferences).toHaveBeenCalled());
  });

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
  it("hydrates the latest resume on mount so the page does not depend on visiting Dashboard first", () => {
    renderRoute(<ResumePage />, "/resume");
    expect(app.verifyLatestResume).toHaveBeenCalled();
  });

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
  const genericJob = (source, id) => ({
    id,
    title: `${source} search`,
    company: source,
    location: "Remote",
    source,
    job_url: `https://jobs.example.test/search/${id}`,
  });

  it("still runs the original (search-link) job search unchanged", async () => {
    renderRoute(<JobSearchPage />, "/jobs");
    await userEvent.click(screen.getByRole("button", { name: /search job links/i }));
    expect(app.search).toHaveBeenCalledWith(false);
  });

  it("loads preferences, resume, and jobs on mount so search links appear without visiting Dashboard first", () => {
    renderRoute(<JobSearchPage />, "/jobs");
    expect(app.loadPreferences).toHaveBeenCalled();
    expect(app.verifyLatestResume).toHaveBeenCalled();
    expect(app.loadJobs).toHaveBeenCalled();
  });

  it("no longer exposes 'Search updated jobs' here — replaced by the dedicated Updated Job Search page", () => {
    renderRoute(<JobSearchPage />, "/jobs");
    expect(screen.queryByRole("button", { name: /search updated jobs/i })).toBeNull();
  });

  it("renders a Remotive direct link in a new tab", () => {
    app.latestResume = { id: 1 };
    app.jobs = [{ id: 12, title: "Java Engineer", company: "Example Co", location: "Remote", source: "Remotive API", description: "A sufficiently detailed job description for direct optimization testing.", job_url: "https://jobs.example.test/12" }];
    renderRoute(<JobSearchPage />, "/jobs");
    const link = screen.getByRole("link", { name: /view job/i });
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("href", "https://jobs.example.test/12");
  });

  it.each([
    ["LinkedIn Jobs", 21],
    ["Indeed", 22],
    ["Dice", 23],
    ["Google Jobs", 24],
    ["ZipRecruiter", 25],
  ])("renders only the supported action for a %s search-link card", (source, id) => {
    app.latestResume = { id: 1 };
    app.jobs = [genericJob(source, id)];

    renderRoute(<JobSearchPage />, "/jobs");

    expect(screen.getByRole("link", { name: "View Jobs" })).toHaveAttribute(
      "href",
      `https://jobs.example.test/search/${id}`,
    );
    expect(screen.queryByRole("button", { name: /optimize and compare scores/i })).toBeNull();
    expect(screen.getByText("Open this source and select a specific job posting to analyze.")).toBeInTheDocument();
    expect(screen.queryByText(/match unavailable/i)).toBeNull();
  });

  it("keeps the Remotive optimization action and its navigation handler", async () => {
    app.latestResume = { id: 1 };
    app.jobs = [{
      id: 31,
      title: "Java Engineer",
      company: "Example Co",
      location: "Remote",
      source: "Remotive API",
      description: "A sufficiently detailed job description for direct optimization testing.",
      job_url: "https://jobs.example.test/31",
    }];

    renderRoute(<JobSearchPage />, "/jobs");

    expect(screen.getByRole("link", { name: "View Job" })).toBeInTheDocument();
    const optimize = screen.getByRole("button", { name: "Optimize and Compare Scores" });
    expect(optimize).toBeEnabled();
    await userEvent.click(optimize);
    expect(app.setAtsSelectedJobId).toHaveBeenCalledWith("31");
    expect(sessionStorage.getItem("atsSelectedJobId")).toBe("31");
  });

  // Resume Match score UI — circular ring, matching Dashboard/Updated Job
  // Search/Selected Jobs. UI-only: same percentage, same label, same buttons.
  describe("resume match score design", () => {
    const jobWithMatch = (matchPercentage) => ({
      id: 41, title: "Java Engineer", company: "Example Co", location: "Remote",
      source: "Remotive API", job_url: "https://jobs.example.test/41",
      description: "A sufficiently detailed job description for direct optimization testing.",
    });

    it("uses the circular MatchScoreRing, never the old rectangular MatchScoreBadge", () => {
      app.latestResume = { id: 1 };
      app.jobs = [jobWithMatch(93)];
      app.matches = [{ job_id: 41, match_percentage: 93, resume_version: "original" }];
      renderRoute(<JobSearchPage />, "/jobs");
      expect(document.querySelector(".match-score-ring")).not.toBeNull();
      expect(document.querySelector(".match-score-badge")).toBeNull();
    });

    it.each([
      [93, "93%", "Strong Match"],
      [79, "79%", "Good Match"],
      [65, "65%", "Moderate Match"],
      [45, "45%", "Low Match"],
    ])("preserves the exact existing percentage and label for a %i%% match", (score, expectedPercent, expectedLabel) => {
      app.latestResume = { id: 1 };
      app.jobs = [jobWithMatch(score)];
      app.matches = [{ job_id: 41, match_percentage: score, resume_version: "original" }];
      renderRoute(<JobSearchPage />, "/jobs");
      expect(screen.getByText(expectedPercent)).toBeInTheDocument();
      expect(screen.getByText(expectedLabel)).toBeInTheDocument();
    });

    it("does not change View Job / Optimize and Compare Scores button behavior", async () => {
      app.latestResume = { id: 1 };
      app.jobs = [jobWithMatch(88)];
      app.matches = [{ job_id: 41, match_percentage: 88, resume_version: "original" }];
      renderRoute(<JobSearchPage />, "/jobs");
      expect(screen.getByRole("link", { name: "View Job" })).toHaveAttribute("href", "https://jobs.example.test/41");
      const optimize = screen.getByRole("button", { name: "Optimize and Compare Scores" });
      await userEvent.click(optimize);
      expect(app.setAtsSelectedJobId).toHaveBeenCalledWith("41");
    });

    // Selected Jobs' new match_status field (COMPLETE/CALCULATING/
    // UNAVAILABLE/FAILED) can end up on a.jobs too — loadSelectedJobs()
    // merges Selected Jobs' payload shape into the shared jobs list. Job
    // Search must never surface that field (no showMatchStatus prop here):
    // unscored jobs stay exactly as before, no new "Calculating…" text.
    it("never shows a Selected-Jobs-only match status indicator, even if the job object carries match_status", () => {
      app.latestResume = { id: 1 };
      app.jobs = [{ ...jobWithMatch(0), match_status: "CALCULATING" }];
      app.matches = [];
      renderRoute(<JobSearchPage />, "/jobs");
      expect(document.querySelector(".match-status-indicator")).toBeNull();
      expect(screen.queryByText("Calculating…")).toBeNull();
    });

    it("renders with no console errors", () => {
      const spy = vi.spyOn(console, "error").mockImplementation(() => {});
      app.latestResume = { id: 1 };
      app.jobs = [jobWithMatch(72)];
      app.matches = [{ job_id: 41, match_percentage: 72, resume_version: "original" }];
      renderRoute(<JobSearchPage />, "/jobs");
      expect(spy).not.toHaveBeenCalled();
      spy.mockRestore();
    });
  });
});

describe("selected jobs", () => {
  const job = { id: 42, title: "Backend Engineer", company: "Acme", location: "Remote", source: "User Selected Job", description: "Detailed backend engineering role requiring APIs and cloud delivery.", job_url: "https://jobs.example.test/42" };
  it("uses the simplified import heading with a responsive, unclipped form layout", () => {
    app.selectedJobs = [job];
    renderRoute(<SelectedJobsPage />, "/selected-jobs");

    expect(screen.getByRole("heading", { name: "Import Selected Job" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "4. Import Selected Job" })).toBeNull();
    expect(document.querySelector(".selected-jobs-page .selected-saved-panel .selected-jobs-scroll")).not.toBeNull();
    expect(document.querySelector(".selected-import-panel")).not.toBeNull();
    expect(screen.getByLabelText(/job description/i)).toBeInTheDocument();
  });

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
    // Cancelling never touches the network or the list — the card stays.
    expect(screen.getByRole("button", { name: /remove job/i })).toBeInTheDocument();
  });

  it("shows the selected-job count derived from the live list, decreasing as jobs are removed", () => {
    const jobB = { ...job, id: 43, title: "Second Job" };
    app.selectedJobs = [job, jobB];
    renderRoute(<SelectedJobsPage />, "/selected-jobs");
    expect(screen.getByText("2 manually selected or imported opportunities")).toBeInTheDocument();
  });

  it("disables only the targeted job's Remove Job button while a removal is in flight — other cards stay interactive", async () => {
    const jobB = { ...job, id: 43, title: "Second Job" };
    app.selectedJobs = [job, jobB];
    app.deletingJobId = 42;
    renderRoute(<SelectedJobsPage />, "/selected-jobs");
    const buttons = screen.getAllByRole("button", { name: /remove job|removing/i });
    expect(buttons[0]).toHaveTextContent("Removing...");
    expect(buttons[0]).toBeDisabled();
    expect(buttons[1]).toHaveTextContent("Remove Job");
    expect(buttons[1]).not.toBeDisabled();
  });

  it("View Job and Optimize and Compare Scores remain unaffected by the Remove Job button", () => {
    app.selectedJobs = [job]; app.latestResume = { id: 2 };
    renderRoute(<SelectedJobsPage />, "/selected-jobs");
    expect(screen.getByRole("link", { name: /view job/i })).toHaveAttribute("href", job.job_url);
    expect(screen.getByRole("button", { name: /optimize and compare/i })).toBeInTheDocument();
  });

  it("clicking Fetch Job Details still calls previewSelectedJobUrl", async () => {
    renderRoute(<SelectedJobsPage />, "/selected-jobs");
    await userEvent.click(screen.getByRole("button", { name: /fetch job details/i }));
    expect(app.previewSelectedJobUrl).toHaveBeenCalled();
  });

  it("submitting the import form still calls saveSelectedJob", async () => {
    renderRoute(<SelectedJobsPage />, "/selected-jobs");
    fireEvent.submit(document.querySelector(".import-job-form"));
    expect(app.saveSelectedJob).toHaveBeenCalled();
  });
});

// Match score accuracy + consistent display — every Selected Job card is in
// exactly one explicit state (job_routes._selected_job_payload's
// match_status), never an unexplained blank.
describe("selected jobs — Resume Match score states", () => {
  const baseJob = { id: 1, title: "Backend Engineer", company: "Acme", location: "Remote", source: "User Selected Job", description: "Detailed backend engineering role.", job_url: "https://jobs.example.test/1" };

  it("STATE A/B/C — a real score renders the circular ring with its existing label, no status text", () => {
    app.selectedJobs = [{ ...baseJob, original_match_percentage: 78, match_status: "COMPLETE" }];
    renderRoute(<SelectedJobsPage />, "/selected-jobs");
    const card = document.querySelector(".selected-jobs-scroll .job-card");
    expect(card.querySelector(".match-score-ring")).not.toBeNull();
    expect(within(card).getByText("78%")).toBeInTheDocument();
    expect(card.querySelector(".match-status-indicator")).toBeNull();
  });

  it("STATE D — CALCULATING shows a pending indicator, never a fake 0% or blank ring", () => {
    app.selectedJobs = [{ ...baseJob, original_match_percentage: null, match_status: "CALCULATING" }];
    renderRoute(<SelectedJobsPage />, "/selected-jobs");
    const card = document.querySelector(".selected-jobs-scroll .job-card");
    expect(within(card).getByText("Calculating…")).toBeInTheDocument();
    expect(card.querySelector(".match-score-ring")).toBeNull();
    expect(within(card).queryByText("0%")).toBeNull();
  });

  it("STATE E — UNAVAILABLE (e.g. insufficient job info) shows 'Match unavailable', not a fake score", () => {
    app.selectedJobs = [{ ...baseJob, original_match_percentage: null, match_status: "UNAVAILABLE" }];
    renderRoute(<SelectedJobsPage />, "/selected-jobs");
    const card = document.querySelector(".selected-jobs-scroll .job-card");
    expect(within(card).getByText("Match unavailable")).toBeInTheDocument();
    expect(card.querySelector(".match-score-ring")).toBeNull();
  });

  it("STATE F — FAILED shows 'Unable to calculate', never a raw error or blank", () => {
    app.selectedJobs = [{ ...baseJob, original_match_percentage: null, match_status: "FAILED" }];
    renderRoute(<SelectedJobsPage />, "/selected-jobs");
    const card = document.querySelector(".selected-jobs-scroll .job-card");
    expect(within(card).getByText("Unable to calculate")).toBeInTheDocument();
    expect(card.querySelector(".match-score-ring")).toBeNull();
  });

  it("job A's FAILED state does not affect job B's real score (error isolation)", () => {
    app.selectedJobs = [
      { ...baseJob, id: 1, title: "Job A", original_match_percentage: null, match_status: "FAILED" },
      { ...baseJob, id: 2, title: "Job B", original_match_percentage: 85, match_status: "COMPLETE" },
    ];
    renderRoute(<SelectedJobsPage />, "/selected-jobs");
    const cards = document.querySelectorAll(".selected-jobs-scroll .job-card");
    expect(within(cards[0]).getByText("Unable to calculate")).toBeInTheDocument();
    expect(within(cards[1]).getByText("85%")).toBeInTheDocument();
    expect(cards[1].querySelector(".match-score-ring")).not.toBeNull();
  });

  it("never shows a status area at all when match_status is entirely absent (e.g. legacy/unexpected shape)", () => {
    app.selectedJobs = [{ ...baseJob, original_match_percentage: null }];
    renderRoute(<SelectedJobsPage />, "/selected-jobs");
    const card = document.querySelector(".selected-jobs-scroll .job-card");
    expect(card.querySelector(".match-status-indicator")).toBeNull();
    expect(card.querySelector(".match-score-ring")).toBeNull();
  });
});

describe("selected jobs — Save Selected Job eligibility", () => {
  // The mocked app object is a plain value, not real React state — its
  // onChange handlers (app.changeSelectedJob) are no-op vi.fn()s, so
  // firing DOM change events never updates app.selectedJobForm. Every test
  // below instead sets the mocked form state directly, exactly how the
  // real AppContext's state would look after the user finished typing.
  const filledForm = (overrides = {}) => ({
    title: "Backend Engineer", company: "Acme", location: "", work_mode: "Remote", job_type: "Full-time",
    description: "A detailed backend role.", skills_required: "", source: "User Selected Job", job_url: "",
    ...overrides,
  });

  it("Save is disabled on an empty/untouched form and shows a neutral (not fetch-mandatory) hint", () => {
    renderRoute(<SelectedJobsPage />, "/selected-jobs");
    expect(screen.getByRole("button", { name: /^save selected job$/i })).toBeDisabled();
    expect(screen.getByText("Fetch the job details or complete the required fields manually.")).toBeInTheDocument();
    expect(screen.queryByText(/fetch and validate the direct job url before saving/i)).toBeNull();
  });

  it("Save is disabled when required fields are only partially filled", () => {
    app.selectedJobForm = { title: "Backend Engineer", company: "", location: "", work_mode: "Remote", job_type: "Full-time", description: "", skills_required: "", source: "User Selected Job", job_url: "" };
    renderRoute(<SelectedJobsPage />, "/selected-jobs");
    expect(screen.getByRole("button", { name: /^save selected job$/i })).toBeDisabled();
    expect(screen.getByText("Complete the required job details before saving.")).toBeInTheDocument();
  });

  // The core fix: a manually-completed job (default "User Selected Job"
  // source, never fetched) must be saveable — job_url is only required by
  // the backend for a source ending in " Imported".
  it("Save is enabled for a manually-completed job with no job_url and no fetch ever attempted", () => {
    app.selectedJobForm = filledForm();
    renderRoute(<SelectedJobsPage />, "/selected-jobs");
    expect(app.selectedJobImportReady).toBe(false); // no fake fetch state required
    expect(screen.getByRole("button", { name: /^save selected job$/i })).not.toBeDisabled();
    expect(screen.getByText("Ready to save.")).toBeInTheDocument();
  });

  it("Save stays disabled for an Imported source until job_url is also filled (matches backend rule)", () => {
    app.selectedJobForm = filledForm({ source: "LinkedIn Imported" });
    renderRoute(<SelectedJobsPage />, "/selected-jobs");
    expect(screen.getByRole("button", { name: /^save selected job$/i })).toBeDisabled();
    expect(screen.getByText("Complete the required job details before saving.")).toBeInTheDocument();
  });

  it("Save is enabled for an Imported source once job_url is also filled (Case A: successful fetch)", () => {
    app.selectedJobForm = {
      title: "Backend Engineer", company: "Acme", location: "Remote", work_mode: "Remote", job_type: "Full-time",
      description: "A detailed backend role.", skills_required: "", source: "LinkedIn Imported", job_url: "https://linkedin.com/jobs/view/1",
    };
    app.selectedJobImportReady = true;
    renderRoute(<SelectedJobsPage />, "/selected-jobs");
    expect(screen.getByRole("button", { name: /^save selected job$/i })).not.toBeDisabled();
    expect(screen.getByText("Ready to save.")).toBeInTheDocument();
  });

  it("Save is disabled while a save is already in progress, even with valid data", () => {
    app.selectedJobForm = { title: "Backend Engineer", company: "Acme", location: "", work_mode: "Remote", job_type: "Full-time", description: "A detailed backend role.", skills_required: "", source: "User Selected Job", job_url: "" };
    app.busy = "selected";
    renderRoute(<SelectedJobsPage />, "/selected-jobs");
    expect(screen.getByRole("button", { name: /^save selected job$/i })).toBeDisabled();
  });

  it("the Job URL field is not natively required for a manual (non-Imported) source", () => {
    app.selectedJobForm = { title: "", company: "", location: "", work_mode: "Remote", job_type: "Full-time", description: "", skills_required: "", source: "User Selected Job", job_url: "" };
    renderRoute(<SelectedJobsPage />, "/selected-jobs");
    expect(screen.getByPlaceholderText(/linkedin\.com\/jobs\/view/i)).not.toBeRequired();
  });

  it("the Job URL field is natively required for an Imported source", () => {
    app.selectedJobForm = { title: "", company: "", location: "", work_mode: "Remote", job_type: "Full-time", description: "", skills_required: "", source: "Dice Imported", job_url: "" };
    renderRoute(<SelectedJobsPage />, "/selected-jobs");
    expect(screen.getByPlaceholderText(/linkedin\.com\/jobs\/view/i)).toBeRequired();
  });

  it("the Job Description textarea carries its internal-scroll styling hook and still accepts long text", async () => {
    renderRoute(<SelectedJobsPage />, "/selected-jobs");
    const textarea = screen.getByLabelText(/^job description$/i);
    expect(textarea).toHaveClass("job-description-textarea");
    const longText = Array.from({ length: 30 }, (_, i) => `Line ${i + 1} of a long job description.`).join("\n");
    await userEvent.type(textarea, longText.slice(0, 50)); // full string is slow to type in tests; a slice exercises the same onChange path
    expect(app.changeSelectedJob).toHaveBeenCalled();
  });

  it("renders with no console errors for a manually-completed, ready-to-save form", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    app.selectedJobForm = filledForm();
    renderRoute(<SelectedJobsPage />, "/selected-jobs");
    expect(spy).not.toHaveBeenCalled();
    spy.mockRestore();
  });
});

describe("selected jobs — layout (Import stationary, Saved Jobs internally scrollable)", () => {
  const makeJob = (id) => ({
    id, title: `Backend Engineer ${id}`, company: "Acme", location: "Remote", source: "User Selected Job",
    description: "Detailed backend engineering role requiring APIs and cloud delivery.", job_url: `https://jobs.example.test/${id}`,
  });

  it("renders both primary panels as siblings under the two-column layout", () => {
    app.selectedJobs = [makeJob(1)];
    renderRoute(<SelectedJobsPage />, "/selected-jobs");
    const layout = document.querySelector(".selected-jobs-page .selected-layout");
    const importPanel = layout.querySelector(":scope > .selected-import-panel");
    const savedPanel = layout.querySelector(":scope > .selected-saved-panel");
    expect(importPanel).not.toBeNull();
    expect(savedPanel).not.toBeNull();
  });

  it("wraps the saved-jobs list in its own internal scroll container, separate from the import form", () => {
    app.selectedJobs = [makeJob(1)];
    renderRoute(<SelectedJobsPage />, "/selected-jobs");
    const scrollBox = document.querySelector(".selected-saved-panel .selected-jobs-scroll");
    expect(scrollBox).not.toBeNull();
    // The import form is not inside the saved-jobs scroll container — it's an
    // entirely separate column that the scroll box has no relationship to.
    expect(document.querySelector(".selected-import-panel .selected-jobs-scroll")).toBeNull();
  });

  it("keeps the Saved Selected Jobs header (title, count, Load Selected Jobs) outside the scrolling list", () => {
    app.selectedJobs = [makeJob(1)];
    renderRoute(<SelectedJobsPage />, "/selected-jobs");
    const savedPanel = document.querySelector(".selected-saved-panel");
    const header = savedPanel.querySelector(":scope > header");
    const scrollBox = savedPanel.querySelector(".selected-jobs-scroll");
    expect(header).not.toBeNull();
    // header is a sibling of the scroll box, never nested inside it.
    expect(scrollBox.contains(header)).toBe(false);
    expect(within(header).getByText("Saved Selected Jobs")).toBeInTheDocument();
    expect(within(header).getByRole("button", { name: /load selected jobs/i })).toBeInTheDocument();
  });

  it("renders every saved job inside the scroll container, none dropped by the new layout", () => {
    app.selectedJobs = [makeJob(1), makeJob(2), makeJob(3), makeJob(4), makeJob(5)];
    renderRoute(<SelectedJobsPage />, "/selected-jobs");
    const scrollBox = document.querySelector(".selected-saved-panel .selected-jobs-scroll");
    expect(within(scrollBox).getAllByRole("button", { name: /remove job/i })).toHaveLength(5);
  });

  it("renders with no console errors", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    app.selectedJobs = [makeJob(1), makeJob(2)];
    app.latestResume = { id: 2 };
    renderRoute(<SelectedJobsPage />, "/selected-jobs");
    expect(spy).not.toHaveBeenCalled();
    spy.mockRestore();
  });
});

describe("email and workflow", () => {
  it("renders email content safely — no Daily Summary control anywhere", async () => {
    // Daily Summary stays removed from the redesigned Email Summary page —
    // there is no "Generate Email Summary" button to click any more. This
    // renders a scanned Gmail event (the page's actual data source now) and
    // confirms its snippet is shown as text, never executed as markup.
    app.jobEmailEvents = [{
      id: 1, subject: "Interview Invitation", sender: "hr@abccorp.com", event_type: "INTERVIEW_INVITE",
      confidence: 0.9, matched_application_id: null, resolution: "NEEDS_CONFIRMATION",
      body_snippet: "<script>bad()</script>", received_at: new Date().toISOString(), is_read: false, is_starred: false,
    }];
    renderRoute(<EmailSummaryPage />, "/emails");
    expect(screen.getByText("<script>bad()</script>")).toBeInTheDocument();
    expect(document.querySelector(".email-preview script")).toBeNull();
    expect(screen.queryByRole("button", { name: /generate email summary/i })).toBeNull();
    expect(screen.queryByRole("tab", { name: /daily summary/i })).toBeNull();
  });

  it("runs workflow once and disables while active", async () => {
    app.busy = "workflow";
    renderRoute(<WorkflowPage />, "/workflow");
    expect(screen.getByRole("button", { name: /workflow running/i })).toBeDisabled();
  });
});
