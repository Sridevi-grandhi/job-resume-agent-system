import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderRoute } from "./render.jsx";
import { makeApp } from "./mockApp";
import AtsAnalyzerPage from "../pages/AtsAnalyzerPage";

let app;
vi.mock("../context/AppContext", () => ({ useApp: () => app }));

// Randstad-style low-score job (the one being viewed) and a later-completed job.
const lowJob = {
  id: 1848,
  title: "Full Stack Engineer (Java / Spring Boot / React)",
  company: "Low Match Co",
  location: "Remote",
  source: "User Selected Job",
  description: "A detailed full stack role with Java, Spring Boot, React and enough content to analyze.",
  job_url: "https://jobs.example.test/1848",
};
const otherJob = {
  id: 1849,
  title: "Full Stack Lead Developer",
  company: "Other Co",
  location: "Remote",
  source: "User Selected Job",
  description: "A detailed full stack lead role with Java, React and enough content to analyze safely.",
  job_url: "https://jobs.example.test/1849",
};
const groups = {
  verified_resume_skills: [],
  weak_or_indirect_skills: [],
  missing_mandatory_skills: [{ skill: "Spring Boot", category: "Framework" }],
  missing_preferred_skills: [],
  can_generate_resume: true,
  blocking_reasons: [],
  generation_warnings: [],
  generation_blocked_message: "",
  generation_warning_message: "",
  evidence: [],
  evidence_history: [],
  evidence_summary: { evidence: [], incomplete: [], user_confirmed: [], counts: {} },
};
const ineligibleCheck = {
  optimization_allowed: false,
  eligibility_status: "INELIGIBLE_LOW_SCORE",
  eligibility_reason: "Original resume match is below the 65% minimum threshold.",
  original_score: 35,
  role_alignment: "MISMATCH",
  job_id: 1848,
  analysis_status: "SUCCESS",
};
// Completed workflow for the OTHER job, still held in app context after the
// Selected Jobs save flow polled it.
const otherJobCompleted = {
  workflow_id: 341,
  job_id: 1849,
  resume_id: 59,
  job: otherJob,
  workflow_status: "COMPLETED",
  optimized_resume_id: 141,
  original_match_percentage: 88,
  original_match_score: 88,
  optimized_match_percentage: 88,
  generated_resume: { plain_text: "Other job generated resume" },
  improvement_report: { bullet_changes: [] },
};

let fetchCalls;
function mockFetch(routes = {}) {
  fetchCalls = [];
  globalThis.fetch = vi.fn((url) => {
    fetchCalls.push(String(url));
    for (const [pattern, body] of Object.entries(routes)) {
      if (String(url).includes(pattern)) {
        return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
      }
    }
    return Promise.resolve(new Response(JSON.stringify(lowJob), { status: 200 }));
  });
}

beforeEach(() => {
  sessionStorage.clear();
  app = makeApp({
    jobs: [lowJob, otherJob],
    latestResume: { id: 59, file_name: "resume.docx" },
    atsSelectedJobId: "1848",
    missingSkillsGroups: groups,
  });
  app.loadMissingSkills.mockResolvedValue(groups);
  app.loadAtsResultForJob.mockResolvedValue(null);
  app.loadSkillConfirmations.mockResolvedValue({ evidence: [], can_generate_resume: true, blocking_reasons: [] });
  app.analyzeAndOptimize.mockResolvedValue({ workflow_status: "COMPLETED", optimized_resume_id: 77, workflow_id: 8 });
  mockFetch();
});

describe("ATS Step 5 score integrity", () => {
  it("never shows another job's completed result, scores, or downloads on this job's Step 5", async () => {
    sessionStorage.setItem("atsWizardStep:1848", "5");
    app.atsResult = otherJobCompleted;
    renderRoute(<AtsAnalyzerPage />, "/optimizer/1848", "/optimizer/:jobId");

    expect(await screen.findByRole("heading", { name: /step 5/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /download docx/i })).toBeNull();
    expect(screen.queryByText("88%")).toBeNull();
    // No editor/download request pairs this job with the other job's optimized resume.
    await waitFor(() => expect(fetchCalls.length).toBeGreaterThan(0));
    expect(fetchCalls.some((url) => url.includes("/api/optimizer/resume/") && url.includes("/1848/141"))).toBe(false);
  });

  it("same-job completed result shows original baseline, current score, and improvement separately", async () => {
    sessionStorage.setItem("atsWizardStep:1849", "5");
    app.atsSelectedJobId = "1849";
    app.atsResult = { ...otherJobCompleted, original_match_percentage: 70, original_match_score: 70 };
    mockFetch({
      "/api/optimizer/resume/": {
        optimized_resume_id: 141,
        final_text: "Edited resume",
        edited_score: 90,
        generated_score: 88,
        original_score: 70,
        validation_status: "passed",
        document: null,
      },
    });
    renderRoute(<AtsAnalyzerPage />, "/optimizer/1849", "/optimizer/:jobId");

    expect(await screen.findByRole("heading", { name: /step 5/i })).toBeInTheDocument();
    const trio = await waitFor(() => {
      const node = document.querySelector(".rw-score-trio");
      expect(node).not.toBeNull();
      expect(node.textContent).toContain("90%");
      return node;
    });
    expect(trio.textContent).toContain("Original70%");
    expect(trio.textContent).toContain("+20");
    expect(fetchCalls.some((url) => url.includes("/api/optimizer/resume/1/1849/141") || url.includes("/1849/141"))).toBe(true);
  });
});

describe("ATS generation eligibility gating", () => {
  it("disables Generate on Step 5 and explains why when the canonical check is ineligible", async () => {
    sessionStorage.setItem("atsWizardStep:1848", "5");
    mockFetch({ "/api/ats-optimizer/eligibility/": ineligibleCheck });
    renderRoute(<AtsAnalyzerPage />, "/optimizer/1848", "/optimizer/:jobId");

    expect(await screen.findByRole("heading", { name: /step 5/i })).toBeInTheDocument();
    const button = screen.getByRole("button", { name: /generate optimized resume/i });
    await waitFor(() => expect(button).toBeDisabled());
    const notice = screen.getByTestId("ats-generation-ineligible");
    expect(notice.textContent).toMatch(/not sufficiently aligned with this role/i);
    expect(notice.textContent).toMatch(/below the 65% minimum threshold/i);
    expect(notice.textContent).toContain("35%");
    expect(notice.textContent).not.toMatch(/add (these|the missing) skills/i);

    fireEvent.click(button);
    expect(app.analyzeAndOptimize).not.toHaveBeenCalled();
  });

  it("blocks generation after the Step 2 analysis reports ineligible", async () => {
    mockFetch({ "/api/ats-optimizer/eligibility/": ineligibleCheck });
    renderRoute(<AtsAnalyzerPage />, "/optimizer/1848", "/optimizer/:jobId");
    await userEvent.click(screen.getByRole("button", { name: /^continue$/i }));
    await userEvent.click(screen.getByRole("button", { name: /analyze job description/i }));
    expect(await screen.findByRole("heading", { name: /step 3/i })).toBeInTheDocument();
    // Existing Step 3 ineligibility explanation still shows the analysis.
    expect(screen.getAllByText(/35%/).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByText(/Generate, Review/i));
    const button = await screen.findByRole("button", { name: /generate optimized resume/i });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(app.analyzeAndOptimize).not.toHaveBeenCalled();
  });

  it("eligible job keeps normal generation available", async () => {
    mockFetch({
      "/api/ats-optimizer/eligibility/": { ...ineligibleCheck, optimization_allowed: true, eligibility_status: "ELIGIBLE", original_score: 82 },
    });
    renderRoute(<AtsAnalyzerPage />, "/optimizer/1848", "/optimizer/:jobId");
    fireEvent.click(screen.getByText(/Generate, Review/i));
    const button = await screen.findByRole("button", { name: /generate optimized resume/i });
    expect(button).not.toBeDisabled();
    await userEvent.click(button);
    await waitFor(() => expect(app.analyzeAndOptimize).toHaveBeenCalledWith(1848, true));
    expect(screen.queryByTestId("ats-generation-ineligible")).toBeNull();
  });
});
