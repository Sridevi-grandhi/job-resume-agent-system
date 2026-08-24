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
