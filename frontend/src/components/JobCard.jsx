import { useNavigate } from "react-router-dom";
import { useApp } from "../context/AppContext";
import MatchScoreBadge from "./MatchScoreBadge";
import MatchScoreRing from "./MatchScoreRing";

const genericSearchSources = new Set([
  "LinkedIn Jobs",
  "Indeed",
  "Dice",
  "Google Jobs",
  "ZipRecruiter",
]);

function truncate(text, max = 280) {
  const value = String(text || "").trim();
  if (!value) return "";
  return value.length > max ? `${value.slice(0, max).trim()}…` : value;
}

// Every Selected Job is in exactly one of these states server-side
// (job_routes._selected_job_payload's match_status) — shown only when a
// real percentage isn't available, so the top-right score region is never
// an unexplained blank. Scoped to Selected Jobs only via showMatchStatus
// (see below), never shown on Job Search/Dashboard cards.
function MatchStatusIndicator({ status }) {
  if (status === "CALCULATING") {
    return <span className="match-status-indicator calculating">Calculating…</span>;
  }
  if (status === "FAILED") {
    return <span className="match-status-indicator failed">Unable to calculate</span>;
  }
  return (
    <span className="match-status-indicator unavailable" title="Not enough job information to calculate a reliable match.">
      Match unavailable
    </span>
  );
}

export default function JobCard({ job, compact = false, selected = false, showRing = false, showMatchStatus = false, onRemove, removing = false }) {
  const { matches, latestResume, setAtsSelectedJobId, busy } = useApp();
  const navigate = useNavigate();
  const match = matches.find((item) => String(item.job_id) === String(job.id || job.job_id) && (item.resume_version || "original") === "original")
    || matches.find((item) => String(item.job_id) === String(job.id || job.job_id));
  const originalMatch = job.original_match_percentage ?? match?.match_percentage;
  // Only meaningful (and only ever present) on Selected Jobs' own job
  // objects — job_routes._selected_job_payload is the sole source of this
  // field, so it's naturally undefined everywhere else.
  const matchStatus = job.match_status;
  const isRemotive = job.source === "Remotive API";
  const isGenericSearchLink = genericSearchSources.has(job.source);
  const isRealJob = isRemotive || job.source === "User Selected Job" || job.source?.endsWith(" Imported");
  const hasDescription = Boolean(job.description?.trim()) && String(job.description).trim().length >= 40;
  const hasUrl = Boolean(String(job.job_url || "").trim());
  const isImportedOrSelected = job.source === "User Selected Job" || job.source?.endsWith(" Imported");
  const canAnalyze = isRealJob && hasDescription && Boolean(latestResume) && (hasUrl || isImportedOrSelected);
  const disabledReason = !latestResume
    ? "Upload a resume first"
    : !hasDescription
        ? "Job description is incomplete"
        : !hasUrl && !isImportedOrSelected
          ? "Direct job URL is required"
          : "";
  const description = truncate(job.description);
  const skills = String(job.skills_required || "").trim();
  const jobUrl = String(job.job_url || "").trim();

  const openAtsAnalyzer = () => {
    const jobId = String(job.id || job.job_id);
    setAtsSelectedJobId(jobId);
    sessionStorage.setItem("atsSelectedJobId", jobId);
    navigate(`/optimizer/${jobId}`, {
      state: {
        job_id: Number(jobId),
        title: job.title,
        company: job.company,
        job_url: job.job_url,
        source: job.source,
      },
    });
  };

  return <article className={`job-card ${compact ? "compact" : ""}`}>
    <div className="company-mark">{(job.company || job.source || "J")[0].toUpperCase()}</div>
    <div className="job-copy">
      <div className="job-title-row">
        <div className="job-title-copy">
          <h3>{job.title || "Job opportunity"}</h3>
          <p>{job.company || job.source || "Unknown company"} · {job.location || "Location not listed"}</p>
        </div>
        <div className="job-match-badges">
          {isRealJob && (
            originalMatch != null
              // selected (SelectedJobsPage) and showRing (Job Search) both
              // opt into the approved circular design — kept as two
              // separate props on purpose: `selected` also gates the Remove
              // Job button and the disabled "View Job" fallback below,
              // neither of which should turn on just because a page wants
              // the circular score.
              ? ((selected || showRing)
                ? <MatchScoreRing score={originalMatch} />
                : <MatchScoreBadge score={originalMatch} />)
              // No real score yet — never a silent blank on Selected Jobs
              // specifically (showMatchStatus), explains exactly why.
              : (showMatchStatus && matchStatus && <MatchStatusIndicator status={matchStatus} />)
          )}
        </div>
      </div>
      <div className="tags">
        {job.source && <span>{job.source}</span>}
        {job.work_mode && <span>{job.work_mode}</span>}
        {job.job_type && <span>{job.job_type}</span>}
      </div>
      {!compact && skills && (
        <p className="matched-skills"><b>Skills:</b> {skills}</p>
      )}
      {!compact && description && (
        <p className="job-description">{description}</p>
      )}
      {!compact && jobUrl && (
        <p className="job-link-row">
          <b>Direct URL:</b>{" "}
          <a href={jobUrl} target="_blank" rel="noreferrer">{jobUrl}</a>
        </p>
      )}
      {!compact && <div className="card-actions">
        {jobUrl ? (
          <a className="button secondary" href={jobUrl} target="_blank" rel="noopener noreferrer">
            {isGenericSearchLink ? "View Jobs" : "View Job"}
          </a>
        ) : selected ? <button className="button secondary" disabled>View Job</button> : null}
        {!isGenericSearchLink && (
          <button
            className={`button ${selected ? "secondary" : "primary"}`}
            onClick={openAtsAnalyzer}
            disabled={!canAnalyze || busy === "ats" || busy === "selected"}
            title={disabledReason || "Optimize Resume for This Job"}
          >
            Optimize and Compare Scores
          </button>
        )}
        {selected && (
          <button
            className="button danger"
            onClick={onRemove}
            disabled={removing}
            aria-label={`Remove job ${job.title || ""}`.trim()}
          >
            {removing ? "Removing..." : "Remove Job"}
          </button>
        )}
        {!isGenericSearchLink && !canAnalyze && disabledReason && (
          <p className="job-disabled-reason">{disabledReason}</p>
        )}
      </div>}
      {!compact && isGenericSearchLink && (
        <p className="job-search-link-help">Open this source and select a specific job posting to analyze.</p>
      )}
    </div>
  </article>;
}
