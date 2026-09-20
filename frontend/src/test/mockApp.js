import { vi } from "vitest";

export function makeApp(overrides = {}) {
  return {
    currentUser: { user_id: 7, email: "candidate@example.test", name: "Test Candidate" },
    backendStatus: "Backend is running", busy: "", messages: {},
    preferences: { job_title: "", location: "", experience: "", work_mode: "", job_type: "", skills: "", schedule_time: "08:00", timezone: "America/Chicago" },
    preferencesSaved: false, latestResume: null, selectedResumeFile: null,
    jobs: [], selectedJobs: [], matches: [], optimizedResumes: [], emailLogs: [], workflowResult: null,
    gmailStatus: { connected: false, status: "DISCONNECTED", email_address: null },
    emailSummaryPreferences: {
      daily_summary_delivery_enabled: false, automatic_gmail_scan_enabled: false, daily_summary_recipient: "", effective_daily_summary_recipient: "candidate@example.test",
      gmail_scan_frequency: "daily", email_types_to_scan: ["job_opportunities", "recruiter_messages", "application_updates", "interview_invitations"],
    },
    applications: [], jobEmailEvents: [],
    selectedJobForm: { title: "", company: "", location: "", work_mode: "Remote", job_type: "Full-time", description: "", skills_required: "", source: "User Selected Job", job_url: "" },
    selectedJobImportReady: false, selectedJobImportStatus: null, deletingJobId: null,
    atsResult: null, activeAtsWorkflowId: null, previousAtsResult: null, atsSelectedJobId: "", requirementAnalysis: null, missingSkillsGroups: null, confirmedSkills: [],
    authenticate: vi.fn(), clearAuthMessages: vi.fn(), requestPasswordReset: vi.fn(), submitPasswordReset: vi.fn(), logout: vi.fn(), changePreferences: vi.fn(), savePreferences: vi.fn(), loadPreferences: vi.fn(),
    setSelectedResumeFile: vi.fn(), uploadResume: vi.fn(), loadLatestResume: vi.fn(), loadJobs: vi.fn(), search: vi.fn(),
    changeSelectedJob: vi.fn(), previewSelectedJobUrl: vi.fn(), clearSelectedJob: vi.fn(), saveSelectedJob: vi.fn(), loadSelectedJobs: vi.fn(), removeSelectedJob: vi.fn(), verifyLatestResume: vi.fn(),
    setAtsSelectedJobId: vi.fn(), setAtsResult: vi.fn(), setMissingSkillsGroups: vi.fn(), analyzeAndOptimize: vi.fn(), loadAtsResultForJob: vi.fn(), loadMissingSkills: vi.fn(), loadSkillConfirmations: vi.fn(), saveConfirmedSkills: vi.fn(), deleteConfirmedSkill: vi.fn(), retryAtsWorkflow: vi.fn(),
    calculateMatches: vi.fn(), loadMatches: vi.fn(), loadOptimizedResumes: vi.fn(), optimizeResume: vi.fn(),
    generateEmailSummary: vi.fn(), loadEmailLogs: vi.fn(), runWorkflow: vi.fn(), refreshUserWorkspaceState: vi.fn(),
    loadGmailStatus: vi.fn(), connectGmailAccount: vi.fn(), disconnectGmailAccount: vi.fn(), scanGmailForJobs: vi.fn(),
    loadEmailSummaryPreferences: vi.fn(), saveEmailSummaryPreferences: vi.fn(),
    loadApplications: vi.fn(), updateApplicationStatusAction: vi.fn(), loadJobEmailEvents: vi.fn(), resolveJobEmailEventAction: vi.fn(),
    markJobEmailEventReadAction: vi.fn(), toggleJobEmailEventStarAction: vi.fn(),
    ...overrides,
  };
}
