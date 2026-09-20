/* eslint-disable react-hooks/exhaustive-deps */
import { useEffect } from "react";
import { useApp } from "../context/AppContext";
import { Page, Panel, Notice } from "../components/Page";
import JobCard from "../components/JobCard";
import EmptyState from "../components/EmptyState";

const workModes = ["Remote", "Hybrid", "Onsite"];
const jobTypes = ["Full-time", "Part-time", "Contract", "Internship", "Temporary"];

export default function SelectedJobsPage() {
  const app = useApp();
  const form = app.selectedJobForm;
  const hasImportPreview = app.selectedJobImportReady;
  const importStatus = app.selectedJobImportStatus;
  // Mirrors the backend's own rule exactly (job_routes.add_selected_job):
  // job_url is only required when source ends with " Imported" — a plain
  // manually-entered job (the default "User Selected Job" source) never
  // needs one. Fetching a URL is a convenience, never a precondition —
  // selectedJobImportReady is deliberately NOT part of this check.
  const isImportedSource = form.source.trim().endsWith(" Imported");
  const hasRequiredCoreFields = Boolean(form.title.trim() && form.company.trim() && form.description.trim());
  const canSave = hasRequiredCoreFields && (!isImportedSource || form.job_url.trim());
  const formUntouched = !hasImportPreview && !form.title.trim() && !form.company.trim() && !form.description.trim();

  useEffect(() => {
    app.verifyLatestResume();
    app.loadSelectedJobs();
  }, [app.currentUser?.user_id]);

  const removeJob = (job) => {
    if (window.confirm("Are you sure you want to remove this selected job?")) {
      app.removeSelectedJob(job.id);
    }
  };

  return <Page className="selected-jobs-page" title="Selected Jobs" subtitle="Import or save a complete individual job. Optimization and both match scores run automatically after a successful save.">
    <div className="two-col selected-layout">
      <Panel className="selected-import-panel" title="Import Selected Job" subtitle="Paste a direct job URL. You can complete or correct every field before saving.">
        <form className="form-grid import-job-form" onSubmit={app.saveSelectedJob}>
          <label className="full">
            Job URL
            <input name="job_url" type="url" value={form.job_url} onChange={app.changeSelectedJob} placeholder="https://www.linkedin.com/jobs/view/1234567890/" required={isImportedSource} />
          </label>
          <div className="import-fetch-actions">
            <button type="button" className="button secondary" onClick={app.previewSelectedJobUrl} disabled={app.busy === "selected-import"}>
              {app.busy === "selected-import" ? "Fetching…" : "Fetch Job Details"}
            </button>
            <button type="button" className="button secondary" onClick={app.clearSelectedJob}>Clear</button>
          </div>
          <Notice>{app.messages.selected}</Notice>
          {importStatus && <div className={`import-field-status full ${importStatus.requires_manual_details ? "warning" : "success"}`}>
            <p><b>Status:</b> {importStatus.status === "complete" ? "Full extraction" : importStatus.status === "partial" ? "Partial extraction" : "Manual completion required"}</p>
            <p><b>Fetched:</b> {importStatus.fields_fetched?.join(", ") || "URL only"}</p>
            <p><b>Missing:</b> {importStatus.fields_missing?.join(", ") || "None"}</p>
            {importStatus.blocked_reason && <p><b>Provider status:</b> {importStatus.blocked_reason}</p>}
          </div>}

          <label>Job Title<input name="title" value={form.title} onChange={app.changeSelectedJob} required /></label>
          <label>Company<input name="company" value={form.company} onChange={app.changeSelectedJob} required /></label>
          <label>Location<input name="location" value={form.location} onChange={app.changeSelectedJob} /></label>
          <label>
            Work Mode
            <select name="work_mode" value={form.work_mode} onChange={app.changeSelectedJob}>
              {workModes.map((mode) => <option key={mode}>{mode}</option>)}
            </select>
          </label>
          <label>
            Job Type
            <select name="job_type" value={form.job_type} onChange={app.changeSelectedJob}>
              {jobTypes.map((type) => <option key={type}>{type}</option>)}
            </select>
          </label>
          <label>Source<input name="source" value={form.source} onChange={app.changeSelectedJob} required /></label>
          <label className="full">Skills Required<input name="skills_required" value={form.skills_required} onChange={app.changeSelectedJob} /></label>
          <label className="full">Job Description<textarea className="job-description-textarea" rows="8" name="description" value={form.description} onChange={app.changeSelectedJob} required /></label>
          <button className="button primary full" disabled={!canSave || app.busy === "selected"}>Save Selected Job</button>
          <p className="import-help full">
            {canSave
              ? "Ready to save."
              : formUntouched
                ? "Fetch the job details or complete the required fields manually."
                : "Complete the required job details before saving."}
          </p>
        </form>
      </Panel>
      <Panel
        className="selected-saved-panel"
        title="Saved Selected Jobs"
        subtitle={`${app.selectedJobs.length} manually selected or imported opportunities`}
        actions={<button className="button secondary" onClick={app.loadSelectedJobs} disabled={app.busy === "selected-load"}>{app.busy === "selected-load" ? "Loading..." : "Load Selected Jobs"}</button>}
      >
        <Notice>{app.messages.selected}</Notice>
        <div className="selected-jobs-scroll">
          {app.selectedJobs.length
            ? <div className="stack">{app.selectedJobs.map((job, index) => <JobCard job={job} key={job.id || index} selected showMatchStatus onRemove={() => removeJob(job)} removing={String(app.deletingJobId) === String(job.id)} />)}</div>
            : <EmptyState title="No selected jobs" text="Import a direct job URL and complete its job details." />}
        </div>
      </Panel>
    </div>
  </Page>;
}
