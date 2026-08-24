import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import MatchScoreBadge from "../components/MatchScoreBadge";
import { matchScoreQuality } from "../utils/matchScoreUtils";
import { evidenceStatusLabel } from "../utils/evidenceLabels";
import JobCard from "../components/JobCard";
import { makeApp } from "./mockApp";
import { vi } from "vitest";

vi.mock("../context/AppContext", () => ({ useApp: () => app }));
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => vi.fn() };
});

let app;

describe("match score presentation", () => {
  it.each([
    [86, "Strong Match"],
    [75, "Good Match"],
    [67, "Moderate Match"],
    [55, "Low Match"],
  ])("maps %s%% to %s without changing the numeric value", (score, label) => {
    expect(matchScoreQuality(score).label).toBe(label);
    render(<MatchScoreBadge score={score} />);
    expect(screen.getByText(`${score}%`)).toBeInTheDocument();
    expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.getByText(/Resume Match/i)).toBeInTheDocument();
  });

  it("renders the redesigned badge on selected job cards", () => {
    app = makeApp({
      latestResume: { id: 1, file_name: "resume.txt" },
      matches: [{ job_id: 42, match_percentage: 67, resume_version: "original" }],
    });
    render(
      <JobCard
        job={{
          id: 42,
          title: "SDET",
          company: "Acme",
          location: "Remote",
          source: "User Selected Job",
          description: "A complete software development engineer in test role with enough detail for analysis.",
          job_url: "https://jobs.example.test/42",
          skills_required: "Selenium, Java",
        }}
        selected
        onRemove={() => {}}
      />,
    );
    expect(screen.getByText("67%")).toBeInTheDocument();
    expect(screen.getByText("Moderate Match")).toBeInTheDocument();
    expect(screen.queryByText(/Resume Match 67%/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /remove job/i })).toHaveClass("button", "danger");
  });
});

describe("evidence labels", () => {
  it("hides raw enums behind friendly copy", () => {
    expect(evidenceStatusLabel("USER_CONFIRMED_PROFESSIONAL")).toBe("Professional experience confirmed");
    expect(evidenceStatusLabel("USER_CONFIRMED_PROJECT")).toBe("Project experience confirmed");
    expect(evidenceStatusLabel("LEARNING_ONLY")).toBe("Learning / training only");
    expect(evidenceStatusLabel("NOT_POSSESSED")).toBe("Not currently possessed");
  });
});
