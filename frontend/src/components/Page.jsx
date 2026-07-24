export function Page({title,subtitle,actions,children}){return <div className="page"><div className="page-heading"><div><h2>{title}</h2><p>{subtitle}</p></div>{actions&&<div className="actions">{actions}</div>}</div>{children}</div>}
export function Panel({title,subtitle,actions,children,className=""}){return <section className={`panel ${className}`}><header><div><h3>{title}</h3>{subtitle&&<p>{subtitle}</p>}</div>{actions}</header>{children}</section>}
export function Notice({children}){return children?<div className="notice">{children}</div>:null}
