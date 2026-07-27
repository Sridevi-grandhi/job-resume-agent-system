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
  const canSave = hasImportPreview && Boolean(
    form.title.trim() && form.company.trim() && form.description.trim() && form.job_url.trim()
  );

  return <Page title="Selected Jobs" subtitle="Import one direct job URL, review its details, and save it for optimization and matching.">
    <div className="two-col selected-layout">
      <Panel title="4. Import Selected Job" subtitle="Paste a direct job URL. You can complete or correct every field before saving.">
        <form className="form-grid import-job-form" onSubmit={app.saveSelectedJob}>
          <label className="full">
            Job URL
            <input name="job_url" type="url" value={form.job_url} onChange={app.changeSelectedJob} placeholder="https://www.linkedin.com/jobs/view/1234567890/" required />
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
          <label className="full">Job Description<textarea rows="8" name="description" value={form.description} onChange={app.changeSelectedJob} required /></label>
          <button className="button primary full" disabled={!canSave || app.busy === "selected"}>Save Selected Job</button>
          {!canSave && <p className="import-help full">{hasImportPreview ? "Complete the job title, company, description, and normalized URL before saving." : "Fetch and validate the direct job URL before saving."}</p>}
        </form>
      </Panel>
      <Panel title="Saved Selected Jobs" subtitle={`${app.selectedJobs.length} manually selected or imported opportunities`}>
        {app.selectedJobs.length
          ? <div className="stack">{app.selectedJobs.map((job, index) => <JobCard job={job} key={job.id || index} />)}</div>
          : <EmptyState title="No selected jobs" text="Import a direct job URL and complete its job details." />}
      </Panel>
    </div>
  </Page>;
}
