import { useState } from "react";
import MatchScoreRing from "./MatchScoreRing";
import StatusBadge from "./StatusBadge";
import { applyClick } from "../utils/applyNavigation";

function formatPostedLabel(postedAt) {
  if (!postedAt) return "";
  try {
    const posted = new Date(postedAt);
    const hours = Math.max(0, (Date.now() - posted.getTime()) / 3600000);
    if (hours < 24) return hours < 1 ? "Just posted" : `${Math.max(1, Math.round(hours))}h ago`;
    const days = Math.round(hours / 24);
    if (days < 14) return `${days}d ago`;
    return `Posted ${posted.toLocaleDateString(undefined, { month: "short", day: "numeric" })}`;
  } catch {
    return "";
  }
}

function metaParts(job) {
  return [job.location, job.work_mode, job.job_type].filter(Boolean);
}

export default function DiscoveredJobCard({ job, userId, onSave, onOptimize, onDetails, onJobExpired, saving = false, optimizing = false, saved = false }) {
  const [applying, setApplying] = useState(false);
  const match = job.match;
  const hasApplyUrl = Boolean(job.application_url);
  const jobClosed = job.action_label === "Job Closed";
  const applyLabel = job.action_label || "View Listing";
  const postedLabel = formatPostedLabel(job.posted_at);
  const details = metaParts(job);

  const handleApplyClick = async () => {
    if (!userId || !job.discovered_job_id || applying) return;
    setApplying(true);
    try {
      const result = await applyClick({ userId, discoveredJobId: job.discovered_job_id });
      if (result?.status === "JOB_EXPIRED" && onJobExpired) {
        onJobExpired(job.discovered_job_id, result);
      }
    } catch {
      // Navigation best-effort; a failed recheck should not trap the user —
      // the tab (if any) has already been closed by applyClick().
    } finally {
      setApplying(false);
    }
  };

  return (
    <article className="job-card uj-card">
      <div className="uj-card-top">
        {job.company_logo_url ? (
          <img className="uj-company-logo" src={job.company_logo_url} alt="" />
        ) : (
          <div className="company-mark">{(job.company || "J")[0].toUpperCase()}</div>
        )}
        <div className="job-copy">
          <div className="job-title-row">
            <div className="job-title-copy">
              <h3>{job.title || "Job opportunity"}</h3>
              <p className="uj-company-name">{job.company || "Unknown company"}</p>
              {details.length > 0 && <p className="uj-card-meta">{details.join(" · ")}</p>}
            </div>
            <div className="job-match-badges">
              {match ? (
                <MatchScoreRing score={match.match_score} className="uj-match-score-ring" />
              ) : job.needs_resume ? (
                <StatusBadge tone="neutral">Upload resume to match</StatusBadge>
              ) : null}
            </div>
          </div>
        </div>
      </div>

      <div className="tags uj-source-tags">
        {job.source_label && <span>{job.source_label}</span>}
        {postedLabel && <span>{postedLabel}</span>}
        <span className="uj-detected-tag">{job.detected_label}</span>
      </div>
      {job.needs_resume && !match && (
        <p className="job-disabled-reason">Upload a resume to calculate a match score for this job.</p>
      )}

      <div className="card-actions">
        <button className="button secondary" onClick={onSave} disabled={saving || saved}>
          {saved ? "Saved" : saving ? "Saving…" : "Save"}
        </button>
        <button className="button secondary" onClick={onDetails}>Details</button>
        {job.source_kind === "company_direct" && /^https:\/\//i.test(job.official_job_url || "") && (
          <a className="button secondary" href={job.official_job_url} target="_blank" rel="noopener noreferrer">View Listing</a>
        )}
        <button className="button secondary" onClick={onOptimize} disabled={optimizing}>
          {optimizing ? "Opening…" : "Optimize Resume"}
        </button>
        {jobClosed ? (
          <button className="button primary" disabled title="This posting is no longer available.">
            Job Closed
          </button>
        ) : hasApplyUrl ? (
          <button
            className="button primary"
            onClick={handleApplyClick}
            disabled={applying}
            title={
              applyLabel.startsWith("Apply on ") && applyLabel !== "Apply on Company Site"
                ? "Only a source listing is available for this job — not a direct employer application page."
                : applyLabel === "Open Career Page"
                  ? "This opens the employer career site. The exact job posting was not confirmed."
                  : undefined
            }
          >
            {applying ? "Checking…" : applyLabel}
          </button>
        ) : (
          <button className="button primary" disabled title="No application link is available for this job">
            {applyLabel}
          </button>
        )}
      </div>
      {job.secondary_action?.url && job.secondary_action.url !== job.application_url && (
        <a className="text-button uj-secondary-action" href={job.secondary_action.url} target="_blank" rel="noopener noreferrer">
          {job.secondary_action.label}
        </a>
      )}
    </article>
  );
}
