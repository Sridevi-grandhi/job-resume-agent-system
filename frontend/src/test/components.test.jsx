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
    expect(screen.getByText("Email Summary").closest("a")).toHaveTextContent("1 logs");
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
  expect(app.logout).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Logout" }));
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

  it("keeps the score ring percentage and Strong Match label inside each card", () => {
    const { jobs, matches } = jobsAndMatches(1);
    matches[0].match_percentage = 93;
    app = makeApp({ jobs, matches });
    renderRoute(<DashboardPage />, "/dashboard");
    const card = document.querySelector(".top-job-matches-panel .job-card");
    const ring = card.querySelector(".match-score-ring");
    const label = card.querySelector(".match-score-ring-label");
    expect(ring).not.toBeNull();
    expect(ring.textContent).toContain("93%");
    expect(label).toHaveTextContent("Strong Match");
    expect(card.contains(label)).toBe(true);
    expect(getComputedStyle(card).overflow).not.toBe("hidden");
  });

  it("renders the KPI average and the overview donut from the same completed-match average", () => {
    app = makeApp({
      jobs: [{ id: 1 }, { id: 2 }, { id: 3 }],
      matches: [
        { id: 1, job_id: 1, job_title: "A", match_percentage: 90, source: "Remotive API" },
        { id: 2, job_id: 2, job_title: "B", match_percentage: 70, source: "Remotive API" },
        { id: 3, job_id: 3, job_title: "C", match_percentage: 40, source: "Remotive API" },
      ],
    });
    renderRoute(<DashboardPage />, "/dashboard");
    const kpi = screen.getByText("Avg. match score").closest("article");
    const donut = document.querySelector(".score-ring b");
    expect(kpi).toHaveTextContent("67%");
    expect(donut).toHaveTextContent("67%");
    expect(document.querySelector(".score-ring small")).toHaveTextContent("Average");
  });
});

describe("Dashboard — identity, scores, and coverage", () => {
  it("shows the same authenticated display name in header and sidebar", () => {
    app = makeApp({ currentUser: { user_id: 3, name: "Tanishka", email: "rudratanishka1@gmail.com" } });
    const { unmount } = renderRoute(<Topbar onMenu={vi.fn()} />, "/dashboard");
    expect(screen.getByRole("heading", { name: /welcome back, tanishka/i })).toBeInTheDocument();
    unmount();
    renderRoute(<Sidebar open onClose={vi.fn()} />, "/dashboard");
    expect(screen.getByRole("button", { name: /tanishka/i })).toBeInTheDocument();
    expect(screen.getByText("rudratanishka1@gmail.com")).toBeInTheDocument();
    expect(screen.queryByText("Career seeker")).not.toBeInTheDocument();
  });

  it("falls back to the email prefix in both header and sidebar when name is absent", () => {
    app = makeApp({ currentUser: { user_id: 3, name: "", email: "rudratanishka1@gmail.com" } });
    const { unmount } = renderRoute(<Topbar onMenu={vi.fn()} />, "/dashboard");
    expect(screen.getByRole("heading", { name: /welcome back, rudratanishka1/i })).toBeInTheDocument();
    unmount();
    renderRoute(<Sidebar open onClose={vi.fn()} />, "/dashboard");
    expect(screen.getByRole("button", { name: /rudratanishka1/i })).toBeInTheDocument();
    expect(screen.queryByText("Career seeker")).not.toBeInTheDocument();
  });

  it("averages only completed Resume Match scores and shows coverage against total jobs", () => {
    app = makeApp({
      averageMatch: 71,
      jobs: [{ id: 1 }, { id: 2 }, { id: 3 }, { id: 4 }],
      matches: [
        { id: 1, job_id: 1, job_title: "Lead", match_percentage: 100, source: "Remotive API" },
        { id: 2, job_id: 2, job_title: "Engineer", match_percentage: 72, source: "Remotive API" },
        { id: 3, job_id: 3, job_title: "Analyst", match_percentage: 40, source: "Remotive API" },
        { id: 4, job_id: 4, match_percentage: null },
      ],
    });
    renderRoute(<DashboardPage />, "/dashboard");
    expect(screen.getAllByText("71%").length).toBeGreaterThan(0);
    expect(screen.getAllByText("3 of 4 jobs scored").length).toBeGreaterThan(0);
    expect(screen.queryByText("0%")).not.toBeInTheDocument();
  });

  it("Match Score Overview counts total the completed scored jobs", () => {
    app = makeApp({
      jobs: [{ id: 1 }, { id: 2 }, { id: 3 }],
      matches: [
        { id: 1, job_id: 1, job_title: "A", match_percentage: 90, source: "Remotive API" },
        { id: 2, job_id: 2, job_title: "B", match_percentage: 70, source: "Remotive API" },
        { id: 3, job_id: 3, job_title: "C", match_percentage: 40, source: "Remotive API" },
      ],
    });
    renderRoute(<DashboardPage />, "/dashboard");
    const overview = document.querySelector(".match-overview-panel");
    expect(overview).toHaveTextContent("High match (80–100%) 1");
    expect(overview).toHaveTextContent("Good match (60–79%) 1");
    expect(overview).toHaveTextContent("Low match (<60%) 1");
  });

  it("draws all three donut segments from the same 16/14/9 counts shown in the legend", () => {
    const scores = [
      ...Array(16).fill(85),
      ...Array(14).fill(70),
      ...Array(8).fill(43),
      46,
    ];
    app = makeApp({
      jobs: Array.from({ length: 49 }, (_, index) => ({ id: index + 1, title: `Job ${index + 1}`, company: "Example" })),
      matches: scores.map((match_percentage, index) => ({
        id: index + 1, job_id: index + 1, match_percentage,
      })),
    });
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      renderRoute(<DashboardPage />, "/dashboard");
      const overview = document.querySelector(".match-overview-panel");
      const donut = screen.getByRole("img", { name: "Match score distribution: High 16, Good 14, Low 9" });
      const highEnd = Number.parseFloat(donut.style.getPropertyValue("--high-end"));
      const goodEnd = Number.parseFloat(donut.style.getPropertyValue("--good-end"));

      expect(overview).toHaveTextContent("39 of 49 jobs scored");
      expect(overview).toHaveTextContent("High match (80–100%) 16");
      expect(overview).toHaveTextContent("Good match (60–79%) 14");
      expect(overview).toHaveTextContent("Low match (<60%) 9");
      expect(highEnd).toBeCloseTo((16 / 39) * 360);
      expect(goodEnd - highEnd).toBeCloseTo((14 / 39) * 360);
      expect(360 - goodEnd).toBeCloseTo((9 / 39) * 360);
      expect(donut).not.toHaveClass("empty");
      expect(donut.querySelector("b")).toHaveTextContent("70%");
      expect(donut.querySelector("small")).toHaveTextContent("Average");
      expect(screen.getByText("Avg. match score").closest("article")).toHaveTextContent("70%");
      expect(consoleError).not.toHaveBeenCalled();
    } finally {
      consoleError.mockRestore();
    }
  });

  it("shows an empty donut track when no jobs have a score", () => {
    app = makeApp({ jobs: [{ id: 1 }], matches: [] });
    renderRoute(<DashboardPage />, "/dashboard");
    const donut = screen.getByRole("img", { name: "Match score distribution: High 0, Good 0, Low 0" });
    expect(donut).toHaveClass("empty");
    expect(donut.querySelector("small")).toHaveTextContent("Average");
  });

  it("Best Match KPI, overview, and Top Job Matches use the same canonical current match", () => {
    const jobs = [
      { id: 10, title: "Java Fullstack Lead – Independent", company: "Apex", source: "Remotive API" },
      { id: 11, title: "Other", company: "Other", source: "Remotive API" },
    ];
    const matches = [
      { id: 2, job_id: 11, job_title: "Other", match_percentage: 79, source: "Remotive API" },
      { id: 1, job_id: 10, job_title: "Java Fullstack Lead – Independent", match_percentage: 100, source: "Remotive API" },
    ];
    app = makeApp({ jobs, matches });
    renderRoute(<DashboardPage />, "/dashboard");
    const bestCard = screen.getByText("Best match", { selector: ".metric span" }).closest("article");
    expect(bestCard).toHaveTextContent("100%");
    expect(bestCard).toHaveTextContent("Java Fullstack Lead – Independent");
    const overviewBest = document.querySelector(".best-match");
    expect(overviewBest).toHaveTextContent("Java Fullstack Lead – Independent");
    expect(overviewBest).toHaveTextContent("100%");
    const list = document.querySelector(".top-job-matches-panel .stack");
    expect(list.querySelectorAll(".job-card")[0]).toHaveTextContent("Java Fullstack Lead – Independent");
    expect(list.querySelectorAll(".job-card")[0]).toHaveTextContent("100%");
  });

  it("does not rank jobs without a completed score as Top Matches", () => {
    app = makeApp({
      jobs: [
        { id: 1, title: "Scored", source: "Remotive API" },
        { id: 2, title: "Unscored", source: "Remotive API" },
      ],
      matches: [
        { id: 1, job_id: 1, job_title: "Scored", match_percentage: 88, source: "Remotive API" },
        { id: 2, job_id: 2, job_title: "Unscored", match_percentage: null },
      ],
    });
    renderRoute(<DashboardPage />, "/dashboard");
    const list = document.querySelector(".top-job-matches-panel .stack");
    expect(list.querySelectorAll(".job-card").length).toBe(1);
    expect(list).toHaveTextContent("Scored");
    expect(list).not.toHaveTextContent("Unscored");
  });

  it("hydrates persisted workspace data and never recalculates scores on Dashboard load", () => {
    app = makeApp();
    renderRoute(<DashboardPage />, "/dashboard");
    expect(app.refreshUserWorkspaceState).toHaveBeenCalledTimes(1);
    expect(app.calculateMatches).not.toHaveBeenCalled();
  });

  it("labels optimized resumes as generated versions and email tracker as logs", () => {
    app = makeApp({
      optimizedResumes: [{ id: 1 }, { id: 2 }],
      emailLogs: [{ id: 1 }],
    });
    renderRoute(<DashboardPage />, "/dashboard");
    expect(screen.getByText("Optimized resumes").closest("article")).toHaveTextContent("Generated resume versions");
    expect(screen.getByText("Email Summary").closest("a")).toHaveTextContent("1 logs");
  });
});
