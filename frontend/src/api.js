const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

async function getErrorMessage(response) {
  const text = await response.text();

  try {
    const data = JSON.parse(text);
    if (typeof data.detail === "string") return data.detail;
    if (data.detail && typeof data.detail === "object") {
      return data.detail.message || JSON.stringify(data.detail);
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

export async function getAtsResultByJob(userId, jobId) {
  const response = await fetch(`${API_BASE_URL}/api/ats-optimizer/result/${userId}/${jobId}`);
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

const TERMINAL_ATS_STATUSES = new Set([
  "COMPLETED",
  "FAILED",
  "VALIDATION_FAILED",
  "INCOMPLETE_JOB",
  "WAITING_FOR_RESUME",
  "SKIPPED_GENERIC_SEARCH_LINK",
]);

export async function pollAtsWorkflow(workflowId, { onProgress, maxAttempts = 90, intervalMs = 2000 } = {}) {
  let latest = null;
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    latest = await getAtsWorkflowStatus(workflowId);
    if (onProgress) onProgress(latest);
    if (TERMINAL_ATS_STATUSES.has(latest.workflow_status)) {
      return latest;
    }
    await sleep(intervalMs);
  }
  return latest;
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

export async function runFullWorkflow(userId) {
  const response = await fetch(`${API_BASE_URL}/api/scheduler/run/${userId}`, {
    method: "POST"
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
