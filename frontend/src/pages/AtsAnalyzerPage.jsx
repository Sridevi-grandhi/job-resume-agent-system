/* eslint-disable react-hooks/exhaustive-deps */
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useApp } from "../context/AppContext";
import { Page, Panel, Notice } from "../components/Page";
import EmptyState from "../components/EmptyState";
import ResumeWorkspace from "../components/resume-workspace/ResumeWorkspace";
import { evidenceStatusLabel } from "../utils/evidenceLabels";
import {
  getEditableOptimizedResume,
  getFinalResumeDownloadUrl,
  getJobDetail,
  getOptimizationEligibility,
  improveResumeSnippet,
  saveEditedOptimizedResume,
} from "../api";

function isGenerationFailureMessage(message) {
  const text = String(message || "");
  return /^(Failed|Validation failed)\b/i.test(text)
    || /evidence incomplete/i.test(text);
}

function buildAtsStatusBanner({
  step,
  busy,
  messages,
  missingSkillsGroups,
  canContinueToOptimization,
  isComplete,
  result,
  disabledReason,
  originalScore,
  currentEditorScore,
}) {
  if (busy === "ats-requirements") return { text: "Analyzing job requirements…", tone: "info" };
  if (busy === "ats-skills") return { text: "Updating skill evidence…", tone: "info" };
  if (busy === "ats" && step >= 5) {
    return { text: messages.ats || "Generating optimized resume…", tone: "info" };
  }

  const analysisFailed = missingSkillsGroups?.analysis_status === "REQUIREMENT_EXTRACTION_FAILED"
    || missingSkillsGroups?.analysis_status === "ANALYSIS_CONSISTENCY_FAILED";
  if (analysisFailed) {
    return {
      text: messages["ats-requirements"] || missingSkillsGroups?.analysis_error || "Requirement analysis failed.",
      tone: "error",
    };
  }

  // Successful requirement analysis (any wizard step before generation) owns the banner.
  // Never allow a prior generation FAILED message to surface here.
  if (missingSkillsGroups && !analysisFailed && step < 5) {
    if (step <= 2) {
      return {
        text: messages["ats-requirements"] || "Job requirements analyzed successfully.",
        tone: "info",
      };
    }
    if (step === 3) {
      return {
        text: messages["ats-skills"] || messages["ats-requirements"] || "Confirm skills that need evidence for this job.",
        tone: "info",
      };
    }
    if (step === 4) {
      return { text: "Evidence review ready. You can continue to generation.", tone: "info" };
    }
  }

  if (step === 3) {
    return {
      text: messages["ats-skills"] || "Confirm skills that need evidence for this job.",
      tone: "info",
    };
  }

  if (step === 4) {
    return { text: "Evidence review ready. You can continue to generation.", tone: "info" };
  }

  if (step === 5) {
    if (isComplete) {
      if (originalScore != null && currentEditorScore != null) {
        const delta = Number(currentEditorScore) - Number(originalScore);
        const deltaLabel = Number.isFinite(delta)
          ? (delta >= 0 ? `(+${Math.round(delta)})` : `(${Math.round(delta)})`)
          : "";
        return {
          text: `Original ${Math.round(Number(originalScore))}% → Current ${Math.round(Number(currentEditorScore))}% ${deltaLabel}. Optimized resume ready.`,
          tone: "info",
        };
      }
      return {
        text: messages.ats || "Optimized resume ready.",
        tone: "info",
      };
    }
    const staleEvidenceFailure = /evidence incomplete/i.test(
      String(messages.ats || result?.error_message || ""),
    );
    const generationFailed = ["FAILED", "VALIDATION_FAILED"].includes(result?.workflow_status)
      && !staleEvidenceFailure;
    if (generationFailed) {
      return {
        text: /timed out waiting/i.test(String(messages.ats || ""))
          ? "Resume generation could not be completed. You can retry."
          : (messages.ats || "Resume generation could not be completed. You can retry."),
        tone: "error",
      };
    }
    if (canContinueToOptimization) {
      return {
        text: messages.ats && !isGenerationFailureMessage(messages.ats)
          ? messages.ats
          : "Ready to generate optimized resume.",
        tone: "info",
      };
    }
    return {
      text: disabledReason || "Upload a resume and select a job to generate.",
      tone: "error",
    };
  }

  // Steps 1–4: never show a stale generation Failed banner.
  if (step < 5 && isGenerationFailureMessage(messages.ats)) {
    return {
      text: messages["ats-requirements"] || messages["ats-skills"] || disabledReason || "",
      tone: "info",
    };
  }

  const text = messages["ats-requirements"] || messages["ats-skills"] || disabledReason || "";
  // Prefer step-scoped messages; only fall back to messages.ats when it is not a failure.
  const fallback = text || (isGenerationFailureMessage(messages.ats) ? "" : (messages.ats || ""));
  return {
    text: fallback,
    tone: "info",
  };
}
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
  { id: 5, label: "Generate, Review & Download" },
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

const QUICK_CONTEXTS = [
  { value: "Current employment", label: "Professional experience" },
  { value: "Personal project", label: "Personal project" },
  { value: "Academic project", label: "Academic project" },
  { value: "Training/coursework", label: "Training / learning" },
];

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

  if (draft.simple_confirmation) {
    return Boolean(draft.experience_level) && Boolean(draft.evidence_type) && hasText(draft.usage_description) && String(draft.usage_description).length <= 300;
  }

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

function groupedByCategory(skills) {
  return skills.reduce((groups, skill) => {
    const category = skill.category || "Other skills";
    (groups[category] ||= []).push(skill);
    return groups;
  }, {});
}

function CompactEvidenceRow({ skillInfo, draft, onChange }) {
  const open = draft.answer === "has_it";
  return (
    <div className={`ats-compact-evidence-row ${open ? "open" : ""}`}>
      <div className="ats-compact-evidence-heading">
        <b>{skillInfo.skill}</b>
        <span className="badge badge-preferred">{open ? "Selected — evidence needed" : "Needs confirmation"}</span>
      </div>
      {open && (
        <div className="ats-compact-evidence-fields">
          <label>Experience level
            <select value={draft.experience_level || ""} onChange={(e) => onChange({ ...draft, experience_level: e.target.value })}>
              <option value="">Select…</option>
              <option value="Beginner">Basic</option>
              <option value="Intermediate">Intermediate</option>
              <option value="Advanced">Advanced</option>
            </select>
          </label>
          <label>Where have you used it?
            <select value={draft.evidence_type || ""} onChange={(e) => onChange({ ...draft, evidence_type: e.target.value })}>
              <option value="">Select…</option>
              {QUICK_CONTEXTS.map((context) => <option key={context.value} value={context.value}>{context.label}</option>)}
            </select>
          </label>
          <label className="ats-compact-description">Briefly describe how you used {skillInfo.skill} (optional)
            <textarea maxLength={300} rows={2} value={draft.usage_description || ""}
              placeholder={`Example: Used ${skillInfo.skill} to build, test, or deliver a project.`}
              onChange={(e) => onChange({ ...draft, usage_description: e.target.value })} />
            <small>{(draft.usage_description || "").length}/300</small>
          </label>
        </div>
      )}
    </div>
  );
}

function listText(value) {
  if (Array.isArray(value)) return value.length ? value.join(", ") : "—";
  return value || "—";
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

function gapItemReason(item) {
  const status = String(item.evidence_status || item.code || "").toUpperCase();
  const requirementType = String(item.requirement_type || item.importance || "").toUpperCase();
  const isPreferred = requirementType === "PREFERRED" || status === "PREFERRED_GAP";
  if (status === "REJECTED" || status === "NOT_POSSESSED") {
    return "Marked as not possessed.";
  }
  if (isPreferred) {
    return "Preferred requirement — no supported evidence.";
  }
  return "Mandatory requirement — no supported evidence.";
}

function gapItemsFromScoreView(scoreView, result) {
  const remaining = scoreView?.remaining_gaps || {};
  const mandatoryMissing = (
    (remaining?.unsupported?.length ? remaining.unsupported : null)
    || scoreView?.mandatory_requirements?.missing
    || scoreView?.missing_mandatory_skills
    || []
  );
  const preferredMissing = (
    (remaining?.preferred_gaps?.length ? remaining.preferred_gaps : null)
    || scoreView?.preferred_requirements?.missing
    || scoreView?.missing_preferred_skills
    || []
  );
  const items = [];
  for (const skill of mandatoryMissing) {
    items.push({
      skill: typeof skill === "string" ? skill : (skill?.skill || skill?.label || String(skill)),
      normalized_skill: typeof skill === "object" ? skill?.normalized_skill : undefined,
      requirement_type: "MANDATORY",
      code: "UNSUPPORTED_REQUIREMENT",
    });
  }
  for (const skill of preferredMissing) {
    const label = typeof skill === "string" ? skill : (skill?.skill || skill?.label || String(skill));
    if (items.some((item) => item.skill === label)) continue;
    items.push({
      skill: label,
      normalized_skill: typeof skill === "object" ? skill?.normalized_skill : undefined,
      requirement_type: "PREFERRED",
      code: "PREFERRED_GAP",
    });
  }
  if (items.length) return items;

  const legacy = result?.unsupported_keywords_not_added || result?.remaining_gaps;
  if (Array.isArray(legacy)) {
    return legacy.map((skill) => ({ skill, requirement_type: "MANDATORY", code: "UNSUPPORTED_REQUIREMENT" }));
  }
  if (legacy?.unsupported?.length || legacy?.preferred_gaps?.length) {
    return [
      ...(legacy.unsupported || []).map((skill) => ({
        skill, requirement_type: "MANDATORY", code: "UNSUPPORTED_REQUIREMENT",
      })),
      ...(legacy.preferred_gaps || []).map((skill) => ({
        skill, requirement_type: "PREFERRED", code: "PREFERRED_GAP",
      })),
    ];
  }
  return [];
}

function RemainingGapsPanel({ items }) {
  const gaps = (items || []).filter((item) => item?.skill);
  if (!gaps.length) return null;
  return (
    <details className="ats-remaining-gaps">
      <summary>Remaining Gaps ({gaps.length})</summary>
      <ul>
        {gaps.map((item) => (
          <li key={`${item.requirement_type || "gap"}-${item.normalized_skill || item.skill}`}>
            <b>{item.skill}</b>
            {" — "}
            {gapItemReason(item)}
          </li>
        ))}
      </ul>
    </details>
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

function draftEvidenceStatusLabel(draft) {
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

// Retained for compatibility with the detailed evidence path used by older saved drafts.
// eslint-disable-next-line no-unused-vars
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
      <p className="ats-skill-meta">Status: <b>{resumeStatus === "MISSING" ? draftEvidenceStatusLabel(draft) : resumeStatus}</b></p>

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
    activeAtsWorkflowId,
    previousAtsResult,
    atsSelectedJobId,
    setAtsSelectedJobId,
    setAtsResult,
    analyzeAndOptimize,
    retryAtsWorkflow,
    loadAtsResultForJob,
    missingSkillsGroups,
    confirmedSkills,
    loadMissingSkills,
    loadSkillConfirmations,
    saveConfirmedSkills,
    deleteConfirmedSkill,
    setMissingSkillsGroups,
    messages,
    busy,
    verifyLatestResume,
    loadPreferences,
  } = useApp();

  const [step, setStep] = useState(1);
  const [authoritativeJob, setAuthoritativeJob] = useState(null);
  const [riskAcknowledged, setRiskAcknowledged] = useState(false);
  const [skillDrafts, setSkillDrafts] = useState({});
  const [confirmationStage, setConfirmationStage] = useState(1);
  const [eligibility, setEligibility] = useState(null);
  const [editableResume, setEditableResume] = useState(null);
  const [editResult, setEditResult] = useState(null);
  const [workspaceDocument, setWorkspaceDocument] = useState(null);
  const [stepTransitionError, setStepTransitionError] = useState("");
  const [pendingRemove, setPendingRemove] = useState(null);

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

  const analyzableJobs = useMemo(() => {
    const byId = new Map();
    for (const job of jobs || []) {
      if (job?.id == null) continue;
      byId.set(String(job.id), job);
    }
    // Merge route/detail fetch without dropping a job that already has a usable description.
    // A sparse authoritative payload must not make selectedJob flicker to null mid-wizard.
    if (authoritativeJob?.id != null) {
      const key = String(authoritativeJob.id);
      const existing = byId.get(key);
      const authLen = String(authoritativeJob.description || "").trim().length;
      const existLen = String(existing?.description || "").trim().length;
      byId.set(
        key,
        existing
          ? (authLen >= existLen
            ? { ...existing, ...authoritativeJob }
            : { ...authoritativeJob, ...existing, id: authoritativeJob.id })
          : authoritativeJob,
      );
    }
    return [...byId.values()].filter((job) => {
      if (genericSearchSources.has(job.source)) return false;
      if (!job.description || String(job.description).trim().length < 40) return false;
      const isSelected = job.source === "User Selected Job" || job.source?.endsWith(" Imported");
      if (!job.job_url && !isSelected) return false;
      return true;
    });
  }, [jobs, authoritativeJob]);

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

  // Reset wizard only on real job switches. Never wipe an in-progress Step 5 from
  // transient selection flicker (authoritative fetch / list refresh).
  const [stepResetForJobId, setStepResetForJobId] = useState(() => selectedJob?.id ?? null);
  const stepRef = useRef(step);
  stepRef.current = step;
  useEffect(() => {
    const nextId = selectedJob?.id ?? null;
    if (nextId == null) return;
    if (stepResetForJobId == null) {
      setStepResetForJobId(nextId);
      return;
    }
    if (String(nextId) === String(stepResetForJobId)) return;
    setStepResetForJobId(nextId);
    setStep(1);
    setConfirmationStage(1);
    setSkillDrafts({});
    setEligibility(null);
    setEditableResume(null);
    setEditResult(null);
    setWorkspaceDocument(null);
    setMissingSkillsGroups(null);
    setStepTransitionError("");
    setPendingRemove(null);
    setAtsResult(null);
  }, [selectedJob?.id, stepResetForJobId, setMissingSkillsGroups, setAtsResult]);

  const disabledReason = !latestResume
    ? "Upload a resume before saving and optimizing jobs."
    : !selectedJob
      ? "Select or import one complete job description before running optimize-and-match."
      : "";

  const result = atsResult;
  const jobInfo = result?.job || selectedJob;
  const status = result?.workflow_status;
  const isGenerating = busy === "ats" || (
    activeAtsWorkflowId
    && result?.workflow_id
    && Number(result.workflow_id) === Number(activeAtsWorkflowId)
    && !["COMPLETED", "FAILED", "VALIDATION_FAILED"].includes(String(status || ""))
  );
  const resultMatchesActiveWorkflow = !activeAtsWorkflowId
    || !result?.workflow_id
    || Number(result.workflow_id) === Number(activeAtsWorkflowId);
  const displayResult = (isGenerating && previousAtsResult) ? previousAtsResult : result;
  const originalScore = displayResult?.original_match_percentage ?? displayResult?.original_match_score ?? displayResult?.original_analysis?.match_percentage;
  const workflowOptimizedScore = displayResult?.optimized_match_percentage ?? displayResult?.optimized_match_score ?? displayResult?.optimized_analysis?.match_percentage;
  const unsupported = displayResult?.unsupported_keywords_not_added || [];
  const generatedPlain = displayResult?.generated_resume?.plain_text || displayResult?.generated_resume_plain || displayResult?.generated_resume;
  const generatedPreview = displayResult?.generated_resume?.formatted_preview || displayResult?.generated_resume_preview_markdown || generatedPlain;
  const canRetry = ["FAILED", "VALIDATION_FAILED"].includes(status);
  const isComplete = status === "COMPLETED" && resultMatchesActiveWorkflow && !isGenerating;
  const currentEditorScore = editResult?.edited_score ?? editableResume?.edited_score ?? (isComplete ? workflowOptimizedScore : null);
  const liveScoreView = editResult?.score || editableResume?.score || null;
  const roleAlignment = displayResult?.role_alignment || displayResult?.original_analysis?.role_alignment;
  const resumeRoles = displayResult?.resume_role_families || displayResult?.original_analysis?.resume_role_families || [];
  const targetRole = displayResult?.target_job_role || displayResult?.original_analysis?.job_role_family;
  const majorMismatch = roleAlignment === "MISMATCH";
  const contentChanged = displayResult?.content_changed;
  const optimizationStatus = displayResult?.optimization_status;
  const whyNot95 = displayResult?.why_not_95;
  const confirmedSkillsUsed = displayResult?.confirmed_skills_incorporated
    || displayResult?.confirmed_skills_used
    || [];
  const improvementReport = displayResult?.improvement_report;
  const generationMetadata = resultMatchesActiveWorkflow && !isGenerating
    ? (result?.generation_metadata || result || {})
    : {};
  const workflowStatusForLabel = String(displayResult?.workflow_status || result?.workflow_status || status || "");
  const finalValidationStatus = String(
    displayResult?.final_validation?.status
    || result?.final_validation?.status
    || ""
  ).toLowerCase();
  const validationPassedForLabel = finalValidationStatus === "passed"
    || finalValidationStatus === "passed_with_warnings"
    || displayResult?.validation_passed === true
    || result?.validation_passed === true;
  const llmGenerationSucceeded = Boolean(
    generationMetadata.llm_used || generationMetadata.generation_method === "llm"
  );
  const generationLabel = isGenerating
    ? "Generating with AI…"
    : workflowStatusForLabel === "VALIDATION_FAILED" && llmGenerationSucceeded
      ? "AI generation completed, but final resume validation failed."
      : workflowStatusForLabel === "COMPLETED" && llmGenerationSucceeded && validationPassedForLabel
      ? `AI Optimized · ${generationMetadata.llm_model || generationMetadata.generation_model || "LLM"}`
      : generationMetadata.fallback_used
        ? generationMetadata.fallback_reason === "timeout"
          ? "Optimized locally · AI generation took too long"
          : "Optimized locally · AI response could not be safely applied"
        : generationMetadata.generation_method === "deterministic_fallback"
          ? "Optimized locally · AI response could not be safely applied"
          : workflowStatusForLabel === "COMPLETED" && generationMetadata.generation_method === "deterministic_fallback"
            ? "Optimized locally · AI response could not be safely applied"
            : null;
  const fallbackReason = null;

  const verifiedSkills = missingSkillsGroups?.verified_resume_skills || [];
  const weakSkills = missingSkillsGroups?.weak_or_indirect_skills || [];
  const mandatorySkills = missingSkillsGroups?.missing_mandatory_skills || [];
  const preferredSkills = missingSkillsGroups?.missing_preferred_skills || [];
  const confirmationSkills = useMemo(() => [...weakSkills, ...mandatorySkills, ...preferredSkills], [weakSkills, mandatorySkills, preferredSkills]);
  // Keep legacy alias used by save/continue helpers — this is the confirmation queue,
  // NOT "skills not found". Weak/indirect evidence is intentionally included.
  const missingSkills = confirmationSkills;
  const notFoundSkills = useMemo(() => [...mandatorySkills, ...preferredSkills], [mandatorySkills, preferredSkills]);

  const answeredDrafts = confirmationSkills.filter((s) => skillDrafts[s.skill]?.answer);
  const allAnsweredValid = answeredDrafts.every((s) => draftIsValid(skillDrafts[s.skill]));
  const canSaveConfirmations = answeredDrafts.length > 0 && allAnsweredValid;
  const blockingReasons = missingSkillsGroups?.blocking_reasons || [];
  const evidenceSummary = missingSkillsGroups?.evidence_summary || {};
  const USER_CONFIRMED_STATUSES = useMemo(() => new Set([
    "USER_CONFIRMED_PROFESSIONAL",
    "USER_CONFIRMED_PROJECT",
    "USER_CONFIRMED_CERTIFICATION",
    "USER_CONFIRMED_TRAINING",
  ]), []);
  // Never fall back to unfiltered confirmedSkills — that reintroduces historical INCOMPLETE rows.
  const activeEvidence = useMemo(() => {
    const fromSummary = Array.isArray(evidenceSummary.evidence) ? evidenceSummary.evidence : null;
    const fromGroups = Array.isArray(missingSkillsGroups?.evidence) ? missingSkillsGroups.evidence : null;
    const rows = fromSummary || fromGroups || [];
    return rows.filter((row) => row && row.evidence_status !== "OBSOLETE" && row.is_active !== false);
  }, [evidenceSummary, missingSkillsGroups]);
  const evidenceHistory = useMemo(() => (
    Array.isArray(evidenceSummary.evidence_history)
      ? evidenceSummary.evidence_history
      : (Array.isArray(missingSkillsGroups?.evidence_history) ? missingSkillsGroups.evidence_history : [])
  ), [evidenceSummary, missingSkillsGroups]);
  const userConfirmedEvidence = useMemo(
    () => (Array.isArray(evidenceSummary.user_confirmed) && evidenceSummary.user_confirmed.length
      ? evidenceSummary.user_confirmed
      : activeEvidence.filter((row) => USER_CONFIRMED_STATUSES.has(row.evidence_status))),
    [evidenceSummary, activeEvidence, USER_CONFIRMED_STATUSES],
  );
  const incompleteEvidence = useMemo(
    () => (Array.isArray(evidenceSummary.incomplete) && evidenceSummary.incomplete.length
      ? evidenceSummary.incomplete
      : activeEvidence.filter((row) => row.evidence_status === "INCOMPLETE")),
    [evidenceSummary, activeEvidence],
  );
  const optionalEvidence = useMemo(
    () => (Array.isArray(evidenceSummary.optional_preferred) ? evidenceSummary.optional_preferred : []),
    [evidenceSummary],
  );
  // Step 4 summary lists unsupported required items for awareness (warnings, not blockers).
  const currentUnresolvedRequired = useMemo(() => {
    const fromWarnings = Array.isArray(missingSkillsGroups?.generation_warnings)
      ? missingSkillsGroups.generation_warnings
      : [];
    if (fromWarnings.length) return fromWarnings;
    const mandatoryIncomplete = incompleteEvidence.filter(
      (row) => (row.requirement_type || "").toUpperCase() === "MANDATORY",
    );
    if (mandatoryIncomplete.length) return mandatoryIncomplete;
    return (blockingReasons || []).filter((item) => {
      const status = String(item.evidence_status || "").toUpperCase();
      return !USER_CONFIRMED_STATUSES.has(status) && status !== "VERIFIED_FROM_RESUME";
    });
  }, [incompleteEvidence, blockingReasons, USER_CONFIRMED_STATUSES, missingSkillsGroups]);

  const optimizedScoreView = liveScoreView
    || result?.optimized_score_view
    || result?.optimized_analysis?.score_view
    || null;
  const authoritativeRemainingGaps = useMemo(() => {
    if (isComplete && (optimizedScoreView || result?.optimized_analysis)) {
      const analysis = result?.optimized_analysis;
      const view = optimizedScoreView || (analysis ? {
        mandatory_requirements: analysis.mandatory_requirements || {
          covered: Math.max(0, (analysis.mandatory_unit_count || (analysis.required_skills || []).length)
            - (analysis.missing_mandatory_skills || []).length),
          total: analysis.mandatory_unit_count || (analysis.required_skills || []).length,
          missing: analysis.missing_mandatory_skills || [],
        },
        remaining_gaps: analysis.remaining_gaps || {
          unsupported: analysis.missing_mandatory_skills || [],
          preferred_gaps: analysis.missing_preferred_skills || [],
        },
        missing_mandatory_skills: analysis.missing_mandatory_skills || [],
      } : null);
      return gapItemsFromScoreView(view, result);
    }
    return currentUnresolvedRequired;
  }, [isComplete, optimizedScoreView, result, currentUnresolvedRequired]);

  // Authoritative gate: analysis must succeed. Unsupported JD gaps never block Continue/Generate.
  const analysisFailed = missingSkillsGroups?.analysis_status === "REQUIREMENT_EXTRACTION_FAILED"
    || missingSkillsGroups?.analysis_status === "ANALYSIS_CONSISTENCY_FAILED";
  const canContinueToOptimization = Boolean(missingSkillsGroups && !analysisFailed);
  const generationWarningMessage = missingSkillsGroups?.generation_warning_message
    || (currentUnresolvedRequired.length
      ? `${currentUnresolvedRequired.map((item) => item.skill).filter(Boolean).join(", ")} ${
        currentUnresolvedRequired.length === 1 ? "is" : "are"
      } not supported and will remain a gap. Generation can continue; unsupported requirements will be excluded.`
      : "");
  const unresolvedRequiredCount = currentUnresolvedRequired.length;

  const summaryCounts = useMemo(() => {
    const backendCounts = missingSkillsGroups?.active_confirmation_counts || evidenceSummary.counts;
    if (backendCounts && typeof backendCounts === "object") {
      const incomplete = backendCounts.incomplete ?? incompleteEvidence.length;
      return {
        verifiedFromResume: backendCounts.verified_from_resume ?? verifiedSkills.length,
        userConfirmedWithEvidence: backendCounts.user_confirmed_with_evidence ?? userConfirmedEvidence.length,
        learningOnly: backendCounts.learning_only ?? 0,
        notPossessed: backendCounts.not_possessed ?? 0,
        incomplete,
      };
    }
    return {
      verifiedFromResume: verifiedSkills.length,
      userConfirmedWithEvidence: userConfirmedEvidence.length,
      learningOnly: activeEvidence.filter((row) => row.evidence_status === "LEARNING_ONLY").length,
      notPossessed: activeEvidence.filter((row) => row.evidence_status === "REJECTED").length,
      incomplete: incompleteEvidence.length,
    };
  }, [verifiedSkills, activeEvidence, missingSkillsGroups, evidenceSummary, userConfirmedEvidence, incompleteEvidence]);

  const updateDraft = (skill, draft) => setSkillDrafts((current) => ({ ...current, [skill]: draft }));

  const toggleQuickSkill = (skill) => setSkillDrafts((current) => {
    const draft = current[skill] || {};
    const selected = draft.answer === "has_it";
    return {
      ...current,
      [skill]: selected
        ? {}
        : { answer: "has_it", simple_confirmation: true, confirmed_accurate: true },
    };
  });

  const startEvidenceConfirmation = () => {
    // Keep drafts frontend-only until Save Evidence — never persist answer:null INCOMPLETE stubs.
    const selected = confirmationSkills.filter((item) => skillDrafts[item.skill]?.answer === "has_it");
    if (!selected.length) return;
    setSkillDrafts((current) => {
      const next = { ...current };
      selected.forEach((item) => {
        next[item.skill] = {
          ...(next[item.skill] || {}),
          answer: "has_it",
          simple_confirmation: true,
          confirmed_accurate: true,
        };
      });
      return next;
    });
    setConfirmationStage(2);
  };

  const denyAllMissingSkills = async () => {
    const items = confirmationSkills.map((item) => ({
      skill: item.skill,
      requirement_type: item.requirement_type || item.importance,
      answer: "not_possessed",
      simple_confirmation: true,
    }));
    setSkillDrafts((current) => {
      const next = { ...current };
      confirmationSkills.forEach((item) => {
        next[item.skill] = { ...(next[item.skill] || {}), answer: "not_possessed", simple_confirmation: true };
      });
      return next;
    });
    if (items.length) {
      await saveConfirmedSkills(selectedJob.id, items);
    }
    setConfirmationStage(2);
    setStep(4);
  };

  const handleSaveConfirmations = async () => {
    const items = answeredDrafts.map((s) => {
      const draft = skillDrafts[s.skill];
      return {
        skill: s.skill,
        requirement_type: s.requirement_type || s.importance,
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
        simple_confirmation: Boolean(draft.simple_confirmation),
      };
    });
    if (!items.length) return;
    await saveConfirmedSkills(selectedJob.id, items);
    setStep(4);
  };

  const maxUnlockedStep = useMemo(() => {
    if (isComplete || ["VALIDATING_RESUME", "VALIDATION_FAILED", "ANALYZING_OPTIMIZED", "GENERATING_RESUME"].includes(status)) {
      return 5;
    }
    if (canContinueToOptimization) return 5;
    // Analysis exists ⇒ Steps 1–4 stay reachable so users can review blockers.
    // Only Step 5 requires zero mandatory unresolved evidence.
    if (missingSkillsGroups) return 4;
    if (selectedJob) return 2;
    return 1;
  }, [selectedJob, missingSkillsGroups, canContinueToOptimization, status, isComplete]);

  useEffect(() => {
    if (!isComplete || !result?.optimized_resume_id || !selectedJob?.id || !currentUser?.user_id) return;
    getEditableOptimizedResume(currentUser.user_id, selectedJob.id, result.optimized_resume_id)
      .then((data) => {
        setEditableResume(data);
        setWorkspaceDocument(data.document || null);
        if (data.score || data.edited_score != null) {
          setEditResult((current) => ({
            ...(current || {}),
            edited_score: data.edited_score,
            generated_score: data.generated_score,
            score: data.score,
            safe_to_download: ["passed", "passed_with_warnings"].includes(String(data.validation_status || "")),
            final_text: data.final_text,
          }));
        }
      })
      .catch(() => null);
  }, [isComplete, result?.optimized_resume_id, selectedJob?.id, currentUser?.user_id]);

  const analyzeRequirementsAndEligibility = async () => {
    const groups = await loadMissingSkills(selectedJob.id);
    if (!groups) return;
    if (groups.analysis_status === "REQUIREMENT_EXTRACTION_FAILED" || groups.analysis_status === "ANALYSIS_CONSISTENCY_FAILED") {
      setEligibility({
        optimization_allowed: false,
        eligibility_status: "ANALYSIS_FAILED",
        analysis_status: groups.analysis_status,
        reason: groups.analysis_error || "Analysis could not be completed.",
        eligibility_reason: groups.analysis_error || "Analysis could not be completed.",
        original_score: null,
      });
      // Stay on Step 2 — do not advance on failed analysis.
      return;
    }
    const check = await getOptimizationEligibility(currentUser.user_id, selectedJob.id);
    setEligibility(check);
    if (check?.analysis_status === "REQUIREMENT_EXTRACTION_FAILED") {
      return;
    }
    // Drop stale FAILED / INELIGIBLE workflow banners when CURRENT analysis succeeds.
    setAtsResult((current) => {
      if (!current) return current;
      const status = String(current.workflow_status || "");
      const stale = status.startsWith("INELIGIBLE")
        || status === "FAILED"
        || status === "VALIDATION_FAILED";
      return stale ? null : current;
    });
    setStep(3);
  };

  const enterOptimizationStep = (event) => {
    event?.preventDefault?.();
    event?.stopPropagation?.();
    const unresolved = currentUnresolvedRequired;
    if (import.meta.env.DEV) {
      console.info("CONTINUE_OPTIMIZATION_CLICK", {
        currentStep: stepRef.current,
        canGenerateResume: canContinueToOptimization,
        can_generate_resume: missingSkillsGroups?.can_generate_resume,
        unresolvedRequiredCount: unresolved.length,
        selectedJobId: selectedJob?.id,
        resumeId: latestResume?.id ?? latestResume?.resume_id,
        analysisId: missingSkillsGroups?.analysis_id,
      });
    }

    // Single invariant: analysis must be present. Unsupported gaps never block navigation.
    if (!missingSkillsGroups) {
      const message = "Could not continue to optimization: current ATS analysis could not be loaded.";
      console.error(message);
      setStepTransitionError(message);
      return;
    }
    if (analysisFailed) {
      setStepTransitionError("Could not continue: requirement analysis failed.");
      return;
    }

    // Context warnings are shown on Step 5 — they must not block navigation.
    const warnings = [];
    if (!selectedJob?.id) warnings.push("No job is selected.");
    if (!(latestResume?.id ?? latestResume?.resume_id)) warnings.push("No resume is loaded.");
    if (generationWarningMessage) warnings.push(generationWarningMessage);
    setStepTransitionError("");
    // Soft amber notice for gaps is rendered from generationWarningMessage on Step 5.
    void warnings;

    // Review page only — never await backend before advancing.
    setStep(5);
  };

  const runGenerateOptimizedResume = async () => {
    if (busy === "ats" || busy === "ats-skills") return;
    if (!selectedJob?.id) {
      setStepTransitionError("Could not generate: no job is selected.");
      return;
    }
    if (!canContinueToOptimization) {
      setStepTransitionError(
        analysisFailed
          ? "Could not generate: requirement analysis failed."
          : "Could not generate: analyze a job with a valid resume first.",
      );
      return;
    }
    setStepTransitionError("");
    // Synchronously occupy the ATS busy channel so rapid double-clicks cannot
    // start a second generate before React re-renders.
    if (busy) return;
    try {
      const readiness = await loadSkillConfirmations(selectedJob.id);
      if (busy === "ats") return;
      const systemBlockers = (readiness?.blocking_reasons || []).filter(
        (item) => item?.code && !String(item.code).includes("EVIDENCE") && item.code !== "MANDATORY_EVIDENCE_INCOMPLETE",
      );
      if (readiness && readiness.can_generate_resume === false && systemBlockers.length) {
        setStepTransitionError(
          readiness.generation_blocked_message
          || systemBlockers.map((item) => item.reason || item.skill).filter(Boolean).join(", ")
          || "Could not generate optimized resume due to a system error.",
        );
        return;
      }
    } catch (error) {
      console.error("Generate readiness check failed:", error);
      setStepTransitionError(
        `Could not generate: ${error?.message || "evidence readiness check failed."}`,
      );
      return;
    }
    const generated = await analyzeAndOptimize(selectedJob.id, true);
    if (generated?.workflow_status === "COMPLETED" && generated?.optimized_resume_id) {
      setStep(5);
      setStepTransitionError("");
    } else if (generated && generated.workflow_status && generated.workflow_status !== "COMPLETED") {
      const err = String(generated.error_message || "");
      // Stale evidence-incomplete failures must not look like hard errors after this product change.
      if (/evidence incomplete/i.test(err)) {
        setStepTransitionError("");
      } else {
        setStepTransitionError(
          generated.error_message
          || `Could not generate: workflow status ${generated.workflow_status}.`,
        );
      }
    }
  };

  const saveWorkspace = async ({ document, plainText, action, skipScore }) => {
    const saved = await saveEditedOptimizedResume(
      currentUser.user_id,
      selectedJob.id,
      result.optimized_resume_id,
      plainText,
      action,
      { document, skipScore: Boolean(skipScore) },
    );
    setEditResult(saved);
    if (saved?.document) setWorkspaceDocument(saved.document);
    return saved;
  };

  const scoreWorkspace = async ({ document, plainText }) => {
    // Auto-save current editor content, then score with the shared ATS engine (no OpenAI).
    const scored = await saveEditedOptimizedResume(
      currentUser.user_id,
      selectedJob.id,
      result.optimized_resume_id,
      plainText,
      "recalculate",
      { document, scoreOnly: false, persist: true, skipScore: false },
    );
    setEditResult((current) => ({ ...(current || {}), ...scored }));
    if (scored?.document) setWorkspaceDocument(scored.document);
    return scored;
  };

  // Download must be available whenever a generated resume document exists
  // with valid current content — never gated on ATS score, accepted-AI-
  // suggestion count, remaining gaps, or JD requirement coverage. The
  // Generated Resume editor (ResumeWorkspace) only ever renders once
  // isComplete is true, which itself already requires workflow_status ===
  // "COMPLETED" — and the workflow service never reaches COMPLETED for a
  // resume whose final validation failed (that path sets VALIDATION_FAILED
  // instead, see ats_optimization_workflow_service.py). So by the time the
  // editor (and this gate) exist at all, the underlying resume has already
  // passed truthfulness validation — re-chasing validation_status/
  // safe_to_download/isComplete field variants here was both redundant and
  // fragile (any one of editResult/editableResume being momentarily null or
  // shaped differently than expected silently hid Download entirely, which
  // was the actual cause of the reported regression).
  const canDownloadOptimized = Boolean(result?.optimized_resume_id);

  const improveWorkspaceSnippet = async ({ text, context, previousSuggestion }) => (
    improveResumeSnippet(currentUser.user_id, selectedJob.id, result.optimized_resume_id, text, context, previousSuggestion)
  );

  const statusBanner = buildAtsStatusBanner({
    step,
    busy,
    messages,
    missingSkillsGroups,
    canContinueToOptimization,
    isComplete,
    result,
    disabledReason,
    originalScore,
    currentEditorScore,
  });

  const confirmRemoveEvidence = async () => {
    if (!pendingRemove?.id || !selectedJob?.id) return;
    await deleteConfirmedSkill(selectedJob.id, pendingRemove.id);
    setPendingRemove(null);
  };

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
      <Notice tone={statusBanner.tone === "error" ? "error" : statusBanner.tone === "warning" ? "warning" : undefined}>
        {statusBanner.text}
      </Notice>

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
          <p className="ats-improvement-note">
            Analyzing resume <b>{latestResume?.file_name || latestResume?.original_file_name || "—"}</b>
            {latestResume?.id ? ` (id ${latestResume.id})` : ""} against job <b>{selectedJob?.title || "—"}</b>
            {selectedJob?.id ? ` (id ${selectedJob.id})` : ""}.
          </p>
          <button
            className="button primary"
            disabled={!selectedJob || busy === "ats-requirements"}
            onClick={analyzeRequirementsAndEligibility}
          >
            {busy === "ats-requirements" ? "Analyzing…" : "Analyze Job Description"}
          </button>
          {busy === "ats-requirements" && <p className="ats-generation-indicator">Loading requirement analysis…</p>}
          {missingSkillsGroups?.analysis_status === "REQUIREMENT_EXTRACTION_FAILED" || missingSkillsGroups?.analysis_status === "ANALYSIS_CONSISTENCY_FAILED" ? (
            <section className="ats-threshold-warning" role="alert">
              <h3>Analysis could not be completed</h3>
              <p>{missingSkillsGroups.analysis_error || "We could not extract requirements from this job description. Please retry."}</p>
              <button className="button primary" onClick={analyzeRequirementsAndEligibility}>Retry Analysis</button>
            </section>
          ) : null}
          {missingSkillsGroups && missingSkillsGroups.analysis_status !== "REQUIREMENT_EXTRACTION_FAILED" && missingSkillsGroups.analysis_status !== "ANALYSIS_CONSISTENCY_FAILED" && (
            <div className="ats-requirements-summary">
              {missingSkillsGroups.score_explanation?.match_percentage != null && (
                <div className="ats-evidence-summary">
                  <p><b>ATS Match:</b> {Math.round(Number(missingSkillsGroups.score_explanation.match_percentage))}%</p>
                  <p><b>Role alignment:</b> {missingSkillsGroups.score_explanation.role_alignment || "—"}</p>
                  <details className="ats-score-explanation" open>
                    <summary>Why this score?</summary>
                    {Object.entries(missingSkillsGroups.score_explanation.component_scores || {}).map(([name, component]) => (
                      <p key={name}>
                        <b>{name.replaceAll("_", " ")}:</b>{" "}
                        {component && typeof component === "object"
                          ? `${component.earned ?? component}/${component.max ?? ""}`
                          : String(component)}
                      </p>
                    ))}
                    {missingSkillsGroups.score_explanation.why_not_95 && <p>{missingSkillsGroups.score_explanation.why_not_95}</p>}
                  </details>
                </div>
              )}
              <p><b>✓ Verified from resume:</b> {verifiedSkills.length} skill(s)</p>
              <p><b>? Possible evidence — confirm:</b> {weakSkills.length} skill(s)</p>
              <p><b>✕ Not found — mandatory:</b> {mandatorySkills.length} skill(s)</p>
              <p><b>✕ Not found — preferred:</b> {preferredSkills.length} skill(s)</p>
              <h4>✓ Verified from Your Resume</h4>
              <ul className="ats-list">
                {verifiedSkills.map((s) => (
                  <li key={s.skill}>{s.skill} <span className="ats-skill-meta">({s.category} · {s.match_type} · {s.evidence_strength})</span>{s.evidence_snippets?.[0] && <small className="ats-evidence-snippet">{s.evidence_snippets[0]}</small>}{s.reason && <small className="ats-evidence-snippet">{s.reason}</small>}</li>
                ))}
                {!verifiedSkills.length && <li>No requirements are clearly supported by the resume yet.</li>}
              </ul>
              <h4>? Possible Evidence — Please Confirm</h4>
              <ul className="ats-list">
                {weakSkills.map((s) => (
                  <li key={s.skill}>{s.skill} <span className="ats-skill-meta">({s.match_type} · {s.evidence_strength})</span>{s.evidence_snippets?.[0] && <small className="ats-evidence-snippet">Possible evidence: {s.evidence_snippets[0]}</small>}{s.reason && <small className="ats-evidence-snippet">{s.reason}</small>}</li>
                ))}
                {!weakSkills.length && <li>No weak or indirect evidence requires confirmation.</li>}
              </ul>
              <h4>✕ Not Found in Resume — Mandatory</h4>
              <ul className="ats-list">
                {mandatorySkills.map((s) => <li key={s.skill}>{s.skill} <span className="ats-skill-meta">({s.category})</span></li>)}
                {!mandatorySkills.length && <li>No mandatory skills are missing.</li>}
              </ul>
              <h4>Optional / Preferred Missing</h4>
              <ul className="ats-list">
                {preferredSkills.map((s) => <li key={s.skill}>{s.skill} <span className="ats-skill-meta">({s.category})</span></li>)}
                {!preferredSkills.length && <li>No preferred skills are missing.</li>}
              </ul>
              <button
                className="button primary"
                disabled={missingSkillsGroups.analysis_status === "REQUIREMENT_EXTRACTION_FAILED"}
                onClick={() => setStep(3)}
              >
                Continue to Skill Confirmation
              </button>
            </div>
          )}
        </Panel>
      )}

      {step === 3 && (
        <Panel title="Step 3 · Confirm Your Current Skills">
          {eligibility?.optimization_allowed === false ? (
            <section className={eligibility.role_alignment === "MISMATCH" ? "ats-major-warning" : "ats-threshold-warning"} role="alert">
              <h3>
                {eligibility.analysis_status === "REQUIREMENT_EXTRACTION_FAILED" || eligibility.eligibility_status === "ANALYSIS_FAILED"
                  ? "Analysis could not be completed"
                  : eligibility.role_alignment === "MISMATCH"
                    ? "Major role mismatch detected"
                    : "Resume below optimization threshold"}
              </h3>
              {eligibility.analysis_status === "REQUIREMENT_EXTRACTION_FAILED" || eligibility.eligibility_status === "ANALYSIS_FAILED" ? (
                <p>{eligibility.reason || eligibility.eligibility_reason || "We could not extract requirements from this job description. Please retry."}</p>
              ) : eligibility.role_alignment === "MISMATCH" ? (
                <p>Your current resume matches this job at <b>{eligibility.original_score}%</b> and targets a different role family. Please review the role details or upload a more relevant resume.</p>
              ) : (
                <p>{eligibility.reason || eligibility.eligibility_reason || "Role aligned, but ATS score is below 65%."}</p>
              )}
              {eligibility.original_score != null && (
              <div className="ats-evidence-summary">
                <p>Current Resume Match: <b>{eligibility.original_score}%</b></p>
                <p>Minimum Required: <b>65%</b></p>
                <p>Resume role: <b>{eligibility.resume_role_family_display || eligibility.resume_role_family || "—"}</b></p>
                <p>Target role: <b>{eligibility.target_job_title || eligibility.job_role_family_display || eligibility.job_role_family || "—"}</b></p>
                <p>Role alignment: <b>{eligibility.role_alignment || eligibility.eligibility_tier}</b></p>
                <p>Suggested role family: <b>{eligibility.suggested_role_family || "—"}</b></p>
              </div>
              )}
              <p><b>Missing core skills:</b> {listText(eligibility.missing_core_skills)}</p>
              <p><b>Missing mandatory skills:</b> {listText(eligibility.missing_mandatory_skills)}</p>
              <p><b>Missing preferred skills:</b> {listText(eligibility.missing_preferred_skills)}</p>
              <details className="ats-score-explanation" open>
                <summary>Why this score?</summary>
                {Object.entries(eligibility.component_scores || {}).map(([name, component]) => (
                  <p key={name}><b>{name.replaceAll("_", " ")}:</b> {name === "education_certifications" && eligibility.education_category_applicable === false ? "N/A (neutral)" : `${component.earned}/${component.max}`}</p>
                ))}
                <p><b>Score cap:</b> {eligibility.score_cap ? `${eligibility.score_cap}% — ${eligibility.score_cap_reason}` : "None"}</p>
                <p><b>Strong evidence:</b> {listText(eligibility.strong_evidence_skills)}</p>
                <p><b>Weak evidence:</b> {listText(eligibility.weak_evidence_skills)}</p>
              </details>
              {eligibility.role_alignment === "MISMATCH" && <p><b>Major mismatch reasons:</b> {listText(eligibility.major_mismatch_reasons)}</p>}
              <div className="ats-confirmation-actions">
                <Link className="button primary" to="/resume">Upload Different Resume</Link>
                <button className="button secondary" onClick={() => setStep(2)}>View Missing Skills</button>
                <Link className="button secondary" to="/selected-jobs">Return to Selected Jobs</Link>
              </div>
            </section>
          ) : (
          <>
          {eligibility && (
            <section className="ats-role-aligned" aria-label="Role alignment summary">
              <h3>Role alignment: {eligibility.role_alignment === "EXACT" ? "Exact" : eligibility.role_alignment}</h3>
              <p><b>Resume role:</b> {eligibility.resume_role_family_display || eligibility.resume_role_family}</p>
              <p><b>Target role:</b> {eligibility.target_job_title || eligibility.job_role_family_display || eligibility.job_role_family}</p>
              <p><b>Role alignment:</b> {eligibility.role_alignment}</p>
              <p><b>Suggested role family:</b> {eligibility.suggested_role_family || eligibility.resume_role_family_display || "—"}</p>
              <p><b>Original match:</b> {eligibility.original_score}%</p>
              <p><b>Optimization:</b> Eligible</p>
              {eligibility.role_classification?.resume?.strong_signals?.length > 0 && (
                <p className="ats-improvement-note">
                  Strong role signals: {listText(eligibility.role_classification.resume.strong_signals)}
                  {(eligibility.role_classification.resume.supporting_signals || []).length > 0
                    ? ` · Supporting/shared: ${listText(eligibility.role_classification.resume.supporting_signals)}`
                    : ""}
                </p>
              )}
              {eligibility.eligibility_warning && <p>{eligibility.eligibility_warning}</p>}
            </section>
          )}
          <p className="ats-improvement-note">
            Your uploaded resume may be outdated. Select only the skills you currently possess. You may be
            asked to provide supporting details. Do not select skills you have not used.
          </p>
          {!missingSkillsGroups ? (
            <EmptyState title="Analyze requirements first" text="Go back to Step 2 and analyze the job description." />
          ) : (
            <>
              <details className="ats-verified-skills">
                <summary>✓ Verified from your resume ({verifiedSkills.length})</summary>
                <div className="ats-verified-skill-chips">
                  {verifiedSkills.map((s) => <span key={s.skill}>{s.skill}</span>)}
                  {!verifiedSkills.length && <p>No requirements are confidently verified yet.</p>}
                </div>
              </details>

              {confirmationStage === 1 ? (
                <section className="ats-quick-confirmation">
                  <h3>Confirm Your Skills</h3>
                  <p>Review the skills below before continuing. Select only the skills you genuinely have experience with.</p>
                  {confirmedSkills.length > 0 && (
                    <p className="ats-improvement-note">
                      This job has {confirmedSkills.length} previously saved decision{confirmedSkills.length === 1 ? "" : "s"}. They are visible in Review Evidence and are not copied into these forms automatically.
                    </p>
                  )}
                  {weakSkills.length > 0 && (
                    <div className="ats-skill-category">
                      <h4>? Possible Evidence Found — Please Confirm</h4>
                      <p className="ats-skill-meta">Your resume mentions these, but we could not confidently confirm detailed professional usage. Confirm only what you have genuinely used.</p>
                      {weakSkills.map((item) => (
                        <div className="ats-quick-skill-item" key={item.skill}>
                          <label className="ats-quick-skill-row">
                            <input type="checkbox" checked={skillDrafts[item.skill]?.answer === "has_it"} onChange={() => toggleQuickSkill(item.skill)} />
                            <span>{item.skill}</span>
                            <span className={`badge ${(item.requirement_type || item.importance) === "PREFERRED" ? "badge-preferred" : "badge-mandatory"}`}>{(item.requirement_type || item.importance) === "PREFERRED" ? "Preferred" : "Required"}</span>
                          </label>
                          {item.evidence_snippets?.[0] && (
                            <small className="ats-evidence-snippet">Possible evidence: {item.evidence_snippets[0]}</small>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                  {(mandatorySkills.length > 0 || preferredSkills.length > 0) && (
                    <div className="ats-skill-category-group">
                      <h4 className="ats-not-found-heading">✕ Not Found in Resume</h4>
                      <p className="ats-skill-meta">The job requires these, but we could not find any evidence of them anywhere in your uploaded resume.</p>
                      {Object.entries(groupedByCategory([...mandatorySkills, ...preferredSkills])).map(([category, skills]) => (
                        <div className="ats-skill-category" key={category}>
                          <h4>{category}</h4>
                          {skills.map((item) => (
                            <label className="ats-quick-skill-row" key={item.skill}>
                              <input type="checkbox" checked={skillDrafts[item.skill]?.answer === "has_it"} onChange={() => toggleQuickSkill(item.skill)} />
                              <span>{item.skill}</span>
                              <span className={`badge ${(item.requirement_type || item.importance) === "PREFERRED" ? "badge-preferred" : "badge-mandatory"}`}>{(item.requirement_type || item.importance) === "PREFERRED" ? "Preferred" : "Required"}</span>
                            </label>
                          ))}
                        </div>
                      ))}
                    </div>
                  )}
                  <div className="ats-confirmation-progress"><b>{confirmationSkills.length} skill{confirmationSkills.length === 1 ? "" : "s"} need review</b><span>{confirmationSkills.filter((s) => skillDrafts[s.skill]?.answer === "has_it").length} selected</span></div>
                  {unresolvedRequiredCount > 0 && (
                    <p className="ats-gap-warning">
                      {unresolvedRequiredCount} required skill{unresolvedRequiredCount === 1 ? "" : "s"} unsupported
                      — they will remain gaps and will not block generation.
                    </p>
                  )}
                  <div className="ats-confirmation-actions">
                    <button className="button primary" disabled={!confirmationSkills.some((s) => skillDrafts[s.skill]?.answer === "has_it")} onClick={startEvidenceConfirmation}>Select skills I have</button>
                    <button className="button secondary" onClick={() => denyAllMissingSkills()}>I don&apos;t have any of these skills</button>
                  </div>
                  {unresolvedRequiredCount === 0 && canContinueToOptimization && (
                    <p className="ats-improvement-note">All required confirmations are resolved. Preferred items are optional.</p>
                  )}
                </section>
              ) : (
                <section className="ats-evidence-confirmation">
                  <div className="ats-section-heading"><div><h3>Tell us about the skills you selected</h3><p>Add evidence in your own words. No values are inferred or generated.</p></div><button className="text-button" onClick={() => setConfirmationStage(1)}>Edit skill selection</button></div>
                  {confirmationSkills.filter((s) => skillDrafts[s.skill]?.answer === "has_it").map((skillInfo) => (
                    <CompactEvidenceRow key={skillInfo.skill} skillInfo={skillInfo} draft={skillDrafts[skillInfo.skill] || {}} onChange={(draft) => updateDraft(skillInfo.skill, draft)} />
                  ))}
                  {!confirmationSkills.some((s) => skillDrafts[s.skill]?.answer === "has_it") && <p className="ats-skill-meta">No skills selected. Your answers will be saved as unavailable for this job.</p>}
                  <p className="ats-confirmation-progress">{answeredDrafts.length} of {confirmationSkills.length} skills reviewed ({notFoundSkills.length} not found in resume, {weakSkills.length} possible evidence)</p>
                </section>
              )}

              <button
                className="button primary"
                disabled={confirmationStage !== 2 || !canSaveConfirmations || busy === "ats-skills"}
                onClick={handleSaveConfirmations}
              >
                {busy === "ats-skills" ? "Saving…" : "Save Evidence"}
              </button>
              {confirmationStage === 2 && <button className="button secondary" onClick={() => setStep(4)}>Skip for now</button>}
              <button
                className="button secondary"
                disabled={!canContinueToOptimization}
                title={!canContinueToOptimization ? "Analyze a job with a valid resume first." : ""}
                onClick={() => setStep(4)}
              >
                Continue to Resume Generation
              </button>
            </>
          )}
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

          <h4>Current verified from resume</h4>
          <ul className="ats-list">
            {verifiedSkills.length
              ? verifiedSkills.map((item) => <li key={item.skill || item.normalized_skill}>{item.skill}</li>)
              : <li>None</li>}
          </ul>

          <h4>Current user-confirmed evidence</h4>
          {userConfirmedEvidence.length ? (
            <ul className="ats-evidence-rows">
              {userConfirmedEvidence.map((row) => (
                <li key={row.id || `${row.skill}-${row.evidence_status}`} className="ats-evidence-row">
                  <div className="ats-evidence-copy">
                    <b>{row.skill}</b>
                    <span>{evidenceStatusLabel(row.evidence_status)}</span>
                  </div>
                  {row.id && (
                    pendingRemove?.id === row.id ? (
                      <div className="ats-evidence-confirm" role="group" aria-label={`Confirm removal of ${row.skill}`}>
                        <p>
                          Remove <b>{row.skill}</b> confirmation? This will remove your user-provided
                          evidence for this skill and may change your ATS score.
                        </p>
                        <div className="ats-evidence-confirm-actions">
                          <button
                            type="button"
                            className="button secondary"
                            onClick={() => setPendingRemove(null)}
                          >
                            Cancel
                          </button>
                          <button
                            type="button"
                            className="button danger"
                            disabled={busy === "ats-skills"}
                            onClick={confirmRemoveEvidence}
                          >
                            {busy === "ats-skills" ? "Removing…" : "Remove"}
                          </button>
                        </div>
                      </div>
                    ) : (
                      <button
                        type="button"
                        className="button danger ats-remove-evidence-btn"
                        onClick={() => setPendingRemove({ id: row.id, skill: row.skill })}
                        aria-label={`Remove evidence for ${row.skill}`}
                      >
                        Remove Evidence
                      </button>
                    )
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <ul className="ats-list"><li>None</li></ul>
          )}

          <h4>Unsupported requirements</h4>
          {currentUnresolvedRequired.length ? (
            <RemainingGapsPanel
              items={currentUnresolvedRequired.map((row) => ({
                ...row,
                requirement_type: row.requirement_type || "MANDATORY",
              }))}
            />
          ) : (
            <ul className="ats-list"><li>None</li></ul>
          )}

          <h4>Optional / preferred evidence</h4>
          <ul className="ats-list">
            {(optionalEvidence.length
              ? optionalEvidence
              : activeEvidence.filter((row) => (row.requirement_type || "").toUpperCase() === "PREFERRED")
            ).length
              ? (optionalEvidence.length
                ? optionalEvidence
                : activeEvidence.filter((row) => (row.requirement_type || "").toUpperCase() === "PREFERRED")
              ).map((row) => (
                <li key={row.id || row.skill}>
                  <b>{row.skill}</b>
                  {row.evidence_status ? ` — ${evidenceStatusLabel(row.evidence_status)}` : ""}
                </li>
              ))
              : <li>None (preferred items never block generation)</li>}
          </ul>

          <p>Still missing (kept out of the optimized resume, will reduce the reachable score):</p>
          <ul className="ats-list">
            {activeEvidence
              .filter((row) => row.evidence_status === "REJECTED")
              .map((row) => <li key={row.id || row.skill}>{row.skill}</li>)}
            {!activeEvidence.some((row) => row.evidence_status === "REJECTED") && <li>None</li>}
          </ul>

          {evidenceHistory.length > 0 && (
            <details className="ats-evidence-history">
              <summary>Previous evidence history (does not affect generation)</summary>
              <ul className="ats-list">
                {evidenceHistory.map((row) => (
                  <li key={`hist-${row.id || row.skill}-${row.evidence_status}`}>
                    {row.skill} — {row.evidence_status}
                  </li>
                ))}
              </ul>
            </details>
          )}

          {stepTransitionError && (
            <p className="ats-required-warning" role="alert">{stepTransitionError}</p>
          )}
          <button
            type="button"
            className="button primary"
            disabled={!canContinueToOptimization}
            title={!canContinueToOptimization ? "Analyze a job with a valid resume first." : ""}
            onClick={enterOptimizationStep}
          >
            Continue to Optimization
          </button>
        </Panel>
      )}

      {step === 5 && (
        <div className="rw-step5">
          <header className="rw-step5-intro">
            <div>
              <h2>Step 5 · Generate / Edit / Score / Download</h2>
              <p>
                Generate a truthful, job-aligned resume, then edit it in a professional document workspace.
                Original and optimized scores use the same ATS engine — unsupported requirements stay in Remaining Gaps.
              </p>
            </div>
            {!isComplete && (
              <div className="ats-confirmation-actions">
                <button
                  type="button"
                  className="button primary"
                  disabled={Boolean(disabledReason) || busy === "ats" || busy === "ats-skills" || !canContinueToOptimization}
                  title={
                    !canContinueToOptimization
                      ? (disabledReason || "Analyze a job with a valid resume first.")
                      : ""
                  }
                  onClick={runGenerateOptimizedResume}
                >
                  {busy === "ats" ? "Generating optimized resume…" : "Generate Optimized Resume"}
                </button>
              </div>
            )}
          </header>

          {stepTransitionError && (
            <p className="ats-required-warning" role="alert">{stepTransitionError}</p>
          )}
          {isGenerating && (
            <p className="ats-generation-indicator">Generating with AI…</p>
          )}
          {!isGenerating && generationLabel && (
            <p className="ats-generation-indicator" title={fallbackReason || undefined}>
              {generationLabel}
              {fallbackReason && generationLabel !== "Optimized locally · AI generation timed out"
                ? ` · ${fallbackReason}`
                : ""}
            </p>
          )}
          {canContinueToOptimization && (
            <RemainingGapsPanel
              items={isComplete ? authoritativeRemainingGaps : currentUnresolvedRequired}
            />
          )}
          {!canContinueToOptimization && (
            <p className="ats-required-warning">
              {disabledReason || "Upload a resume and analyze a job before generating."}
            </p>
          )}
          {status && !isComplete
            && !(status === "FAILED" && /evidence incomplete/i.test(String(result?.error_message || messages.ats || "")))
            && <PipelineProgress status={status} />}

          {result && majorMismatch && (
            <section className="ats-major-warning" role="alert">
              <h3>Major role mismatch detected</h3>
              <p>The uploaded resume is primarily aligned with {listText(resumeRoles)}, while this job requires {targetRole || "a different core role"} experience.</p>
              <p>This optimizer will not add unsupported technologies or experience.</p>
              <p>{result.warning || result.original_analysis?.warning_message}</p>
            </section>
          )}

          {isComplete && (
            <>
              {optimizationStatus === "NO_MEANINGFUL_CHANGE" && (
                <div className="ats-gap-banner" role="alert">
                  Your resume is already strongly aligned with this job. No additional evidence-backed
                  changes were available to safely increase the score.
                </div>
              )}
              {optimizationStatus === "NO_IMPROVEMENT" && (
                <div className="ats-gap-banner" role="alert">
                  Content was updated, but the ATS score did not increase under truthfulness constraints.
                  Confirm additional genuine skills with evidence or edit the resume below.
                </div>
              )}
              {whyNot95 && (
                <div className="ats-why-not-95">
                  <b>Why not 95%?</b>
                  <p>{whyNot95}</p>
                </div>
              )}

              {(workspaceDocument || editableResume || generatedPlain) ? (
                <ResumeWorkspace
                  key={result.optimized_resume_id || "workspace"}
                  initialDocument={workspaceDocument}
                  initialText={editableResume?.final_text || generatedPlain || generatedPreview || ""}
                  originalScore={originalScore}
                  initialCurrentScore={currentEditorScore}
                  initialScore={liveScoreView || optimizedScoreView || null}
                  improvementReport={improvementReport}
                  resultMeta={{
                    content_changed: contentChanged,
                    confirmed_skills_used: confirmedSkillsUsed,
                    unsupported_keywords_not_added: unsupported,
                  }}
                  gapsUnsupported={gapItemsFromScoreView(liveScoreView || optimizedScoreView, result).map((item) => item.skill)}
                  gapsPreferred={
                    (optimizedScoreView?.remaining_gaps?.preferred_gaps
                      || optimizedScoreView?.preferred_requirements?.missing
                      || result?.optimized_analysis?.missing_preferred_skills
                      || [])
                  }
                  confirmedSkillsIncorporated={confirmedSkillsUsed}
                  generationLabel={generationLabel}
                  fallbackReason={fallbackReason}
                  downloadDocxUrl={
                    canDownloadOptimized
                      ? getFinalResumeDownloadUrl(currentUser.user_id, selectedJob.id, result.optimized_resume_id, "docx")
                      : null
                  }
                  downloadPdfUrl={
                    canDownloadOptimized
                      ? getFinalResumeDownloadUrl(currentUser.user_id, selectedJob.id, result.optimized_resume_id, "pdf")
                      : null
                  }
                  downloadTxtUrl={
                    canDownloadOptimized
                      ? getFinalResumeDownloadUrl(currentUser.user_id, selectedJob.id, result.optimized_resume_id, "txt")
                      : null
                  }
                  onSave={saveWorkspace}
                  onScore={scoreWorkspace}
                  onImprove={improveWorkspaceSnippet}
                  onRegenerate={runGenerateOptimizedResume}
                  generating={busy === "ats"}
                />
              ) : (
                <EmptyState title="Resume workspace loading" text="Preparing your editable optimized resume…" />
              )}
            </>
          )}
        </div>
      )}
    </Page>
  );
}
