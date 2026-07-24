import StatusBadge from "./StatusBadge";
export default function MatchCard({match}){return <article className="match-card"><div><h3>{match.job_title||match.title||`Job ${match.job_id}`}</h3><p>{match.company||match.source||"Saved job"}</p></div><StatusBadge>{Math.round(match.match_percentage||0)}% match</StatusBadge></article>}
