import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import userEvent from "@testing-library/user-event";
import { renderRoute } from "./render.jsx";
import { makeApp } from "./mockApp";
import DashboardPage from "../pages/DashboardPage";
import WorkflowProgress from "../components/WorkflowProgress";
import MatchScorePage from "../pages/MatchScorePage";
import OptimizerPage from "../pages/OptimizerPage";
import MatchCard from "../components/MatchCard";
import Sidebar from "../components/Sidebar";
import Topbar from "../components/Topbar";
import ProtectedRoute from "../components/ProtectedRoute";

let app;
vi.mock("../context/AppContext", () => ({ useApp: () => app }));

it("renders dashboard metrics, matches, workflow progress, and activity", () => {
  app = makeApp({
    preferencesSaved: true, latestResume: { file_name: "resume.txt" }, averageMatch: 75,
    jobs: [{ id: 2, title: "Engineer", company: "Acme", source: "Indeed" }],
    selectedJobs: [{ id: 2 }], matches: [{ id: 1, job_id: 2, job_title: "Engineer", match_percentage: 75 }],
    optimizedResumes: [{ id: 1 }], emailLogs: [{ id: 1 }], workflowResult: { status: "done" },
  });
  renderRoute(<DashboardPage />, "/dashboard");
  expect(screen.getAllByText("75%").length).toBeGreaterThan(0);
  expect(screen.getByText("Recent Activity")).toBeInTheDocument();
});

// Dashboard state tracking fix — Dashboard must independently hydrate itself
// (never depend on Workflow, or any other page, having been visited first).
it("hydrates itself on mount by calling refreshUserWorkspaceState — Workflow is never rendered in this test", () => {
  app = makeApp();
  renderRoute(<DashboardPage />, "/dashboard");
  expect(app.refreshUserWorkspaceState).toHaveBeenCalledTimes(1);
});

it("direct dashboard load reflects already-persisted state without Workflow ever mounting", () => {
  app = makeApp({
    preferencesSaved: true, latestResume: { file_name: "resume.pdf" },
    jobs: [{ id: 1 }, { id: 2 }, { id: 3 }, { id: 4 }, { id: 5 }],
  });
  renderRoute(<DashboardPage />, "/dashboard");
  expect(screen.getByText("Total jobs").closest("article")).toHaveTextContent("5");
});

it("re-hydrates on a new login (currentUser change) so a fresh session never starts from stale Pending values", () => {
  app = makeApp();
  const { rerender } = renderRoute(<DashboardPage />, "/dashboard");
  expect(app.refreshUserWorkspaceState).toHaveBeenCalledTimes(1);
  app.currentUser = { user_id: 99, email: "second@example.test", name: "Second Candidate" };
  rerender(<MemoryRouter initialEntries={["/dashboard"]}><DashboardPage /></MemoryRouter>);
  expect(app.refreshUserWorkspaceState).toHaveBeenCalledTimes(2);
});

it("returning to Dashboard after a remount (e.g. navigating back from another page) re-hydrates and shows the updated count", () => {
  app = makeApp({ jobs: [{ id: 1 }] });
  const { unmount } = renderRoute(<DashboardPage />, "/dashboard");
  expect(screen.getByText("Total jobs").closest("article")).toHaveTextContent("1");
  unmount();
  // Simulates the user having saved a job on another page in the meantime —
  // Dashboard's own next mount must independently pick this up.
  app = makeApp({ jobs: [{ id: 1 }, { id: 2 }, { id: 3 }] });
  renderRoute(<DashboardPage />, "/dashboard");
  expect(app.refreshUserWorkspaceState).toHaveBeenCalledTimes(1);
  expect(screen.getByText("Total jobs").closest("article")).toHaveTextContent("3");
});

describe("WorkflowProgress tile — Dashboard progress tracker", () => {
  it("shows Pending/Optional for every feature with no persisted data, without Workflow having run", () => {
    app = makeApp();
    renderRoute(<WorkflowProgress />, "/dashboard");
    expect(screen.getByText("Preferences").closest("a")).toHaveTextContent("Pending");
    expect(screen.getByText("Resume").closest("a")).toHaveTextContent("Pending");
    expect(screen.getByText("Job Search").closest("a")).toHaveTextContent("Pending");
    expect(screen.getByText("Selected Jobs").closest("a")).toHaveTextContent("Optional");
    expect(screen.getByText("ATS Analyzer").closest("a")).toHaveTextContent("Pending");
    expect(screen.getByText("Email Summary").closest("a")).toHaveTextContent("Pending");
    expect(screen.getByText("Workflow").closest("a")).toHaveTextContent("Ready");
  });

  it("marks every persisted feature done from real data alone — no Workflow click required", () => {
    app = makeApp({
      preferencesSaved: true, latestResume: { file_name: "r.pdf" },
      jobs: [{ id: 1 }], selectedJobs: [{ id: 1 }],
      optimizedResumes: [{ id: 1 }], emailLogs: [{ id: 1 }],
    });
    renderRoute(<WorkflowProgress />, "/dashboard");
    expect(screen.getByText("Preferences").closest("a")).toHaveTextContent("Saved");
    expect(screen.getByText("Resume").closest("a")).toHaveTextContent("Uploaded");
    expect(screen.getByText("Job Search").closest("a")).toHaveTextContent("1 jobs");
    expect(screen.getByText("Selected Jobs").closest("a")).toHaveTextContent("1 added");
    expect(screen.getByText("ATS Analyzer").closest("a")).toHaveTextContent("1 optimized");
    expect(screen.getByText("Email Summary").closest("a")).toHaveTextContent("1 sent");
  });

  it("Workflow tile reads Complete from a prior run's persisted matches+email logs, never only from clicking Run this session", () => {
    app = makeApp({ workflowResult: null, matches: [{ id: 1 }], emailLogs: [{ id: 1 }] });
    renderRoute(<WorkflowProgress />, "/dashboard");
    expect(screen.getByText("Workflow").closest("a")).toHaveTextContent("Complete");
  });

  it("Workflow tile still reads Complete immediately after Run full workflow this session (workflowResult set)", () => {
    app = makeApp({ workflowResult: { status: "done" }, matches: [], emailLogs: [] });
    renderRoute(<WorkflowProgress />, "/dashboard");
    expect(screen.getByText("Workflow").closest("a")).toHaveTextContent("Complete");
  });

  it("Workflow tile stays Ready when neither a session run nor persisted matches+email logs exist", () => {
    app = makeApp({ workflowResult: null, matches: [{ id: 1 }], emailLogs: [] });
    renderRoute(<WorkflowProgress />, "/dashboard");
    expect(screen.getByText("Workflow").closest("a")).toHaveTextContent("Ready");
  });
});

it("renders match table and optimized resume", async () => {
  app = makeApp({ averageMatch: 82, jobs: [{ id: 4, title: "Java Engineer", company: "Acme", job_url: "https://jobs.example.test/4" }], matches: [{ id: 1, job_id: 4, match_percentage: 82, matched_keywords: ["Java"], missing_keywords: ["Kafka"] }], optimizedResumes: [{ id: 3, job_id: 4, optimized_text: "Safe optimized content" }] });
  const { unmount } = renderRoute(<MatchScorePage />, "/matches");
  expect(screen.getAllByText("82%").length).toBeGreaterThan(0);
  await userEvent.click(screen.getByRole("button", { name: /calculate matches/i }));
  expect(app.calculateMatches).toHaveBeenCalled(); unmount();
  renderRoute(<OptimizerPage />, "/optimizer");
  expect(screen.getByText("Safe optimized content")).toBeInTheDocument();
});

it("renders navigation chrome and logout", async () => {
  app = makeApp();
  const close = vi.fn(); const menu = vi.fn();
  const { unmount } = renderRoute(<Sidebar open onClose={close} />, "/dashboard");
  await userEvent.click(screen.getByRole("button", { name: /test candidate/i }));
  expect(app.logout).toHaveBeenCalled(); unmount();
  renderRoute(<Topbar onMenu={menu} />, "/dashboard");
  await userEvent.click(screen.getByRole("button", { name: "☰" }));
  expect(menu).toHaveBeenCalled();
});

it("redirects unauthenticated protected routes", () => {
  app = makeApp({ currentUser: null });
  renderRoute(<ProtectedRoute />, "/private");
  expect(app.currentUser).toBeNull();
});

it("renders standalone match card fallbacks", () => {
  app = makeApp();
  renderRoute(<MatchCard match={{ job_id: 8, match_percentage: 61 }} />);
  expect(screen.getByText("Job 8")).toBeInTheDocument();
  expect(screen.getByText("61% match")).toBeInTheDocument();
});

// Dashboard Top Job Matches — circular score UI + internal scroll fix.
describe("Dashboard — Top Job Matches score design + internal scroll", () => {
  const jobsAndMatches = (count) => {
    const jobs = Array.from({ length: count }, (_, i) => ({ id: i + 1, title: `Engineer ${i + 1}`, company: "Acme", source: "Remotive API" }));
    const matches = jobs.map((job, i) => ({ id: job.id, job_id: job.id, match_percentage: 90 - i }));
    return { jobs, matches };
  };

  it("renders the circular MatchScoreRing, never the rectangular MatchScoreBadge", () => {
    const { jobs, matches } = jobsAndMatches(1);
    app = makeApp({ jobs, matches });
    renderRoute(<DashboardPage />, "/dashboard");
    const panel = document.querySelector(".top-job-matches-panel");
    expect(panel.querySelector(".match-score-ring")).not.toBeNull();
    expect(panel.querySelector(".match-score-badge")).toBeNull();
  });

  it("keeps the existing match percentage and label calculation unchanged (thresholds untouched)", () => {
    const jobs = [
      { id: 1, title: "A", company: "Acme", source: "Remotive API" },
      { id: 2, title: "B", company: "Acme", source: "Remotive API" },
      { id: 3, title: "C", company: "Acme", source: "Remotive API" },
    ];
    const matches = [
      { id: 1, job_id: 1, match_percentage: 93 }, // >=85 -> Strong Match
      { id: 2, job_id: 2, match_percentage: 79 }, // >=70 -> Good Match
      { id: 3, job_id: 3, match_percentage: 65 }, // >=60 -> Moderate Match
    ];
    app = makeApp({ jobs, matches });
    renderRoute(<DashboardPage />, "/dashboard");
    const panel = document.querySelector(".top-job-matches-panel");
    expect(panel.textContent).toContain("93%");
    expect(panel.textContent).toContain("Strong Match");
    expect(panel.textContent).toContain("79%");
    expect(panel.textContent).toContain("Good Match");
    expect(panel.textContent).toContain("65%");
    expect(panel.textContent).toContain("Moderate Match");
  });

  it("renders every calculated match (not capped at 4) inside the scrollable list", () => {
    const { jobs, matches } = jobsAndMatches(10);
    app = makeApp({ jobs, matches });
    renderRoute(<DashboardPage />, "/dashboard");
    const list = document.querySelector(".top-job-matches-panel .stack");
    expect(list).not.toBeNull();
    expect(list.querySelectorAll(".job-card").length).toBe(10);
  });

  it("Top Job Matches panel and its internal list carry the fixed-height/scroll hooks, independent of job count", () => {
    const { jobs, matches } = jobsAndMatches(10);
    app = makeApp({ jobs, matches });
    renderRoute(<DashboardPage />, "/dashboard");
    const panel = document.querySelector(".top-job-matches-panel");
    expect(panel).not.toBeNull(); // CSS: display:flex, flex-direction:column, max-height
    const list = panel.querySelector(".stack");
    expect(list).not.toBeNull(); // CSS: flex:1, min-height:0, overflow-y:auto
  });

  it("header and 'View all' stay outside the scrollable list and remain unchanged", () => {
    const { jobs, matches } = jobsAndMatches(10);
    app = makeApp({ jobs, matches });
    renderRoute(<DashboardPage />, "/dashboard");
    const panel = document.querySelector(".top-job-matches-panel");
    const header = panel.querySelector(":scope > header");
    expect(header.textContent).toContain("Top Job Matches");
    const viewAll = screen.getByRole("link", { name: /view all/i });
    expect(viewAll).toHaveAttribute("href", "/jobs");
    // The header (and its View all link) must not be inside the scrolling list.
    expect(panel.querySelector(".stack")?.contains(viewAll)).toBe(false);
  });

  it("empty state renders with no scrollable list present (no unnecessary scrollbar)", () => {
    app = makeApp({ matches: [] });
    renderRoute(<DashboardPage />, "/dashboard");
    const panel = document.querySelector(".top-job-matches-panel");
    expect(panel.querySelector(".stack")).toBeNull();
    expect(screen.getByText("No matches calculated")).toBeInTheDocument();
  });

  it("few matches (1-3) render without being force-capped or hidden", () => {
    const { jobs, matches } = jobsAndMatches(2);
    app = makeApp({ jobs, matches });
    renderRoute(<DashboardPage />, "/dashboard");
    const list = document.querySelector(".top-job-matches-panel .stack");
    expect(list.querySelectorAll(".job-card").length).toBe(2);
  });

  it("renders with no console errors", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    const { jobs, matches } = jobsAndMatches(10);
    app = makeApp({ jobs, matches });
    renderRoute(<DashboardPage />, "/dashboard");
    expect(spy).not.toHaveBeenCalled();
    spy.mockRestore();
  });
});
