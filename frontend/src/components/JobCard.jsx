import { useApp } from "../context/AppContext";
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

export default function JobCard({ job, compact = false }) {
  const { matches, optimizeResume, busy } = useApp();
  const match = matches.find((item) => String(item.job_id) === String(job.id || job.job_id));
  const isRemotive = job.source === "Remotive API";
  const isRealJob = isRemotive || job.source === "User Selected Job" || job.source?.endsWith(" Imported");
  const canOptimize = isRealJob && Boolean(job.description?.trim());
  const description = truncate(job.description);
  const skills = String(job.skills_required || "").trim();
  const jobUrl = String(job.job_url || "").trim();

  return <article className={`job-card ${compact ? "compact" : ""}`}>
    <div className="company-mark">{(job.company || job.source || "J")[0].toUpperCase()}</div>
    <div className="job-copy">
      <div className="job-title-row">
        <div>
          <h3>{job.title || "Job opportunity"}</h3>
          <p>{job.company || job.source || "Unknown company"} · {job.location || "Location not listed"}</p>
        </div>
        <div className="job-match-badges">
          {match && isRealJob && (
            <StatusBadge tone="neutral">Resume Match {Math.round(match.match_percentage)}%</StatusBadge>
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
        {jobUrl && <a className="button secondary" href={jobUrl} target="_blank" rel="noreferrer">View Job ↗</a>}
        {canOptimize && <button className="button primary" onClick={() => optimizeResume(job.id || job.job_id)} disabled={busy === "optimizer"}>Optimize Resume</button>}
      </div>}
    </div>
  </article>;
}
