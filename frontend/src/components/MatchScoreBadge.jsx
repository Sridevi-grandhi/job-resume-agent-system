import { formatMatchPercent, matchScoreQuality } from "../utils/matchScoreUtils";

const TOOLTIP = "Resume Match compares the selected job description with your current uploaded resume.";

export default function MatchScoreBadge({ score, className = "" }) {
  const quality = matchScoreQuality(score);
  const percent = formatMatchPercent(score);
  if (quality.level === "unknown") return null;

  return (
    <div
      className={`match-score-badge tone-${quality.tone} ${className}`.trim()}
      title={TOOLTIP}
      aria-label={`Resume Match ${percent}, ${quality.label}. ${TOOLTIP}`}
    >
      <span className="match-score-value">{percent}</span>
      <span className="match-score-meta">
        <span className="match-score-caption">Resume Match</span>
        <span className="match-score-label">{quality.label}</span>
      </span>
    </div>
  );
}
