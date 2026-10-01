/** User-facing ATS wizard steps vs internal Step-5 generation substages.

Wizard Step 2 ("Analyze Requirements") is a separate API from the generate
pipeline's ANALYZING_ORIGINAL substage (original-resume scoring). Mixing those
is what made Step 5 look like it jumped back to Step 2.
*/

export const WIZARD_STEP = {
  SELECT_JOB: 1,
  ANALYZE_REQUIREMENTS: 2,
  CONFIRM_SKILLS: 3,
  REVIEW_EVIDENCE: 4,
  GENERATE: 5,
};

/** Backend workflow_status values that belong to wizard Step 5. */
export const STEP5_WORKFLOW_STATUSES = new Set([
  "ANALYZING_ORIGINAL",
  "GENERATING_RESUME",
  "VALIDATING_RESUME",
  "ANALYZING_OPTIMIZED",
  "COMPLETED",
  "VALIDATION_FAILED",
  "FAILED",
  "INELIGIBLE_LOW_SCORE",
  "INELIGIBLE_ROLE_ALIGNMENT",
]);

export const STEP5_SUBSTAGES = {
  ANALYZING_ORIGINAL: "calculating_original_score",
  GENERATING_RESUME: "generating_resume",
  VALIDATING_RESUME: "validating_resume",
  ANALYZING_OPTIMIZED: "calculating_optimized_score",
  COMPLETED: "completed",
  VALIDATION_FAILED: "failed",
  FAILED: "failed",
  PENDING: "generating_resume",
};

export function generationSubstage(status) {
  const key = String(status || "").toUpperCase();
  return STEP5_SUBSTAGES[key] || null;
}

export function isStep5WorkflowStatus(status, { generating = false, hasGeneratedResume = false } = {}) {
  if (generating || hasGeneratedResume) return true;
  return STEP5_WORKFLOW_STATUSES.has(String(status || "").toUpperCase());
}

export function majorStepFromWorkflow(status, options = {}) {
  if (isStep5WorkflowStatus(status, options)) return WIZARD_STEP.GENERATE;
  return null;
}

/** Automatic wizard updates never move backward. User clicks and invalidation may. */
export function nextMonotonicStep(current, incoming) {
  const from = Number(current) || WIZARD_STEP.SELECT_JOB;
  const to = Number(incoming) || from;
  if (!Number.isFinite(to) || to < 1) return from;
  return Math.max(from, to);
}

export function applyStaleWizardStep({ currentStep, incomingStep, incomingGeneration, activeGeneration }) {
  if (incomingGeneration != null && activeGeneration != null && incomingGeneration !== activeGeneration) {
    return currentStep;
  }
  return nextMonotonicStep(currentStep, incomingStep);
}

/** Job a workflow payload belongs to (workflow_to_dict exposes both shapes). */
export function atsResultJobId(result) {
  const id = result?.job_id ?? result?.job?.id ?? result?.job?.job_id;
  return id == null || id === "" ? null : String(id);
}

function sameScope(incoming, current, key) {
  const a = key === "job" ? atsResultJobId(incoming) : incoming?.resume_id;
  const b = key === "job" ? atsResultJobId(current) : current?.resume_id;
  return a == null || b == null || String(a) === String(b);
}

export function isAtsResultNewer(incoming, current) {
  if (!incoming) return false;
  if (!current) return true;
  // Workflow ids are global across jobs: a result for a different job always
  // replaces the current one — never compare ids across job scopes.
  if (!sameScope(incoming, current, "job")) return true;
  const incomingId = Number(incoming.workflow_id);
  const currentId = Number(current.workflow_id);
  if (Number.isFinite(incomingId) && Number.isFinite(currentId) && incomingId !== currentId) {
    return incomingId >= currentId;
  }
  const incomingAt = Date.parse(incoming.updated_at || incoming.completed_at || incoming.last_activity_at || "") || 0;
  const currentAt = Date.parse(current.updated_at || current.completed_at || current.last_activity_at || "") || 0;
  if (incomingAt && currentAt) return incomingAt >= currentAt;
  return true;
}

/** Keep generated resume/score if a stale payload arrives without them. */
export function mergeAtsResult(incoming, current) {
  if (!incoming) return current || null;
  if (!isAtsResultNewer(incoming, current)) {
    return current;
  }
  // Only carry a generated resume forward within the same job + source resume;
  // never graft another job's optimized resume or scores onto this payload.
  const keepGenerated = current?.optimized_resume_id
    && !incoming?.optimized_resume_id
    && sameScope(incoming, current, "job")
    && sameScope(incoming, current, "resume");
  if (!keepGenerated) return incoming;
  return {
    ...incoming,
    optimized_resume_id: current.optimized_resume_id,
    generated_resume: incoming.generated_resume || current.generated_resume,
    generated_resume_plain: incoming.generated_resume_plain || current.generated_resume_plain,
    generated_resume_plain_text: incoming.generated_resume_plain_text || current.generated_resume_plain_text,
    optimized_match_percentage: incoming.optimized_match_percentage ?? current.optimized_match_percentage,
    optimized_match_score: incoming.optimized_match_score ?? current.optimized_match_score,
    original_match_percentage: incoming.original_match_percentage ?? current.original_match_percentage,
    original_match_score: incoming.original_match_score ?? current.original_match_score,
  };
}

export function wizardStorageKey(jobId) {
  return jobId == null ? null : `atsWizardStep:${jobId}`;
}

export function readStoredWizardStep(jobId) {
  const key = wizardStorageKey(jobId);
  if (!key || typeof sessionStorage === "undefined") return null;
  try {
    const value = Number(sessionStorage.getItem(key));
    return value >= 1 && value <= 5 ? value : null;
  } catch {
    return null;
  }
}

export function writeStoredWizardStep(jobId, step) {
  const key = wizardStorageKey(jobId);
  if (!key || typeof sessionStorage === "undefined") return;
  try {
    sessionStorage.setItem(key, String(step));
    if (Number(step) >= WIZARD_STEP.GENERATE) {
      clearGuidedRestart(jobId);
    }
  } catch {
    /* ignore quota / private mode */
  }
}

export function clearStoredWizardStep(jobId) {
  const key = wizardStorageKey(jobId);
  if (!key || typeof sessionStorage === "undefined") return;
  try {
    sessionStorage.removeItem(key);
  } catch {
    /* ignore */
  }
}

/**
 * Two ATS entry modes:
 * - resume_existing_workflow: sidebar /ats-analyzer restores the persisted wizard
 * - start_optimization: Selected Jobs "Optimize and Compare Scores" starts Step 1
 *
 * The start intent is stored in sessionStorage *and* location.state so the
 * initial render can land on Step 1 before completed-workflow hydration runs.
 * It must be consumed once so later refresh restores the real persisted step.
 */
export const ATS_ENTRY_MODE = {
  START_OPTIMIZATION: "start_optimization",
  START_SELECTED_JOB: "start_selected_job",
  RESUME_EXISTING: "resume_existing_workflow",
};

function freshSelectedJobKey(userId, jobId) {
  return userId == null || jobId == null ? null : `atsFreshSelected:${userId}:${jobId}`;
}

export function claimAtsWizardForUser(userId, jobId) {
  if (userId == null || jobId == null || typeof sessionStorage === "undefined") return;
  const key = `atsWizardOwner:${jobId}`;
  const owner = sessionStorage.getItem(key);
  if (owner && owner !== String(userId)) {
    clearStoredWizardStep(jobId);
    clearGuidedRestart(jobId);
    consumeStartOptimizationIntent(jobId);
  }
  sessionStorage.setItem(key, String(userId));
}

export function writeFreshSelectedJobIntent(userId, jobId) {
  const key = freshSelectedJobKey(userId, jobId);
  if (!key || typeof sessionStorage === "undefined") return;
  claimAtsWizardForUser(userId, jobId);
  sessionStorage.setItem(key, "1");
  writeGuidedRestart(jobId);
  writeStoredWizardStep(jobId, WIZARD_STEP.ANALYZE_REQUIREMENTS);
}

export function peekFreshSelectedJobIntent(userId, jobId) {
  const key = freshSelectedJobKey(userId, jobId);
  return Boolean(key && typeof sessionStorage !== "undefined" && sessionStorage.getItem(key) === "1");
}

export function clearFreshSelectedJobIntent(userId, jobId) {
  const key = freshSelectedJobKey(userId, jobId);
  if (key && typeof sessionStorage !== "undefined") sessionStorage.removeItem(key);
}

export function startOptimizationIntentKey(jobId) {
  return jobId == null ? null : `atsStartOptimization:${jobId}`;
}

export function guidedRestartKey(jobId) {
  return jobId == null ? null : `atsGuidedRestart:${jobId}`;
}

export function peekGuidedRestart(jobId) {
  const key = guidedRestartKey(jobId);
  if (!key || typeof sessionStorage === "undefined") return false;
  try {
    return sessionStorage.getItem(key) === "1";
  } catch {
    return false;
  }
}

export function writeGuidedRestart(jobId) {
  const key = guidedRestartKey(jobId);
  if (!key || typeof sessionStorage === "undefined") return;
  try {
    sessionStorage.setItem(key, "1");
  } catch {
    /* ignore quota / private mode */
  }
}

export function clearGuidedRestart(jobId) {
  const key = guidedRestartKey(jobId);
  if (!key || typeof sessionStorage === "undefined") return;
  try {
    sessionStorage.removeItem(key);
  } catch {
    /* ignore */
  }
}

export function writeStartOptimizationIntent(jobId) {
  const key = startOptimizationIntentKey(jobId);
  if (!key || typeof sessionStorage === "undefined") return;
  try {
    sessionStorage.setItem(key, "1");
    writeGuidedRestart(jobId);
  } catch {
    /* ignore quota / private mode */
  }
}

export function peekStartOptimizationIntent(jobId, locationState) {
  if (locationState?.atsEntryMode === ATS_ENTRY_MODE.START_OPTIMIZATION) return true;
  const key = startOptimizationIntentKey(jobId);
  if (!key || typeof sessionStorage === "undefined") return false;
  try {
    return sessionStorage.getItem(key) === "1";
  } catch {
    return false;
  }
}

export function consumeStartOptimizationIntent(jobId) {
  const key = startOptimizationIntentKey(jobId);
  if (!key || typeof sessionStorage === "undefined") return false;
  try {
    const present = sessionStorage.getItem(key) === "1";
    if (present) sessionStorage.removeItem(key);
    return present;
  } catch {
    return false;
  }
}

export function readHistoryAtsEntryMode() {
  try {
    return window.history?.state?.usr?.atsEntryMode
      || window.history?.state?.state?.atsEntryMode
      || null;
  } catch {
    return null;
  }
}

export function resolveInitialWizardStep(jobId, locationState) {
  if (locationState?.atsEntryMode === ATS_ENTRY_MODE.START_SELECTED_JOB) {
    return WIZARD_STEP.ANALYZE_REQUIREMENTS;
  }
  const fromHistory = readHistoryAtsEntryMode() === ATS_ENTRY_MODE.START_OPTIMIZATION;
  if (fromHistory || peekStartOptimizationIntent(jobId, locationState)) {
    return WIZARD_STEP.SELECT_JOB;
  }
  return readStoredWizardStep(jobId) || WIZARD_STEP.SELECT_JOB;
}

export function stripAtsEntryMode(locationState) {
  if (!locationState || typeof locationState !== "object") return locationState || {};
  const { atsEntryMode: _ignored, ...rest } = locationState;
  return rest;
}

/**
 * After start_optimization is consumed, a completed workflow must not jump
 * the visible step to 5. Persist the user's current step instead.
 * Sidebar /ats-analyzer with no start intent still restores Step 5 when
 * stored is missing/≥5 or a Step-5 workflow is in progress.
 */
export function resolveRehydratedWizardStep({
  storedStep,
  generating = false,
  completed = false,
  failedOnStep5 = false,
  derivedStep = null,
} = {}) {
  if (generating) return WIZARD_STEP.GENERATE;
  if (storedStep != null) return storedStep;
  if (completed || failedOnStep5 || derivedStep === WIZARD_STEP.GENERATE) {
    return WIZARD_STEP.GENERATE;
  }
  return derivedStep || WIZARD_STEP.SELECT_JOB;
}
