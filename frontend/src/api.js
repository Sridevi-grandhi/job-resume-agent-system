const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

async function getErrorMessage(response) {
  const text = await response.text();

  try {
    const data = JSON.parse(text);
    if (typeof data.detail === "string") return data.detail;
    // FastAPI/Pydantic 422s send `detail` as an array of validation error
    // objects (e.g. [{"type":"value_error", "loc":[...], "msg":"..."}]) —
    // never surface that raw shape to the user.
    if (Array.isArray(data.detail)) return "Please enter a valid setting.";
    if (data.detail && typeof data.detail === "object") {
      return data.detail.message || "Please enter a valid setting.";
    }
    return data.message || text;
  } catch {
    return text || "Something went wrong";
  }
}

export async function checkBackendHealth() {
  const response = await fetch(`${API_BASE_URL}/health`);

  if (!response.ok) {
    throw new Error("Backend health check failed");
  }

  return response.json();
}

export async function signupUser(userData) {
  const response = await fetch(`${API_BASE_URL}/api/auth/signup`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(userData)
  });

  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }

  return response.json();
}

export async function loginUser(loginData) {
  const response = await fetch(`${API_BASE_URL}/api/auth/login`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(loginData)
  });

  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }

  return response.json();
}

export async function forgotPassword(email) {
  const response = await fetch(`${API_BASE_URL}/api/auth/forgot-password`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ email })
  });

  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }

  return response.json();
}

export async function resetPassword({ token, new_password }) {
  const response = await fetch(`${API_BASE_URL}/api/auth/reset-password`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ token, new_password })
  });

  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }

  return response.json();
}

export async function saveUserPreferences(preferences) {
  const response = await fetch(`${API_BASE_URL}/api/preferences`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(preferences)
  });

  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }

  return response.json();
}

export async function getUserPreferences(userId) {
  const response = await fetch(`${API_BASE_URL}/api/preferences/${userId}`);

  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }

  return response.json();
}

export async function uploadResume(userId, file) {
  const formData = new FormData();

  formData.append("user_id", userId);
  formData.append("file", file);

  const response = await fetch(`${API_BASE_URL}/api/resume/upload`, {
    method: "POST",
    body: formData
  });

  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }

  return response.json();
}

export async function getLatestResume(userId) {
  const response = await fetch(`${API_BASE_URL}/api/resume/latest/${userId}`);

  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }

  return response.json();
}

// Existing Job Search Agent
// Uses LinkedIn, Indeed, Dice, Google Jobs, ZipRecruiter search links
export async function searchJobs(userId) {
  const response = await fetch(`${API_BASE_URL}/api/jobs/search/${userId}`, {
    method: "POST"
  });

  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }

  return response.json();
}

// Updated Job Search Agent
// Uses Remotive API real jobs
export async function searchUpdatedJobs(userId) {
  const response = await fetch(`${API_BASE_URL}/api/jobs/search-updated/${userId}`, {
    method: "POST"
  });

  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }

  return response.json();
}

// Manually selected job from LinkedIn, Dice, Indeed, etc.
export async function addSelectedJob(userId, selectedJobData) {
  const response = await fetch(`${API_BASE_URL}/api/jobs/selected/${userId}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(selectedJobData)
  });

  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }

  return response.json();
}

export async function getSelectedJobs(userId) {
  const response = await fetch(`${API_BASE_URL}/api/jobs/selected/${userId}`);

  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }

  return response.json();
}

export async function deleteSelectedJob(userId, jobId) {
  const response = await fetch(
    `${API_BASE_URL}/api/jobs/selected/${userId}/${jobId}`,
    { method: "DELETE" }
  );

  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }

  return response.json();
}

export async function getJobDetail(userId, jobId) {
  const response = await fetch(
    `${API_BASE_URL}/api/jobs/detail/${userId}/${jobId}`
  );

  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }

  return response.json();
}

export async function getJobs(userId) {
  const response = await fetch(`${API_BASE_URL}/api/jobs/${userId}`);

  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }

  return response.json();
}

export async function calculateMatches(userId) {
  const response = await fetch(`${API_BASE_URL}/api/match/calculate/${userId}`, {
    method: "POST"
  });

  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }

  return response.json();
}

export async function getMatches(userId) {
  const response = await fetch(`${API_BASE_URL}/api/match/${userId}`);

  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }

  return response.json();
}

export async function optimizeResume(userId, jobId) {
  const response = await fetch(`${API_BASE_URL}/api/resume/optimize/${userId}/${jobId}`, {
    method: "POST"
  });

  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }

  return response.json();
}

// ATS Resume Optimizer & Match Analyzer (async workflow)
export async function startAtsOptimization(userId, jobId, force = false) {
  const query = force ? "?force=true" : "";
  const response = await fetch(
    `${API_BASE_URL}/api/ats-optimizer/generate/${userId}/${jobId}${query}`,
    { method: "POST" }
  );
  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }
  return response.json();
}

export async function getAtsWorkflowStatus(workflowId) {
  const response = await fetch(`${API_BASE_URL}/api/ats-optimizer/status/${workflowId}`);
  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }
  return response.json();
}

export async function getAtsResultByJob(userId, jobId, { workflowId } = {}) {
  const query = workflowId != null ? `?workflow_id=${encodeURIComponent(workflowId)}` : "";
  const response = await fetch(`${API_BASE_URL}/api/ats-optimizer/result/${userId}/${jobId}${query}`);
  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }
  return response.json();
}

export async function retryAtsWorkflow(workflowId) {
  const response = await fetch(`${API_BASE_URL}/api/ats-optimizer/retry/${workflowId}`, {
    method: "POST",
  });
  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }
  return response.json();
}

export function getAtsDownloadUrl(workflowId) {
  return `${API_BASE_URL}/api/ats-optimizer/download/${workflowId}?format=txt`;
}

export async function getJobRequirements(userId, jobId) {
  const response = await fetch(`${API_BASE_URL}/api/ats-optimizer/requirements/${userId}/${jobId}`);
  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }
  return response.json();
}

export async function getMissingSkills(userId, jobId, { forceRefresh = false } = {}) {
  const query = forceRefresh ? "?force_refresh=true" : "";
  const response = await fetch(
    `${API_BASE_URL}/api/ats-optimizer/missing-skills/${userId}/${jobId}${query}`
  );
  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }
  return response.json();
}

export async function analyzeOptimizerRequirements(userId, jobId, { forceRefresh = true } = {}) {
  const query = forceRefresh ? "?force_refresh=true" : "?force_refresh=false";
  const response = await fetch(
    `${API_BASE_URL}/api/ats-optimizer/analyze/${userId}/${jobId}${query}`,
    { method: "POST" }
  );
  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }
  return response.json();
}

export async function getOptimizationEligibility(userId, jobId) {
  const response = await fetch(`${API_BASE_URL}/api/ats-optimizer/eligibility/${userId}/${jobId}`);
  if (!response.ok) throw new Error(await getErrorMessage(response));
  return response.json();
}

export async function getEditableOptimizedResume(userId, jobId, optimizedResumeId) {
  const response = await fetch(`${API_BASE_URL}/api/optimizer/resume/${userId}/${jobId}/${optimizedResumeId}`);
  if (!response.ok) throw new Error(await getErrorMessage(response));
  return response.json();
}

export async function saveEditedOptimizedResume(userId, jobId, optimizedResumeId, editedResumeText, action, options = {}) {
  const body = {
    optimized_resume_id: optimizedResumeId,
    action,
    persist: options.persist !== false,
    score_only: Boolean(options.scoreOnly),
    skip_score: Boolean(options.skipScore),
    presentation_only: Boolean(options.presentationOnly),
  };
  if (options.document) {
    body.document = options.document;
  } else {
    body.edited_resume_text = editedResumeText;
  }
  const response = await fetch(`${API_BASE_URL}/api/optimizer/resume/${userId}/${jobId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await getErrorMessage(response));
  return response.json();
}

export async function improveResumeSnippet(userId, jobId, optimizedResumeId, text, context = "bullet", previousSuggestion = "") {
  const body = { optimized_resume_id: optimizedResumeId, text, context };
  if (previousSuggestion) body.previous_suggestion = previousSuggestion;
  const response = await fetch(`${API_BASE_URL}/api/optimizer/resume/${userId}/${jobId}/improve-snippet`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await getErrorMessage(response));
  return response.json();
}

export function getFinalResumeDownloadUrl(userId, jobId, optimizedResumeId, format = "docx") {
  const query = format && format !== "docx" ? `?format=${encodeURIComponent(format)}` : "";
  return `${API_BASE_URL}/api/optimizer/resume/${userId}/${jobId}/${optimizedResumeId}/download${query}`;
}

export async function getEvidence(userId, jobId) {
  const response = await fetch(`${API_BASE_URL}/api/ats-optimizer/evidence/${userId}/${jobId}`);
  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }
  return response.json();
}

export async function saveEvidence(userId, jobId, items) {
  const response = await fetch(
    `${API_BASE_URL}/api/ats-optimizer/evidence/${userId}/${jobId}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items }),
    }
  );
  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }
  return response.json();
}

export async function deleteEvidence(userId, jobId, evidenceId) {
  const response = await fetch(
    `${API_BASE_URL}/api/ats-optimizer/evidence/${userId}/${jobId}/${evidenceId}`,
    { method: "DELETE" }
  );
  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }
  return response.json();
}

async function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

const ATS_STATUS_ALIASES = {
  GENERATING: "GENERATING_RESUME",
  GENERATED: "COMPLETED",
  CALCULATING_SCORE: "ANALYZING_OPTIMIZED",
  CALCULATING_OPTIMIZED_MATCH: "ANALYZING_OPTIMIZED",
  COMPLETE: "COMPLETED",
  SUCCESS: "COMPLETED",
  ERROR: "FAILED",
  FAILURE: "FAILED",
};

export function normalizeAtsWorkflowStatus(status) {
  const upper = String(status || "").trim().toUpperCase().replace(/[\s-]+/g, "_");
  return ATS_STATUS_ALIASES[upper] || upper;
}

const TERMINAL_ATS_STATUSES = new Set([
  "COMPLETED",
  "FAILED",
  "VALIDATION_FAILED",
  "INCOMPLETE_JOB",
  "INELIGIBLE_LOW_SCORE", "INELIGIBLE_ROLE_ALIGNMENT",
  "WAITING_FOR_RESUME",
  "SKIPPED_GENERIC_SEARCH_LINK",
]);

export const DEFAULT_ATS_POLL_INTERVAL_MS = 2000;
// Must exceed backend generation budget (LLM timeout + fallback + scoring).
export const DEFAULT_ATS_GENERATION_TIMEOUT_SECONDS = 600;

/**
 * Poll a single workflow id until terminal or cancelled.
 * Pass `{ signal }` / `{ isCancelled }` so Retry/new generates stop old polls.
 */
export async function pollAtsWorkflow(workflowId, {
  onProgress,
  maxAttempts,
  intervalMs,
  timeoutSeconds,
  signal,
  isCancelled,
} = {}) {
  const targetId = Number(workflowId);
  const interval = Number.isFinite(intervalMs) ? intervalMs : DEFAULT_ATS_POLL_INTERVAL_MS;
  const timeout = Number.isFinite(timeoutSeconds) ? timeoutSeconds : DEFAULT_ATS_GENERATION_TIMEOUT_SECONDS;
  const attempts = maxAttempts ?? Math.max(1, Math.ceil((timeout * 1000) / Math.max(interval, 1)));
  let latest = null;

  const cancelled = () => Boolean(
    (signal && signal.aborted)
    || (typeof isCancelled === "function" && isCancelled())
  );

  for (let attempt = 0; attempt < attempts; attempt += 1) {
    if (cancelled()) {
      return latest
        ? { ...latest, workflow_status: normalizeAtsWorkflowStatus(latest.workflow_status), poll_cancelled: true }
        : { workflow_id: targetId, workflow_status: "FAILED", error_message: "Polling cancelled.", poll_cancelled: true };
    }
    latest = await getAtsWorkflowStatus(workflowId);
    const responseId = Number(latest?.workflow_id ?? workflowId);
    if (responseId !== targetId) {
      // Stale response for a different workflow — ignore.
      if (interval > 0) await sleep(interval);
      continue;
    }
    const normalized = normalizeAtsWorkflowStatus(latest?.workflow_status);
    latest = { ...latest, workflow_status: normalized };
    if (onProgress && !cancelled()) onProgress(latest);
    if (TERMINAL_ATS_STATUSES.has(normalized)) {
      return latest;
    }
    if (interval > 0) await sleep(interval);
  }

  // Final authoritative check before declaring client-side timeout failure.
  try {
    if (!cancelled()) {
      latest = await getAtsWorkflowStatus(workflowId);
      const normalized = normalizeAtsWorkflowStatus(latest?.workflow_status);
      latest = { ...latest, workflow_status: normalized };
      if (TERMINAL_ATS_STATUSES.has(normalized)) {
        if (onProgress) onProgress(latest);
        return latest;
      }
    }
  } catch {
    /* keep timedOut path */
  }

  const timedOut = latest || { workflow_id: targetId, workflow_status: "FAILED" };
  if (!TERMINAL_ATS_STATUSES.has(normalizeAtsWorkflowStatus(timedOut.workflow_status))) {
    const llmReason = timedOut.llm_failure_reason || timedOut.fallback_reason;
    const fallbackNote = timedOut.fallback_attempted
      ? (timedOut.fallback_succeeded
        ? ""
        : (timedOut.fallback_failure_reason || "Deterministic fallback did not complete in time."))
      : "Deterministic fallback may still be running on the server — retry status shortly.";
    return {
      ...timedOut,
      workflow_status: "FAILED",
      error_message: timedOut.error_message || (
        llmReason
          ? `AI generation failed (${llmReason}). ${fallbackNote}`.trim()
          : "Resume generation could not be completed within the client wait window. Retry to refresh status."
      ),
    };
  }
  return { ...timedOut, workflow_status: normalizeAtsWorkflowStatus(timedOut.workflow_status) };
}

// Combined optimize-and-match (starts shared ATS workflow)
export async function analyzeAndOptimizeResume(userId, jobId, force = false) {
  const query = force ? "?force=true" : "";
  const response = await fetch(
    `${API_BASE_URL}/api/resume/optimize-and-match/${userId}/${jobId}${query}`,
    { method: "POST" }
  );

  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }

  const started = await response.json();
  if (started.workflow_id && !TERMINAL_ATS_STATUSES.has(started.workflow_status)) {
    return pollAtsWorkflow(started.workflow_id);
  }
  return started;
}

// Backward-compatible alias for the ATS route
export async function analyzeOptimizeViaAtsRoute(userId, jobId) {
  const response = await fetch(
    `${API_BASE_URL}/api/ats/analyze-optimize/${userId}/${jobId}`,
    { method: "POST" }
  );

  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }

  return response.json();
}

export async function getOptimizedResumes(userId) {
  const response = await fetch(`${API_BASE_URL}/api/resume/optimized/${userId}`);

  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }

  return response.json();
}

export async function generateEmailSummary(userId) {
  const response = await fetch(`${API_BASE_URL}/api/email/generate/${userId}`, {
    method: "POST"
  });

  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }

  return response.json();
}

export async function getEmailLogs(userId) {
  const response = await fetch(`${API_BASE_URL}/api/email/logs/${userId}`);

  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }

  return response.json();
}

// Email Summary — Gmail connection, application tracking
async function emailGet(path) {
  const response = await fetch(`${API_BASE_URL}${path}`);
  if (!response.ok) throw new Error(await getErrorMessage(response));
  return response.json();
}

async function emailPost(path, body) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  if (!response.ok) throw new Error(await getErrorMessage(response));
  return response.json();
}

async function emailPatch(path, body) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  if (!response.ok) throw new Error(await getErrorMessage(response));
  return response.json();
}

export function getGmailConfigStatus() {
  return emailGet(`/api/email/gmail/config-status`);
}

export function getEmailSummaryPreferences(userId) {
  return emailGet(`/api/email/preferences/${userId}`);
}

export function updateEmailSummaryPreferences(userId, preferences) {
  return emailPatch(`/api/email/preferences/${userId}`, preferences);
}

export function getGmailStatus(userId) {
  return emailGet(`/api/email/gmail/status/${userId}`);
}

export function connectGmail(userId) {
  return emailGet(`/api/email/gmail/connect/${userId}`);
}

export function disconnectGmail(userId) {
  return emailPost(`/api/email/gmail/disconnect/${userId}`);
}

export function scanJobEmails(userId) {
  return emailPost(`/api/email/gmail/scan/${userId}`);
}

export function getApplications(userId) {
  return emailGet(`/api/email/applications/${userId}`);
}

export function getApplicationHistory(userId, applicationId) {
  return emailGet(`/api/email/applications/${userId}/${applicationId}/history`);
}

export function updateApplicationStatus(userId, applicationId, newStatus) {
  return emailPost(`/api/email/applications/${userId}/${applicationId}/status`, { new_status: newStatus });
}

export function getJobEmailEvents(userId) {
  return emailGet(`/api/email/events/${userId}`);
}

export function resolveJobEmailEvent(userId, eventId, action, jobId) {
  return emailPost(`/api/email/events/${userId}/${eventId}/resolve`, { action, job_id: jobId ?? null });
}

// CareerBoost-local only — Gmail stays read-only, so neither of these calls
// a Gmail write/modify API. Marking read is one-way; the star is a toggle.
export function markJobEmailEventRead(userId, eventId) {
  return emailPost(`/api/email/events/${userId}/${eventId}/mark-read`);
}

export function toggleJobEmailEventStar(userId, eventId) {
  return emailPost(`/api/email/events/${userId}/${eventId}/toggle-star`);
}

export async function runFullWorkflow(userId) {
  const response = await fetch(`${API_BASE_URL}/api/scheduler/run/${userId}`, {
    method: "POST"
  });

  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }

  return response.json();
}

// Updated Job Search — career page / ATS tracker discovery + resume matching
export async function getDiscoveredJobs(userId, params = {}) {
  const query = new URLSearchParams(
    Object.entries(params).filter(([, value]) => value !== undefined && value !== null && value !== "")
  ).toString();
  const response = await fetch(
    `${API_BASE_URL}/api/job-discovery/jobs/${userId}${query ? `?${query}` : ""}`
  );
  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }
  return response.json();
}

export async function refreshDiscoveredJobs(userId) {
  const response = await fetch(`${API_BASE_URL}/api/job-discovery/refresh/${userId}`, {
    method: "POST",
  });
  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }
  return response.json();
}

export async function saveDiscoveredJob(userId, discoveredJobId) {
  const response = await fetch(`${API_BASE_URL}/api/job-discovery/save/${userId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ discovered_job_id: discoveredJobId }),
  });
  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }
  return response.json();
}

export async function getJobDiscoverySources() {
  const response = await fetch(`${API_BASE_URL}/api/job-discovery/sources`);
  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }
  return response.json();
}

// Fire-and-forget apply tracking — never blocks the browser's navigation to
// the employer's real application page (see DiscoveredJobCard's Apply link).
export async function recordJobApply(userId, discoveredJobId) {
  const response = await fetch(`${API_BASE_URL}/api/job-discovery/apply/${userId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ discovered_job_id: discoveredJobId }),
  });
  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }
  return response.json();
}

export async function importJobByUrl(userId, jobUrl) {
  const response = await fetch(`${API_BASE_URL}/api/jobs/import-url/${userId}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      job_url: jobUrl
    })
  });

  if (!response.ok) {
    throw new Error(await getErrorMessage(response));
  }

  return response.json();
}
