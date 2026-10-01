import { beforeEach, describe, expect, it } from "vitest";
import {
  ATS_ENTRY_MODE,
  WIZARD_STEP,
  applyStaleWizardStep,
  clearGuidedRestart,
  consumeStartOptimizationIntent,
  generationSubstage,
  isAtsResultNewer,
  majorStepFromWorkflow,
  mergeAtsResult,
  nextMonotonicStep,
  peekGuidedRestart,
  peekStartOptimizationIntent,
  readStoredWizardStep,
  resolveInitialWizardStep,
  resolveRehydratedWizardStep,
  stripAtsEntryMode,
  writeGuidedRestart,
  writeStartOptimizationIntent,
  writeStoredWizardStep,
  clearStoredWizardStep,
} from "./atsWizardState";

describe("ATS wizard state helpers", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it("A. normal workflow steps only move forward", () => {
    let step = 1;
    for (const next of [2, 3, 4, 5]) {
      step = nextMonotonicStep(step, next);
    }
    expect(step).toBe(5);
    expect(nextMonotonicStep(5, 2)).toBe(5);
    expect(nextMonotonicStep(5, 3)).toBe(5);
    expect(nextMonotonicStep(4, 2)).toBe(4);
  });

  it("B. Step-5 internal analysis stays major step 5", () => {
    expect(majorStepFromWorkflow("ANALYZING_ORIGINAL")).toBe(WIZARD_STEP.GENERATE);
    expect(generationSubstage("ANALYZING_ORIGINAL")).toBe("calculating_original_score");
    expect(generationSubstage("VALIDATING_RESUME")).toBe("validating_resume");
    expect(generationSubstage("ANALYZING_OPTIMIZED")).toBe("calculating_optimized_score");
    expect(nextMonotonicStep(5, majorStepFromWorkflow("ANALYZING_ORIGINAL"))).toBe(5);
  });

  it("C. stale Step-2 response cannot overwrite Step 5", () => {
    expect(applyStaleWizardStep({
      currentStep: 5,
      incomingStep: 2,
      incomingGeneration: 1,
      activeGeneration: 2,
    })).toBe(5);
    expect(applyStaleWizardStep({
      currentStep: 5,
      incomingStep: 2,
      incomingGeneration: 2,
      activeGeneration: 2,
    })).toBe(5);
  });

  it("E. session storage rehydrates Step 5", () => {
    writeStoredWizardStep(42, 5);
    expect(readStoredWizardStep(42)).toBe(5);
    clearStoredWizardStep(42);
    expect(readStoredWizardStep(42)).toBeNull();
  });

  it("stale workflow payloads cannot drop a generated resume", () => {
    const current = {
      workflow_id: 10,
      workflow_status: "COMPLETED",
      updated_at: "2026-09-23T18:00:00Z",
      optimized_resume_id: 91,
      generated_resume: { plain_text: "Kept resume" },
      optimized_match_percentage: 82,
    };
    const incoming = {
      workflow_id: 10,
      workflow_status: "ANALYZING_ORIGINAL",
      updated_at: "2026-09-23T17:00:00Z",
    };
    expect(isAtsResultNewer(incoming, current)).toBe(false);
    expect(mergeAtsResult(incoming, current)).toEqual(current);
  });

  it("a lower-id workflow for a DIFFERENT job replaces the current result (no cross-job id comparison)", () => {
    // Incident: Job 1849 completed as workflow 341 (original 88, resume 141);
    // opening Job 1848 loaded its own INELIGIBLE workflow 340 (original 35).
    const current = {
      workflow_id: 341, job_id: 1849, resume_id: 59, workflow_status: "COMPLETED",
      optimized_resume_id: 141, original_match_percentage: 88, optimized_match_percentage: 88,
      generated_resume: { plain_text: "Other job resume" },
    };
    const incoming = {
      workflow_id: 340, job_id: 1848, resume_id: 59, workflow_status: "INELIGIBLE_LOW_SCORE",
      optimized_resume_id: null, original_match_percentage: 35, optimized_match_percentage: null,
    };
    expect(isAtsResultNewer(incoming, current)).toBe(true);
    const merged = mergeAtsResult(incoming, current);
    expect(merged).toEqual(incoming);
    expect(merged.optimized_resume_id).toBeNull();
    expect(merged.original_match_percentage).toBe(35);
  });

  it("job id is read from the nested job payload when job_id is absent", () => {
    const current = { workflow_id: 9, job: { id: 5 }, optimized_resume_id: 3, workflow_status: "COMPLETED" };
    const incoming = { workflow_id: 2, job: { id: 6 }, workflow_status: "PENDING" };
    expect(mergeAtsResult(incoming, current)).toEqual(incoming);
  });

  it("never grafts a generated resume from another source resume of the same job", () => {
    const current = { workflow_id: 10, job_id: 7, resume_id: 1, workflow_status: "COMPLETED", optimized_resume_id: 91, original_match_percentage: 80 };
    const incoming = { workflow_id: 11, job_id: 7, resume_id: 2, workflow_status: "INELIGIBLE_LOW_SCORE", original_match_percentage: 40 };
    const merged = mergeAtsResult(incoming, current);
    expect(merged.optimized_resume_id).toBeUndefined();
    expect(merged.original_match_percentage).toBe(40);
  });

  it("newer same-workflow updates are applied", () => {
    const current = { workflow_id: 10, workflow_status: "GENERATING_RESUME", updated_at: "2026-09-23T17:00:00Z" };
    const incoming = { workflow_id: 10, workflow_status: "VALIDATING_RESUME", updated_at: "2026-09-23T17:01:00Z" };
    expect(mergeAtsResult(incoming, current)).toEqual(incoming);
  });

  it("start_optimization intent forces Step 1 even when stored step is 5", () => {
    writeStoredWizardStep(42, 5);
    writeStartOptimizationIntent(42);
    expect(peekStartOptimizationIntent(42)).toBe(true);
    expect(peekGuidedRestart(42)).toBe(true);
    expect(resolveInitialWizardStep(42, { atsEntryMode: ATS_ENTRY_MODE.START_OPTIMIZATION })).toBe(1);
    expect(consumeStartOptimizationIntent(42)).toBe(true);
    expect(peekStartOptimizationIntent(42)).toBe(false);
    expect(peekGuidedRestart(42)).toBe(true);
  });

  it("consumed start intent does not keep forcing Step 1 on later refresh", () => {
    writeGuidedRestart(42);
    writeStoredWizardStep(42, 3);
    expect(peekStartOptimizationIntent(42)).toBe(false);
    expect(resolveInitialWizardStep(42)).toBe(3);
    expect(resolveRehydratedWizardStep({
      storedStep: 3,
      completed: true,
    })).toBe(3);
  });

  it("sidebar restore still uses stored Step 5 / completed workflow when no start intent exists", () => {
    writeStoredWizardStep(42, 5);
    expect(resolveInitialWizardStep(42)).toBe(5);
    expect(resolveRehydratedWizardStep({
      storedStep: null,
      completed: true,
    })).toBe(5);
    expect(resolveRehydratedWizardStep({
      storedStep: 5,
      completed: true,
    })).toBe(5);
  });

  it("reaching Step 5 clears the guided-restart flag", () => {
    writeGuidedRestart(42);
    writeStoredWizardStep(42, 5);
    expect(peekGuidedRestart(42)).toBe(false);
    clearGuidedRestart(42);
  });

  it("stripAtsEntryMode removes only the one-time navigation intent", () => {
    expect(stripAtsEntryMode({
      job_id: 42,
      title: "Java Engineer",
      atsEntryMode: ATS_ENTRY_MODE.START_OPTIMIZATION,
    })).toEqual({ job_id: 42, title: "Java Engineer" });
  });
});
