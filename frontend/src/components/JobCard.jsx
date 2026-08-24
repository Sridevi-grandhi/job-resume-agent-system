import { useNavigate } from "react-router-dom";
import { useApp } from "../context/AppContext";
import MatchScoreBadge from "./MatchScoreBadge";
import StatusBadge from "./StatusBadge";

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

export default function JobCard({ job, compact = false, selected = false, onRemove, removing = false }) {
  const { matches, latestResume, setAtsSelectedJobId, busy } = useApp();
  const navigate = useNavigate();
  const match = matches.find((item) => String(item.job_id) === String(job.id || job.job_id) && (item.resume_version || "original") === "original")
    || matches.find((item) => String(item.job_id) === String(job.id || job.job_id));
  const originalMatch = job.original_match_percentage ?? match?.match_percentage;
  const isRemotive = job.source === "Remotive API";
  const isRealJob = isRemotive || job.source === "User Selected Job" || job.source?.endsWith(" Imported");
  const hasDescription = Boolean(job.description?.trim()) && String(job.description).trim().length >= 40;
  const hasUrl = Boolean(String(job.job_url || "").trim());
  const isImportedOrSelected = job.source === "User Selected Job" || job.source?.endsWith(" Imported");
  const canAnalyze = isRealJob && hasDescription && Boolean(latestResume) && (hasUrl || isImportedOrSelected);
  const disabledReason = !latestResume
    ? "Upload a resume first"
    : genericSearchSources.has(job.source)
      ? "Generic search links cannot be analyzed"
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
          {originalMatch != null && isRealJob && (
            <MatchScoreBadge score={originalMatch} />
          )}
          {genericSearchSources.has(job.source) && (
            <StatusBadge tone="neutral">Match unavailable</StatusBadge>
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
            {isRemotive ? "View Job / Apply" : "View Job ↗"}
          </a>
        ) : selected ? <button className="button secondary" disabled>View Job</button> : null}
        <button
          className="button primary"
          onClick={openAtsAnalyzer}
          disabled={!canAnalyze || busy === "ats" || busy === "selected"}
          title={disabledReason || "Optimize Resume for This Job"}
        >
          {isRemotive ? "Optimize Resume for This Job" : "Optimize and Compare Scores"}
        </button>
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
        {!canAnalyze && disabledReason && (
          <p className="job-disabled-reason">{disabledReason}</p>
        )}
      </div>}
    </div>
  </article>;
}
