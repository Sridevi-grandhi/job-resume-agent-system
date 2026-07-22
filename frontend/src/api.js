const API_BASE_URL = "http://127.0.0.1:8000";


async function getErrorMessage(response) {
  const text = await response.text();

  try {
    const data = JSON.parse(text);
    return data.detail || data.message || text;
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


/*
  IMPORTANT:
  This function now uses the NEW Real Job Search Agent.

  Backend endpoint:
  POST /api/jobs/search-real/{user_id}

  Old mock endpoint was:
  POST /api/jobs/search/{user_id}
*/
export async function searchJobs(userId) {
  const response = await fetch(`${API_BASE_URL}/api/jobs/search-real/${userId}`, {
    method: "POST"
  });

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