import {Icon} from "./Icons";
export default function MetricCard({label,value,detail,icon,tone="blue"}){return <article className="metric"><div><span>{label}</span><strong>{value}</strong><small>{detail}</small></div><i className={tone}><Icon name={icon}/></i></article>}
