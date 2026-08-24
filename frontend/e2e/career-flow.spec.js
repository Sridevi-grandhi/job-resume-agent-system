import { expect, test } from "@playwright/test";

const user = { user_id: 7, email: "candidate@example.test", name: "Test Candidate" };
const job = { id: 42, title: "Java Engineer", company: "Acme Labs", location: "Remote", source: "Remotive API", description: "Build reliable Java services using Maven and cloud deployment practices.", job_url: "https://jobs.example.test/42", skills_required: "Java, Maven" };

async function mockBackend(page, { hasResume = true, jobs = [job] } = {}) {
  await page.route("http://localhost:8000/**", async (route) => {
    const url = route.request().url();
    let body = { message: "OK" };
    let status = 200;
    if (url.endsWith("/health")) body = { status: "healthy" };
    else if (url.includes("/api/auth/")) body = user;
    else if (url.includes("/api/preferences/7") && route.request().method() === "GET") body = { user_id: 7, job_title: "Java Engineer", location: "Remote", work_mode: "Remote", job_type: "Full-time", skills: "Java, Maven", schedule_time: "08:00:00", timezone: "America/Chicago" };
    else if (url.includes("/api/preferences")) body = { message: "Preferences saved" };
    else if (url.includes("/api/resume/latest/7")) { if (hasResume) body = { id: 4, file_name: "java-resume.txt", parsed_text: "Java Maven experience" }; else { status = 404; body = { detail: "No resume" }; } }
    else if (url.includes("/api/resume/upload")) body = { id: 4, file_name: "java-resume.txt", parsed_text: "Java Maven experience", message: "Resume uploaded" };
    else if (url.includes("/api/jobs/search-updated/7")) body = { jobs };
    else if (url.includes("/api/jobs/search/7")) body = { jobs: [{ id: 3, title: "Search Java jobs", company: "LinkedIn Jobs", source: "LinkedIn Jobs", job_url: "https://linkedin.example.test/search" }] };
    else if (url.includes("/api/jobs/selected/7/42") && route.request().method() === "DELETE") body = { message: "Removed" };
    else if (url.includes("/api/jobs/selected/7")) body = { jobs };
    else if (url.includes("/api/jobs/detail/7/42")) body = job;
    else if (url.includes("/api/ats-optimizer/analyze/7/42")) body = { verified_resume_skills: [{ skill: "Maven", category: "Build Tool", match_type: "EXACT", evidence_strength: "strong", resume_status: "VERIFIED" }], weak_or_indirect_skills: [], missing_mandatory_skills: [], missing_preferred_skills: [] };
    else if (url.includes("/api/ats-optimizer/result/7/42")) body = { workflow_id: 8, workflow_status: "COMPLETED", job, original_match_percentage: 57, optimized_match_percentage: 82, score_improvement: 25, safe_to_apply: true, final_validation: { status: "passed", warnings: [] }, generated_resume: { plain_text: "Safe Java Maven resume" } };
    else if (url.includes("/api/ats-optimizer/evidence/7/42")) body = { evidence: [] };
    else if (url.includes("/api/email/logs/7")) body = { email_logs: [] };
    else if (url.includes("/api/email/generate/7")) body = { message: "Email summary generated" };
    await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  });
}

async function login(page, options) {
  await mockBackend(page, options);
  await page.goto("/login");
  await page.getByLabel("Email address").fill(user.email);
  await page.getByLabel("Password").fill("test-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/dashboard/);
}

test("happy path reaches updated jobs and ATS score separation", async ({ page }) => {
  await login(page);
  await page.goto("/preferences");
  await page.getByLabel("Job Title").fill("Java Engineer");
  await page.getByLabel("Location").fill("Remote");
  await page.getByRole("button", { name: "Save Preferences" }).click();
  await page.getByRole("link", { name: "Job Search" }).click();
  await page.getByRole("button", { name: /search updated jobs/i }).click();
  await expect(page.getByText("Acme Labs")).toBeVisible();
  await page.goto("/optimizer/42");
  await page.getByText("Compare Scores").click();
  await expect(page.getByText("57%")).toBeVisible();
  await expect(page.getByText("82%")).toBeVisible();
  await expect(page.getByText("+25")).toBeVisible();
});

test("no resume disables selected-job optimization", async ({ page }) => {
  await login(page, { hasResume: false });
  await page.goto("/selected-jobs");
  await expect(page.getByRole("button", { name: /optimize resume/i })).toBeDisabled();
  await expect(page.getByText("Upload a resume first")).toBeVisible();
});

test("Maven is verified and not missing", async ({ page }) => {
  await login(page);
  await page.goto("/optimizer/42");
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: /analyze job description/i }).click();
  await expect(page.getByText("Maven")).toBeVisible();
  await expect(page.getByText("No mandatory skills are missing.")).toBeVisible();
});

test("deleting a selected job removes it", async ({ page }) => {
  await login(page);
  await page.goto("/selected-jobs");
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: /remove job/i }).click();
  await expect(page.getByText("Java Engineer")).not.toBeVisible();
});

test("users without shared API jobs remain isolated", async ({ page }) => {
  await login(page, { jobs: [] });
  await page.goto("/selected-jobs");
  await expect(page.getByText("No selected jobs", { exact: true })).toBeVisible();
});

test("major mismatch warning remains visible", async ({ page }) => {
  await mockBackend(page);
  await page.addInitScript((savedUser) => localStorage.setItem("currentUser", JSON.stringify(savedUser)), user);
  await page.route("**/api/ats-optimizer/result/7/42", (route) => route.fulfill({ json: { workflow_status: "COMPLETED", job, original_match_percentage: 25, optimized_match_percentage: 25, score_status: "NOT_RECOMMENDED", role_alignment: "MISMATCH", safe_to_apply: false, final_validation: { status: "failed", warnings: ["Unsupported Java claim blocked"] } } }));
  await page.goto("/optimizer/42");
  await expect(page.getByText(/major role mismatch/i)).toBeVisible();
});
