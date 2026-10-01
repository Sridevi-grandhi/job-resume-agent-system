import { expect, test } from "@playwright/test";

const user = { user_id: 7, email: "candidate@example.test", name: "Test Candidate" };
const job = {
  id: 42,
  title: "Senior Engineer – Java / Spring Boot / React – Hybrid",
  company: "Genesis10",
  location: "Hybrid",
  source: "User Selected Job",
  description: "A detailed Java backend position with Spring Boot, React, and enough content to analyze safely.",
  job_url: "https://jobs.example.test/42",
  skills_required: "Java, Spring Boot, React",
};
const otherJob = {
  id: 99,
  title: "Data Science Engineer",
  company: "Adobe Systems",
  location: "Remote",
  source: "User Selected Job",
  description: "A detailed data science role with Python, SQL, and enough content to analyze safely.",
  job_url: "https://jobs.example.test/99",
};

const completedResult = {
  workflow_id: 8,
  workflow_status: "COMPLETED",
  job,
  optimized_resume_id: 91,
  original_match_percentage: 79,
  optimized_match_percentage: 79,
  generated_resume: { plain_text: "Existing optimized resume" },
};

async function mockBackend(page) {
  let generatePosts = 0;
  await page.route(/http:\/\/localhost:800[01]\//, async (route) => {
    const url = route.request().url();
    const method = route.request().method();
    if (method === "POST" && /\/api\/ats-optimizer\/generate\//.test(url)) generatePosts += 1;
    if (method === "POST" && /\/api\/ats\/analyze-optimize\//.test(url)) generatePosts += 1;
    let body = { message: "OK" };
    let status = 200;
    if (url.endsWith("/health")) body = { status: "healthy" };
    else if (url.includes("/api/auth/")) body = user;
    else if (url.includes("/api/resume/latest/7")) body = { id: 4, file_name: "java-resume.txt", parsed_text: "Java Maven experience" };
    else if (url.includes("/api/jobs/selected/7")) body = { jobs: [job, otherJob] };
    else if (url.includes("/api/jobs/detail/7/99")) body = otherJob;
    else if (url.includes("/api/jobs/detail/7/42")) body = job;
    else if (url.includes("/api/jobs/7")) body = { jobs: [job, otherJob] };
    else if (url.includes("/api/ats-optimizer/result/7/99")) body = { workflow_status: null };
    else if (url.includes("/api/ats-optimizer/result/7/42")) body = completedResult;
    else if (url.includes("/api/ats-optimizer/evidence/")) body = { evidence: [] };
    else if (url.includes("/api/ats-optimizer/missing-skills/")) body = { verified_resume_skills: [], weak_or_indirect_skills: [], missing_mandatory_skills: [], missing_preferred_skills: [] };
    else if (url.includes("/api/ats-optimizer/analyze/")) body = { verified_resume_skills: [], weak_or_indirect_skills: [], missing_mandatory_skills: [], missing_preferred_skills: [] };
    else if (url.includes("/api/match/")) body = { matches: [] };
    else if (url.includes("/api/preferences/")) body = { user_id: 7 };
    else if (url.includes("/api/optimized")) body = { optimized_resumes: [] };
    await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  });
  return () => generatePosts;
}

async function login(page) {
  const generateCount = await mockBackend(page);
  await page.goto("/login");
  await page.getByLabel("Email address").fill(user.email);
  await page.getByLabel("Password").fill("test-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/dashboard/);
  return generateCount;
}

test("Selected Jobs Optimize opens ATS Step 1 with start_optimization and does not generate", async ({ page }) => {
  const generateCount = await login(page);
  await page.goto("/selected-jobs");
  const card = page.locator(".job-card", { hasText: "Genesis10" }).first();
  await card.getByRole("button", { name: /optimize and compare scores/i }).click();
  await expect(page).toHaveURL(/\/optimizer\/42/);
  await expect(page.getByRole("heading", { name: /step 1/i })).toBeVisible();
  await expect(page.locator("li.active", { hasText: "Select Job" })).toBeVisible();
  await expect(page.getByLabel(/select job/i)).toHaveValue("42");
  await expect(page.getByRole("heading", { name: /step 5/i })).toHaveCount(0);
  expect(generateCount()).toBe(0);
});

test("sidebar ATS restores Step 5 for a completed workflow", async ({ page }) => {
  await login(page);
  await page.evaluate(() => {
    sessionStorage.setItem("atsSelectedJobId", "42");
    sessionStorage.setItem("atsWizardStep:42", "5");
  });
  await page.goto("/ats-analyzer");
  await expect(page.getByRole("heading", { name: /step 5/i })).toBeVisible();
});
