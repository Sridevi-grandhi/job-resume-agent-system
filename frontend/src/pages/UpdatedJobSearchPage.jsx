/* eslint-disable react-hooks/exhaustive-deps */
import { useEffect, useRef, useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { useApp } from "../context/AppContext";
import { Page, Notice } from "../components/Page";
import EmptyState from "../components/EmptyState";
import MetricCard from "../components/MetricCard";
import DiscoveredJobCard from "../components/DiscoveredJobCard";
import JobDetailsDrawer from "../components/JobDetailsDrawer";
import { getDiscoveredJobs, refreshDiscoveredJobs, saveDiscoveredJob } from "../api";
import { ATS_ENTRY_MODE, writeFreshSelectedJobIntent } from "../utils/atsWizardState";

const WORK_MODES = ["Remote", "Hybrid", "Onsite"];
const EMPLOYMENT_TYPES = ["Full-time", "Part-time", "Contract", "Internship", "Freelance"];
const SORTS = [
  ["best_match", "Best Match"],
  ["newest_detected", "Newest Detected"],
  ["newest_posted", "Newest Posted"],
];
const TOP_MATCHES_COUNT = 6;

const emptyFilters = {
  search: "", location: "", work_mode: "", company: "",
  employment_type: "", date_detected: "", min_score: "",
};

function formatLastUpdated(iso) {
  if (!iso) return "Not yet run";
  try {
    return new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  } catch {
    return "—";
  }
}

export default function UpdatedJobSearchPage() {
  const { currentUser, startAtsForSelectedJob } = useApp();
  const navigate = useNavigate();
  const userId = currentUser?.user_id;

  const [filters, setFilters] = useState(emptyFilters);
  const [sort, setSort] = useState("best_match");
  const [jobs, setJobs] = useState([]);
  const [summary, setSummary] = useState(null);
  const [sources, setSources] = useState([]);
  const [hasResume, setHasResume] = useState(true);
  const [hasPreferences, setHasPreferences] = useState(false);
  const [preferenceSummary, setPreferenceSummary] = useState(null);
  const [loading, setLoading] = useState(false);
  const [loadingMessage, setLoadingMessage] = useState("Checking job sources…");
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [savingId, setSavingId] = useState(null);
  const [savedIds, setSavedIds] = useState(() => new Set());
  const [initialized, setInitialized] = useState(false);
  const [detailsJob, setDetailsJob] = useState(null);
  const [notice, setNotice] = useState("");

  const searchDebounce = useRef(null);

  const loadJobs = async (message = "Finding new opportunities…") => {
    if (!userId) return;
    setLoading(true);
    setLoadingMessage(message);
    setError("");
    try {
      const params = { ...filters, sort };
      const data = await getDiscoveredJobs(userId, params);
      setJobs(Array.isArray(data.jobs) ? data.jobs : []);
      setSummary(data.summary || null);
      setSources(Array.isArray(data.sources) ? data.sources : []);
      setHasResume(Boolean(data.has_resume));
      setHasPreferences(Boolean(data.has_preferences));
      setPreferenceSummary(data.preference_summary || null);
    } catch (err) {
      setError(err?.message || "Unable to refresh some job sources. Previously discovered jobs are still available.");
    } finally {
      setLoading(false);
      setInitialized(true);
    }
  };

  useEffect(() => {
    loadJobs("Finding new opportunities…");
  }, [userId, sort, filters.work_mode, filters.employment_type, filters.date_detected, filters.min_score, filters.location, filters.company]);

  useEffect(() => {
    if (!initialized) return;
    if (searchDebounce.current) clearTimeout(searchDebounce.current);
    searchDebounce.current = setTimeout(() => loadJobs("Finding new opportunities…"), 400);
    return () => clearTimeout(searchDebounce.current);
  }, [filters.search]);

  const changeFilter = (key) => (event) => setFilters((current) => ({ ...current, [key]: event.target.value }));

  const handleRefresh = async () => {
    if (!userId || refreshing) return;
    setRefreshing(true);
    setLoadingMessage("Checking job sources…");
    setError("");
    try {
      const result = await refreshDiscoveredJobs(userId);
      if (result?.already_running) {
        setError("A refresh is already in progress. Showing the most recently discovered jobs.");
      }
      await loadJobs("Calculating resume matches…");
    } catch (err) {
      setError(err?.message || "Unable to refresh some job sources. Previously discovered jobs are still available.");
    } finally {
      setRefreshing(false);
    }
  };

  const handleSave = async (job) => {
    if (!userId) return;
    setSavingId(job.discovered_job_id);
    try {
      await saveDiscoveredJob(userId, job.discovered_job_id);
      setSavedIds((current) => new Set(current).add(job.discovered_job_id));
    } catch (err) {
      setError(err?.message || "Could not save this job right now.");
    } finally {
      setSavingId(null);
    }
  };

  // Step 6/15: the backend just confirmed (at click-time) that this posting
  // is gone — patch it in place so the card/drawer switch to "Job Closed"
  // immediately, without waiting for the next full reload (which will also
  // simply omit it, per Step 7's active-listing exclusion).
  const patchJobExpired = (job, result) => ({
    ...job,
    availability_status: "EXPIRED",
    availability_reason: result?.availability_reason || job.availability_reason,
    action_label: "Job Closed",
    can_apply: false,
  });

  const handleJobExpired = (discoveredJobId, result) => {
    setJobs((current) => current.map((job) => (
      job.discovered_job_id === discoveredJobId ? patchJobExpired(job, result) : job
    )));
    setDetailsJob((current) => (
      current && current.discovered_job_id === discoveredJobId ? patchJobExpired(current, result) : current
    ));
    setNotice("This job just closed and is no longer accepting applications.");
  };

  const handleOptimize = async (job) => {
    if (!userId) return;
    setSavingId(job.discovered_job_id);
    try {
      const result = await saveDiscoveredJob(userId, job.discovered_job_id, { startWorkflow: false });
      setSavedIds((current) => new Set(current).add(job.discovered_job_id));
      const jobId = result.job_id;
      startAtsForSelectedJob(jobId);
      sessionStorage.setItem("atsSelectedJobId", String(jobId));
      writeFreshSelectedJobIntent(userId, jobId);
      navigate(`/optimizer/${jobId}`, {
        state: { job_id: Number(jobId), title: job.title, company: job.company, job_url: job.direct_url, source: "User Selected Job", atsEntryMode: ATS_ENTRY_MODE.START_SELECTED_JOB },
      });
    } catch (err) {
      setError(err?.message || "Could not open the ATS Optimizer for this job right now.");
    } finally {
      setSavingId(null);
    }
  };

  const summaryCards = [
    { label: "Recently Detected", value: summary?.recently_detected ?? 0, icon: "updatedJobs", tone: "blue" },
    { label: "Companies Monitored", value: summary?.companies_monitored ?? 0, icon: "dashboard", tone: "purple" },
    { label: "Strong Matches", value: summary?.strong_matches ?? 0, icon: "matches", tone: "green" },
    { label: "Sources Checked", value: summary?.sources_checked ?? 0, icon: "workflow", tone: "sky" },
    { label: "Last Updated", value: formatLastUpdated(summary?.last_updated), icon: "dashboard", tone: "blue" },
  ];

  const topMatches = jobs.filter((job) => job.match).slice(0, TOP_MATCHES_COUNT);
  const topMatchIds = new Set(topMatches.map((job) => job.discovered_job_id));
  const remainingJobs = jobs.filter((job) => !topMatchIds.has(job.discovered_job_id));

  const renderCard = (job) => (
    <DiscoveredJobCard
      key={job.discovered_job_id}
      job={job}
      userId={userId}
      onSave={() => handleSave(job)}
      onOptimize={() => handleOptimize(job)}
      onDetails={() => setDetailsJob(job)}
      onJobExpired={handleJobExpired}
      saving={savingId === job.discovered_job_id}
      optimizing={savingId === job.discovered_job_id}
      saved={savedIds.has(job.discovered_job_id)}
    />
  );

  return (
    <Page
      title="Updated Job Search"
      subtitle="Browse current openings that match your saved job preference, then save, review, optimize, or apply."
      actions={
        <button className="button primary" onClick={handleRefresh} disabled={refreshing}>
          {refreshing ? "Refreshing…" : "Refresh Jobs"}
        </button>
      }
    >
      <div className="metrics uj-summary">
        {summaryCards.map((card) => <MetricCard key={card.label} {...card} />)}
      </div>

      {!hasResume && (
        <Notice tone="warning">
          Upload a resume to calculate personalized match scores. <Link to="/resume">Go to Resume</Link>
        </Notice>
      )}
      {initialized && !hasPreferences && (
        <Notice tone="warning">
          Save a job title preference to focus this feed on your target role. <Link to="/preferences">Go to Preferences</Link>
        </Notice>
      )}
      {preferenceSummary?.job_title && (
        <p className="uj-preference-context">
          Showing roles matching <b>{preferenceSummary.job_title}</b>
          {preferenceSummary.location ? ` · ${preferenceSummary.location}` : ""}
          {preferenceSummary.work_mode ? ` · ${preferenceSummary.work_mode}` : ""}
          {preferenceSummary.job_type ? ` · ${preferenceSummary.job_type}` : ""}
        </p>
      )}
      <Notice tone="error">{error}</Notice>
      <Notice tone="warning">{notice}</Notice>

      <div className="uj-toolbar">
        <input
          className="uj-search-input"
          type="search"
          placeholder="Search by title, company, or keyword"
          value={filters.search}
          onChange={changeFilter("search")}
        />
        <select value={sort} onChange={(event) => setSort(event.target.value)}>
          {SORTS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </div>

      <div className="uj-filters">
        <input placeholder="Location" value={filters.location} onChange={changeFilter("location")} />
        <select value={filters.work_mode} onChange={changeFilter("work_mode")}>
          <option value="">Any work mode</option>
          {WORK_MODES.map((mode) => <option key={mode} value={mode}>{mode}</option>)}
        </select>
        <input placeholder="Company" value={filters.company} onChange={changeFilter("company")} />
        <select value={filters.date_detected} onChange={changeFilter("date_detected")}>
          <option value="">Any time detected</option>
          <option value="24h">Last 24 hours</option>
          <option value="7d">Last 7 days</option>
        </select>
        <select value={filters.employment_type} onChange={changeFilter("employment_type")}>
          <option value="">Any employment type</option>
          {EMPLOYMENT_TYPES.map((type) => <option key={type} value={type}>{type}</option>)}
        </select>
        <select value={filters.min_score} onChange={changeFilter("min_score")}>
          <option value="">Any match score</option>
          <option value="60">60%+</option>
          <option value="70">70%+</option>
          <option value="85">85%+</option>
        </select>
      </div>

      {sources.some((source) => source.status === "LIVE_ERROR") && (
        <Notice tone="warning">Some job sources are temporarily unavailable. Other sources are still being checked.</Notice>
      )}

      {loading ? (
        <EmptyState title={loadingMessage} text="This usually takes just a few seconds." />
      ) : error && !jobs.length ? (
        <EmptyState title="Unable to refresh some job sources." text="Previously discovered jobs are still available. Try Refresh Jobs again." />
      ) : jobs.length ? (
        <>
          {topMatches.length > 0 && (
            <>
              <div className="results-head">
                <h3>Top Job Matches</h3>
                <p>Strongest resume matches among jobs already relevant to your saved job preference</p>
              </div>
              <div className="job-grid uj-job-grid">{topMatches.map(renderCard)}</div>
            </>
          )}
          {remainingJobs.length > 0 && (
            <>
              <div className="results-head">
                <h3>All Jobs <span>{jobs.length}</span></h3>
                <p>Browse current openings for your saved preference</p>
              </div>
              <div className="job-grid uj-job-grid">{remainingJobs.map(renderCard)}</div>
            </>
          )}
        </>
      ) : (
        <EmptyState
          title="No matching jobs detected yet."
          text="We are checking supported job sources based on your saved preferences. Try broadening your preferences or refresh again later."
        />
      )}

      <JobDetailsDrawer
        job={detailsJob}
        userId={userId}
        onClose={() => setDetailsJob(null)}
        onSave={() => detailsJob && handleSave(detailsJob)}
        onOptimize={() => detailsJob && handleOptimize(detailsJob)}
        onJobExpired={handleJobExpired}
        saving={detailsJob && savingId === detailsJob.discovered_job_id}
        optimizing={detailsJob && savingId === detailsJob.discovered_job_id}
        saved={detailsJob && savedIds.has(detailsJob.discovered_job_id)}
      />
    </Page>
  );
}
