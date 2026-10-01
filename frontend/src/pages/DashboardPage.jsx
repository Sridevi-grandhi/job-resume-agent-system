import { useEffect } from "react";
import { Link } from "react-router-dom";
import { useApp } from "../context/AppContext";
import WorkflowProgress from "../components/WorkflowProgress";
import MetricCard from "../components/MetricCard";
import JobCard from "../components/JobCard";
import EmptyState from "../components/EmptyState";
import { Panel } from "../components/Page";
import {
  averageCompletedMatch,
  bestCanonicalMatch,
  matchScoreDistribution,
  rankedCanonicalMatches,
} from "../utils/matchScoreUtils";

function jobFromMatch(match, jobs) {
  const job = jobs.find((item) => String(item.id || item.job_id) === String(match.job_id)) || {};
  return {
    ...job,
    id: job.id || match.job_id,
    title: match.job_title || job.title,
    company: match.company || job.company,
    location: match.location || job.location,
    work_mode: match.work_mode || job.work_mode,
    job_type: match.job_type || job.job_type,
    source: match.source || job.source || "User Selected Job",
    job_url: match.job_url || job.job_url,
    original_match_percentage: match.match_percentage,
  };
}

export default function DashboardPage() {
  const a = useApp();
  useEffect(() => { a.refreshUserWorkspaceState(); }, [a.currentUser]);

  const scored = rankedCanonicalMatches(a.matches);
  const best = bestCanonicalMatch(a.matches);
  const average = averageCompletedMatch(a.matches);
  const distribution = matchScoreDistribution(a.matches);
  const highEnd = distribution.scored ? (distribution.high / distribution.scored) * 360 : 0;
  const goodEnd = distribution.scored ? ((distribution.high + distribution.good) / distribution.scored) * 360 : 0;
  const totalJobs = a.jobs.length;
  const coverageLabel = totalJobs
    ? `${distribution.scored} of ${totalJobs} jobs scored`
    : distribution.scored
      ? `${distribution.scored} jobs scored`
      : "";
  const bestTitle = best ? (best.job_title || jobFromMatch(best, a.jobs).title || "Top opportunity") : "No scores yet";

  const activity = [
    a.latestResume && ["Resume uploaded", a.latestResume.file_name || "Latest resume"],
    distribution.scored && ["Match scores calculated", `${distribution.scored} jobs scored`],
    a.emailLogs.length && ["Email summary generated", `${a.emailLogs.length} email logs`],
    totalJobs && ["Jobs found", `${totalJobs} opportunities loaded`],
  ].filter(Boolean);

  return (
    <div className="dashboard">
      <WorkflowProgress />
      <div className="metrics">
        <MetricCard label="Total jobs" value={totalJobs} detail="Across all sources" icon="jobs" />
        <MetricCard
          label="Avg. match score"
          value={average == null ? "—" : `${average}%`}
          detail={coverageLabel || "Awaiting calculation"}
          icon="matches"
          tone="green"
        />
        <MetricCard
          label="Best match"
          value={best ? `${Math.round(best.match_percentage)}%` : "—"}
          detail={bestTitle}
          icon="selected"
          tone="amber"
        />
        <MetricCard
          label="Optimized resumes"
          value={a.optimizedResumes.length}
          detail="Generated resume versions"
          icon="resume"
          tone="purple"
        />
        <MetricCard
          label="Email logs"
          value={a.emailLogs.length}
          detail="Summary history"
          icon="emails"
          tone="sky"
        />
      </div>
      <div className="dash-grid">
        <Panel className="top-job-matches-panel" title="Top Job Matches" actions={<Link className="text-button" to="/jobs">View all</Link>}>
          {scored.length ? (
            <div className="stack">
              {scored.map((match, index) => (
                <JobCard compact selected key={match.id || match.job_id || index} job={jobFromMatch(match, a.jobs)} />
              ))}
            </div>
          ) : (
            <EmptyState title="No matches calculated" text="Calculate matches to see your strongest opportunities." />
          )}
        </Panel>
        <Panel className="match-overview-panel" title="Match Score Overview" subtitle={coverageLabel || undefined}>
          <div className="score-overview">
            <div
              className={`score-ring${distribution.scored ? "" : " empty"}`}
              style={{ "--high-end": `${highEnd}deg`, "--good-end": `${goodEnd}deg` }}
              role="img"
              aria-label={`Match score distribution: High ${distribution.high}, Good ${distribution.good}, Low ${distribution.low}`}
            >
              <span>
                <b>{average == null ? "—" : `${average}%`}</b>
                <small>Average</small>
              </span>
            </div>
            <div className="legend">
              <p><i className="green" />High match (80–100%) <b>{distribution.high}</b></p>
              <p><i className="blue" />Good match (60–79%) <b>{distribution.good}</b></p>
              <p><i className="amber" />Low match (&lt;60%) <b>{distribution.low}</b></p>
            </div>
          </div>
          {best && (
            <div className="best-match">
              <span>Best match<strong>{bestTitle}</strong></span>
              <b>{Math.round(best.match_percentage)}%</b>
            </div>
          )}
        </Panel>
        <Panel className="recent-activity-panel" title="Recent Activity">
          {activity.length ? (
            <div className="activity">
              {activity.map(([title, sub], index) => (
                <div key={title}>
                  <i>{["✦", "✓", "✉", "⌕"][index]}</i>
                  <span><b>{title}</b><small>{sub}</small></span>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState title="No recent activity" text="Your completed actions will appear here." />
          )}
        </Panel>
      </div>
    </div>
  );
}
