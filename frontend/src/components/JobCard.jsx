import { useApp } from "../context/AppContext";
import StatusBadge from "./StatusBadge";

const genericSearchSources = new Set([
  "LinkedIn Jobs",
  "Indeed",
  "Dice",
  "Google Jobs",
  "ZipRecruiter",
]);

export default function JobCard({ job, compact = false }) {
  const { matches, optimizeResume, busy } = useApp();
  const match = matches.find((item) => String(item.job_id) === String(job.id || job.job_id));
  const isRemotive = job.source === "Remotive API";
  const isRealJob = isRemotive || job.source === "User Selected Job";
  const canOptimize = isRealJob && Boolean(job.description?.trim());
  const matchedSkills = Array.isArray(job.matched_preference_skills)
    ? job.matched_preference_skills
    : [];

  return <article className={`job-card ${compact ? "compact" : ""}`}>
    <div className="company-mark">{(job.company || job.source || "J")[0].toUpperCase()}</div>
    <div className="job-copy">
      <div className="job-title-row">
        <div>
          <h3>{job.title || "Job opportunity"}</h3>
          <p>{job.company || job.source || "Unknown company"} · {job.candidate_required_location || job.location || "Location not listed"}</p>
        </div>
        <div className="job-match-badges">
          {isRemotive && Number.isFinite(Number(job.relevance_score)) && (
            <StatusBadge>Preference Match {Math.round(Number(job.relevance_score))}%</StatusBadge>
          )}
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
      {matchedSkills.length > 0 && (
        <p className="matched-skills"><b>Matched skills:</b> {matchedSkills.join(", ")}</p>
      )}
      {!compact && <div className="card-actions">
        {job.job_url && <a className="button secondary" href={job.job_url} target="_blank" rel="noreferrer">View Job ↗</a>}
        {canOptimize && <button className="button primary" onClick={() => optimizeResume(job.id || job.job_id)} disabled={busy === "optimizer"}>Optimize Resume</button>}
      </div>}
    </div>
  </article>;
}
