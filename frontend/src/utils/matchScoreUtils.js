/** Presentation helpers for resume/job match scores (does not change scoring). */

export function matchScoreQuality(score) {
  const value = Number(score);
  if (!Number.isFinite(value)) {
    return { level: "unknown", label: "Match unavailable", tone: "neutral" };
  }
  if (value >= 85) return { level: "strong", label: "Strong Match", tone: "strong" };
  if (value >= 70) return { level: "good", label: "Good Match", tone: "good" };
  if (value >= 60) return { level: "moderate", label: "Moderate Match", tone: "moderate" };
  return { level: "low", label: "Low Match", tone: "low" };
}

export function formatMatchPercent(score) {
  const value = Number(score);
  if (!Number.isFinite(value)) return "—";
  return `${Math.round(value)}%`;
}

/** Completed original Resume Match rows only — never null/pending/unavailable. */
export function isCompletedMatchScore(match) {
  if (!match || match.match_percentage == null || match.match_percentage === "") return false;
  return Number.isFinite(Number(match.match_percentage));
}

export function completedMatches(matches) {
  return (matches || []).filter(isCompletedMatchScore);
}

export function averageCompletedMatch(matches) {
  const scored = completedMatches(matches);
  if (!scored.length) return null;
  const sum = scored.reduce((total, match) => total + Number(match.match_percentage), 0);
  return Math.round(sum / scored.length);
}

/** Dashboard Match Score Overview buckets — existing 80 / 60 thresholds, unchanged. */
export function matchScoreDistribution(matches) {
  const scored = completedMatches(matches);
  const high = scored.filter((match) => Number(match.match_percentage) >= 80).length;
  const good = scored.filter((match) => {
    const value = Number(match.match_percentage);
    return value >= 60 && value < 80;
  }).length;
  return { high, good, low: scored.length - high - good, scored: scored.length };
}

export function bestCanonicalMatch(matches) {
  const scored = completedMatches(matches);
  if (!scored.length) return null;
  return [...scored].sort((left, right) => Number(right.match_percentage) - Number(left.match_percentage))[0];
}

export function rankedCanonicalMatches(matches) {
  return [...completedMatches(matches)].sort(
    (left, right) => Number(right.match_percentage) - Number(left.match_percentage),
  );
}
