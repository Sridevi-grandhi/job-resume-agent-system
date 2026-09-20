import { useEffect, useRef, useState } from "react";
import MatchScoreBadge from "./MatchScoreBadge";
import StatusBadge from "./StatusBadge";
import { applyClick } from "../utils/applyNavigation";

function formatDateLabel(value, prefix) {
  if (!value) return "";
  try {
    return `${prefix} ${new Date(value).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}`;
  } catch {
    return "";
  }
}

// Backend is the single source of truth for availability display (Step 10)
// — never re-derived from age or anything else on the frontend.
function availabilityDisplay(job) {
  if (job.availability_status === "EXPIRED") return { label: "Job Closed", tone: "warning" };
  if (job.availability_status === "ACTIVE") return { label: "Active", tone: "success" };
  return { label: "Availability not recently verified", tone: "neutral" };
}

export default function JobDetailsDrawer({ job, userId, onClose, onSave, onOptimize, onJobExpired, saving = false, optimizing = false, saved = false }) {
  const [applying, setApplying] = useState(false);
  const drawerRef = useRef(null);

  useEffect(() => {
    if (drawerRef.current) drawerRef.current.scrollTop = 0;
  }, [job?.discovered_job_id]);

  if (!job) return null;
  const match = job.match;
  const fitting = match?.fitting_skills || [];
  const missing = match?.missing_skills || [];
  const postedLabel = formatDateLabel(job.posted_at, "Posted");
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
  const availability = availabilityDisplay(job);

  const handleApplyClick = async () => {
    if (!userId || !job.discovered_job_id || applying) return;
    setApplying(true);
    try {
      const result = await applyClick({ userId, discoveredJobId: job.discovered_job_id });
      if (result?.status === "JOB_EXPIRED" && onJobExpired) {
        onJobExpired(job.discovered_job_id, result);
      }
    } catch {
      // Navigation best-effort — see DiscoveredJobCard's identical handler.
    } finally {
      setApplying(false);
    }
  };

  return (
    <div className="uj-drawer-overlay" onClick={onClose}>
      <aside className="uj-drawer" ref={drawerRef} tabIndex={0} onClick={(event) => event.stopPropagation()} role="dialog" aria-label="Job details">
        <header className="uj-drawer-head">
          <div>
            <h2>{job.title}</h2>
            <p className="uj-company-name">{job.company}</p>
          </div>
          <button className="uj-drawer-close" onClick={onClose} aria-label="Close job details">✕</button>
        </header>

        <div className="uj-drawer-meta">
          <p><b>Location:</b> {job.location || "Not specified"}</p>
          <p><b>Work mode:</b> {job.work_mode || "Not specified"}</p>
          <p><b>Employment type:</b> {job.job_type || "Not specified"}</p>
          {postedLabel && <p><b>{postedLabel}</b></p>}
          <p><b>{job.detected_label}</b></p>
          {job.apply_destination_type !== "AGGREGATOR_LISTING" && (
            <p><b>Availability:</b> <StatusBadge tone={availability.tone}>{availability.label}</StatusBadge></p>
          )}
        </div>

        {match ? (
          <div className="uj-drawer-match">
            <MatchScoreBadge score={match.match_score} />
            {match.verdict && <p className="uj-drawer-verdict">{match.verdict}</p>}
            <div className="uj-drawer-skills">
              {fitting.length > 0 && (
                <div>
                  <h4>Matching Skills</h4>
                  <div className="uj-skill-chips">
                    {fitting.map((skill) => <span key={skill} className="uj-skill-chip fit">{skill}</span>)}
                  </div>
                </div>
              )}
              {missing.length > 0 && (
                <div>
                  <h4>Missing / Gap Skills</h4>
                  <div className="uj-skill-chips">
                    {missing.map((skill) => <span key={skill} className="uj-skill-chip gap">{skill}</span>)}
                  </div>
                </div>
              )}
            </div>
          </div>
        ) : job.needs_resume ? (
          <p className="job-disabled-reason">Upload a resume to calculate a match score for this job.</p>
        ) : null}

        <div className="uj-drawer-jd">
          <h4>Job Description</h4>
          <p>{job.description || "No description available."}</p>
        </div>

        <footer className="uj-drawer-actions">
          {job.direct_url && (
            <a className="button secondary" href={job.direct_url} target="_blank" rel="noopener noreferrer">
              View Original Posting
            </a>
          )}
          <button className="button secondary" onClick={onSave} disabled={saving || saved}>
            {saved ? "Saved" : saving ? "Saving…" : "Save"}
          </button>
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
        </footer>
        {job.secondary_action?.url && (
          // Step 8/9: the employer/ATS destination above may itself be
          // blocked for this user (403, Cloudflare, geo restriction) —
          // CareerBoost cannot bypass that. This is the actual stored
          // source listing this posting was also discovered through,
          // offered as another real route to the same opportunity.
          <p className="uj-drawer-secondary-action">
            <a href={job.secondary_action.url} target="_blank" rel="noopener noreferrer">
              {job.secondary_action.label}
            </a>
          </p>
        )}
      </aside>
    </div>
  );
}
