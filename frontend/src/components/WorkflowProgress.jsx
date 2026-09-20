import {Link} from "react-router-dom"; import {Icon} from "./Icons"; import {useApp} from "../context/AppContext";
export default function WorkflowProgress(){
  const a=useApp();
  // The Workflow tile must never be the thing that initializes the OTHER
  // tiles (that coupling is exactly the bug this fixes) — and it must not
  // rely solely on a.workflowResult, which is only ever set by clicking
  // "Run full workflow" THIS session. A prior run's real, persisted outputs
  // (matches + email logs already existing) are just as authoritative as
  // that session-only flag, so either one marks this tile done.
  const workflowDone = Boolean(a.workflowResult) || (a.matches.length > 0 && a.emailLogs.length > 0);
  const items=[
    ["/preferences","preferences","Preferences",a.preferencesSaved?"Saved":"Pending",a.preferencesSaved],
    ["/resume","resume","Resume",a.latestResume?"Uploaded":"Pending",!!a.latestResume],
    ["/jobs","jobs","Job Search",a.jobs.length?`${a.jobs.length} jobs`:"Pending",!!a.jobs.length],
    ["/selected-jobs","selected","Selected Jobs",a.selectedJobs.length?`${a.selectedJobs.length} added`:"Optional",!!a.selectedJobs.length],
    ["/ats-analyzer","optimizer","ATS Analyzer",a.atsResult?"Analyzed":a.optimizedResumes.length?`${a.optimizedResumes.length} optimized`:"Pending",!!(a.atsResult||a.optimizedResumes.length||a.matches.length)],
    ["/emails","emails","Email Summary",a.emailLogs.length?`${a.emailLogs.length} sent`:"Pending",!!a.emailLogs.length],
    ["/workflow","workflow","Workflow",workflowDone?"Complete":"Ready",workflowDone],
  ];
  return <section className="workflow-progress">{items.map(([to,icon,label,status,done],i)=><Link key={to} to={to} className={done?"done":""}><div className="flow-icon"><Icon name={icon} size={18}/></div><b>{label}</b><small>{status}</small>{i<items.length-1&&<i className="connector"/>}</Link>)}</section>;
}
