/* eslint-disable react-hooks/exhaustive-deps, react-refresh/only-export-components */
import { createContext, useContext, useEffect, useMemo, useState } from "react";
import * as api from "../api";

const AppContext = createContext(null);
const emptyPreferences = { job_title:"", location:"", experience:"", work_mode:"", job_type:"", skills:"", schedule_time:"08:00", timezone:"America/Chicago" };
const emptySelectedJob = { title:"", company:"", location:"", work_mode:"Remote", job_type:"Full-time", description:"", skills_required:"", source:"User Selected Job", job_url:"" };
const messageOf = (error) => error?.message || "Something went wrong.";

export function AppProvider({ children }) {
  const [currentUser, setCurrentUser] = useState(() => { try { return JSON.parse(localStorage.getItem("currentUser")); } catch { return null; } });
  const [backendStatus, setBackendStatus] = useState("Checking backend…");
  const [preferences, setPreferences] = useState(emptyPreferences);
  const [preferencesSaved, setPreferencesSaved] = useState(false);
  const [latestResume, setLatestResume] = useState(null);
  const [selectedResumeFile, setSelectedResumeFile] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [selectedJobForm, setSelectedJobForm] = useState(emptySelectedJob);
  const [selectedJobImportReady, setSelectedJobImportReady] = useState(false);
  const [selectedJobImportStatus, setSelectedJobImportStatus] = useState(null);
  const [optimizedResumes, setOptimizedResumes] = useState([]);
  const [matches, setMatches] = useState([]);
  const [emailLogs, setEmailLogs] = useState([]);
  const [workflowResult, setWorkflowResult] = useState(null);
  const [messages, setMessages] = useState({});
  const [busy, setBusy] = useState("");
  const userId = currentUser?.user_id;
  const tell = (key, value) => setMessages((old) => ({ ...old, [key]: value }));
  const task = async (key, fn) => { setBusy(key); try { return await fn(); } catch (e) { tell(key, messageOf(e)); } finally { setBusy(""); } };

  useEffect(() => { api.checkBackendHealth().then((d) => setBackendStatus(d.status || "Backend is running")).catch(() => setBackendStatus("Backend not connected")); }, []);

  const resetData = () => {
    setPreferences(emptyPreferences); setPreferencesSaved(false); setLatestResume(null); setSelectedResumeFile(null);
    setJobs([]); setSelectedJobForm(emptySelectedJob); setSelectedJobImportReady(false); setSelectedJobImportStatus(null); setOptimizedResumes([]); setMatches([]); setEmailLogs([]);
    setWorkflowResult(null); setMessages({});
  };
  const authenticate = async (mode, form) => task("auth", async () => {
    const data = mode === "login" ? await api.loginUser(form) : await api.signupUser(form);
    const user = { user_id:data.user_id, email:data.email, name:data.name || form.name || "" };
    localStorage.setItem("currentUser", JSON.stringify(user)); resetData(); setCurrentUser(user); return true;
  });
  const logout = () => { localStorage.removeItem("currentUser"); resetData(); setCurrentUser(null); };
  const changePreferences = (e) => { setPreferences((p) => ({...p,[e.target.name]:e.target.value})); setPreferencesSaved(false); };
  const savePreferences = (e) => { e?.preventDefault(); return task("preferences", async () => { const d=await api.saveUserPreferences({...preferences,user_id:Number(userId)}); setPreferencesSaved(true); tell("preferences",d.message||"Preferences saved."); }); };
  const loadPreferences = () => task("preferences", async () => { const d=await api.getUserPreferences(userId); setPreferences({...emptyPreferences,...d,schedule_time:d.schedule_time?.slice(0,5)||"08:00"}); setPreferencesSaved(true); tell("preferences","Saved preferences loaded."); });
  const uploadResume = () => task("resume", async () => { if(!selectedResumeFile) throw new Error("Choose a resume file first."); const d=await api.uploadResume(userId,selectedResumeFile); setLatestResume(d); tell("resume",d.message||"Resume uploaded."); });
  const loadLatestResume = () => task("resume", async () => { const d=await api.getLatestResume(userId); setLatestResume(d); tell("resume","Latest resume loaded."); });
  const loadJobs = () => task("jobs", async () => { const d=await api.getJobs(userId); setJobs(Array.isArray(d)?d:d.jobs||[]); tell("jobs","Jobs loaded."); });
  const search = (updated=false) => task("jobs", async () => { if(!preferencesSaved) throw new Error("Save your preferences before searching."); const d=await (updated?api.searchUpdatedJobs(userId):api.searchJobs(userId)); tell("jobs",d.message||"Search complete."); const list=await api.getJobs(userId); const saved=Array.isArray(list)?list:list.jobs||[]; const metadata=new Map((d.jobs||[]).map(job=>[String(job.id),job])); setJobs(saved.map(job=>({...job,...(metadata.get(String(job.id))||{})}))); });
  const changeSelectedJob = (e) => setSelectedJobForm((p)=>({...p,[e.target.name]:e.target.value}));
  const previewSelectedJobUrl = () => task("selected-import",async()=>{ if(!selectedJobForm.job_url.trim()) throw new Error("Paste a direct job URL first."); const d=await api.importJobByUrl(userId,selectedJobForm.job_url); setSelectedJobForm((current)=>({...emptySelectedJob,title:d.title||current.title,company:d.company||current.company,location:d.location||current.location,work_mode:d.work_mode||current.work_mode||"Remote",job_type:d.job_type||current.job_type||"Full-time",description:d.description||current.description,skills_required:d.skills_required||current.skills_required,source:d.save_source,job_url:d.normalized_job_url})); setSelectedJobImportReady(true); setSelectedJobImportStatus(d); const statusMessage=d.status==="complete"?"Job details fetched successfully.":d.status==="partial"?"Some job details were fetched. Complete the missing fields before saving.":"The job URL was identified, but this provider did not expose the full details. Paste the missing fields below."; tell("selected",statusMessage); return d; });
  const clearSelectedJob = () => { setSelectedJobForm(emptySelectedJob); setSelectedJobImportReady(false); setSelectedJobImportStatus(null); tell("selected",""); };
  const saveSelectedJob = (e) => { e.preventDefault(); return task("selected",async()=>{ const d=await api.addSelectedJob(userId,selectedJobForm); setSelectedJobForm(emptySelectedJob); setSelectedJobImportReady(false); setSelectedJobImportStatus(null); tell("selected",d.message||"Selected job saved."); const list=await api.getJobs(userId); setJobs(Array.isArray(list)?list:list.jobs||[]); }); };
  const loadOptimizedResumes = () => task("optimizer",async()=>{ const d=await api.getOptimizedResumes(userId); setOptimizedResumes(Array.isArray(d)?d:d.optimized_resumes||[]); tell("optimizer","Optimized resumes loaded."); });
  const optimizeResume = (jobId) => task("optimizer",async()=>{ const d=await api.optimizeResume(userId,jobId); tell("optimizer",d.message||"Resume optimized."); const list=await api.getOptimizedResumes(userId); setOptimizedResumes(Array.isArray(list)?list:list.optimized_resumes||[]); });
  const loadMatches = () => task("matches",async()=>{ const d=await api.getMatches(userId); setMatches(Array.isArray(d)?d:d.matches||[]); tell("matches","Matches loaded."); });
  const calculateMatches = () => task("matches",async()=>{ const d=await api.calculateMatches(userId); tell("matches",d.message||"Matches calculated."); const list=await api.getMatches(userId); setMatches(Array.isArray(list)?list:list.matches||[]); });
  const loadEmailLogs = () => task("emails",async()=>{ const d=await api.getEmailLogs(userId); setEmailLogs(Array.isArray(d)?d:d.email_logs||[]); tell("emails","Email logs loaded."); });
  const generateEmailSummary = () => task("emails",async()=>{ const d=await api.generateEmailSummary(userId); tell("emails",d.message||"Email summary generated."); const list=await api.getEmailLogs(userId); setEmailLogs(Array.isArray(list)?list:list.email_logs||[]); });
  const runWorkflow = () => task("workflow",async()=>{ if(!preferencesSaved) throw new Error("Save your preferences before running the workflow."); const d=await api.runFullWorkflow(userId); setWorkflowResult(d); tell("workflow",d.message||"Workflow completed."); await Promise.all([loadJobs(),loadOptimizedResumes(),loadMatches(),loadEmailLogs()]); });
  const selectedJobs = jobs.filter((j)=>j.source==="User Selected Job"||j.source?.endsWith(" Imported"));
  const averageMatch = matches.length ? Math.round(matches.reduce((s,m)=>s+Number(m.match_percentage||0),0)/matches.length) : 0;
  const value = useMemo(()=>({currentUser,backendStatus,preferences,preferencesSaved,latestResume,selectedResumeFile,jobs,selectedJobs,selectedJobForm,selectedJobImportReady,selectedJobImportStatus,optimizedResumes,matches,emailLogs,workflowResult,messages,busy,averageMatch,authenticate,logout,changePreferences,savePreferences,loadPreferences,setSelectedResumeFile,uploadResume,loadLatestResume,loadJobs,search,changeSelectedJob,previewSelectedJobUrl,clearSelectedJob,saveSelectedJob,optimizeResume,loadOptimizedResumes,calculateMatches,loadMatches,generateEmailSummary,loadEmailLogs,runWorkflow}),[currentUser,backendStatus,preferences,preferencesSaved,latestResume,selectedResumeFile,jobs,selectedJobForm,selectedJobImportReady,selectedJobImportStatus,optimizedResumes,matches,emailLogs,workflowResult,messages,busy,averageMatch]);
  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}
export const useApp = () => useContext(AppContext);
