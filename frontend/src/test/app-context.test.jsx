import { act, render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider, useApp } from "../context/AppContext";
import * as api from "../api";

vi.mock("../api", () => {
  const fn = () => vi.fn().mockResolvedValue({});
  return {
    checkBackendHealth: fn(), loginUser: fn(), signupUser: fn(), updateProfileName: fn(), saveUserPreferences: fn(), getUserPreferences: fn(),
    uploadResume: fn(), getLatestResume: fn(), getJobs: fn(), searchJobs: fn(), searchUpdatedJobs: fn(), importJobByUrl: fn(),
    getSelectedJobs: fn(), addSelectedJob: fn(), deleteSelectedJob: fn(), getOptimizedResumes: fn(), optimizeResume: fn(),
    startAtsOptimization: fn(), pollAtsWorkflow: fn(), retryAtsWorkflow: fn(), getAtsResultByJob: fn(), getJobRequirements: fn(),
    analyzeOptimizerRequirements: fn(), getMissingSkills: fn(), getEvidence: fn(), saveEvidence: fn(), deleteEvidence: fn(),
    getMatches: fn(), calculateMatches: fn(), getEmailLogs: fn(), generateEmailSummary: fn(), runFullWorkflow: fn(),
    getGmailStatus: fn(), disconnectGmail: fn(), scanJobEmails: fn(), getApplications: fn(), getJobEmailEvents: fn(),
    getEmailSummaryPreferences: fn(), updateEmailSummaryPreferences: fn(),
  };
});

let app;
// Test probe intentionally exposes the latest provider value to integration assertions.
// eslint-disable-next-line react-hooks/globals
function Probe() { app = useApp(); return <div>{app.currentUser?.email || "guest"}</div>; }
const mount = () => render(<AppProvider><Probe /></AppProvider>);

beforeEach(() => {
  Object.values(api).forEach((mock) => mock.mockReset?.().mockResolvedValue({}));
  api.checkBackendHealth.mockResolvedValue({ status: "healthy" });
});

describe("application state provider", () => {
  it("keeps a newly saved notification preference when an older settings load finishes late", async () => {
    localStorage.setItem("currentUser", JSON.stringify({ user_id: 7, email: "candidate@example.test" }));
    let finishLoad;
    api.getEmailSummaryPreferences.mockImplementation(() => new Promise((resolve) => { finishLoad = resolve; }));
    api.updateEmailSummaryPreferences.mockResolvedValue({
      gmail_scan_frequency: "every_1_hour", job_alerts_enabled: false,
      application_updates_enabled: true, interview_alerts_enabled: true,
    });
    mount();
    let loading;
    await act(async () => { loading = app.loadEmailSummaryPreferences(); await Promise.resolve(); });
    await act(() => app.saveEmailSummaryPreferences({ job_alerts_enabled: false }));
    await act(async () => {
      finishLoad({ gmail_scan_frequency: "daily", job_alerts_enabled: true });
      await loading;
    });
    expect(app.emailSummaryPreferences.job_alerts_enabled).toBe(false);
    expect(app.emailSummaryPreferences.gmail_scan_frequency).toBe("every_1_hour");
  });
  it("keeps Gmail scan and disconnect completion messages after refreshing status", async () => {
    localStorage.setItem("currentUser", JSON.stringify({ user_id: 7, email: "candidate@example.test" }));
    api.scanJobEmails.mockResolvedValue({ messages_checked: 2 });
    api.getGmailStatus.mockResolvedValue({ connected: true });
    api.getApplications.mockResolvedValue({ applications: [] });
    api.getJobEmailEvents.mockResolvedValue({ events: [] });
    mount();
    await act(() => app.scanGmailForJobs());
    expect(app.messages.gmail).toBe("Scan complete — checked 2 message(s).");
    await act(() => app.disconnectGmailAccount());
    expect(app.messages.gmail).toBe("Gmail disconnected.");
  });

  it("authenticates, persists, restores, and logs out", async () => {
    api.loginUser.mockResolvedValue({ user_id: 7, email: "candidate@example.test", name: "Candidate" });
    mount();
    await act(() => app.authenticate("login", { email: "candidate@example.test", password: "secret" }));
    expect(JSON.parse(localStorage.getItem("currentUser")).user_id).toBe(7);
    expect(JSON.parse(localStorage.getItem("currentUser")).name).toBe("Candidate");
    act(() => app.logout());
    expect(localStorage.getItem("currentUser")).toBeNull();
  });

  it("synchronizes a saved profile name in shared state and local storage only after success", async () => {
    localStorage.setItem("currentUser", JSON.stringify({ user_id: 7, name: "Tanishka", email: "candidate@example.test" }));
    api.updateProfileName.mockResolvedValueOnce({ user_id: 7, name: "Tanishka Test", email: "candidate@example.test" });
    mount();
    await act(() => app.saveProfileName({ name: "Tanishka Test", current_password: "secret123" }));
    expect(api.updateProfileName).toHaveBeenCalledWith(7, { name: "Tanishka Test", current_password: "secret123" });
    expect(app.currentUser.name).toBe("Tanishka Test");
    expect(JSON.parse(localStorage.getItem("currentUser")).name).toBe("Tanishka Test");
    api.updateProfileName.mockRejectedValueOnce(new Error("backend detail"));
    await act(() => app.saveProfileName({ name: "Wrong", current_password: "wrong" }));
    expect(app.currentUser.name).toBe("Tanishka Test");
    expect(JSON.parse(localStorage.getItem("currentUser")).name).toBe("Tanishka Test");
  });

  it("handles preferences and resume lifecycle", async () => {
    localStorage.setItem("currentUser", JSON.stringify({ user_id: 7, email: "a@example.test" }));
    api.getUserPreferences.mockResolvedValue({ job_title: "Engineer", schedule_time: "09:30:00" });
    api.saveUserPreferences.mockResolvedValue({ message: "Saved" });
    api.uploadResume.mockResolvedValue({ id: 4, file_name: "resume.txt" });
    api.getLatestResume.mockResolvedValue({ id: 4, file_name: "resume.txt" });
    mount();
    act(() => app.changePreferences({ target: { name: "location", value: "Chicago" } }));
    await act(() => app.loadPreferences());
    await act(() => app.savePreferences({ preventDefault: vi.fn() }));
    const file = new File(["resume"], "resume.txt");
    act(() => app.setSelectedResumeFile(file));
    await act(() => app.uploadResume());
    await act(() => app.loadLatestResume());
    expect(api.uploadResume).toHaveBeenCalledWith(7, file);
  });

  it("keeps existing and updated job searches separate", async () => {
    localStorage.setItem("currentUser", JSON.stringify({ user_id: 7 }));
    api.getUserPreferences.mockResolvedValue({ job_title: "Engineer" });
    api.searchJobs.mockResolvedValue({ jobs: [{ id: 1, source: "Indeed" }] });
    api.searchUpdatedJobs.mockResolvedValue({ jobs: [{ id: 2, source: "Remotive API" }] });
    api.getJobs.mockResolvedValue([{ id: 3 }]);
    mount(); await act(() => app.loadPreferences());
    await act(() => app.search(false)); expect(api.searchJobs).toHaveBeenCalledWith(7);
    await act(() => app.search(true)); expect(api.searchUpdatedJobs).toHaveBeenCalledWith(7);
    await act(() => app.loadJobs());
  });

  it("imports, saves, loads, and removes selected jobs", async () => {
    localStorage.setItem("currentUser", JSON.stringify({ user_id: 7 }));
    const job = { id: 9, title: "Engineer", company: "Acme", description: "Detailed role", job_url: "https://jobs.example.test/9" };
    api.importJobByUrl.mockResolvedValue({ ...job, status: "complete", normalized_job_url: job.job_url });
    api.addSelectedJob.mockResolvedValue({ job, message: "Saved" });
    api.getSelectedJobs.mockResolvedValue({ jobs: [job] });
    api.deleteSelectedJob.mockResolvedValue({ message: "Removed" });
    mount();
    act(() => app.changeSelectedJob({ target: { name: "job_url", value: job.job_url } }));
    await act(() => app.previewSelectedJobUrl());
    await act(() => app.saveSelectedJob({ preventDefault: vi.fn() }));
    await act(() => app.loadSelectedJobs());
    await act(() => app.removeSelectedJob(9));
    expect(api.deleteSelectedJob).toHaveBeenCalledWith(7, 9);
    act(() => app.clearSelectedJob());
  });

  describe("removeSelectedJob", () => {
    const jobA = { id: 9, title: "Engineer A", company: "Acme" };
    const jobB = { id: 10, title: "Engineer B", company: "Beta" };

    async function mountWithTwoSelectedJobs() {
      localStorage.setItem("currentUser", JSON.stringify({ user_id: 7 }));
      api.getSelectedJobs.mockResolvedValue({ jobs: [jobA, jobB] });
      mount();
      await act(() => app.loadSelectedJobs());
    }

    it("removes only the targeted job from state on success — the other job is untouched", async () => {
      await mountWithTwoSelectedJobs();
      api.deleteSelectedJob.mockResolvedValue({ message: "Selected job removed successfully", job_id: 9 });
      await act(() => app.removeSelectedJob(9));
      expect(app.selectedJobs.map((j) => j.id)).toEqual([10]);
      expect(app.messages.selected).toBe("Selected job removed successfully");
    });

    it("does not re-fetch the whole list after a successful delete (no redundant, extra-failure-prone call)", async () => {
      await mountWithTwoSelectedJobs();
      api.deleteSelectedJob.mockResolvedValue({ message: "Removed" });
      api.getSelectedJobs.mockClear();
      await act(() => app.removeSelectedJob(9));
      expect(api.getSelectedJobs).not.toHaveBeenCalled();
    });

    it("keeps the job visible and shows a friendly message on a raw network failure (never a silent optimistic removal)", async () => {
      await mountWithTwoSelectedJobs();
      api.deleteSelectedJob.mockRejectedValue(new TypeError("Failed to fetch"));
      await act(() => app.removeSelectedJob(9));
      expect(app.selectedJobs.map((j) => j.id)).toEqual([9, 10]);
      expect(app.messages.selected).toBe("Unable to remove this job. Please try again.");
    });

    it("surfaces a real backend error message as-is (e.g. 404 Selected job not found)", async () => {
      await mountWithTwoSelectedJobs();
      api.deleteSelectedJob.mockRejectedValue(new Error("Selected job not found"));
      await act(() => app.removeSelectedJob(9));
      expect(app.selectedJobs.map((j) => j.id)).toEqual([9, 10]);
      expect(app.messages.selected).toBe("Selected job not found");
    });
  });

  describe("saveSelectedJob — payload, success, failure", () => {
    const manualJob = {
      title: "Backend Engineer", company: "Acme", location: "Remote", work_mode: "Remote", job_type: "Full-time",
      description: "A detailed backend role.", skills_required: "Java", source: "User Selected Job", job_url: "",
    };

    async function mountWithForm(form) {
      localStorage.setItem("currentUser", JSON.stringify({ user_id: 7 }));
      mount();
      for (const [name, value] of Object.entries(form)) {
        act(() => app.changeSelectedJob({ target: { name, value } }));
      }
    }

    it("calls addSelectedJob with the exact current form — payload unchanged", async () => {
      await mountWithForm(manualJob);
      api.addSelectedJob.mockResolvedValue({ job: { id: 20, ...manualJob }, message: "Saved" });
      api.getSelectedJobs.mockResolvedValue([{ id: 20, ...manualJob }]);
      await act(() => app.saveSelectedJob({ preventDefault: vi.fn() }));
      expect(api.addSelectedJob).toHaveBeenCalledWith(7, expect.objectContaining(manualJob));
    });

    it("a successful save adds the new job to selectedJobs (count increases) without a page refresh", async () => {
      await mountWithForm(manualJob);
      const saved = { id: 21, ...manualJob };
      api.addSelectedJob.mockResolvedValue({ job: saved, message: "Job saved successfully" });
      api.getSelectedJobs.mockResolvedValue([saved]);
      expect(app.selectedJobs).toHaveLength(0);
      await act(() => app.saveSelectedJob({ preventDefault: vi.fn() }));
      expect(app.selectedJobs).toHaveLength(1);
      expect(app.selectedJobs[0].id).toBe(21);
      expect(app.messages.selected).toBe("Job saved successfully");
    });

    it("a successful save clears the form for the next entry", async () => {
      await mountWithForm(manualJob);
      api.addSelectedJob.mockResolvedValue({ job: { id: 22, ...manualJob }, message: "Saved" });
      api.getSelectedJobs.mockResolvedValue([{ id: 22, ...manualJob }]);
      await act(() => app.saveSelectedJob({ preventDefault: vi.fn() }));
      expect(app.selectedJobForm.title).toBe("");
      expect(app.selectedJobForm.description).toBe("");
    });

    it("a failed save preserves every entered field — never clears or pretends success", async () => {
      await mountWithForm(manualJob);
      api.addSelectedJob.mockRejectedValue(new Error("Unable to save job right now."));
      await act(() => app.saveSelectedJob({ preventDefault: vi.fn() }));
      expect(app.selectedJobForm).toMatchObject(manualJob);
      expect(app.selectedJobs).toHaveLength(0);
      expect(app.messages.selected).toBe("Unable to save job right now.");
    });

    it("a failed save allows an immediate retry with the same data", async () => {
      await mountWithForm(manualJob);
      api.addSelectedJob.mockRejectedValueOnce(new Error("Network error")).mockResolvedValueOnce({ job: { id: 23, ...manualJob }, message: "Saved" });
      api.getSelectedJobs.mockResolvedValue([{ id: 23, ...manualJob }]);
      await act(() => app.saveSelectedJob({ preventDefault: vi.fn() }));
      expect(app.selectedJobs).toHaveLength(0);
      await act(() => app.saveSelectedJob({ preventDefault: vi.fn() }));
      expect(api.addSelectedJob).toHaveBeenCalledTimes(2);
      expect(app.selectedJobs).toHaveLength(1);
    });
  });

  it("runs ATS requirements, evidence, scores, email, and workflow APIs", async () => {
    localStorage.setItem("currentUser", JSON.stringify({ user_id: 7 }));
    api.startAtsOptimization.mockResolvedValue({ workflow_id: 3, workflow_status: "PENDING" });
    api.pollAtsWorkflow.mockResolvedValue({ workflow_id: 3, workflow_status: "COMPLETED", original_match_percentage: 57, optimized_match_percentage: 82 });
    api.getJobRequirements.mockResolvedValue({ skills: [] });
    api.analyzeOptimizerRequirements.mockResolvedValue({ verified_resume_skills: [] });
    api.getEvidence.mockResolvedValue({ evidence: [] }); api.saveEvidence.mockResolvedValue({ evidence: [] });
    api.getMatches.mockResolvedValue({ matches: [] }); api.getOptimizedResumes.mockResolvedValue({ optimized_resumes: [] });
    api.getEmailLogs.mockResolvedValue({ logs: [] }); api.runFullWorkflow.mockResolvedValue({ message: "Done" });
    mount();
    act(() => app.setAtsSelectedJobId("9"));
    await act(() => app.analyzeAndOptimize(9));
    await act(() => app.loadJobRequirements(9)); await act(() => app.loadMissingSkills(9));
    await act(() => app.loadSkillConfirmations(9)); await act(() => app.saveConfirmedSkills(9, [{ skill: "Kafka" }]));
    await act(() => app.deleteConfirmedSkill(9, 2));
    await act(() => app.loadMatches()); await act(() => app.calculateMatches());
    await act(() => app.loadEmailLogs()); await act(() => app.generateEmailSummary());
    await act(() => app.loadOptimizedResumes()); await act(() => app.optimizeResume(9));
    expect(api.startAtsOptimization).toHaveBeenCalledWith(7, 9, false);
  });

  it("captures request errors as user-facing messages", async () => {
    localStorage.setItem("currentUser", JSON.stringify({ user_id: 7 }));
    api.getJobs.mockRejectedValue(new Error("Network unavailable"));
    mount();
    await act(() => app.loadJobs());
    await waitFor(() => expect(app.messages.jobs).toBe("Network unavailable"));
  });

  it("covers verification failures, retries, cached results, and full workflow", async () => {
    localStorage.setItem("currentUser", JSON.stringify({ user_id: 7 }));
    api.getLatestResume.mockRejectedValue(new Error("Missing"));
    api.retryAtsWorkflow.mockResolvedValue({ workflow_id: 5 });
    api.pollAtsWorkflow.mockResolvedValue({ workflow_id: 5, workflow_status: "FAILED", error_message: "Validation failed" });
    api.getAtsResultByJob.mockRejectedValue(new Error("No result"));
    api.getMissingSkills.mockResolvedValue({ missing_mandatory_skills: [] });
    api.getUserPreferences.mockResolvedValue({ job_title: "Engineer" });
    api.runFullWorkflow.mockResolvedValue({ message: "Workflow complete" });
    api.getJobs.mockResolvedValue([]); api.getOptimizedResumes.mockResolvedValue([]); api.getMatches.mockResolvedValue([]); api.getEmailLogs.mockResolvedValue([]);
    mount();
    await act(() => app.verifyLatestResume());
    await act(() => app.retryAtsWorkflow(5));
    await act(() => app.loadAtsResultForJob(9));
    await act(() => app.loadMissingSkills(9, { forceRefresh: false }));
    await act(() => app.loadPreferences());
    await act(() => app.runWorkflow());
    expect(api.runFullWorkflow).toHaveBeenCalledWith(7);
  });

  it("does not apply stale completed metadata while a new workflow is generating", async () => {
    localStorage.setItem("currentUser", JSON.stringify({ user_id: 7 }));
    api.startAtsOptimization.mockResolvedValue({
      workflow_id: 9,
      workflow_status: "PENDING",
      created_new: true,
      result: { workflow_id: 9, workflow_status: "PENDING" },
    });
    api.pollAtsWorkflow.mockImplementation(async (_id, { onProgress } = {}) => {
      onProgress?.({ workflow_id: 9, workflow_status: "GENERATING_RESUME" });
      return {
        workflow_id: 9,
        workflow_status: "COMPLETED",
        original_match_percentage: 80,
        optimized_match_percentage: 88,
        generation_metadata: { llm_used: true, llm_model: "llama3.1:8b" },
      };
    });
    mount();
    act(() => app.setAtsResult({
      workflow_id: 8,
      workflow_status: "COMPLETED",
      generation_metadata: { fallback_used: true, fallback_reason: "timeout" },
      original_match_percentage: 80,
      optimized_match_percentage: 88,
    }));
    await act(() => app.analyzeAndOptimize(9, true));
    expect(app.atsResult.workflow_id).toBe(9);
    expect(app.atsResult.generation_metadata?.fallback_used).not.toBe(true);
    expect(app.activeAtsWorkflowId).toBe(9);
  });

  it("handles terminal ATS starts and selected-job workflow completion", async () => {
    localStorage.setItem("currentUser", JSON.stringify({ user_id: 7 }));
    const job = { id: 9, title: "Engineer", company: "Acme", description: "A complete job description", job_url: "https://jobs.example.test/9" };
    api.startAtsOptimization.mockResolvedValue({ workflow_id: 2, workflow_status: "COMPLETED", result: { workflow_status: "COMPLETED" } });
    api.addSelectedJob.mockResolvedValue({ job, workflow_id: 2, workflow_status: "PENDING" });
    api.getSelectedJobs.mockResolvedValue([job]);
    api.pollAtsWorkflow.mockResolvedValue({ workflow_id: 2, workflow_status: "COMPLETED", original_match_percentage: 57, optimized_match_percentage: 82 });
    api.getOptimizedResumes.mockResolvedValue([]); api.getMatches.mockResolvedValue([]);
    mount();
    await act(() => app.analyzeAndOptimize(9, true));
    act(() => app.changeSelectedJob({ target: { name: "title", value: "Engineer" } }));
    await act(() => app.saveSelectedJob({ preventDefault: vi.fn() }));
    expect(api.pollAtsWorkflow).toHaveBeenCalled();
  });

  // Match score accuracy — success message must reflect the actual
  // workflow outcome, never falsely claim "optimize-and-match completed"
  // when the workflow ended some other way (e.g. INELIGIBLE_LOW_SCORE — a
  // real original score was computed, optimization just wasn't run).
  it("shows the real computed score, not a false 'completed' claim, when the workflow ends INELIGIBLE_LOW_SCORE", async () => {
    localStorage.setItem("currentUser", JSON.stringify({ user_id: 7 }));
    const job = { id: 10, title: "Engineer", company: "Acme", description: "A complete job description", job_url: "https://jobs.example.test/10" };
    api.addSelectedJob.mockResolvedValue({ job, workflow_id: 3, workflow_status: "PENDING", message: "Job saved. ATS optimization workflow started." });
    api.getSelectedJobs.mockResolvedValue([job]);
    api.pollAtsWorkflow.mockResolvedValue({ workflow_id: 3, workflow_status: "INELIGIBLE_LOW_SCORE", original_match_percentage: 35, optimized_match_percentage: null, error_message: null });
    mount();
    await act(() => app.saveSelectedJob({ preventDefault: vi.fn() }));
    expect(app.messages.selected).toBe("Job saved. Resume match calculated: 35%.");
    expect(app.messages.selected).not.toContain("optimize-and-match completed");
  });

  // Section 20/21: the card must update once scoring settles, without the
  // user needing to visit Workflow/ATS Optimizer or refresh the browser.
  it("re-fetches Selected Jobs after the ATS poll settles, so the card shows its final score without a manual refresh", async () => {
    localStorage.setItem("currentUser", JSON.stringify({ user_id: 7 }));
    const job = { id: 12, title: "Engineer", company: "Acme", description: "A complete job description", job_url: "https://jobs.example.test/12" };
    api.addSelectedJob.mockResolvedValue({ job, workflow_id: 5, workflow_status: "PENDING", message: "Job saved. ATS optimization workflow started." });
    api.getSelectedJobs
      .mockResolvedValueOnce([{ ...job, original_match_percentage: null, match_status: "CALCULATING" }]) // right after save, before polling
      .mockResolvedValueOnce([{ ...job, original_match_percentage: 78, match_status: "COMPLETE" }]); // after the poll settles
    api.pollAtsWorkflow.mockResolvedValue({ workflow_id: 5, workflow_status: "COMPLETED", original_match_percentage: 78, optimized_match_percentage: 85 });
    api.getOptimizedResumes.mockResolvedValue([]); api.getMatches.mockResolvedValue([]);
    mount();
    await act(() => app.saveSelectedJob({ preventDefault: vi.fn() }));
    expect(api.getSelectedJobs).toHaveBeenCalledTimes(2);
    expect(app.selectedJobs[0].original_match_percentage).toBe(78);
    expect(app.selectedJobs[0].match_status).toBe("COMPLETE");
  });

  it("still shows a clean error (never a raw message) when the workflow genuinely fails with no score", async () => {
    localStorage.setItem("currentUser", JSON.stringify({ user_id: 7 }));
    const job = { id: 11, title: "Engineer", company: "Acme", description: "A complete job description", job_url: "https://jobs.example.test/11" };
    api.addSelectedJob.mockResolvedValue({ job, workflow_id: 4, workflow_status: "PENDING", message: "Job saved. ATS optimization workflow started." });
    api.getSelectedJobs.mockResolvedValue([job]);
    api.pollAtsWorkflow.mockResolvedValue({ workflow_id: 4, workflow_status: "FAILED", original_match_percentage: null, optimized_match_percentage: null, error_message: "Resume generation could not be completed." });
    mount();
    await act(() => app.saveSelectedJob({ preventDefault: vi.fn() }));
    expect(app.messages.selected).toBe("Resume generation could not be completed.");
    expect(app.messages.selected).not.toContain("optimize-and-match completed");
  });
});

// Dashboard state tracking fix — refreshUserWorkspaceState is the single
// hydration function Dashboard calls on its own mount so it never needs
// Preferences/Resume/Job Search/Selected Jobs/ATS/Workflow to have been
// visited first. It reuses the exact same loaders those pages already call
// themselves (loadPreferences, verifyLatestResume, loadJobs,
// loadSelectedJobs, loadOptimizedResumes, loadMatches, loadEmailLogs) —
// no new API calls, no new business logic.
describe("refreshUserWorkspaceState — Dashboard hydration", () => {
  it("independently loads preferences, resume, jobs, selected jobs, optimized resumes, matches, and email logs", async () => {
    localStorage.setItem("currentUser", JSON.stringify({ user_id: 7, email: "a@example.test" }));
    api.getUserPreferences.mockResolvedValue({ job_title: "Engineer", schedule_time: "09:00:00" });
    api.getLatestResume.mockResolvedValue({ id: 4, file_name: "resume.txt" });
    api.getJobs.mockResolvedValue([{ id: 1, title: "Engineer" }]);
    api.getSelectedJobs.mockResolvedValue([{ id: 1, title: "Engineer" }]);
    api.getOptimizedResumes.mockResolvedValue([{ id: 2 }]);
    api.getMatches.mockResolvedValue([{ id: 3, job_id: 1, match_percentage: 80 }]);
    api.getEmailLogs.mockResolvedValue([{ id: 5 }]);
    mount();

    await act(() => app.refreshUserWorkspaceState());

    expect(app.preferencesSaved).toBe(true);
    expect(app.latestResume).toEqual({ id: 4, file_name: "resume.txt" });
    expect(app.jobs.some((job) => job.id === 1)).toBe(true);
    expect(app.selectedJobs).toEqual([{ id: 1, title: "Engineer" }]);
    expect(app.optimizedResumes).toEqual([{ id: 2 }]);
    expect(app.matches).toEqual([{ id: 3, job_id: 1, match_percentage: 80 }]);
    expect(app.emailLogs).toEqual([{ id: 5 }]);
  });

  it("is a no-op with no logged-in user — never calls any workspace API without a real user id", async () => {
    mount();
    await act(() => app.refreshUserWorkspaceState());
    expect(api.getUserPreferences).not.toHaveBeenCalled();
    expect(api.getJobs).not.toHaveBeenCalled();
    expect(api.getEmailLogs).not.toHaveBeenCalled();
  });

  it("one failing source (e.g. no preferences saved yet) never blocks the others from loading", async () => {
    localStorage.setItem("currentUser", JSON.stringify({ user_id: 7, email: "a@example.test" }));
    api.getUserPreferences.mockRejectedValue(new Error("Not found"));
    api.getLatestResume.mockResolvedValue({ id: 4, file_name: "resume.txt" });
    api.getJobs.mockResolvedValue([{ id: 1 }]);
    api.getEmailLogs.mockResolvedValue([{ id: 9 }]);
    mount();

    await act(() => app.refreshUserWorkspaceState());

    expect(app.preferencesSaved).toBe(false); // the failed source stays at its safe default
    expect(app.latestResume).toEqual({ id: 4, file_name: "resume.txt" }); // siblings still loaded
    expect(app.emailLogs).toEqual([{ id: 9 }]);
  });

  it("no resume yet (404) resolves to null silently, without surfacing a scary error", async () => {
    localStorage.setItem("currentUser", JSON.stringify({ user_id: 7, email: "a@example.test" }));
    api.getLatestResume.mockRejectedValue(new Error("Not found"));
    mount();

    await act(() => app.refreshUserWorkspaceState());

    expect(app.latestResume).toBeNull();
  });

  it("D/J. overlapping generate clicks start one workflow and ignore status reloads", async () => {
    localStorage.setItem("currentUser", JSON.stringify({ user_id: 7 }));
    api.startAtsOptimization.mockResolvedValue({
      workflow_id: 3,
      workflow_status: "PENDING",
      created_new: true,
      result: { workflow_id: 3, workflow_status: "PENDING" },
    });
    let finishPoll;
    api.pollAtsWorkflow.mockImplementation(async (_id, { onProgress } = {}) => {
      onProgress?.({
        workflow_id: 3,
        workflow_status: "ANALYZING_ORIGINAL",
        updated_at: "2026-09-23T18:00:01Z",
        major_step: 5,
      });
      return new Promise((resolve) => {
        finishPoll = resolve;
      });
    });
    api.getOptimizedResumes.mockResolvedValue({ optimized_resumes: [] });
    api.getMatches.mockResolvedValue([]);
    mount();
    let first;
    await act(async () => {
      first = app.analyzeAndOptimize(9, true);
      await Promise.resolve();
      const second = await app.analyzeAndOptimize(9, true);
      expect(second).toBeNull();
      await app.loadAtsResultForJob(9);
      await app.loadMissingSkills(9);
    });
    expect(api.startAtsOptimization).toHaveBeenCalledTimes(1);
    expect(api.getAtsResultByJob).not.toHaveBeenCalled();
    expect(api.analyzeOptimizerRequirements).not.toHaveBeenCalled();
    expect(app.messages.ats).toMatch(/already in progress|Analyzing Original Resume/i);
    expect(app.atsResult.workflow_status).toBe("ANALYZING_ORIGINAL");
    await act(async () => {
      finishPoll({
        workflow_id: 3,
        workflow_status: "COMPLETED",
        updated_at: "2026-09-23T18:00:20Z",
        original_match_percentage: 57,
        optimized_match_percentage: 82,
      });
      await first;
    });
    expect(app.atsResult.workflow_status).toBe("COMPLETED");
    expect(api.startAtsOptimization).toHaveBeenCalledTimes(1);
  });

  it("C. older ANALYZING_ORIGINAL payload cannot overwrite a newer completed result", async () => {
    localStorage.setItem("currentUser", JSON.stringify({ user_id: 7 }));
    api.getAtsResultByJob.mockResolvedValue({
      workflow_id: 10,
      workflow_status: "ANALYZING_ORIGINAL",
      updated_at: "2026-09-23T17:00:00Z",
    });
    mount();
    act(() => app.setAtsResult({
      workflow_id: 10,
      workflow_status: "COMPLETED",
      updated_at: "2026-09-23T18:00:00Z",
      optimized_resume_id: 91,
      generated_resume: { plain_text: "Kept" },
      optimized_match_percentage: 82,
    }));
    await act(() => app.loadAtsResultForJob(9));
    expect(app.atsResult.workflow_status).toBe("COMPLETED");
    expect(app.atsResult.optimized_resume_id).toBe(91);
    expect(app.atsResult.generated_resume.plain_text).toBe("Kept");
  });

  it("F. retry does not re-run requirement analysis", async () => {
    localStorage.setItem("currentUser", JSON.stringify({ user_id: 7 }));
    api.retryAtsWorkflow.mockResolvedValue({ workflow_id: 5, workflow_status: "PENDING" });
    api.pollAtsWorkflow.mockResolvedValue({
      workflow_id: 5,
      workflow_status: "COMPLETED",
      original_match_percentage: 57,
      optimized_match_percentage: 82,
    });
    api.getEvidence.mockResolvedValue({ evidence: [], can_generate_resume: true });
    api.getOptimizedResumes.mockResolvedValue({ optimized_resumes: [] });
    mount();
    act(() => app.setAtsSelectedJobId("9"));
    await act(() => app.retryAtsWorkflow(5));
    expect(api.retryAtsWorkflow).toHaveBeenCalledWith(5);
    expect(api.analyzeOptimizerRequirements).not.toHaveBeenCalled();
    expect(api.startAtsOptimization).not.toHaveBeenCalled();
  });
});
