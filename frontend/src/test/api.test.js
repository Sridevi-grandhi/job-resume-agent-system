import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";

const ok = (data = { ok: true }) => Promise.resolve(new Response(JSON.stringify(data), { status: 200, headers: { "Content-Type": "application/json" } }));

beforeEach(() => { globalThis.fetch = vi.fn(() => ok()); });

describe("API contract", () => {
  it("uses the correct auth, preferences, resume and job endpoints", async () => {
    await api.checkBackendHealth();
    await api.signupUser({ email: "a@example.test", password: "secret" });
    await api.loginUser({ email: "a@example.test", password: "secret" });
    await api.forgotPassword("a@example.test");
    await api.resetPassword({ token: "demo-token-value-123456", new_password: "newsecret1" });
    await api.saveUserPreferences({ user_id: 7, job_title: "Engineer" });
    await api.getUserPreferences(7);
    await api.uploadResume(7, new File(["resume"], "resume.txt"));
    await api.getLatestResume(7);
    await api.searchJobs(7);
    await api.searchUpdatedJobs(7);
    await api.addSelectedJob(7, { title: "Engineer" });
    await api.getSelectedJobs(7);
    await api.deleteSelectedJob(7, 9);
    await api.getJobDetail(7, 9);
    await api.getJobs(7);
    const urls = fetch.mock.calls.map(([url]) => url);
    expect(urls).toContain("http://localhost:8000/api/auth/forgot-password");
    expect(urls).toContain("http://localhost:8000/api/auth/reset-password");
    expect(urls).toContain("http://localhost:8000/api/jobs/search/7");
    expect(urls).toContain("http://localhost:8000/api/jobs/search-updated/7");
    const uploadCall = fetch.mock.calls.find(([, options]) => options?.body instanceof FormData);
    expect(uploadCall).toBeTruthy();
  });

  it("covers matching, optimizer, evidence, email and workflow endpoints", async () => {
    await api.calculateMatches(7); await api.getMatches(7); await api.optimizeResume(7, 9);
    await api.startAtsOptimization(7, 9, true); await api.getAtsWorkflowStatus(3);
    await api.getAtsResultByJob(7, 9); await api.retryAtsWorkflow(3);
    await api.getJobRequirements(7, 9); await api.getMissingSkills(7, 9, { forceRefresh: true });
    await api.analyzeOptimizerRequirements(7, 9, { forceRefresh: false });
    await api.getEvidence(7, 9); await api.saveEvidence(7, 9, [{ skill: "Maven" }]); await api.deleteEvidence(7, 9, 2);
    await api.getOptimizedResumes(7); await api.generateEmailSummary(7); await api.getEmailLogs(7); await api.runFullWorkflow(7);
    await api.importJobByUrl(7, "https://jobs.example.test/9");
    expect(api.getAtsDownloadUrl(3)).toBe("http://localhost:8000/api/ats-optimizer/download/3?format=txt");
    // Item 7: the exact current generated-resume id must appear in the download URL.
    expect(api.getFinalResumeDownloadUrl(7, 9, 91, "docx")).toBe("http://localhost:8000/api/optimizer/resume/7/9/91/download");
    expect(api.getFinalResumeDownloadUrl(7, 9, 91, "pdf")).toBe("http://localhost:8000/api/optimizer/resume/7/9/91/download?format=pdf");
    expect(api.getFinalResumeDownloadUrl(7, 9, 91, "txt")).toBe("http://localhost:8000/api/optimizer/resume/7/9/91/download?format=txt");
    expect(fetch).toHaveBeenCalledWith("http://localhost:8000/api/ats-optimizer/generate/7/9?force=true", { method: "POST" });
  });

  it("polls until terminal and supports compatibility optimizer routes", async () => {
    fetch
      .mockImplementationOnce(() => ok({ workflow_status: "COMPLETED" }))
      .mockImplementationOnce(() => ok({ workflow_status: "COMPLETED" }))
      .mockImplementationOnce(() => ok({ result: true }));
    const progress = vi.fn();
    expect((await api.pollAtsWorkflow(1, { onProgress: progress, intervalMs: 0 })).workflow_status).toBe("COMPLETED");
    expect((await api.analyzeAndOptimizeResume(7, 9)).workflow_status).toBe("COMPLETED");
    await api.analyzeOptimizeViaAtsRoute(7, 9);
    expect(progress).toHaveBeenCalledOnce();
  });

  it("polls through generating/validating aliases until COMPLETED", async () => {
    fetch
      .mockImplementationOnce(() => ok({ workflow_status: "GENERATING" }))
      .mockImplementationOnce(() => ok({ workflow_status: "GENERATING_RESUME" }))
      .mockImplementationOnce(() => ok({ workflow_status: "VALIDATING_RESUME" }))
      .mockImplementationOnce(() => ok({ workflow_status: "CALCULATING_SCORE" }))
      .mockImplementationOnce(() => ok({ workflow_status: "COMPLETED", optimized_resume_id: 9 }));
    const progress = vi.fn();
    const result = await api.pollAtsWorkflow(4, { onProgress: progress, intervalMs: 0, timeoutSeconds: 180 });
    expect(result.workflow_status).toBe("COMPLETED");
    expect(result.optimized_resume_id).toBe(9);
    expect(progress.mock.calls.map((call) => call[0].workflow_status)).toEqual([
      "GENERATING_RESUME",
      "GENERATING_RESUME",
      "VALIDATING_RESUME",
      "ANALYZING_OPTIMIZED",
      "COMPLETED",
    ]);
  });

  it("keeps polling after more than 30 seconds of elapsed budget until completion", async () => {
    const statuses = [
      ...Array.from({ length: 16 }, () => ({ workflow_status: "GENERATING_RESUME" })),
      { workflow_status: "COMPLETED", optimized_resume_id: 12 },
    ];
    let index = 0;
    fetch.mockImplementation(() => {
      const payload = statuses[Math.min(index, statuses.length - 1)];
      index += 1;
      return ok(payload);
    });
    const result = await api.pollAtsWorkflow(5, { intervalMs: 0, timeoutSeconds: 45, maxAttempts: 20 });
    expect(result.workflow_status).toBe("COMPLETED");
    expect(index).toBeGreaterThan(15);
  });

  it("does not claim a poll timeout after 30 seconds when completion arrives later", async () => {
    fetch
      .mockImplementationOnce(() => ok({ workflow_status: "GENERATING_RESUME" }))
      .mockImplementationOnce(() => ok({ workflow_status: "COMPLETED" }));
    const result = await api.pollAtsWorkflow(6, { intervalMs: 0, timeoutSeconds: 45 });
    expect(result.workflow_status).toBe("COMPLETED");
    expect(result.error_message || "").not.toMatch(/timed out waiting/i);
  });

  it("uses a friendly completion failure message when the poll budget is exhausted", async () => {
    fetch.mockImplementation(() => ok({ workflow_id: 7, workflow_status: "GENERATING_RESUME" }));
    const result = await api.pollAtsWorkflow(7, { intervalMs: 0, timeoutSeconds: 4, maxAttempts: 2 });
    expect(result.workflow_status).toBe("FAILED");
    expect(result.error_message).toMatch(/could not be completed|wait window|retry/i);
  });

  it("ignores stale poll responses from a different workflow id", async () => {
    fetch
      .mockImplementationOnce(() => ok({ workflow_id: 99, workflow_status: "GENERATING_RESUME" }))
      .mockImplementationOnce(() => ok({ workflow_id: 8, workflow_status: "COMPLETED", optimized_resume_id: 1 }));
    const progress = vi.fn();
    const result = await api.pollAtsWorkflow(8, { onProgress: progress, intervalMs: 0, timeoutSeconds: 30 });
    expect(result.workflow_status).toBe("COMPLETED");
    expect(progress).toHaveBeenCalledTimes(1);
    expect(progress.mock.calls[0][0].workflow_id).toBe(8);
  });

  it("stops polling when isCancelled becomes true", async () => {
    let calls = 0;
    fetch.mockImplementation(() => {
      calls += 1;
      return ok({ workflow_id: 9, workflow_status: "GENERATING_RESUME" });
    });
    let cancelled = false;
    const resultPromise = api.pollAtsWorkflow(9, {
      intervalMs: 0,
      timeoutSeconds: 30,
      maxAttempts: 10,
      isCancelled: () => cancelled,
    });
    cancelled = true;
    const result = await resultPromise;
    expect(result.poll_cancelled).toBe(true);
    expect(calls).toBeLessThan(10);
  });

  it("surfaces JSON, object and plain-text backend errors", async () => {
    fetch.mockImplementationOnce(() => Promise.resolve(new Response(JSON.stringify({ detail: "Invalid credentials" }), { status: 401 })));
    await expect(api.loginUser({})).rejects.toThrow("Invalid credentials");
    fetch.mockImplementationOnce(() => Promise.resolve(new Response(JSON.stringify({ detail: { message: "Nested failure" } }), { status: 400 })));
    await expect(api.signupUser({})).rejects.toThrow("Nested failure");
    fetch.mockImplementationOnce(() => Promise.resolve(new Response("Service unavailable", { status: 503 })));
    await expect(api.checkBackendHealth()).rejects.toThrow("Backend health check failed");
  });

  it("never surfaces a raw FastAPI/Pydantic validation error array", async () => {
    fetch.mockImplementationOnce(() => Promise.resolve(new Response(
      JSON.stringify({ detail: [{ type: "literal_error", loc: ["body", "gmail_scan_frequency"], msg: "Input should be 'daily'" }] }),
      { status: 422 },
    )));
    await expect(api.updateEmailSummaryPreferences(7, { gmail_scan_frequency: "hourly" })).rejects.toThrow("Please enter a valid setting.");
  });

  it("marks a job email event read and toggles its star via the correct endpoints", async () => {
    await api.markJobEmailEventRead(7, 42);
    await api.toggleJobEmailEventStar(7, 42);
    const urls = fetch.mock.calls.map(([url]) => url);
    expect(urls).toContain("http://localhost:8000/api/email/events/7/42/mark-read");
    expect(urls).toContain("http://localhost:8000/api/email/events/7/42/toggle-star");
  });

  it("rejects unsuccessful responses from every write and read contract", async () => {
    const calls = [
      () => api.saveUserPreferences({}), () => api.getUserPreferences(1), () => api.uploadResume(1, new File([], "x.txt")),
      () => api.getLatestResume(1), () => api.searchJobs(1), () => api.searchUpdatedJobs(1), () => api.addSelectedJob(1, {}),
      () => api.getSelectedJobs(1), () => api.deleteSelectedJob(1, 2), () => api.getJobDetail(1, 2), () => api.getJobs(1),
      () => api.calculateMatches(1), () => api.getMatches(1), () => api.optimizeResume(1, 2), () => api.startAtsOptimization(1, 2),
      () => api.getAtsWorkflowStatus(2), () => api.getAtsResultByJob(1, 2), () => api.retryAtsWorkflow(2),
      () => api.getJobRequirements(1, 2), () => api.getMissingSkills(1, 2), () => api.analyzeOptimizerRequirements(1, 2),
      () => api.getEvidence(1, 2), () => api.saveEvidence(1, 2, []), () => api.deleteEvidence(1, 2, 3),
      () => api.analyzeOptimizeViaAtsRoute(1, 2), () => api.getOptimizedResumes(1), () => api.generateEmailSummary(1),
      () => api.getEmailLogs(1), () => api.runFullWorkflow(1), () => api.importJobByUrl(1, "https://example.test/job"),
    ];
    for (const call of calls) {
      fetch.mockImplementationOnce(() => Promise.resolve(new Response(JSON.stringify({ message: "Rejected" }), { status: 400 })));
      await expect(call()).rejects.toThrow("Rejected");
    }
  });
});
