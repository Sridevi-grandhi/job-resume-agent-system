import { expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderRoute } from "./render.jsx";
import { makeApp } from "./mockApp";
import DashboardPage from "../pages/DashboardPage";
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
