import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { renderRoute } from "./render.jsx";
import { makeApp } from "./mockApp";
import AtsAnalyzerPage from "../pages/AtsAnalyzerPage";

let app;
vi.mock("../context/AppContext", () => ({ useApp: () => app }));

const job = {
  id: 42,
  title: "Java Engineer",
  company: "Acme",
  location: "Remote",
  source: "User Selected Job",
  description: "A detailed Java backend position with enough content to be analyzed safely.",
  job_url: "https://jobs.example.test/42",
};
const otherJob = {
  id: 99,
  title: "DevOps Engineer",
  company: "Other",
  location: "Remote",
  source: "User Selected Job",
  description: "A detailed DevOps role with Kubernetes, Terraform, and AWS enough to analyze.",
  job_url: "https://jobs.example.test/99",
};
const groups = {
  verified_resume_skills: [{ skill: "Maven", category: "Build Tool", match_type: "EXACT", evidence_strength: "strong", resume_status: "VERIFIED", evidence_snippets: ["Used Maven builds"] }],
  weak_or_indirect_skills: [],
  missing_mandatory_skills: [],
  missing_preferred_skills: [],
  can_generate_resume: true,
  blocking_reasons: [],
  generation_warnings: [],
  generation_blocked_message: "",
  generation_warning_message: "",
  evidence: [],
  evidence_history: [],
  evidence_summary: {
    evidence: [],
    incomplete: [],
    user_confirmed: [],
    counts: { verified_from_resume: 1, user_confirmed_with_evidence: 0, learning_only: 0, not_possessed: 0, incomplete: 0 },
  },
};

beforeEach(() => {
  sessionStorage.clear();
  app = makeApp({
    jobs: [job, otherJob],
    latestResume: { id: 4, file_name: "resume.txt" },
    atsSelectedJobId: "42",
    missingSkillsGroups: groups,
  });
  app.loadMissingSkills.mockResolvedValue(groups);
  app.loadAtsResultForJob.mockResolvedValue(null);
  app.loadSkillConfirmations.mockResolvedValue({ evidence: [], can_generate_resume: true, blocking_reasons: [] });
  app.analyzeAndOptimize.mockResolvedValue({ workflow_status: "COMPLETED", optimized_resume_id: 77, workflow_id: 8 });
  globalThis.fetch = vi.fn(() => Promise.resolve(new Response(JSON.stringify(job), { status: 200 })));
});

describe("ATS wizard production stability", () => {
  it("A. walks 1→2→3→4→5 without regressing", async () => {
    renderRoute(<AtsAnalyzerPage />, "/optimizer/42", "/optimizer/:jobId");
    expect(screen.getByRole("heading", { name: /step 1/i })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /^continue$/i }));
    expect(screen.getByRole("heading", { name: /step 2/i })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /analyze job description/i }));
    expect(await screen.findByRole("heading", { name: /step 3/i })).toBeInTheDocument();
    fireEvent.click(screen.getByText("Review Evidence"));
    expect(screen.getByRole("heading", { name: /review evidence/i })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /continue to optimization/i }));
    expect(screen.getByRole("heading", { name: /step 5/i })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /step 2 · analyze/i })).not.toBeInTheDocument();
  });

  it("B. ANALYZING_ORIGINAL during generate keeps wizard Step 5", () => {
    app.busy = "ats";
    app.activeAtsWorkflowId = 88;
    app.atsResult = { workflow_id: 88, workflow_status: "ANALYZING_ORIGINAL", job, major_step: 5, generation_substage: "calculating_original_score" };
    renderRoute(<AtsAnalyzerPage />, "/optimizer/42", "/optimizer/:jobId");
    expect(screen.getByRole("heading", { name: /step 5/i })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /step 2 · analyze/i })).not.toBeInTheDocument();
    expect(screen.getByText(/Generation progress \(Step 5\)/i)).toBeInTheDocument();
    expect(screen.getByText("Analyzing Original Resume")).toBeInTheDocument();
    expect(screen.getByText("Select Job").closest("li")).not.toHaveClass("active");
    expect(screen.getByText("Analyze Requirements").closest("li")).not.toHaveClass("active");
    expect(screen.getByText(/Generate, Review/i).closest("li")).toHaveClass("active");
  });

  it("E. refresh during Step 5 rehydrates Step 5", () => {
    sessionStorage.setItem("atsWizardStep:42", "5");
    app.busy = "ats";
    app.activeAtsWorkflowId = 12;
    app.atsResult = { workflow_id: 12, workflow_status: "VALIDATING_RESUME", job, major_step: 5 };
    renderRoute(<AtsAnalyzerPage />, "/optimizer/42", "/optimizer/:jobId");
    expect(screen.getByRole("heading", { name: /step 5/i })).toBeInTheDocument();
    expect(screen.getByText("Validating Resume")).toBeInTheDocument();
  });

  it("F. validation failure Retry stays on Step 5", async () => {
    app.atsResult = {
      workflow_id: 10,
      workflow_status: "VALIDATION_FAILED",
      job,
      error_message: "Validation failed",
      final_validation: { status: "failed", errors: ["Protected field changed"] },
    };
    sessionStorage.setItem("atsWizardStep:42", "5");
    renderRoute(<AtsAnalyzerPage />, "/optimizer/42", "/optimizer/:jobId");
    expect(screen.getByRole("heading", { name: /step 5/i })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(app.retryAtsWorkflow).toHaveBeenCalledWith(10);
    expect(app.loadMissingSkills).not.toHaveBeenCalledWith(42, expect.objectContaining({ forceRefresh: true }));
    expect(screen.getByRole("heading", { name: /step 5/i })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /step 2 · analyze/i })).not.toBeInTheDocument();
  });

  it("G. uploading a different resume invalidates later wizard steps", async () => {
    const view = renderRoute(<AtsAnalyzerPage />, "/optimizer/42", "/optimizer/:jobId");
    fireEvent.click(screen.getByText(/Generate, Review/i));
    expect(screen.getByRole("heading", { name: /step 5/i })).toBeInTheDocument();
    app.latestResume = { id: 9, file_name: "new-resume.txt" };
    view.rerender(
      <MemoryRouter initialEntries={["/optimizer/42"]}>
        <Routes>
          <Route path="/optimizer/:jobId" element={<AtsAnalyzerPage />} />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /step 2/i })).toBeInTheDocument();
    });
    expect(screen.queryByRole("heading", { name: /step 5/i })).not.toBeInTheDocument();
  });

  it("J. double-click Generate starts one generation", async () => {
    let resolveGenerate;
    app.analyzeAndOptimize.mockImplementation(() => new Promise((resolve) => {
      resolveGenerate = resolve;
    }));
    renderRoute(<AtsAnalyzerPage />, "/optimizer/42", "/optimizer/:jobId");
    fireEvent.click(screen.getByText(/Generate, Review/i));
    const generateBtn = screen.getByRole("button", { name: /generate optimized resume/i });
    const click = userEvent.click(generateBtn);
    const clickAgain = userEvent.click(generateBtn);
    await Promise.allSettled([click, clickAgain]);
    expect(app.analyzeAndOptimize).toHaveBeenCalledTimes(1);
    resolveGenerate({ workflow_status: "COMPLETED", optimized_resume_id: 77, workflow_id: 8 });
    await waitFor(() => expect(screen.getByRole("heading", { name: /step 5/i })).toBeInTheDocument());
  });

  it("refresh on /ats-analyzer restores Step 5 from the stored selected job", async () => {
    sessionStorage.setItem("atsSelectedJobId", "42");
    sessionStorage.setItem("atsWizardStep:42", "5");
    app.missingSkillsGroups = null;
    app.atsResult = {
      workflow_id: 8,
      workflow_status: "COMPLETED",
      job,
      optimized_resume_id: 91,
      generated_resume: { plain_text: "Java Maven resume without Scrum" },
      original_match_percentage: 73,
      optimized_match_percentage: 75,
      remaining_gaps: { unsupported: ["Scrum", "Angular"], preferred_gaps: [] },
    };
    renderRoute(<AtsAnalyzerPage />, "/ats-analyzer");
    expect(app.loadSelectedJobs).toHaveBeenCalled();
    expect(app.verifyLatestResume).toHaveBeenCalled();
    expect(await screen.findByRole("heading", { name: /step 5/i })).toBeInTheDocument();
    expect(screen.getByText(/Remaining Gaps \(2\)/i)).toBeInTheDocument();
    expect(screen.getAllByText("Scrum").length).toBeGreaterThan(0);
    expect(screen.queryByText(/Upload a resume and analyze a job before generating/i)).not.toBeInTheDocument();
  });
});
