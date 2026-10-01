/* eslint-disable react-hooks/exhaustive-deps, react-refresh/only-export-components */
import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";
import * as api from "../api";
import { mergeAtsResult } from "../utils/atsWizardState";
import { averageCompletedMatch } from "../utils/matchScoreUtils";

const AppContext = createContext(null);
const emptyPreferences = { job_title:"", location:"", experience:"", work_mode:"", job_type:"", skills:"", schedule_time:"08:00", timezone:"America/Chicago" };
const ALL_EMAIL_CATEGORIES = ["job_opportunities", "recruiter_messages", "application_updates", "interview_invitations"];
const emptyEmailSummaryPreferences = {
  daily_summary_delivery_enabled: false, automatic_gmail_scan_enabled: false, daily_summary_recipient: "", effective_daily_summary_recipient: "",
  gmail_scan_frequency: "daily", email_types_to_scan: ALL_EMAIL_CATEGORIES,
  job_alerts_enabled: true, application_updates_enabled: true, interview_alerts_enabled: true,
};
const emptySelectedJob = { title:"", company:"", location:"", work_mode:"Remote", job_type:"Full-time", description:"", skills_required:"", source:"User Selected Job", job_url:"" };
const messageOf = (error) => error?.message || "Something went wrong.";

const STATUS_LABELS = {
  PENDING: "Ready",
  WAITING_FOR_RESUME: "Waiting for resume upload",
  INCOMPLETE_JOB: "Incomplete job — cannot optimize",
  ANALYZING_ORIGINAL: "Analyzing Original Resume",
  GENERATING_RESUME: "Generating optimized resume",
  VALIDATING_RESUME: "Validating resume",
  VALIDATION_FAILED: "Validation failed",
  ANALYZING_OPTIMIZED: "Calculating optimized match",
  COMPLETED: "Completed",
  FAILED: "Failed",
  INELIGIBLE_LOW_SCORE: "Below 65% optimization threshold",
  INELIGIBLE_ROLE_ALIGNMENT: "Role alignment blocks optimization",
  SKIPPED_GENERIC_SEARCH_LINK: "Skipped generic search link",
};

const TERMINAL_ATS_START_STATUSES = new Set([
  "COMPLETED",
  "FAILED",
  "VALIDATION_FAILED",
  "INCOMPLETE_JOB",
  "WAITING_FOR_RESUME",
  "INELIGIBLE_LOW_SCORE",
  "INELIGIBLE_ROLE_ALIGNMENT",
  "SKIPPED_GENERIC_SEARCH_LINK",
]);

function statusLabel(status, detail) {
  if (status === "VALIDATION_FAILED") {
    return "Validation failed. The generated resume contained unsupported information.";
  }
  const base = STATUS_LABELS[status] || status || "Processing…";
  return detail ? `${base}: ${detail}` : base;
}

export function AppProvider({ children }) {
  const [currentUser, setCurrentUser] = useState(() => { try { return JSON.parse(localStorage.getItem("currentUser")); } catch { return null; } });
  const [backendStatus, setBackendStatus] = useState("Checking backend…");
  const [preferences, setPreferences] = useState(emptyPreferences);
  const [preferencesSaved, setPreferencesSaved] = useState(false);
  const [latestResume, setLatestResume] = useState(null);
  const [selectedResumeFile, setSelectedResumeFile] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [selectedJobs, setSelectedJobs] = useState([]);
  const [selectedJobForm, setSelectedJobForm] = useState(emptySelectedJob);
  const [selectedJobImportReady, setSelectedJobImportReady] = useState(false);
  const [selectedJobImportStatus, setSelectedJobImportStatus] = useState(null);
  const [optimizedResumes, setOptimizedResumes] = useState([]);
  const [matches, setMatches] = useState([]);
  const [atsResult, setAtsResult] = useState(null);
  const [activeAtsWorkflowId, setActiveAtsWorkflowId] = useState(null);
  const [previousAtsResult, setPreviousAtsResult] = useState(null);
  const [atsSelectedJobId, setAtsSelectedJobId] = useState("");
  const [requirementAnalysis, setRequirementAnalysis] = useState(null);
  const [missingSkillsGroups, setMissingSkillsGroups] = useState(null);
  const [confirmedSkills, setConfirmedSkills] = useState([]);
  const [emailLogs, setEmailLogs] = useState([]);
  const [emailSummaryPreferences, setEmailSummaryPreferences] = useState(emptyEmailSummaryPreferences);
  const [gmailStatus, setGmailStatus] = useState(null);
  const [applications, setApplications] = useState([]);
  const [jobEmailEvents, setJobEmailEvents] = useState([]);
  const [workflowResult, setWorkflowResult] = useState(null);
  const [messages, setMessages] = useState({});
  const [busy, setBusy] = useState("");
  const [deletingJobId, setDeletingJobId] = useState(null);
  const userId = currentUser?.user_id;
  const activeAtsWorkflowIdRef = useRef(null);
  const atsPollGenerationRef = useRef(0);
  const atsGeneratingLockRef = useRef(false);
  const busyTicketRef = useRef(0);
  const busyKeyRef = useRef("");
  const missingSkillsGroupsRef = useRef(null);
  const emailPreferencesSaveVersionRef = useRef(0);
  const tell = (key, value) => setMessages((old) => ({ ...old, [key]: value }));
  const clearMessage = (key) => setMessages((old) => {
    if (!(key in old)) return old;
    const next = { ...old };
    delete next[key];
    return next;
  });
  const task = async (key, fn) => {
    if (key === "ats") {
      if (atsGeneratingLockRef.current || busyKeyRef.current === "ats") {
        tell("ats", "Resume generation is already in progress.");
        return null;
      }
      atsGeneratingLockRef.current = true;
    }
    const stealBusy = !(atsGeneratingLockRef.current && key !== "ats");
    const ticket = ++busyTicketRef.current;
    if (stealBusy) {
      busyKeyRef.current = key;
      setBusy(key);
      clearMessage(key);
    }
    try { return await fn(); } catch (e) { tell(key, messageOf(e)); } finally {
      if (key === "ats") atsGeneratingLockRef.current = false;
      if (stealBusy && busyTicketRef.current === ticket && busyKeyRef.current === key) {
        busyKeyRef.current = "";
        setBusy("");
      }
    }
  };

  const beginAtsPoll = (workflowId) => {
    atsPollGenerationRef.current += 1;
    const generation = atsPollGenerationRef.current;
    activeAtsWorkflowIdRef.current = Number(workflowId);
    return {
      generation,
      isCancelled: () => (
        generation !== atsPollGenerationRef.current
        || Number(activeAtsWorkflowIdRef.current) !== Number(workflowId)
      ),
      onProgress: (status) => {
        if (generation !== atsPollGenerationRef.current) return;
        if (Number(status?.workflow_id) !== Number(workflowId)) return;
        setAtsResult((current) => mergeAtsResult(status, current) || current);
        tell("ats", statusLabel(status.workflow_status));
      },
    };
  };

  const cancelAtsPolling = () => {
    atsPollGenerationRef.current += 1;
    activeAtsWorkflowIdRef.current = null;
  };

  const startAtsForSelectedJob = (jobId) => {
    cancelAtsPolling();
    setActiveAtsWorkflowId(null);
    setPreviousAtsResult(null);
    setAtsResult(null);
    setRequirementAnalysis(null);
    setMissingSkillsGroups(null);
    missingSkillsGroupsRef.current = null;
    setConfirmedSkills([]);
    clearMessage("ats");
    clearMessage("ats-requirements");
    setAtsSelectedJobId(String(jobId));
  };

  useEffect(() => { api.checkBackendHealth().then((d) => setBackendStatus(d.status || "Backend is running")).catch(() => setBackendStatus("Backend not connected")); }, []);

  useEffect(() => () => {
    cancelAtsPolling();
    atsGeneratingLockRef.current = false;
  }, []);

  const resetData = () => {
    setPreferences(emptyPreferences); setPreferencesSaved(false); setLatestResume(null); setSelectedResumeFile(null);
    setJobs([]); setSelectedJobs([]); setSelectedJobForm(emptySelectedJob); setSelectedJobImportReady(false); setSelectedJobImportStatus(null); setOptimizedResumes([]); setMatches([]);
    setAtsResult(null); setAtsSelectedJobId(""); setRequirementAnalysis(null); setMissingSkillsGroups(null); setConfirmedSkills([]); setEmailLogs([]);
    setWorkflowResult(null); setEmailSummaryPreferences(emptyEmailSummaryPreferences); setMessages({});
  };
  const authenticate = async (mode, form) => task(mode === "signup" ? "authSignup" : "authLogin", async () => {
    const data = mode === "login" ? await api.loginUser(form) : await api.signupUser(form);
    const user = { user_id:data.user_id, email:data.email, name:String(data.name || form.name || "").trim() };
    localStorage.setItem("currentUser", JSON.stringify(user)); resetData(); setCurrentUser(user); return true;
  });
  const clearAuthMessages = () => {
    setMessages((old) => {
      const next = { ...old };
      delete next.auth;
      delete next.authLogin;
      delete next.authSignup;
      delete next.authForgot;
      delete next.authReset;
      return next;
    });
  };
  const requestPasswordReset = (email) => task("authForgot", async () => {
    const data = await api.forgotPassword(email);
    tell("authForgot", data.message || "If an account exists for this email, password reset instructions have been sent.");
    return data;
  });
  const submitPasswordReset = ({ token, new_password }) => task("authReset", async () => {
    const data = await api.resetPassword({ token, new_password });
    tell("authReset", data.message || "Password updated successfully.");
    return data;
  });
  const logout = () => { localStorage.removeItem("currentUser"); resetData(); setCurrentUser(null); };
  const saveProfileName = ({ name, current_password }) => task("profile", async () => {
    const data = await api.updateProfileName(userId, { name, current_password });
    const user = { ...currentUser, user_id: data.user_id, name: data.name, email: data.email };
    localStorage.setItem("currentUser", JSON.stringify(user));
    setCurrentUser(user);
    tell("profile", "Profile saved.");
    return data;
  });
  const changePreferences = (e) => { setPreferences((p) => ({...p,[e.target.name]:e.target.value})); setPreferencesSaved(false); };
  const savePreferences = (e) => { e?.preventDefault(); return task("preferences", async () => { const d=await api.saveUserPreferences({...preferences,user_id:Number(userId)}); setPreferencesSaved(true); tell("preferences",d.message||"Preferences saved."); }); };
  const loadPreferences = () => task("preferences", async () => { const d=await api.getUserPreferences(userId); setPreferences({...emptyPreferences,...d,schedule_time:d.schedule_time?.slice(0,5)||"08:00"}); setPreferencesSaved(true); tell("preferences","Saved preferences loaded."); });
  const uploadResume = () => task("resume", async () => {
    if (!selectedResumeFile) throw new Error("Choose a resume file first.");
    const d = await api.uploadResume(userId, selectedResumeFile);
    setLatestResume(d);
    // Invalidate optimizer skill groups so the next analyze uses the new resume.
    setMissingSkillsGroups(null);
    setRequirementAnalysis(null);
    tell("resume", d.message || "Resume uploaded.");
    return d;
  });
  const loadLatestResume = () => task("resume", async () => { const d=await api.getLatestResume(userId); setLatestResume(d); tell("resume","Latest resume loaded."); });
  const verifyLatestResume = async () => {
    try {
      const d = await api.getLatestResume(userId);
      setLatestResume(d);
      return d;
    } catch {
      setLatestResume(null);
      return null;
    }
  };
  const loadJobs = () => task("jobs", async () => { const d=await api.getJobs(userId); setJobs(Array.isArray(d)?d:d.jobs||[]); tell("jobs","Jobs loaded."); });
  const search = (updated = false) => task("jobs", async () => {
    if (!preferencesSaved) throw new Error("Save your preferences before searching.");
    if (updated) {
      // Updated Job Search: use only this endpoint's Remotive jobs.
      // Do not reload GET /api/jobs (that mixes in Existing search-link records).
      const d = await api.searchUpdatedJobs(userId);
      setJobs(Array.isArray(d.jobs) ? d.jobs : []);
      tell("jobs", d.message || "Updated Job Search complete.");
      return d;
    }
    // Existing Job Search: show only the search-link jobs from this endpoint.
    const d = await api.searchJobs(userId);
    setJobs(Array.isArray(d.jobs) ? d.jobs : []);
    tell("jobs", d.message || "Search Jobs complete.");
    return d;
  });
  const changeSelectedJob = (e) => setSelectedJobForm((p)=>({...p,[e.target.name]:e.target.value}));
  const previewSelectedJobUrl = () => task("selected-import",async()=>{ if(!selectedJobForm.job_url.trim()) throw new Error("Paste a direct job URL first."); const d=await api.importJobByUrl(userId,selectedJobForm.job_url); setSelectedJobForm((current)=>({...emptySelectedJob,title:d.title||current.title,company:d.company||current.company,location:d.location||current.location,work_mode:d.work_mode||current.work_mode||"Remote",job_type:d.job_type||current.job_type||"Full-time",description:d.description||current.description,skills_required:d.skills_required||current.skills_required,source:d.save_source,job_url:d.normalized_job_url})); setSelectedJobImportReady(true); setSelectedJobImportStatus(d); const statusMessage=d.status==="complete"?"Job details fetched successfully.":d.status==="partial"?"Some job details were fetched. Complete the missing fields before saving.":"The job URL was identified, but this provider did not expose the full details. Paste the missing fields below."; tell("selected",statusMessage); return d; });
  const clearSelectedJob = () => { setSelectedJobForm(emptySelectedJob); setSelectedJobImportReady(false); setSelectedJobImportStatus(null); tell("selected",""); };
  const loadSelectedJobs = () => task("selected-load", async () => {
    const d = await api.getSelectedJobs(userId);
    const list = Array.isArray(d) ? d : d.jobs || [];
    setSelectedJobs(list);
    setJobs((current) => [
      ...list,
      ...current.filter((job) => !list.some((selected) => String(selected.id) === String(job.id))),
    ]);
    tell("selected", list.length ? "Selected jobs loaded successfully" : "No selected jobs found");
    return list;
  });
  const removeSelectedJob = async (jobId) => {
    setDeletingJobId(jobId);
    try {
      const d = await api.deleteSelectedJob(userId, jobId);
      // The backend only ever deletes/detaches records tied to this one
      // job_id — no other Job row is affected — so this local filter is
      // already the exact correct post-delete state. Deliberately no
      // redundant getSelectedJobs() re-fetch here: an unrelated transient
      // failure in a second network call must never turn an already-
      // successful removal into a false "Failed to fetch" for the user.
      setSelectedJobs((current) => current.filter((job) => String(job.id) !== String(jobId)));
      setJobs((current) => current.filter((job) => String(job.id) !== String(jobId)));
      if (String(atsSelectedJobId) === String(jobId)) {
        setAtsSelectedJobId("");
        setAtsResult(null);
      }
      tell("selected", d.message || "Selected job removed successfully");
      return d;
    } catch (error) {
      // A raw fetch()-level failure (network unreachable, CORS-blocked
      // error response, etc.) throws a TypeError with a terse browser
      // string ("Failed to fetch") — not user-friendly and not from our
      // own code. A real backend-reported failure (404 Selected job not
      // found, etc.) is already a clean message via getErrorMessage(), so
      // it's shown as-is.
      const friendly = error instanceof TypeError ? "Unable to remove this job. Please try again." : messageOf(error);
      tell("selected", friendly);
      return null;
    } finally {
      setDeletingJobId(null);
    }
  };
  const saveSelectedJob = (e) => { e.preventDefault(); return task("selected",async()=>{
    tell("selected", "Saving job…");
    const d=await api.addSelectedJob(userId,selectedJobForm);
    setSelectedJobForm(emptySelectedJob);
    setSelectedJobImportReady(false);
    setSelectedJobImportStatus(null);
    const list=await api.getSelectedJobs(userId);
    setSelectedJobs(Array.isArray(list)?list:list.jobs||[]);
    setJobs((current) => [d.job, ...current.filter((job) => String(job.id) !== String(d.job?.id))].filter(Boolean));
    if (d.job?.id) setAtsSelectedJobId(String(d.job.id));

    if (d.workflow_id) {
      tell("selected", d.message || "Job saved. Running ATS optimization…");
      tell("ats", `Workflow ${d.workflow_status || "PENDING"}…`);
      const analysis = await api.pollAtsWorkflow(d.workflow_id, {
        onProgress: (status) => {
          setAtsResult(status);
          tell("ats", statusLabel(status.workflow_status));
        },
      });
      setAtsResult(analysis);
      // Refresh the Selected Jobs list so the just-saved card reflects its
      // final match_status/score the moment the workflow settles — never
      // requiring the user to revisit Workflow/ATS Optimizer or refresh
      // the browser to see a score that already finished calculating.
      try {
        const refreshed = await api.getSelectedJobs(userId);
        setSelectedJobs(Array.isArray(refreshed) ? refreshed : refreshed.jobs || []);
      } catch {
        // Best-effort refresh only — the card still shows its pre-poll
        // state (Calculating…/no score yet), never a crash, and the next
        // Load Selected Jobs or page visit picks up the real state.
      }
      if (analysis.workflow_status === "COMPLETED") {
        tell("selected", "Job saved and ATS optimize-and-match completed.");
        tell("ats", `Original ${analysis.original_match_percentage}% → Optimized ${analysis.optimized_match_percentage}%`);
        const [opt, matchList] = await Promise.all([
          api.getOptimizedResumes(userId).catch(() => ({ optimized_resumes: [] })),
          api.getMatches(userId).catch(() => []),
        ]);
        setOptimizedResumes(Array.isArray(opt) ? opt : opt.optimized_resumes || []);
        setMatches(Array.isArray(matchList) ? matchList : matchList.matches || []);
      } else if (analysis.original_match_percentage != null && !analysis.error_message) {
        // A genuine, non-fake score exists (e.g. INELIGIBLE_LOW_SCORE — the
        // original match was computed, optimization just wasn't worthwhile)
        // — say so accurately instead of a vague "finished with warnings"
        // that gives no indication a real score is already on the card.
        tell("selected", `Job saved. Resume match calculated: ${analysis.original_match_percentage}%.`);
        tell("ats", statusLabel(analysis.workflow_status, analysis.error_message));
      } else {
        tell("selected", analysis.error_message || d.message || "Job saved. ATS workflow finished with warnings.");
        tell("ats", statusLabel(analysis.workflow_status, analysis.error_message));
      }
      return d;
    }

    tell("selected", d.message || "Selected job saved.");
    if (d.workflow_status) tell("ats", statusLabel(d.workflow_status, d.reason || d.message));
    return d;
  }); };
  const loadOptimizedResumes = () => task("optimizer",async()=>{ const d=await api.getOptimizedResumes(userId); setOptimizedResumes(Array.isArray(d)?d:d.optimized_resumes||[]); tell("optimizer","Optimized resumes loaded."); });
  const optimizeResume = (jobId) => task("optimizer",async()=>{ const d=await api.optimizeResume(userId,jobId); tell("optimizer",d.message||"Resume optimized."); const list=await api.getOptimizedResumes(userId); setOptimizedResumes(Array.isArray(list)?list:list.optimized_resumes||[]); });
  const analyzeAndOptimize = (jobId, force = false) => task("ats", async () => {
    const id = jobId || atsSelectedJobId;
    if (!id) throw new Error("Select a job before running optimize-and-match.");
    setAtsSelectedJobId(String(id));
    tell("ats", "Generating with AI…");
    cancelAtsPolling();
    setAtsResult((current) => {
      if (current?.workflow_status === "COMPLETED" && current?.workflow_id) {
        setPreviousAtsResult(current);
      }
      return {
        ...(current?.workflow_status === "COMPLETED" ? current : {}),
        workflow_id: current?.workflow_id,
        workflow_status: "PENDING",
        job_id: Number(id),
        generation_metadata: null,
        fallback_used: false,
        generation_method: null,
        fallback_reason: null,
      };
    });
    const started = await api.startAtsOptimization(userId, id, force);
    const workflowId = started.workflow_id;
    setActiveAtsWorkflowId(workflowId ?? null);
    activeAtsWorkflowIdRef.current = workflowId != null ? Number(workflowId) : null;
    if (started.created_new === false && started.reuse_reason === "completed_unchanged") {
      setAtsResult(started.result || started);
      setPreviousAtsResult(null);
      setActiveAtsWorkflowId(workflowId ?? null);
    } else if (workflowId && !TERMINAL_ATS_START_STATUSES.has(started.workflow_status)) {
      setAtsResult((current) => ({
        ...current,
        ...(started.result || started),
        workflow_id: workflowId,
        workflow_status: started.workflow_status || "PENDING",
        generation_metadata: null,
        fallback_used: false,
        generation_method: null,
        fallback_reason: null,
      }));
    } else {
      setAtsResult(started.result || started);
    }
    let finalResult = started.result || started;
    if (workflowId && !TERMINAL_ATS_START_STATUSES.has(started.workflow_status)) {
      const poll = beginAtsPoll(workflowId);
      finalResult = await api.pollAtsWorkflow(workflowId, {
        onProgress: poll.onProgress,
        isCancelled: poll.isCancelled,
        intervalMs: started.poll_interval_ms,
        timeoutSeconds: started.poll_timeout_seconds,
      });
      if (poll.isCancelled() || finalResult?.poll_cancelled) {
        return finalResult;
      }
      if (Number(finalResult?.workflow_id) === Number(workflowId)) {
        setAtsResult((current) => mergeAtsResult(finalResult, current) || finalResult);
        setPreviousAtsResult(null);
      }
    }
    setActiveAtsWorkflowId(finalResult?.workflow_id ?? workflowId ?? null);
    activeAtsWorkflowIdRef.current = finalResult?.workflow_id != null
      ? Number(finalResult.workflow_id)
      : (workflowId != null ? Number(workflowId) : null);
    if (finalResult.workflow_status === "COMPLETED") {
      const method = finalResult.generation_metadata?.generation_method
        || finalResult.generation_method
        || (finalResult.fallback_used ? "deterministic_fallback" : "llm");
      const label = finalResult.fallback_used || method === "deterministic_fallback"
        ? "Generation: Deterministic fallback"
        : `AI Optimized · ${finalResult.llm_model || finalResult.generation_metadata?.llm_model || "LLM"}`.trim();
      tell("ats", `Original ${finalResult.original_match_percentage}% → Optimized ${finalResult.optimized_match_percentage}% (+${finalResult.score_improvement}). ${label}`);
      const [opt, matchList] = await Promise.all([
        api.getOptimizedResumes(userId).catch(() => ({ optimized_resumes: [] })),
        api.getMatches(userId).catch(() => []),
      ]);
      setOptimizedResumes(Array.isArray(opt) ? opt : opt.optimized_resumes || []);
      setMatches(Array.isArray(matchList) ? matchList : matchList.matches || []);
      return finalResult;
    }
    const detail = finalResult.error_message
      || started.message
      || (Array.isArray(started.blocking_reasons) && started.blocking_reasons[0]?.reason)
      || "";
    tell("ats", statusLabel(finalResult.workflow_status, detail));
    return finalResult;
  });
  const retryAtsWorkflow = (workflowId) => task("ats", async () => {
    if (!workflowId) throw new Error("No workflow to retry.");
    clearMessage("ats");
    tell("ats", "Retrying ATS workflow…");
    cancelAtsPolling();
    // Reload current evidence/readiness before retry so stale INCOMPLETE is not restored.
    const jobId = atsSelectedJobId;
    if (jobId) {
      try {
        const evidence = await api.getEvidence(userId, jobId);
        setConfirmedSkills(Array.isArray(evidence.evidence) ? evidence.evidence : []);
        setMissingSkillsGroups((current) => applyEvidenceWorkflowState(evidence, current));
        if (evidence.can_generate_resume) {
          setAtsResult(null);
        }
      } catch {
        /* continue retry even if evidence refresh fails */
      }
    }
    const started = await api.retryAtsWorkflow(workflowId);
    const poll = beginAtsPoll(started.workflow_id);
    const d = await api.pollAtsWorkflow(started.workflow_id, {
      onProgress: poll.onProgress,
      isCancelled: poll.isCancelled,
      intervalMs: started.poll_interval_ms,
      timeoutSeconds: started.poll_timeout_seconds,
    });
    if (poll.isCancelled() || d?.poll_cancelled) return d;
    setAtsResult((current) => mergeAtsResult(d, current) || d);
    if (d.workflow_status === "COMPLETED") {
      tell(
        "ats",
        `Original ${d.original_match_percentage}% → Optimized ${d.optimized_match_percentage}%`,
      );
    } else {
      tell("ats", statusLabel(d.workflow_status, d.error_message));
    }
    return d;
  });
  const loadAtsResultForJob = async (jobId) => {
    const id = jobId || atsSelectedJobId;
    if (!id) return null;
    // Status GET is read-only and must never occupy the generate busy channel
    // or overwrite an in-flight Step-5 workflow with a stale job lookup.
    if (atsGeneratingLockRef.current || activeAtsWorkflowIdRef.current) {
      return null;
    }
    try {
      const d = await api.getAtsResultByJob(userId, id);
      if (atsGeneratingLockRef.current || activeAtsWorkflowIdRef.current) {
        return null;
      }
      setAtsResult((current) => mergeAtsResult(d, current) || d);
      const status = String(d.workflow_status || "");
      // Do not stamp Step 2 Analyze Requirements with a historical generation failure.
      // Keep the workflow record available for Step 5 retry without a sticky Failed banner.
      if (status === "COMPLETED") {
        tell(
          "ats",
          `Original ${d.original_match_percentage}% → Optimized ${d.optimized_match_percentage}%`,
        );
      } else if (status === "FAILED" || status === "VALIDATION_FAILED") {
        clearMessage("ats");
      } else if (status) {
        tell("ats", statusLabel(d.workflow_status));
      }
      return d;
    } catch {
      if (atsGeneratingLockRef.current || activeAtsWorkflowIdRef.current) {
        return null;
      }
      setAtsResult(null);
      clearMessage("ats");
      return null;
    }
  };
  const loadJobRequirements = (jobId) => task("ats-requirements", async () => {
    const id = jobId || atsSelectedJobId;
    if (!id) throw new Error("Select a job before analyzing its requirements.");
    const d = await api.getJobRequirements(userId, id);
    setRequirementAnalysis(d);
    clearMessage("ats");
    tell("ats-requirements", "Job requirements analyzed successfully.");
    return d;
  });
  const loadMissingSkills = (jobId, { forceRefresh = true } = {}) => {
    if (atsGeneratingLockRef.current) {
      return Promise.resolve(missingSkillsGroupsRef.current);
    }
    return task("ats-requirements", async () => {
    const id = jobId || atsSelectedJobId;
    if (!id) throw new Error("Select a job before analyzing its requirements.");
    const d = forceRefresh
      ? await api.analyzeOptimizerRequirements(userId, id, { forceRefresh: true })
      : await api.getMissingSkills(userId, id, { forceRefresh: false });
    if (atsGeneratingLockRef.current) {
      return missingSkillsGroupsRef.current;
    }
    // Full replace — never merge evidence-only payloads over analysis arrays.
    setMissingSkillsGroups(d);
    missingSkillsGroupsRef.current = d;
    if (d?.analysis_status === "REQUIREMENT_EXTRACTION_FAILED" || d?.analysis_status === "ANALYSIS_CONSISTENCY_FAILED") {
      tell("ats-requirements", d.analysis_error || "We could not extract requirements from this job description. Please retry.");
    } else {
      // Successful analysis must replace any prior generation Failed banner.
      if (!atsGeneratingLockRef.current) {
        clearMessage("ats");
      }
      tell("ats-requirements", "Job requirements analyzed successfully.");
      setAtsResult((current) => {
        if (!current || atsGeneratingLockRef.current) return current;
        const status = String(current.workflow_status || "");
        const staleFailure = status === "FAILED"
          || status === "VALIDATION_FAILED"
          || status.startsWith("INELIGIBLE");
        return staleFailure ? null : current;
      });
    }
    return d;
  });
  };
  const applyEvidenceWorkflowState = (d, current = null) => {
    // Evidence endpoints intentionally omit skill classification arrays. Never invent
    // a "successful" empty analysis from readiness-only payloads.
    const hasAnalysisArrays = current && (
      Array.isArray(current.verified_resume_skills)
      || Array.isArray(current.skills)
      || current.analysis_status === "SUCCESS"
      || current.analysis_status === "REQUIREMENT_EXTRACTION_FAILED"
    );
    if (!hasAnalysisArrays) {
      return current;
    }
    return {
      ...current,
      evidence: Array.isArray(d.evidence) ? d.evidence : (current.evidence || []),
      evidence_history: Array.isArray(d.evidence_history) ? d.evidence_history : (current.evidence_history || []),
      evidence_summary: d.evidence_summary || current.evidence_summary,
      // Never clobber a known-true gate with an omitted field from readiness-only payloads.
      can_generate_resume: d.can_generate_resume ?? current.can_generate_resume,
      blocking_reasons: Array.isArray(d.blocking_reasons) ? d.blocking_reasons : (current.blocking_reasons || []),
      generation_warnings: Array.isArray(d.generation_warnings) ? d.generation_warnings : (current.generation_warnings || []),
      current_required_confirmations: d.current_required_confirmations || current.current_required_confirmations || [],
      current_optional_confirmations: d.current_optional_confirmations || current.current_optional_confirmations || [],
      preferred_unresolved: d.preferred_unresolved || current.preferred_unresolved || [],
      generation_blocked_message: d.generation_blocked_message ?? current.generation_blocked_message ?? "",
      generation_warning_message: d.generation_warning_message ?? current.generation_warning_message ?? "",
      active_confirmation_counts: d.active_confirmation_counts || current.active_confirmation_counts || {},
      analysis_id: d.analysis_id || current.analysis_id,
      workflow_run_id: d.workflow_run_id || current.workflow_run_id,
    };
  };
  const loadSkillConfirmations = (jobId) => task("ats-skills", async () => {
    const id = jobId || atsSelectedJobId;
    if (!id) return null;
    const d = await api.getEvidence(userId, id);
    setConfirmedSkills(Array.isArray(d.evidence) ? d.evidence : []);
    setMissingSkillsGroups((current) => {
      const next = applyEvidenceWorkflowState(d, current);
      missingSkillsGroupsRef.current = next;
      return next;
    });
    return d;
  });
  const saveConfirmedSkills = (jobId, items) => task("ats-skills", async () => {
    const id = jobId || atsSelectedJobId;
    if (!id) throw new Error("Select a job before saving skill confirmations.");
    const d = await api.saveEvidence(userId, id, items);
    setConfirmedSkills(Array.isArray(d.evidence) ? d.evidence : []);
    // Apply readiness immediately so Continue enables without reload.
    setMissingSkillsGroups((current) => {
      const next = applyEvidenceWorkflowState(d, current);
      missingSkillsGroupsRef.current = next;
      return next;
    });
    // Clear stale generation failures once current evidence unblocks generation.
    if (d.can_generate_resume) {
      setAtsResult((current) => {
        if (!current) return current;
        const failedEvidence = String(current.workflow_status || "") === "FAILED"
          && /evidence incomplete/i.test(String(current.error_message || ""));
        return failedEvidence ? null : current;
      });
      tell("ats", "");
    }
    try {
      const refreshed = await api.analyzeOptimizerRequirements(userId, id, { forceRefresh: true });
      setMissingSkillsGroups(refreshed);
      if (Array.isArray(refreshed.evidence)) setConfirmedSkills(refreshed.evidence);
      if (refreshed.can_generate_resume) {
        setAtsResult((current) => {
          if (!current) return current;
          const failedEvidence = String(current.workflow_status || "") === "FAILED"
            && /evidence incomplete/i.test(String(current.error_message || ""));
          return failedEvidence ? null : current;
        });
      }
    } catch {
      /* keep saved evidence even if refresh fails */
    }
    tell("ats-skills", d.message || "Evidence saved.");
    return d;
  });
  const deleteConfirmedSkill = (jobId, evidenceId) => task("ats-skills", async () => {
    const id = jobId || atsSelectedJobId;
    if (!id) throw new Error("Select a job first.");
    const d = await api.deleteEvidence(userId, id, evidenceId);
    setConfirmedSkills(Array.isArray(d.evidence) ? d.evidence : []);
    setMissingSkillsGroups((current) => {
      const next = applyEvidenceWorkflowState(d, current);
      missingSkillsGroupsRef.current = next;
      return next;
    });
    try {
      const refreshed = await api.analyzeOptimizerRequirements(userId, id, { forceRefresh: true });
      setMissingSkillsGroups(refreshed);
      if (Array.isArray(refreshed.evidence)) setConfirmedSkills(refreshed.evidence);
    } catch {
      /* ignore refresh failure */
    }
    tell("ats-skills", d.message || "Evidence removed.");
    return d;
  });
  const loadMatches = () => task("matches",async()=>{ const d=await api.getMatches(userId); setMatches(Array.isArray(d)?d:d.matches||[]); tell("matches","Matches loaded."); });
  const calculateMatches = () => task("matches",async()=>{ const d=await api.calculateMatches(userId); tell("matches",d.message||"Matches calculated."); const list=await api.getMatches(userId); setMatches(Array.isArray(list)?list:list.matches||[]); });
  const loadEmailLogs = () => task("emails",async()=>{ const d=await api.getEmailLogs(userId); setEmailLogs(Array.isArray(d)?d:d.logs||[]); tell("emails","Email logs loaded."); });
  const generateEmailSummary = () => task("emails",async()=>{ const d=await api.generateEmailSummary(userId); tell("emails",d.message||"Email summary generated."); const list=await api.getEmailLogs(userId); setEmailLogs(Array.isArray(list)?list:list.logs||[]); });
  const loadEmailSummaryPreferences = () => task("email-settings", async () => {
    const saveVersion = emailPreferencesSaveVersionRef.current;
    const data = await api.getEmailSummaryPreferences(userId);
    if (saveVersion === emailPreferencesSaveVersionRef.current) {
      setEmailSummaryPreferences({ ...emptyEmailSummaryPreferences, ...data, daily_summary_recipient: data.daily_summary_recipient || "" });
    }
    return data;
  });
  const saveEmailSummaryPreferences = (values) => task("email-settings", async () => {
    const data = await api.updateEmailSummaryPreferences(userId, values);
    emailPreferencesSaveVersionRef.current += 1;
    setEmailSummaryPreferences({ ...emptyEmailSummaryPreferences, ...data, daily_summary_recipient: data.daily_summary_recipient || "" });
    tell("email-settings", "Email Summary settings saved.");
    return data;
  });
  const runWorkflow = () => task("workflow",async()=>{ if(!preferencesSaved) throw new Error("Save your preferences before running the workflow."); const d=await api.runFullWorkflow(userId); setWorkflowResult(d); tell("workflow",d.message||"Workflow completed."); await Promise.all([loadJobs(),loadOptimizedResumes(),loadMatches(),loadEmailLogs()]); });

  // Dashboard progress tracker fix: every one of these loaders already
  // exists and is normally triggered by its own owning page's manual
  // "Load"/mount action (Preferences, Resume, Job Search, Selected Jobs,
  // ATS Optimizer, Workflow). Dashboard previously read this same context
  // state but never fetched anything itself, so it only ever looked correct
  // once the user happened to visit (or the workflow happened to bulk-load)
  // each of those pages first. This reuses the exact same loaders — no new
  // API calls, no new business logic — so Dashboard can hydrate itself
  // independently on mount/login/refresh. Each loader already swallows its
  // own error via task()/verifyLatestResume's own try/catch, so one failing
  // source (e.g. no preferences saved yet) never blocks the others.
  const refreshUserWorkspaceState = async () => {
    if (!userId) return;
    await Promise.all([
      loadPreferences(),
      verifyLatestResume(),
      loadJobs(),
      loadSelectedJobs(),
      loadOptimizedResumes(),
      loadMatches(),
      loadEmailLogs(),
    ]);
  };

  // Email Summary — Gmail connection, application tracking, recruiter drafts
  const loadGmailStatus = () => task("gmail",async()=>{ const d=await api.getGmailStatus(userId); setGmailStatus(d); });
  const connectGmailAccount = () => task("gmail",async()=>{ const d=await api.connectGmail(userId); if(d.authorization_url) window.location.href=d.authorization_url; });
  const disconnectGmailAccount = () => task("gmail",async()=>{ await api.disconnectGmail(userId); await loadGmailStatus(); tell("gmail","Gmail disconnected."); });
  const scanGmailForJobs = () => task("gmail",async()=>{ const d=await api.scanJobEmails(userId); await Promise.all([loadGmailStatus(),loadApplications(),loadJobEmailEvents()]); tell("gmail",`Scan complete — checked ${d.messages_checked||0} message(s).`); });
  const loadApplications = () => task("applications",async()=>{ const d=await api.getApplications(userId); setApplications(Array.isArray(d.applications)?d.applications:[]); });
  const updateApplicationStatusAction = (applicationId,newStatus) => task("applications",async()=>{ await api.updateApplicationStatus(userId,applicationId,newStatus); tell("applications","Status updated."); await loadApplications(); });
  const loadJobEmailEvents = () => task("applications",async()=>{ const d=await api.getJobEmailEvents(userId); setJobEmailEvents(Array.isArray(d.events)?d.events:[]); });
  const resolveJobEmailEventAction = (eventId,action,jobId) => task("applications",async()=>{ await api.resolveJobEmailEvent(userId,eventId,action,jobId); tell("applications","Update resolved."); await Promise.all([loadJobEmailEvents(),loadApplications()]); });
  // CareerBoost-local only (Gmail stays read-only) — update the one changed
  // event in place from the response rather than refetching the whole list.
  const markJobEmailEventReadAction = (eventId) => task("email-events",async()=>{
    const updated = await api.markJobEmailEventRead(userId,eventId);
    setJobEmailEvents((old)=>old.map((event)=>(event.id===updated.id?updated:event)));
  });
  const toggleJobEmailEventStarAction = (eventId) => task("email-events",async()=>{
    const updated = await api.toggleJobEmailEventStar(userId,eventId);
    setJobEmailEvents((old)=>old.map((event)=>(event.id===updated.id?updated:event)));
  });
  const averageMatch = averageCompletedMatch(matches) ?? 0;
  const value = useMemo(()=>({
    currentUser,backendStatus,preferences,preferencesSaved,latestResume,selectedResumeFile,jobs,selectedJobs,selectedJobForm,selectedJobImportReady,selectedJobImportStatus,
    optimizedResumes,matches,atsResult,activeAtsWorkflowId,previousAtsResult,atsSelectedJobId,requirementAnalysis,missingSkillsGroups,confirmedSkills,emailLogs,workflowResult,messages,busy,deletingJobId,averageMatch,
    gmailStatus,applications,jobEmailEvents,
    authenticate,clearAuthMessages,requestPasswordReset,submitPasswordReset,logout,saveProfileName,changePreferences,savePreferences,loadPreferences,setSelectedResumeFile,uploadResume,loadLatestResume,loadJobs,search,
    changeSelectedJob,previewSelectedJobUrl,clearSelectedJob,saveSelectedJob,loadSelectedJobs,removeSelectedJob,verifyLatestResume,optimizeResume,loadOptimizedResumes,analyzeAndOptimize,retryAtsWorkflow,loadAtsResultForJob,setAtsSelectedJobId,setAtsResult,startAtsForSelectedJob,
    loadJobRequirements,loadMissingSkills,loadSkillConfirmations,saveConfirmedSkills,deleteConfirmedSkill,setMissingSkillsGroups,
    calculateMatches,loadMatches,generateEmailSummary,loadEmailLogs,runWorkflow,refreshUserWorkspaceState,emailSummaryPreferences,loadEmailSummaryPreferences,saveEmailSummaryPreferences,
    loadGmailStatus,connectGmailAccount,disconnectGmailAccount,scanGmailForJobs,loadApplications,updateApplicationStatusAction,loadJobEmailEvents,resolveJobEmailEventAction,markJobEmailEventReadAction,toggleJobEmailEventStarAction
  }),[currentUser,backendStatus,preferences,preferencesSaved,latestResume,selectedResumeFile,jobs,selectedJobs,selectedJobForm,selectedJobImportReady,selectedJobImportStatus,optimizedResumes,matches,atsResult,activeAtsWorkflowId,previousAtsResult,atsSelectedJobId,requirementAnalysis,missingSkillsGroups,confirmedSkills,emailLogs,workflowResult,messages,busy,deletingJobId,averageMatch,gmailStatus,applications,jobEmailEvents,emailSummaryPreferences]);
  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}
export const useApp = () => useContext(AppContext);
