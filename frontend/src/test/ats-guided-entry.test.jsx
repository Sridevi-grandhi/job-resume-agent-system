import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation, useParams } from "react-router-dom";
import { render } from "@testing-library/react";
import { renderRoute } from "./render.jsx";
import { makeApp } from "./mockApp";
import JobCard from "../components/JobCard";
import SelectedJobsPage from "../pages/SelectedJobsPage";
import AtsAnalyzerPage from "../pages/AtsAnalyzerPage";
import { ATS_ENTRY_MODE, writeFreshSelectedJobIntent, writeGuidedRestart, writeStartOptimizationIntent, writeStoredWizardStep } from "../utils/atsWizardState";

let app;
vi.mock("../context/AppContext", () => ({ useApp: () => app }));

const job = {
  id: 42,
  title: "Senior Engineer – Java / Spring Boot / React – Hybrid",
  company: "Genesis10",
  location: "Hybrid",
  source: "User Selected Job",
  description: "A detailed Java backend position with Spring Boot, React, and enough content to analyze safely.",
  job_url: "https://jobs.example.test/42",
};
const otherJob = {
  id: 99,
  title: "DevOps Engineer",
  company: "Other Co",
  location: "Remote",
  source: "User Selected Job",
  description: "A detailed DevOps role with Kubernetes, Terraform, and AWS enough to analyze safely.",
  job_url: "https://jobs.example.test/99",
};

const completedResult = {
  workflow_id: 8,
  workflow_status: "COMPLETED",
  job,
  optimized_resume_id: 91,
  generated_resume: { plain_text: "Existing optimized resume for job 42" },
  original_match_percentage: 79,
  optimized_match_percentage: 79,
};

function NavCapture() {
  const location = useLocation();
  const { jobId } = useParams();
  return (
    <pre data-testid="nav-state">
      {JSON.stringify({ pathname: location.pathname, jobId, state: location.state || {} })}
    </pre>
  );
}

function renderSelectedJobsToOptimizer() {
  return render(
    <MemoryRouter initialEntries={["/selected-jobs"]}>
      <Routes>
        <Route path="/selected-jobs" element={<SelectedJobsPage />} />
        <Route path="/optimizer/:jobId" element={<NavCapture />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  app = makeApp({
    jobs: [job, otherJob],
    selectedJobs: [job],
    latestResume: { id: 4, file_name: "resume.txt" },
    atsSelectedJobId: "42",
  });
  app.loadMissingSkills.mockResolvedValue(null);
  app.loadAtsResultForJob.mockResolvedValue(null);
  app.loadSkillConfirmations.mockResolvedValue({ evidence: [] });
  globalThis.fetch = vi.fn((url) => {
    const payload = String(url).includes("/99") ? otherJob : job;
    return Promise.resolve(new Response(JSON.stringify(payload), { status: 200 }));
  });
});

describe("Selected Jobs Optimize starts guided ATS flow", () => {
  it("Updated Job Search starts Step 2 for the clicked job with later steps locked", async () => {
    writeStoredWizardStep(42, 5);
    writeStoredWizardStep(99, 5);
    writeFreshSelectedJobIntent(7, 99);
    app.atsSelectedJobId = "42";
    app.atsResult = completedResult;
    renderRoute(
      <AtsAnalyzerPage />,
      "/optimizer/99",
      "/optimizer/:jobId",
      { job_id: 99, atsEntryMode: ATS_ENTRY_MODE.START_SELECTED_JOB },
    );
    expect(await screen.findByRole("heading", { name: /step 2/i })).toBeInTheDocument();
    expect(screen.getByText(/against job/i)).toHaveTextContent(otherJob.title);
    const steps = document.querySelectorAll(".ats-wizard-steps li");
    expect(steps[0]).toHaveClass("done");
    expect(steps[1]).toHaveClass("active");
    for (const step of [2, 3, 4]) expect(steps[step]).toHaveClass("locked");
    expect(app.loadAtsResultForJob).not.toHaveBeenCalled();
    expect(app.analyzeAndOptimize).not.toHaveBeenCalled();
  });

  it("Updated Job Search start intent is scoped to the current user", async () => {
    writeFreshSelectedJobIntent(7, 99);
    app.currentUser = { user_id: 8 };
    app.atsSelectedJobId = "99";
    renderRoute(<AtsAnalyzerPage />, "/optimizer/99", "/optimizer/:jobId");
    expect(await screen.findByRole("heading", { name: /step 1/i })).toBeInTheDocument();
  });

  it("CASE 1: Optimize click passes job id and start_optimization intent", async () => {
    renderSelectedJobsToOptimizer();
    await userEvent.click(screen.getByRole("button", { name: /optimize and compare scores/i }));
    const nav = JSON.parse(screen.getByTestId("nav-state").textContent);
    expect(app.setAtsSelectedJobId).toHaveBeenCalledWith("42");
    expect(nav.pathname).toBe("/optimizer/42");
    expect(nav.jobId).toBe("42");
    expect(nav.state.job_id).toBe(42);
    expect(nav.state.atsEntryMode).toBe(ATS_ENTRY_MODE.START_OPTIMIZATION);
    expect(sessionStorage.getItem("atsSelectedJobId")).toBe("42");
    expect(sessionStorage.getItem("atsStartOptimization:42")).toBe("1");
    expect(sessionStorage.getItem("atsGuidedRestart:42")).toBe("1");
  });

  it("CASE 2: ATS receives start_optimization and opens Step 1 with the clicked job preselected", async () => {
    writeStartOptimizationIntent(42);
    renderRoute(
      <AtsAnalyzerPage />,
      "/optimizer/42",
      "/optimizer/:jobId",
      { job_id: 42, title: job.title, company: job.company, atsEntryMode: ATS_ENTRY_MODE.START_OPTIMIZATION },
    );
    expect(await screen.findByRole("heading", { name: /step 1/i })).toBeInTheDocument();
    const picker = screen.getByLabelText(/select job/i);
    expect(picker).toHaveValue("42");
    expect(screen.getByText(job.title, { selector: "b" })).toBeInTheDocument();
    expect(document.querySelector(".ats-job-meta")).toHaveTextContent("Genesis10");
    expect(screen.queryByRole("heading", { name: /step 5/i })).not.toBeInTheDocument();
  });

  it("CASE 3: existing completed workflow + start_optimization still opens Step 1, not Step 5", async () => {
    writeStoredWizardStep(42, 5);
    writeStartOptimizationIntent(42);
    app.atsResult = completedResult;
    renderRoute(
      <AtsAnalyzerPage />,
      "/optimizer/42",
      "/optimizer/:jobId",
      { job_id: 42, atsEntryMode: ATS_ENTRY_MODE.START_OPTIMIZATION },
    );
    expect(await screen.findByRole("heading", { name: /step 1/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/select job/i)).toHaveValue("42");
    expect(screen.queryByRole("heading", { name: /step 5/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /generate optimized resume/i })).not.toBeInTheDocument();
    expect(app.analyzeAndOptimize).not.toHaveBeenCalled();
  });

  it("CASE 4: sidebar /ats-analyzer with completed workflow restores Step 5", async () => {
    sessionStorage.setItem("atsSelectedJobId", "42");
    writeStoredWizardStep(42, 5);
    app.atsResult = completedResult;
    renderRoute(<AtsAnalyzerPage />, "/ats-analyzer");
    expect(await screen.findByRole("heading", { name: /step 5/i })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /step 1 · select job/i })).not.toBeInTheDocument();
  });

  it("CASE 5: consumed start intent restores the persisted step on refresh, not Step 1", async () => {
    writeGuidedRestart(42);
    writeStoredWizardStep(42, 3);
    app.atsResult = completedResult;
    renderRoute(<AtsAnalyzerPage />, "/optimizer/42", "/optimizer/:jobId");
    expect(await screen.findByRole("heading", { name: /step 3/i })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /step 1 · select job/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /step 5/i })).not.toBeInTheDocument();
  });

  it("CASE 6: Selected Jobs Optimize does not call generate", async () => {
    renderSelectedJobsToOptimizer();
    await userEvent.click(screen.getByRole("button", { name: /optimize and compare scores/i }));
    expect(app.analyzeAndOptimize).not.toHaveBeenCalled();
    expect(app.retryAtsWorkflow).not.toHaveBeenCalled();
    const posted = (globalThis.fetch.mock?.calls || []).filter((call) => {
      const init = call[1] || {};
      return String(init.method || "GET").toUpperCase() === "POST";
    });
    expect(posted).toEqual([]);
  });

  it("CASE 7: generation control is not available before Step 5", async () => {
    writeStartOptimizationIntent(42);
    app.atsResult = completedResult;
    renderRoute(
      <AtsAnalyzerPage />,
      "/optimizer/42",
      "/optimizer/:jobId",
      { job_id: 42, atsEntryMode: ATS_ENTRY_MODE.START_OPTIMIZATION },
    );
    expect(await screen.findByRole("heading", { name: /step 1/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /generate optimized resume/i })).not.toBeInTheDocument();
    expect(app.analyzeAndOptimize).not.toHaveBeenCalled();
  });

  it("CASE 8: optimizing a different job does not reuse the previous job wizard step", async () => {
    writeStoredWizardStep(42, 5);
    app.atsResult = completedResult;
    app.atsSelectedJobId = "99";
    writeStartOptimizationIntent(99);
    renderRoute(
      <AtsAnalyzerPage />,
      "/optimizer/99",
      "/optimizer/:jobId",
      { job_id: 99, title: otherJob.title, company: otherJob.company, atsEntryMode: ATS_ENTRY_MODE.START_OPTIMIZATION },
    );
    expect(await screen.findByRole("heading", { name: /step 1/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/select job/i)).toHaveValue("99");
    expect(screen.getByText(otherJob.title)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /step 5/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/Existing optimized resume for job 42/i)).not.toBeInTheDocument();
  });

  it("Job Search Optimize does not set start_optimization", async () => {
    render(
      <MemoryRouter initialEntries={["/jobs"]}>
        <Routes>
          <Route path="/jobs" element={<JobCard job={job} showRing />} />
          <Route path="/optimizer/:jobId" element={<NavCapture />} />
        </Routes>
      </MemoryRouter>,
    );
    await userEvent.click(screen.getByRole("button", { name: /optimize and compare scores/i }));
    const nav = JSON.parse(screen.getByTestId("nav-state").textContent);
    expect(nav.state.atsEntryMode).toBeUndefined();
    expect(sessionStorage.getItem("atsStartOptimization:42")).toBeNull();
  });

  it("consumes start_optimization so a later mount without intent can restore Step 5", async () => {
    writeStoredWizardStep(42, 5);
    writeStartOptimizationIntent(42);
    app.atsResult = completedResult;
    const first = renderRoute(
      <AtsAnalyzerPage />,
      "/optimizer/42",
      "/optimizer/:jobId",
      { job_id: 42, atsEntryMode: ATS_ENTRY_MODE.START_OPTIMIZATION },
    );
    expect(await screen.findByRole("heading", { name: /step 1/i })).toBeInTheDocument();
    await waitFor(() => {
      expect(sessionStorage.getItem("atsStartOptimization:42")).toBeNull();
    });
    first.unmount();
    sessionStorage.removeItem("atsGuidedRestart:42");
    writeStoredWizardStep(42, 5);
    renderRoute(<AtsAnalyzerPage />, "/ats-analyzer");
    expect(await screen.findByRole("heading", { name: /step 5/i })).toBeInTheDocument();
  });
});
