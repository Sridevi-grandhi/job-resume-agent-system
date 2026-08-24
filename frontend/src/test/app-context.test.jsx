import { act, render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider, useApp } from "../context/AppContext";
import * as api from "../api";

vi.mock("../api", () => {
  const fn = () => vi.fn().mockResolvedValue({});
  return {
    checkBackendHealth: fn(), loginUser: fn(), signupUser: fn(), saveUserPreferences: fn(), getUserPreferences: fn(),
    uploadResume: fn(), getLatestResume: fn(), getJobs: fn(), searchJobs: fn(), searchUpdatedJobs: fn(), importJobByUrl: fn(),
    getSelectedJobs: fn(), addSelectedJob: fn(), deleteSelectedJob: fn(), getOptimizedResumes: fn(), optimizeResume: fn(),
    startAtsOptimization: fn(), pollAtsWorkflow: fn(), retryAtsWorkflow: fn(), getAtsResultByJob: fn(), getJobRequirements: fn(),
    analyzeOptimizerRequirements: fn(), getMissingSkills: fn(), getEvidence: fn(), saveEvidence: fn(), deleteEvidence: fn(),
    getMatches: fn(), calculateMatches: fn(), getEmailLogs: fn(), generateEmailSummary: fn(), runFullWorkflow: fn(),
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
  it("authenticates, persists, restores, and logs out", async () => {
    api.loginUser.mockResolvedValue({ user_id: 7, email: "candidate@example.test", name: "Candidate" });
    mount();
    await act(() => app.authenticate("login", { email: "candidate@example.test", password: "secret" }));
    expect(JSON.parse(localStorage.getItem("currentUser")).user_id).toBe(7);
    act(() => app.logout());
    expect(localStorage.getItem("currentUser")).toBeNull();
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

  it("runs ATS requirements, evidence, scores, email, and workflow APIs", async () => {
    localStorage.setItem("currentUser", JSON.stringify({ user_id: 7 }));
    api.startAtsOptimization.mockResolvedValue({ workflow_id: 3, workflow_status: "PENDING" });
    api.pollAtsWorkflow.mockResolvedValue({ workflow_id: 3, workflow_status: "COMPLETED", original_match_percentage: 57, optimized_match_percentage: 82 });
    api.getJobRequirements.mockResolvedValue({ skills: [] });
    api.analyzeOptimizerRequirements.mockResolvedValue({ verified_resume_skills: [] });
    api.getEvidence.mockResolvedValue({ evidence: [] }); api.saveEvidence.mockResolvedValue({ evidence: [] });
    api.getMatches.mockResolvedValue({ matches: [] }); api.getOptimizedResumes.mockResolvedValue({ optimized_resumes: [] });
    api.getEmailLogs.mockResolvedValue({ email_logs: [] }); api.runFullWorkflow.mockResolvedValue({ message: "Done" });
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
});
