/* eslint-disable react-hooks/exhaustive-deps */
import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { useApp } from "../context/AppContext";
import { Page, Panel, Notice } from "../components/Page";
import EmptyState from "../components/EmptyState";
import { getAtsDownloadUrl, getJobDetail } from "../api";

const genericSearchSources = new Set([
  "LinkedIn Jobs",
  "Indeed",
  "Dice",
  "Google Jobs",
  "ZipRecruiter",
]);

const PIPELINE_STEPS = [
  { key: "PENDING", label: "Saved" },
  { key: "ANALYZING_ORIGINAL", label: "Analyzing Original Resume" },
  { key: "GENERATING_RESUME", label: "Generating ATS Resume" },
  { key: "VALIDATING_RESUME", label: "Validating Resume" },
  { key: "ANALYZING_OPTIMIZED", label: "Calculating Optimized Match" },
  { key: "COMPLETED", label: "Completed" },
];

const STEP_ORDER = {
  PENDING: 0,
  WAITING_FOR_RESUME: 0,
  INCOMPLETE_JOB: 0,
  ANALYZING_ORIGINAL: 1,
  GENERATING_RESUME: 2,
  VALIDATING_RESUME: 3,
  VALIDATION_FAILED: 3,
  ANALYZING_OPTIMIZED: 4,
  COMPLETED: 5,
  FAILED: -1,
};

const WIZARD_STEPS = [
  { id: 1, label: "Select Job" },
  { id: 2, label: "Analyze Requirements" },
  { id: 3, label: "Confirm Skills" },
  { id: 4, label: "Review Evidence" },
  { id: 5, label: "Generate Resume" },
  { id: 6, label: "Validate Resume" },
  { id: 7, label: "Compare Scores" },
];

const EVIDENCE_TYPES = [
  "Current employment",
  "Previous employment",
  "Professional project",
  "Personal project",
  "Academic project",
  "Certification",
  "Training/coursework",
  "Learning only",
];

const EXPERIENCE_LEVELS = ["Beginner", "Intermediate", "Advanced"];
const EXPERIENCE_RANGES = ["Less than 6 months", "6-12 months", "1-2 years", "2-4 years", "4+ years"];

const EMPLOYMENT_EVIDENCE_TYPES = new Set(["Current employment", "Previous employment"]);
const PROJECT_EVIDENCE_TYPES = new Set(["Professional project", "Personal project", "Academic project"]);

const NEW_EMPLOYMENT_SENTINEL = "__new__";

const FIELD_SPECS = {
  employer_name: { label: "Employer name", type: "text" },
  role_title: { label: "Job title", type: "text" },
  start_date: { label: "Start date", type: "text", placeholder: "e.g. 2024-01" },
  end_date: { label: "End date", type: "text", placeholder: "e.g. 2025-03" },
  currently_using: { label: "Currently employed", type: "checkbox" },
  project_name: { label: "Project name", type: "text" },
  work_completed: { label: "What was built", type: "textarea" },
  certification_name: { label: "Certification name", type: "text" },
  provider_name: { label: "Issuer", type: "text" },
  experience_level: { label: "Experience level", type: "select", options: EXPERIENCE_LEVELS },
  experience_range: { label: "Approximate experience", type: "select", options: EXPERIENCE_RANGES },
  usage_description: { label: "Describe how you used this skill", type: "textarea" },
  related_technologies: { label: "Related tools or technologies (optional)", type: "text" },
  supporting_context: { label: "Supporting project or context (optional)", type: "text" },
  impact_description: { label: "Any measurable outcome? (optional)", type: "textarea" },
  confirmed_accurate: { label: "I confirm this information is accurate.", type: "checkbox" },
};

// Fields specific to each evidence type, shown ABOVE the shared fields below.
// Employment types are handled separately (position picker + conditional new-employment fields).
const TYPE_SPECIFIC_FIELDS = {
  "Professional project": [{ key: "project_name" }, { key: "work_completed" }],
  "Personal project": [{ key: "project_name" }, { key: "work_completed" }],
  "Academic project": [{ key: "project_name" }, { key: "work_completed" }],
  Certification: [
    { key: "certification_name" },
    { key: "provider_name", label: "Issuer" },
    { key: "start_date", label: "Date earned (optional)" },
  ],
  "Training/coursework": [
    { key: "project_name", label: "Course or training name" },
    { key: "provider_name", label: "Provider (optional)" },
  ],
};

const NEW_EMPLOYMENT_FIELDS = [
  { key: "employer_name" },
  { key: "role_title" },
  { key: "start_date" },
  { key: "end_date" },
  { key: "currently_using" },
];

// Shown for every evidence type once selected (except Learning only, which needs nothing).
const SHARED_FIELDS = [
  { key: "experience_level" },
  { key: "experience_range" },
  { key: "usage_description" },
  { key: "related_technologies" },
  { key: "supporting_context" },
  { key: "impact_description" },
];

function hasText(value) {
  return Boolean(String(value ?? "").trim());
}

function draftIsValid(draft) {
  const answer = draft.answer;
  if (!answer || answer === "not_possessed" || answer === "learning_only") return true;
  const evidenceType = draft.evidence_type;
  if (!evidenceType) return false;

  const missing = [];
  if (!draft.experience_level) missing.push("experience_level");
  if (!draft.experience_range) missing.push("experience_range");
  if (!hasText(draft.usage_description)) missing.push("usage_description");
  if (!draft.confirmed_accurate) missing.push("confirmed_accurate");

  if (EMPLOYMENT_EVIDENCE_TYPES.has(evidenceType) && draft.is_new_employment) {
    if (!hasText(draft.employer_name)) missing.push("employer_name");
    if (!hasText(draft.role_title)) missing.push("role_title");
    if (!hasText(draft.start_date)) missing.push("start_date");
    if (!draft.currently_using && !hasText(draft.end_date)) missing.push("end_date");
  } else if (PROJECT_EVIDENCE_TYPES.has(evidenceType)) {
    if (!hasText(draft.project_name)) missing.push("project_name");
    if (!hasText(draft.work_completed)) missing.push("work_completed");
  } else if (evidenceType === "Certification") {
    if (!hasText(draft.certification_name)) missing.push("certification_name");
    if (!hasText(draft.provider_name)) missing.push("provider_name");
  } else if (evidenceType === "Training/coursework") {
    if (!hasText(draft.project_name)) missing.push("project_name");
  }
  return missing.length === 0;
}

function listText(value) {
  if (Array.isArray(value)) return value.length ? value.join(", ") : "—";
  return value || "—";
}

function ScoreCard({ label, score, tone = "neutral" }) {
  return (
    <div className={`ats-score-card ${tone}`}>
      <span>{label}</span>
      <b>{Number.isFinite(Number(score)) ? `${Math.round(Number(score))}%` : "—"}</b>
    </div>
  );
}

const METER_LABELS = {
  resume_quality: "Resume Quality",
  ats_match: "ATS Match",
  keyword_coverage: "Keyword Coverage",
  formatting: "Formatting",
};

function MeterBar({ label, before, after }) {
  return (
    <div className="ats-meter">
      <div className="ats-meter-label">
        <span>{label}</span>
        <span>{Math.round(before)}% → <b>{Math.round(after)}%</b></span>
      </div>
      <div className="ats-meter-track">
        <div className="ats-meter-fill before" style={{ width: `${Math.min(100, before)}%` }} />
      </div>
      <div className="ats-meter-track">
        <div className="ats-meter-fill after" style={{ width: `${Math.min(100, after)}%` }} />
      </div>
    </div>
  );
}

function ImprovementReportPanel({ report }) {
  if (!report) return null;
  const meters = report.meters || {};
  return (
    <Panel title="AI Resume Improvement Report">
      <div className="ats-improvement-meters">
        {Object.entries(meters).map(([key, value]) => (
          <MeterBar key={key} label={METER_LABELS[key] || key} before={value.before} after={value.after} />
        ))}
      </div>
      <div className="document-grid">
        <Panel title="What Changed">
          <p><b>New skills added (verified):</b> {listText(report.new_skills_added)}</p>
          <p><b>Keywords added:</b> {listText(report.keywords_added)}</p>
          <p><b>Sections improved:</b> {listText(report.sections_improved)}</p>
          <p><b>Bullets rewritten:</b> {report.bullets_rewritten ?? 0}</p>
        </Panel>
        <Panel title="Still Missing">
          <p>{listText(report.missing_skills_remaining)}</p>
          {!report.missing_skills_remaining?.length && <p className="ats-skill-meta">No mandatory skills remain missing.</p>}
        </Panel>
      </div>
      <Panel title="Why your score changed">
        <ul className="ats-list">
          {(report.explanation || []).map((reason, index) => <li key={index}>{reason}</li>)}
        </ul>
      </Panel>
      {report.ats_recommendations?.length > 0 && (
        <Panel title="ATS Recommendations">
          <ul className="ats-list">
            {report.ats_recommendations.map((item, index) => <li key={index}>{item}</li>)}
          </ul>
        </Panel>
      )}
    </Panel>
  );
}

function PipelineProgress({ status }) {
  const current = STEP_ORDER[status] ?? 0;
  const failed = status === "FAILED" || status === "VALIDATION_FAILED";
  return (
    <ol className="ats-pipeline">
      {PIPELINE_STEPS.map((step, index) => {
        const done = !failed && current >= index;
        const active = !failed && current === index && status !== "COMPLETED";
        return (
          <li key={step.key} className={`${done ? "done" : ""} ${active ? "active" : ""} ${failed && index === Math.max(current, 0) ? "failed" : ""}`}>
            {step.label}
          </li>
        );
      })}
    </ol>
  );
}

function WizardSteps({ step, maxUnlockedStep, onSelect }) {
  return (
    <ol className="ats-wizard-steps">
      {WIZARD_STEPS.map((s) => {
        const unlocked = s.id <= maxUnlockedStep;
        const done = s.id < step;
        const active = s.id === step;
        return (
          <li
            key={s.id}
            className={`${done ? "done" : ""} ${active ? "active" : ""} ${!unlocked ? "locked" : ""}`}
            onClick={() => unlocked && onSelect(s.id)}
          >
            <span className="ats-wizard-step-index">{s.id}</span>
            {s.label}
          </li>
        );
      })}
    </ol>
  );
}

function evidenceStatusLabel(draft) {
  if (draft.evidence_status === "VERIFIED_FROM_RESUME") return "Verified from resume";
  if (!draft.answer) return "Evidence required";
  if (draft.answer === "not_possessed") return "Marked as not possessed";
  if (draft.answer === "learning_only") return "Learning only";
  return draftIsValid(draft) ? "Evidence saved" : "Incomplete evidence";
}

function EvidenceField({ fieldKey, draft, onChange, labelOverride }) {
  const spec = FIELD_SPECS[fieldKey];
  if (!spec) return null;
  const label = labelOverride || spec.label;
  if (spec.type === "checkbox") {
    return (
      <label className="ats-skill-checkbox">
        <input
          type="checkbox"
          checked={Boolean(draft[fieldKey])}
          onChange={(e) => onChange({ ...draft, [fieldKey]: e.target.checked })}
        />
        {label}
      </label>
    );
  }
  if (spec.type === "select") {
    return (
      <label>
        {label}
        <select value={draft[fieldKey] || ""} onChange={(e) => onChange({ ...draft, [fieldKey]: e.target.value })}>
          <option value="">Select…</option>
          {spec.options.map((option) => (
            <option key={option} value={option}>{option}</option>
          ))}
        </select>
      </label>
    );
  }
  if (spec.type === "textarea") {
    return (
      <label className="ats-skill-evidence-description">
        {label}
        <textarea
          rows={2}
          value={draft[fieldKey] || ""}
          onChange={(e) => onChange({ ...draft, [fieldKey]: e.target.value })}
        />
      </label>
    );
  }
  return (
    <label>
      {label}
      <input
        type="text"
        placeholder={spec.placeholder}
        value={draft[fieldKey] ?? ""}
        onChange={(e) => onChange({ ...draft, [fieldKey]: e.target.value })}
      />
    </label>
  );
}

function EmploymentPositionPicker({ draft, onChange, resumeEmployments }) {
  const value = draft.is_new_employment ? NEW_EMPLOYMENT_SENTINEL : (draft.employer_name || "");
  return (
    <label className="ats-skill-evidence-description">
      Which existing resume position should this skill be added to? (optional)
      <select
        value={value}
        onChange={(e) => {
          const selected = e.target.value;
          if (selected === NEW_EMPLOYMENT_SENTINEL) {
            onChange({ ...draft, is_new_employment: true, employer_name: "", role_title: "", start_date: "", end_date: "", currently_using: false });
          } else {
            onChange({ ...draft, is_new_employment: false, employer_name: selected, role_title: "", start_date: "", end_date: "", currently_using: false });
          }
        }}
      >
        <option value="">Unspecified / most recent role</option>
        {(resumeEmployments || []).map((emp) => (
          <option key={emp.index} value={emp.company}>
            {emp.company}{emp.title ? ` — ${emp.title}` : ""}{emp.dates ? ` (${emp.dates})` : ""}
          </option>
        ))}
        <option value={NEW_EMPLOYMENT_SENTINEL}>New employment not present in resume</option>
      </select>
    </label>
  );
}

function SkillModulesPicker({ skill, modules, draft, onChange }) {
  if (!modules?.length) return null;
  const selected = new Set((draft.skill_modules || "").split(",").map((m) => m.trim()).filter(Boolean));
  const toggle = (module) => {
    const next = new Set(selected);
    if (next.has(module)) next.delete(module);
    else next.add(module);
    onChange({ ...draft, skill_modules: Array.from(next).join(", ") });
  };
  return (
    <div className="ats-skill-evidence-description">
      <span>Which {skill} modules or features did you use?</span>
      <div className="ats-module-checkbox-group">
        {modules.map((module) => (
          <label key={module} className="ats-skill-checkbox">
            <input type="checkbox" checked={selected.has(module)} onChange={() => toggle(module)} />
            {module}
          </label>
        ))}
      </div>
    </div>
  );
}

function SkillConfirmationCard({ skillInfo, draft, onChange, isPreferred, resumeEmployments }) {
  const valid = draftIsValid(draft);
  const evidenceType = draft.evidence_type;
  const isEmployment = EMPLOYMENT_EVIDENCE_TYPES.has(evidenceType);
  const typeFields = TYPE_SPECIFIC_FIELDS[evidenceType] || [];
  const interview = skillInfo.interview || {};
  const prompts = interview.prompts || {};
  const resumeStatus = skillInfo.resume_status || skillInfo.status || "MISSING";
  const foundInResume = Boolean(skillInfo.found ?? skillInfo.found_in_resume);
  const isVerified = resumeStatus === "VERIFIED";
  const sharedFieldLabels = {
    usage_description: prompts.description,
    related_technologies: prompts.related_technologies,
    impact_description: prompts.outcome,
  };
  if (isVerified) {
    return (
      <div className="ats-skill-card ats-skill-card-verified">
        <div className="ats-skill-card-header">
          <b>{skillInfo.skill}</b>
          <span className="badge badge-verified">Verified from resume</span>
          <span className="ats-skill-meta">{skillInfo.category}</span>
        </div>
        <p className="ats-skill-meta">Found in uploaded resume: <b>Yes</b></p>
        <p className="ats-skill-meta">Status: <b>VERIFIED</b></p>
        {skillInfo.evidence_snippets?.map((snippet, index) => (
          <p className="ats-evidence-snippet" key={`${skillInfo.skill}-${index}`}>{snippet}</p>
        ))}
      </div>
    );
  }
  return (
    <div className="ats-skill-card">
      <div className="ats-skill-card-header">
        <b>{skillInfo.skill}</b>
        <span className={`badge ${isPreferred ? "badge-preferred" : "badge-mandatory"}`}>
          {isPreferred ? "Preferred" : "Mandatory"}
        </span>
        <span className="ats-skill-meta">{skillInfo.category}</span>
      </div>
      {skillInfo.why_it_matters && <p className="ats-skill-meta">{skillInfo.why_it_matters}</p>}
      <p className="ats-skill-meta">
        Found in uploaded resume: <b>{foundInResume ? `Yes — ${skillInfo.evidence_strength || resumeStatus} evidence` : "No"}</b>
      </p>
      <p className="ats-skill-meta">Status: <b>{resumeStatus === "MISSING" ? evidenceStatusLabel(draft) : resumeStatus}</b></p>

      <p className="ats-skill-question">Do you currently have this skill?</p>
      <div className="ats-skill-answer-buttons">
        <button
          type="button"
          className={`button ${draft.answer === "has_it" ? "primary" : "secondary"}`}
          onClick={() => onChange({ ...draft, answer: "has_it", evidence_type: draft.evidence_type || "" })}
        >
          Yes
        </button>
        <button
          type="button"
          className={`button ${draft.answer === "not_possessed" ? "primary" : "secondary"}`}
          onClick={() => onChange({ ...draft, answer: "not_possessed" })}
        >
          No, I do not have this skill
        </button>
      </div>

      {draft.answer === "has_it" && (
        <div className="ats-skill-form">
          <label>
            {prompts.where_used || "How did you gain this skill?"}
            <select
              value={evidenceType || ""}
              onChange={(e) => {
                const newType = e.target.value;
                if (newType === "Learning only") {
                  onChange({ ...draft, answer: "learning_only", evidence_type: "" });
                } else {
                  onChange({ ...draft, evidence_type: newType, is_new_employment: false, employer_name: "", role_title: "" });
                }
              }}
            >
              <option value="">Select…</option>
              {EVIDENCE_TYPES.map((type) => (
                <option key={type} value={type}>{type}</option>
              ))}
            </select>
          </label>

          {isEmployment && (
            <EmploymentPositionPicker draft={draft} onChange={onChange} resumeEmployments={resumeEmployments} />
          )}
          {isEmployment && draft.is_new_employment && NEW_EMPLOYMENT_FIELDS.map(({ key, label }) => (
            <EvidenceField key={key} fieldKey={key} draft={draft} onChange={onChange} labelOverride={label} />
          ))}

          {typeFields.map(({ key, label }) => (
            <EvidenceField key={key} fieldKey={key} draft={draft} onChange={onChange} labelOverride={label} />
          ))}

          {evidenceType && (
            <SkillModulesPicker skill={skillInfo.skill} modules={interview.modules} draft={draft} onChange={onChange} />
          )}

          {evidenceType && SHARED_FIELDS.map(({ key, label }) => (
            <EvidenceField key={key} fieldKey={key} draft={draft} onChange={onChange} labelOverride={label || sharedFieldLabels[key]} />
          ))}
          {evidenceType && (
            <EvidenceField fieldKey="confirmed_accurate" draft={draft} onChange={onChange} />
          )}

          {evidenceType && !valid && (
            <p className="ats-skill-error">
              Add the required details above — a checkbox alone is not evidence and will not increase your score.
            </p>
          )}
        </div>
      )}
      {draft.answer === "learning_only" && (
        <p className="ats-skill-meta">
          This will appear only under “Currently Learning” — it will not be shown as professional experience,
          and receives very low scoring credit.
        </p>
      )}
    </div>
  );
}

export default function AtsAnalyzerPage() {
  const { jobId: routeJobId } = useParams();
  const {
    currentUser,
    jobs,
    latestResume,
    atsResult,
    atsSelectedJobId,
    setAtsSelectedJobId,
    analyzeAndOptimize,
    retryAtsWorkflow,
    loadAtsResultForJob,
    missingSkillsGroups,
    confirmedSkills,
    loadMissingSkills,
    loadSkillConfirmations,
    saveConfirmedSkills,
    deleteConfirmedSkill,
    messages,
    busy,
    verifyLatestResume,
    loadPreferences,
  } = useApp();

  const [step, setStep] = useState(1);
  const [viewMode, setViewMode] = useState("optimized");
  const [activeTab, setActiveTab] = useState("scores");
  const [authoritativeJob, setAuthoritativeJob] = useState(null);
  const [riskAcknowledged, setRiskAcknowledged] = useState(false);
  const [skillDrafts, setSkillDrafts] = useState({});

  useEffect(() => {
    if (!routeJobId || !currentUser?.user_id) return;
    setAtsSelectedJobId(String(routeJobId));
    sessionStorage.setItem("atsSelectedJobId", String(routeJobId));
    verifyLatestResume();
    loadPreferences();
    getJobDetail(currentUser.user_id, routeJobId)
      .then(setAuthoritativeJob)
      .catch(() => setAuthoritativeJob(null));
  }, [routeJobId, currentUser?.user_id]);

  const analyzableJobs = useMemo(
    () =>
      [
        ...(authoritativeJob ? [authoritativeJob] : []),
        ...(jobs || []).filter((job) => String(job.id) !== String(authoritativeJob?.id)),
      ].filter((job) => {
        if (genericSearchSources.has(job.source)) return false;
        if (!job.description || String(job.description).trim().length < 40) return false;
        const isSelected = job.source === "User Selected Job" || job.source?.endsWith(" Imported");
        if (!job.job_url && !isSelected) return false;
        return true;
      }),
    [jobs, authoritativeJob]
  );

  const selectedJob =
    analyzableJobs.find((job) => String(job.id) === String(atsSelectedJobId)) ||
    (!routeJobId ? analyzableJobs[0] : null) ||
    null;

  useEffect(() => {
    if (selectedJob?.id) {
      loadAtsResultForJob(selectedJob.id);
      loadSkillConfirmations(selectedJob.id);
    }
  }, [selectedJob?.id]);

  // Reset wizard progress when the selected job changes. Comparing against the
  // previous render's job id (instead of a useEffect) avoids an extra cascading render.
  const [stepResetForJobId, setStepResetForJobId] = useState(selectedJob?.id);
  if (selectedJob?.id !== stepResetForJobId) {
    setStepResetForJobId(selectedJob?.id);
    setStep(1);
  }

  // Seed drafts from previously-saved evidence once loaded, same pattern as above.
  const [seededConfirmedSkills, setSeededConfirmedSkills] = useState(null);
  if (confirmedSkills?.length && confirmedSkills !== seededConfirmedSkills) {
    setSeededConfirmedSkills(confirmedSkills);
    setSkillDrafts((current) => {
      const next = { ...current };
      confirmedSkills.forEach((row) => {
        const answer =
          row.evidence_status === "LEARNING_ONLY" ? "learning_only" :
          row.evidence_status === "REJECTED" ? "not_possessed" :
          row.confirmed ? "has_it" : undefined;
        next[row.skill] = {
          id: row.id,
          answer,
          evidence_status: row.evidence_status,
          evidence_type: row.evidence_type || "",
          is_new_employment: Boolean(row.employer_name) && Boolean(row.role_title || row.start_date),
          employer_name: row.employer_name || "",
          role_title: row.role_title || "",
          project_name: row.project_name || "",
          certification_name: row.certification_name || "",
          provider_name: row.provider_name || "",
          start_date: row.start_date || "",
          end_date: row.end_date || "",
          currently_using: Boolean(row.currently_using),
          experience_level: row.experience_level || "",
          experience_range: row.experience_range || "",
          usage_description: row.usage_description || "",
          work_completed: row.work_completed || "",
          related_technologies: row.related_technologies || "",
          supporting_context: row.supporting_context || "",
          impact_description: row.impact_description || "",
          skill_modules: row.skill_modules || "",
          confirmed_accurate: Boolean(row.confirmed_accurate),
        };
      });
      return next;
    });
  }

  const disabledReason = !latestResume
    ? "Upload a resume before saving and optimizing jobs."
    : !selectedJob
      ? "Select or import one complete job description before running optimize-and-match."
      : "";

  const result = atsResult;
  const jobInfo = result?.job || selectedJob;
  const status = result?.workflow_status;
  const originalScore = result?.original_match_percentage ?? result?.original_match_score ?? result?.original_analysis?.match_percentage;
  const optimizedScore = result?.optimized_match_percentage ?? result?.optimized_match_score ?? result?.optimized_analysis?.match_percentage;
  const improvement = result?.improvement_percentage ?? result?.score_improvement;
  const matchedBefore = result?.original_matched_keywords || result?.original_analysis?.matched_keywords;
  const missingBefore = result?.original_missing_keywords || result?.original_analysis?.missing_keywords;
  const matchedAfter = result?.optimized_matched_keywords || result?.optimized_analysis?.matched_keywords;
  const missingAfter = result?.remaining_missing_keywords || result?.optimized_missing_keywords || result?.optimized_analysis?.missing_keywords;
  const newlyMatched = result?.newly_matched_keywords || [];
  const unsupported = result?.unsupported_keywords_not_added || [];
  const warnings = result?.validation_warnings || result?.final_validation?.warnings || [];
  const generatedPlain = result?.generated_resume?.plain_text || result?.generated_resume_plain || result?.generated_resume;
  const generatedPreview = result?.generated_resume?.formatted_preview || result?.generated_resume_preview_markdown || generatedPlain;
  const canRetry = ["FAILED", "VALIDATION_FAILED"].includes(status);
  const isComplete = status === "COMPLETED";
  const scoreStatus = result?.score_status || result?.optimized_analysis?.score_status || result?.original_analysis?.score_status;
  const roleAlignment = result?.role_alignment || result?.original_analysis?.role_alignment;
  const resumeRoles = result?.resume_role_families || result?.original_analysis?.resume_role_families || [];
  const targetRole = result?.target_job_role || result?.original_analysis?.job_role_family;
  const safeToApply = result?.safe_to_apply ?? result?.optimized_analysis?.safe_to_apply ?? false;
  const missingMandatory = result?.critical_missing_skills || result?.original_analysis?.missing_mandatory_skills || [];
  const matchedCore = result?.matched_core_skills || result?.original_analysis?.matched_core_skills || [];
  const matchedTransferable = result?.matched_transferable_skills || result?.original_analysis?.matched_transferable_skills || [];
  const scoreCap = result?.gap_analysis?.score_cap ?? result?.original_analysis?.score_cap_applied;
  const scoreCapReason = result?.gap_analysis?.score_cap_reason || result?.original_analysis?.score_cap_reason;
  const majorMismatch = scoreStatus === "NOT_RECOMMENDED" || roleAlignment === "MISMATCH";
  const contentChanged = result?.content_changed;
  const optimizationStatus = result?.optimization_status;
  const whyNot95 = result?.why_not_95;
  const confirmedSkillsUsed = result?.confirmed_skills_used || [];
  const improvementReport = result?.improvement_report;

  const verifiedSkills = missingSkillsGroups?.verified_resume_skills || [];
  const weakSkills = missingSkillsGroups?.weak_or_indirect_skills || [];
  const mandatorySkills = missingSkillsGroups?.missing_mandatory_skills || [];
  const preferredSkills = missingSkillsGroups?.missing_preferred_skills || [];
  const confirmationSkills = useMemo(() => [...weakSkills, ...mandatorySkills, ...preferredSkills], [weakSkills, mandatorySkills, preferredSkills]);
  const missingSkills = confirmationSkills;

  const answeredDrafts = missingSkills.filter((s) => skillDrafts[s.skill]?.answer);
  const allAnsweredValid = answeredDrafts.every((s) => draftIsValid(skillDrafts[s.skill]));
  const allMandatoryAnswered = [...weakSkills.filter((s) => s.requirement_type === "MANDATORY"), ...mandatorySkills].every((s) => skillDrafts[s.skill]?.answer);
  const canSaveConfirmations = answeredDrafts.length > 0 && allAnsweredValid;
  const canContinueToOptimization = Boolean(missingSkillsGroups) && allMandatoryAnswered && allAnsweredValid;

  const summaryCounts = useMemo(() => {
    const counts = {
      verifiedFromResume: verifiedSkills.length,
      userConfirmedWithEvidence: 0,
      learningOnly: 0,
      notPossessed: 0,
      incomplete: 0,
    };
    confirmedSkills.forEach((row) => {
      if (["USER_CONFIRMED_PROFESSIONAL", "USER_CONFIRMED_PROJECT", "USER_CONFIRMED_CERTIFICATION", "USER_CONFIRMED_TRAINING"].includes(row.evidence_status)) {
        counts.userConfirmedWithEvidence += 1;
      } else if (row.evidence_status === "LEARNING_ONLY") {
        counts.learningOnly += 1;
      } else if (row.evidence_status === "REJECTED") {
        counts.notPossessed += 1;
      }
    });
    missingSkills.forEach((s) => {
      const draft = skillDrafts[s.skill];
      if (draft?.answer === "has_it" && !draftIsValid(draft)) counts.incomplete += 1;
    });
    return counts;
  }, [verifiedSkills, confirmedSkills, missingSkills, skillDrafts]);

  const updateDraft = (skill, draft) => setSkillDrafts((current) => ({ ...current, [skill]: draft }));

  const handleSaveConfirmations = async () => {
    const items = answeredDrafts.map((s) => {
      const draft = skillDrafts[s.skill];
      return {
        skill: s.skill,
        requirement_type: s.importance,
        answer: draft.answer,
        evidence_type: draft.answer === "has_it" ? draft.evidence_type || null : null,
        is_new_employment: Boolean(draft.is_new_employment),
        employer_name: draft.employer_name || null,
        role_title: draft.role_title || null,
        start_date: draft.start_date || null,
        end_date: draft.end_date || null,
        currently_using: Boolean(draft.currently_using),
        project_name: draft.project_name || null,
        certification_name: draft.certification_name || null,
        provider_name: draft.provider_name || null,
        experience_level: draft.experience_level || null,
        experience_range: draft.experience_range || null,
        usage_description: draft.usage_description || null,
        work_completed: draft.work_completed || null,
        related_technologies: draft.related_technologies || null,
        supporting_context: draft.supporting_context || null,
        impact_description: draft.impact_description || null,
        skill_modules: draft.skill_modules || null,
        confirmed_accurate: Boolean(draft.confirmed_accurate),
      };
    });
    if (!items.length) return;
    await saveConfirmedSkills(selectedJob.id, items);
  };

  const maxUnlockedStep = useMemo(() => {
    if (isComplete) return 7;
    if (["VALIDATING_RESUME", "VALIDATION_FAILED", "ANALYZING_OPTIMIZED"].includes(status)) return 6;
    if (["GENERATING_RESUME"].includes(status)) return 5;
    if (canContinueToOptimization) return 5;
    if (missingSkillsGroups) return 3;
    if (selectedJob) return 2;
    return 1;
  }, [selectedJob, missingSkillsGroups, canContinueToOptimization, status, isComplete]);

  return (
    <Page
      title="ATS Optimizer & Match Analysis"
      subtitle="Analyze requirements, confirm your current skills, then generate and compare truthful ATS scores."
      actions={
        canRetry && result?.workflow_id ? (
          <button
            className="button secondary"
            disabled={busy === "ats"}
            onClick={() => retryAtsWorkflow(result.workflow_id)}
          >
            Retry
          </button>
        ) : null
      }
    >
      <Notice>{messages.ats || messages["ats-requirements"] || messages["ats-skills"] || disabledReason}</Notice>

      <WizardSteps step={step} maxUnlockedStep={maxUnlockedStep} onSelect={setStep} />

      {step === 1 && (
        <Panel title="Step 1 · Select Job">
          <div className="ats-job-picker">
            <label>
              Select job
              <select
                value={selectedJob ? String(selectedJob.id) : ""}
                onChange={(e) => setAtsSelectedJobId(e.target.value)}
              >
                {!analyzableJobs.length && <option value="">No analyzable jobs</option>}
                {analyzableJobs.map((job) => (
                  <option key={job.id} value={job.id}>
                    {job.title} · {job.company}
                  </option>
                ))}
              </select>
            </label>
            {jobInfo && (
              <div className="ats-job-meta">
                <p><b>{jobInfo.title}</b></p>
                <p>{jobInfo.company} · {jobInfo.location || "Location not listed"}</p>
                <p>Source: {jobInfo.source || "—"}</p>
                {jobInfo.job_url && <p><a href={jobInfo.job_url} target="_blank" rel="noreferrer">View Original Job</a></p>}
                <p>Resume: {result?.resume_file_name || latestResume?.file_name || "—"}</p>
              </div>
            )}
          </div>
          <button
            className="button primary"
            disabled={Boolean(disabledReason)}
            onClick={() => setStep(2)}
          >
            Continue
          </button>
        </Panel>
      )}

      {step === 2 && (
        <Panel title="Step 2 · Analyze Job Requirements">
          <button
            className="button primary"
            disabled={!selectedJob || busy === "ats-requirements"}
            onClick={() => loadMissingSkills(selectedJob.id).then((d) => d && setStep(3))}
          >
            {busy === "ats-requirements" ? "Analyzing…" : "Analyze Job Description"}
          </button>
          {missingSkillsGroups && (
            <div className="ats-requirements-summary">
              <p><b>Verified from resume:</b> {verifiedSkills.length} skill(s)</p>
              <p><b>Weak or indirect:</b> {weakSkills.length} skill(s)</p>
              <p><b>Missing — mandatory:</b> {mandatorySkills.length} skill(s)</p>
              <p><b>Missing — preferred:</b> {preferredSkills.length} skill(s)</p>
              <h4>Verified in Resume</h4>
              <ul className="ats-list">
                {verifiedSkills.map((s) => (
                  <li key={s.skill}>{s.skill} <span className="ats-skill-meta">({s.category} · {s.match_type} · {s.evidence_strength})</span>{s.evidence_snippets?.[0] && <small className="ats-evidence-snippet">{s.evidence_snippets[0]}</small>}</li>
                ))}
                {!verifiedSkills.length && <li>No requirements are clearly supported by the resume yet.</li>}
              </ul>
              <h4>Weak or Indirect Evidence — Confirmation Recommended</h4>
              <ul className="ats-list">
                {weakSkills.map((s) => <li key={s.skill}>{s.skill} <span className="ats-skill-meta">({s.match_type} · {s.evidence_strength})</span></li>)}
                {!weakSkills.length && <li>No weak or indirect evidence requires confirmation.</li>}
              </ul>
              <h4>Missing — Evidence Required</h4>
              <ul className="ats-list">
                {mandatorySkills.map((s) => <li key={s.skill}>{s.skill} <span className="ats-skill-meta">({s.category})</span></li>)}
                {!mandatorySkills.length && <li>No mandatory skills are missing.</li>}
              </ul>
              <h4>Optional / Preferred</h4>
              <ul className="ats-list">
                {preferredSkills.map((s) => <li key={s.skill}>{s.skill} <span className="ats-skill-meta">({s.category})</span></li>)}
                {!preferredSkills.length && <li>No preferred skills are missing.</li>}
              </ul>
              <button className="button primary" onClick={() => setStep(3)}>Continue to Skill Confirmation</button>
            </div>
          )}
        </Panel>
      )}

      {step === 3 && (
        <Panel title="Step 3 · Confirm Your Current Skills">
          <p className="ats-improvement-note">
            Your uploaded resume may be outdated. Select only the skills you currently possess. You may be
            asked to provide supporting details. Do not select skills you have not used.
          </p>
          {!missingSkillsGroups ? (
            <EmptyState title="Analyze requirements first" text="Go back to Step 2 and analyze the job description." />
          ) : (
            <>
              <h4>Verified in Resume — no action needed</h4>
              <div className="ats-skill-list">
                {verifiedSkills.map((s) => (
                  <div key={s.skill} className="ats-skill-card ats-skill-card-verified">
                    <div className="ats-skill-card-header">
                      <b>{s.skill}</b>
                      <span className="badge badge-verified">Verified from resume</span>
                      <span className="ats-skill-meta">{s.category}</span>
                    </div>
                    <p className="ats-skill-meta">Found in uploaded resume: <b>Yes</b></p>
                    <p className="ats-skill-meta">Status: <b>{s.resume_status || "VERIFIED"}</b></p>
                    <p className="ats-skill-meta">Evidence: {s.evidence_strength}</p>
                    {s.evidence_snippets?.map((snippet, index) => <p className="ats-evidence-snippet" key={`${s.skill}-${index}`}>{snippet}</p>)}
                  </div>
                ))}
                {!verifiedSkills.length && <p className="ats-skill-meta">No requirements are verified from the resume yet.</p>}
              </div>

              <h4>Weak or Indirect Evidence — Confirmation Recommended</h4>
              <div className="ats-skill-list">
                {weakSkills.map((skillInfo) => (
                  <SkillConfirmationCard
                    key={skillInfo.skill}
                    skillInfo={skillInfo}
                    isPreferred={skillInfo.requirement_type === "PREFERRED"}
                    draft={skillDrafts[skillInfo.skill] || {}}
                    onChange={(draft) => updateDraft(skillInfo.skill, draft)}
                    resumeEmployments={missingSkillsGroups?.resume_employments}
                  />
                ))}
                {!weakSkills.length && <p className="ats-skill-meta">No weak or indirect skills need confirmation.</p>}
              </div>

              <h4>Missing — Evidence Required (Mandatory)</h4>
              <div className="ats-skill-list">
                {mandatorySkills.map((skillInfo) => (
                  <SkillConfirmationCard
                    key={skillInfo.skill}
                    skillInfo={skillInfo}
                    draft={skillDrafts[skillInfo.skill] || {}}
                    onChange={(draft) => updateDraft(skillInfo.skill, draft)}
                    resumeEmployments={missingSkillsGroups?.resume_employments}
                  />
                ))}
                {!mandatorySkills.length && <p className="ats-skill-meta">No mandatory skills are missing.</p>}
              </div>

              <h4>Optional / Preferred</h4>
              <div className="ats-skill-list">
                {preferredSkills.map((skillInfo) => (
                  <SkillConfirmationCard
                    key={skillInfo.skill}
                    skillInfo={skillInfo}
                    isPreferred
                    draft={skillDrafts[skillInfo.skill] || {}}
                    onChange={(draft) => updateDraft(skillInfo.skill, draft)}
                    resumeEmployments={missingSkillsGroups?.resume_employments}
                  />
                ))}
                {!preferredSkills.length && <p className="ats-skill-meta">No preferred skills are missing.</p>}
              </div>

              <button
                className="button primary"
                disabled={!canSaveConfirmations || busy === "ats-skills"}
                onClick={handleSaveConfirmations}
              >
                {busy === "ats-skills" ? "Saving…" : "Save Evidence"}
              </button>
              <button
                className="button secondary"
                disabled={!canContinueToOptimization}
                title={!canContinueToOptimization ? "Answer every mandatory skill (evidence, learning only, or I do not have this skill) first." : ""}
                onClick={() => setStep(4)}
              >
                Continue to Resume Generation
              </button>
            </>
          )}
        </Panel>
      )}

      {step === 4 && (
        <Panel title="Step 4 · Review Evidence and Gaps">
          <div className="ats-evidence-summary">
            <p>Verified from resume: <b>{summaryCounts.verifiedFromResume}</b></p>
            <p>User-confirmed with evidence: <b>{summaryCounts.userConfirmedWithEvidence}</b></p>
            <p>Learning only: <b>{summaryCounts.learningOnly}</b></p>
            <p>Not possessed: <b>{summaryCounts.notPossessed}</b></p>
            <p>Incomplete evidence: <b>{summaryCounts.incomplete}</b></p>
          </div>
          <p>Saved evidence:</p>
          <ul className="ats-list">
            {confirmedSkills.length
              ? confirmedSkills.map((row) => (
                  <li key={row.id}>
                    {row.skill} — {row.evidence_status}
                    <button className="text-button" onClick={() => deleteConfirmedSkill(selectedJob.id, row.id)}>Remove Confirmation</button>
                  </li>
                ))
              : <li>No additional skills confirmed — scoring will rely on the uploaded resume only.</li>}
          </ul>
          <p>Still missing (kept out of the optimized resume, will reduce the reachable score):</p>
          <ul className="ats-list">
            {mandatorySkills
              .filter((s) => skillDrafts[s.skill]?.answer === "not_possessed")
              .map((s) => <li key={s.skill}>{s.skill}</li>)}
          </ul>
          <button
            className="button primary"
            disabled={!canContinueToOptimization}
            title={!canContinueToOptimization ? "Answer every mandatory skill first." : ""}
            onClick={() => setStep(5)}
          >
            Continue to Optimization
          </button>
        </Panel>
      )}

      {step === 5 && (
        <Panel title="Step 5 · Generate Optimized Resume">
          <p className="ats-improvement-note">
            Your final score is based on verified resume evidence and the current skills you confirmed.
            Selecting a skill without supporting details will not increase your score.
          </p>
          <button
            className="button primary"
            disabled={Boolean(disabledReason) || busy === "ats"}
            onClick={() => selectedJob && analyzeAndOptimize(selectedJob.id).then(() => setStep(6))}
          >
            {busy === "ats" ? "Processing…" : "Generate Optimized Resume"}
          </button>
          {status && <PipelineProgress status={status} />}
        </Panel>
      )}

      {result && majorMismatch && (
        <section className="ats-major-warning" role="alert">
          <h3>Major role mismatch detected</h3>
          <p>The uploaded resume is primarily aligned with {listText(resumeRoles)}, while this job requires {targetRole || "a different core role"} experience.</p>
          <p>This optimizer will not add unsupported technologies or experience.</p>
          <p>{result.warning || result.original_analysis?.warning_message}</p>
        </section>
      )}

      {step === 6 && (
        <Panel title={`Step 6 · Validate Resume · ${result?.final_validation?.status || "—"}`}>
          {!result ? (
            <EmptyState title="No validation yet" text="Generate the optimized resume in Step 5 first." />
          ) : (
            <div className="document-grid">
              <Panel title="Final Validation">
                <ul className="ats-list">
                  {warnings.map((item, index) => <li key={`w-${index}`}>{item}</li>)}
                  {(result.final_validation?.errors || []).map((item, index) => (
                    <li key={`e-${index}`} className="ats-error">{item}</li>
                  ))}
                  {!warnings.length && !result.final_validation?.errors?.length && <li>No validation warnings.</li>}
                </ul>
              </Panel>
              <Panel title="Technology Validation">
                <p><b>Selected:</b> {listText(result.technology_validation?.selectedTechnologies)}</p>
                <p><b>Rejected:</b> {listText(result.technology_validation?.rejectedTechnologies)}</p>
              </Panel>
              <Panel title="Client Validation">
                <p><b>Domain:</b> {result.client_validation?.detectedClientDomain || "—"}</p>
              </Panel>
            </div>
          )}
          <button className="button primary" disabled={status !== "COMPLETED"} onClick={() => setStep(7)}>
            Continue to Score Comparison
          </button>
        </Panel>
      )}

      {step === 7 && (!result ? (
        <EmptyState
          title="No ATS result yet"
          text="Complete Step 5 (Generate Optimized Resume) first."
        />
      ) : (
        <>
          <Panel title="Step 7 · Compare Scores">
            <div className="ats-score-row">
              <ScoreCard label="Original Resume Match" score={originalScore} />
              <ScoreCard label="Optimized Resume Match" score={optimizedScore} tone="good" />
              <div className="ats-score-card accent">
                <span>Improvement</span>
                <b>{Number.isFinite(Number(improvement)) ? `${Number(improvement) >= 0 ? "+" : ""}${Math.round(Number(improvement))}` : "—"}</b>
              </div>
            </div>

            {optimizationStatus === "NO_MEANINGFUL_CHANGE" && (
              <div className="ats-gap-banner" role="alert">
                The generated resume is identical to the original resume. No unsupported keywords or claims
                were added, so the score could not change. Confirm more current skills with evidence in Step 3
                to unlock further truthful improvements.
              </div>
            )}
            {contentChanged && confirmedSkillsUsed.length > 0 && (
              <div className="ats-gap-banner ats-gap-banner-info">
                Score includes user-confirmed experience not present in the uploaded resume: {listText(confirmedSkillsUsed)}.
              </div>
            )}
            {whyNot95 && (
              <div className="ats-why-not-95">
                <b>Why not 95%?</b>
                <p>{whyNot95}</p>
              </div>
            )}

            <ImprovementReportPanel report={improvementReport} />

            <Panel title="Why this score?">
              <div className="ats-score-explanation">
                <p><b>Match label:</b> {scoreStatus || "—"}</p>
                <p><b>Role alignment:</b> {listText(resumeRoles)} versus {targetRole || "—"} — {roleAlignment || "—"}</p>
                <p><b>Core matched skills:</b> {listText(matchedCore)}</p>
                <p><b>Transferable matched skills:</b> {listText(matchedTransferable)}</p>
                <p><b>Missing mandatory skills:</b> {listText(missingMandatory)}</p>
                <p><b>Score cap:</b> {scoreCap ? `${scoreCap}% — ${scoreCapReason}` : "No eligibility cap applied"}</p>
                <p><b>Recommendation:</b> {result.final_recommendation || result.original_analysis?.recommendation || "—"}</p>
              </div>
            </Panel>
            <p className="ats-improvement-note">
              Improvement = optimized score − original score (percentage points). Scores are never forced upward — truthfulness wins.
            </p>
            {result.error_message && <Notice>{result.error_message}</Notice>}
            <button
              className="button secondary"
              onClick={() => selectedJob && analyzeAndOptimize(selectedJob.id, true)}
              disabled={Boolean(disabledReason) || busy === "ats"}
            >
              Recalculate Scores
            </button>
          </Panel>

          <div className="ats-tabs">
            {[
              ["keywords", "Matched Keywords"],
              ["missing", "Missing Keywords"],
              ["suggestions", "Suggestions"],
              ["resume", "Optimized Resume"],
            ].map(([key, label]) => (
              <button
                key={key}
                type="button"
                className={`button ${activeTab === key ? "primary" : "secondary"}`}
                onClick={() => setActiveTab(key)}
              >
                {label}
              </button>
            ))}
          </div>

          {activeTab === "keywords" && (
            <div className="document-grid">
              <Panel title="Original Matched Keywords"><p>{listText(matchedBefore)}</p></Panel>
              <Panel title="Optimized Matched Keywords"><p>{listText(matchedAfter)}</p></Panel>
              <Panel title="Newly Matched Keywords"><p>{listText(newlyMatched)}</p></Panel>
            </div>
          )}

          {activeTab === "missing" && (
            <div className="document-grid">
              <Panel title="Original Missing Keywords"><p>{listText(missingBefore)}</p></Panel>
              <Panel title="Remaining Missing Keywords"><p>{listText(missingAfter)}</p></Panel>
              <Panel title="Unsupported Keywords Not Added">
                <p>{listText(unsupported)}</p>
                {unsupported?.length > 0 && (
                  <p className="ats-improvement-note">
                    Truthfulness warning: these keywords were not added because the original resume does not provide verified experience.
                  </p>
                )}
              </Panel>
            </div>
          )}

          {activeTab === "suggestions" && (
            <Panel title="Suggestions">
              <ul className="ats-list">
                {(result.final_suggestions || result.recommendations || result.optimization_notes || result.original_suggestions || []).map((item, index) => (
                  <li key={`s-${index}`}>{item}</li>
                ))}
              </ul>
              {result.final_recommendation && <p><b>Recommendation:</b> {result.final_recommendation}</p>}
            </Panel>
          )}

          {activeTab === "resume" && (
            <>
              <div className="ats-resume-toolbar">
                <button
                  className={`button ${viewMode === "original" ? "primary" : "secondary"}`}
                  onClick={() => setViewMode("original")}
                >
                  View Original Resume
                </button>
                <button
                  className={`button ${viewMode === "optimized" ? "primary" : "secondary"}`}
                  onClick={() => setViewMode("optimized")}
                >
                  Preview Optimized Resume
                </button>
                {isComplete && result.workflow_id && (
                  <a
                    className="button secondary"
                    href={getAtsDownloadUrl(result.workflow_id)}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {safeToApply ? "Download Optimized Resume" : "Download Truth-Preserving Version"}
                  </a>
                )}
              </div>
              <Panel title={viewMode === "original" ? "Original Resume" : safeToApply ? "ATS-Optimized Resume" : "Transferable Skills Resume — Major Gaps Remain"}>
                <div className="text-preview tall">
                  {viewMode === "original"
                    ? result.original_resume || latestResume?.text_preview || "Original resume text unavailable."
                    : generatedPreview || "Optimized resume text unavailable."}
                </div>
              </Panel>
            </>
          )}

          {jobInfo?.job_url && majorMismatch && (
            <div className="ats-risk-ack">
              <label><input type="checkbox" checked={riskAcknowledged} onChange={(event) => setRiskAcknowledged(event.target.checked)} /> I understand that my resume does not currently meet the core role requirements.</label>
              <a className={`button secondary ${riskAcknowledged ? "" : "disabled"}`} href={riskAcknowledged ? jobInfo.job_url : undefined} target="_blank" rel="noreferrer" aria-disabled={!riskAcknowledged}>View Job / Apply</a>
            </div>
          )}
        </>
      ))}
    </Page>
  );
}
