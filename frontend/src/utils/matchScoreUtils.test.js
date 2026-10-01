import { describe, expect, it } from "vitest";
import {
  averageCompletedMatch,
  bestCanonicalMatch,
  completedMatches,
  matchScoreDistribution,
  rankedCanonicalMatches,
} from "./matchScoreUtils";

describe("completed Resume Match selection", () => {
  const matches = [
    { id: 1, job_id: 10, job_title: "Lead", match_percentage: 100 },
    { id: 2, job_id: 11, job_title: "Engineer", match_percentage: 72 },
    { id: 3, job_id: 12, job_title: "Analyst", match_percentage: 40 },
    { id: 4, job_id: 13, match_percentage: null },
    { id: 5, job_id: 14 },
    { id: 6, job_id: 15, match_percentage: "pending" },
  ];

  it("excludes null/unavailable scores from the completed set", () => {
    expect(completedMatches(matches).map((row) => row.job_id)).toEqual([10, 11, 12]);
  });

  it("averages only completed scores", () => {
    expect(averageCompletedMatch(matches)).toBe(71);
    expect(averageCompletedMatch([])).toBeNull();
    expect(averageCompletedMatch([{ match_percentage: null }])).toBeNull();
  });

  it("distribution totals the scored jobs using existing 80/60 buckets", () => {
    expect(matchScoreDistribution(matches)).toEqual({ high: 1, good: 1, low: 1, scored: 3 });
  });

  it("Best Match is the highest canonical completed score", () => {
    expect(bestCanonicalMatch(matches)).toMatchObject({ job_id: 10, match_percentage: 100 });
    expect(rankedCanonicalMatches(matches)[0].job_id).toBe(10);
  });
});
