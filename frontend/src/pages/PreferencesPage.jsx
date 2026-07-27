import { useApp } from "../context/AppContext";
import { Page, Panel, Notice } from "../components/Page";
import StatusBadge from "../components/StatusBadge";

const workModeOptions = ["Remote", "Hybrid", "Onsite"];
const jobTypeOptions = ["Full-time", "Part-time", "Contract", "Internship", "Temporary"];
const timezoneOptions = ["America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles"];

function SelectField({ name, label, value, options, onChange }) {
  return <label>{label}<select name={name} value={value} onChange={onChange}>
    <option value="">Select {label.toLowerCase()}</option>
    {options.map((option) => <option key={option} value={option}>{option}</option>)}
  </select></label>;
}

export default function PreferencesPage() {
  const app = useApp();
  const preferences = app.preferences;
  return <Page title="Preferences" subtitle="Update your job preferences and search settings." actions={
    <StatusBadge tone={app.preferencesSaved ? "success" : "warning"}>{app.preferencesSaved ? "Saved" : "Unsaved changes"}</StatusBadge>
  }>
    <Panel title="Job Preferences" subtitle="These details guide job discovery, optimization and matching.">
      <form className="form-grid" onSubmit={app.savePreferences}>
        <label>Job Title<input name="job_title" value={preferences.job_title} placeholder="e.g. Product Designer" onChange={app.changePreferences} required /></label>
        <label>Location<input name="location" value={preferences.location} placeholder="e.g. Chicago, IL" onChange={app.changePreferences} required /></label>
        <label>Experience<input name="experience" value={preferences.experience} placeholder="Example: 5+ years" onChange={app.changePreferences} /></label>
        <SelectField name="work_mode" label="Work Mode" value={preferences.work_mode} options={workModeOptions} onChange={app.changePreferences} />
        <SelectField name="job_type" label="Job Type" value={preferences.job_type} options={jobTypeOptions} onChange={app.changePreferences} />
        <label className="full">Skills<textarea name="skills" value={preferences.skills} placeholder="React, Figma, user research" rows="3" onChange={app.changePreferences} /></label>
        <label>Schedule Time<input name="schedule_time" type="time" value={preferences.schedule_time} onChange={app.changePreferences} /></label>
        <SelectField name="timezone" label="Timezone" value={preferences.timezone} options={timezoneOptions} onChange={app.changePreferences} />
        <div className="form-actions">
          <button type="button" className="button secondary" onClick={app.loadPreferences} disabled={app.busy === "preferences"}>Load Saved Preferences</button>
          <button className="button primary" disabled={app.busy === "preferences"}>Save Preferences</button>
        </div>
      </form>
      <Notice>{app.messages.preferences}</Notice>
    </Panel>
  </Page>;
}
