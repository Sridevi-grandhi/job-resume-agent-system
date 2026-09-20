import { useState } from "react";
import MatchScoreRing from "./MatchScoreRing";
import StatusBadge from "./StatusBadge";
import { applyClick } from "../utils/applyNavigation";

function formatPostedLabel(postedAt) {
  if (!postedAt) return "";
  try {
    return `Posted ${new Date(postedAt).toLocaleDateString(undefined, { month: "short", day: "numeric" })}`;
  } catch {
    return "";
  }
}

export default function DiscoveredJobCard({ job, userId, onSave, onOptimize, onDetails, onJobExpired, saving = false, optimizing = false, saved = false }) {
  const [applying, setApplying] = useState(false);
  const match = job.match;
  const fitting = match?.fitting_skills || [];
  const missing = match?.missing_skills || [];
  const hasApplyUrl = Boolean(job.application_url);
  // can_apply means "verified SPECIFIC_JOB + ACTIVE" (a true Apply), not
  // "clickable" — Open Employer Site/View Listing are legitimate, always
  // clickable navigations with can_apply=false. Only a confirmed-closed
  // posting disables the button.
  const jobClosed = job.action_label === "Job Closed";
  // Backend is the single source of truth for the action label (Step 15 —
  // resolve_apply_action) — the frontend never re-derives Apply vs Job
  // Closed vs View Listing itself. Fallback only covers a payload from
  // before this field existed.
  const applyLabel = job.action_label || (job.is_direct_apply !== false ? "Apply" : "View Listing");
  const postedLabel = formatPostedLabel(job.posted_at);

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
            <p>
              {job.location || "Location not listed"}
              {job.work_mode ? ` · ${job.work_mode}` : ""}
              {job.job_type ? ` · ${job.job_type}` : ""}
            </p>
          </div>
          <div className="job-match-badges">
            {match ? (
              <MatchScoreRing score={match.match_score} className="uj-match-score-ring" />
            ) : job.needs_resume ? (
              <StatusBadge tone="neutral">Upload resume to match</StatusBadge>
            ) : null}
          </div>
        </div>

        <div className="tags uj-source-tags">
          {postedLabel && <span>{postedLabel}</span>}
          <span className="uj-detected-tag">{job.detected_label}</span>
        </div>

        {fitting.length > 0 && (
          <p className="matched-skills"><b>Skills:</b> {fitting.slice(0, 4).join(" · ")}</p>
        )}
        {match && missing.length > 0 && (
          <p className="uj-gap-count">{missing.length} gap{missing.length === 1 ? "" : "s"}</p>
        )}
        {match?.verdict && <p className="job-description">{match.verdict}</p>}
        {!match && job.needs_resume && (
          <p className="job-disabled-reason">Upload a resume to calculate a match score for this job.</p>
        )}

        <div className="card-actions">
          <button className="button secondary" onClick={onSave} disabled={saving || saved}>
            {saved ? "Saved" : saving ? "Saving…" : "Save"}
          </button>
          <button className="button secondary" onClick={onDetails}>Details</button>
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
                applyLabel === "View Listing"
                  ? "Only a source listing is available for this job — not a direct employer link."
                  : applyLabel === "Open Employer Site"
                    ? "This is a real employer/ATS link, but we haven't recently confirmed the posting is still open."
                    : undefined
              }
            >
              {applying ? "Checking…" : applyLabel}
            </button>
          ) : (
            <button className="button primary" disabled title="No application link is available for this job">
              Apply
            </button>
          )}
        </div>
        {job.secondary_action?.url && (
          // A direct employer/ATS destination may itself be blocked for
          // this user (403, Cloudflare, geo restriction, a login wall) —
          // CareerBoost cannot bypass that. When the posting was also
          // discovered via an aggregator, this gives another real route
          // to the same opportunity (Step 8/9) — never fabricated, always
          // the actual stored source listing.
          <a className="text-button uj-secondary-action" href={job.secondary_action.url} target="_blank" rel="noopener noreferrer">
            {job.secondary_action.label}
          </a>
        )}
      </div>
    </article>
  );
}
