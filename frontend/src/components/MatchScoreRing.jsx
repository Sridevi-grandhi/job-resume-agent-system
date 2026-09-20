import { formatMatchPercent, matchScoreQuality } from "../utils/matchScoreUtils";

const TOOLTIP = "Resume Match compares the selected job description with your current uploaded resume.";

export default function MatchScoreRing({ score, className = "" }) {
  if (score == null || String(score).trim() === "") return null;

  const numericScore = Number(score);
  const quality = matchScoreQuality(score);
  const percent = formatMatchPercent(score);
  if (quality.level === "unknown") return null;

  const progress = Math.min(100, Math.max(0, numericScore));
  const tone = quality.level === "low" && numericScore < 40 ? "very-low" : quality.tone;

  return (
    <div
      className={`match-score-ring tone-${tone} ${className}`.trim()}
      title={TOOLTIP}
      aria-label={`Resume match ${Math.round(numericScore)} percent — ${quality.label}. ${TOOLTIP}`}
    >
      <span className="match-score-ring-graphic" aria-hidden="true">
        <svg viewBox="0 0 44 44">
          <circle className="match-score-ring-track" cx="22" cy="22" r="18" pathLength="100" />
          <circle
            className="match-score-ring-progress"
            cx="22"
            cy="22"
            r="18"
            pathLength="100"
            strokeDasharray={`${progress} 100`}
          />
        </svg>
        <strong>{percent}</strong>
      </span>
      <span className="match-score-ring-label">{quality.label}</span>
    </div>
  );
}
