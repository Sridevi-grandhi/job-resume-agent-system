/** Friendly labels for evidence status enums shown in the ATS UI. */

const EVIDENCE_STATUS_LABELS = {
  USER_CONFIRMED_PROFESSIONAL: "Professional experience confirmed",
  USER_CONFIRMED_PROJECT: "Project experience confirmed",
  USER_CONFIRMED_CERTIFICATION: "Certification confirmed",
  USER_CONFIRMED_TRAINING: "Training / coursework confirmed",
  LEARNING_ONLY: "Learning / training only",
  NOT_POSSESSED: "Not currently possessed",
  REJECTED: "Not currently possessed",
  INCOMPLETE: "Evidence incomplete",
  VERIFIED_FROM_RESUME: "Verified from resume",
  OBSOLETE: "Superseded",
};

export function evidenceStatusLabel(status) {
  if (!status) return "Evidence recorded";
  return EVIDENCE_STATUS_LABELS[status] || String(status).replace(/_/g, " ").toLowerCase();
}
