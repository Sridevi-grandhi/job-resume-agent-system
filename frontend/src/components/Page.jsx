import { useLocation } from "react-router-dom";
import { useApp } from "../context/AppContext";
import PageNavigation from "./PageNavigation";

const navigationByPath = {
  "/preferences": {
    nextPath: "/resume",
    stateKey: "preferencesSaved",
    disabledMessage: "Save your preferences before continuing.",
  },
  "/resume": {
    nextPath: "/jobs",
    stateKey: "latestResume",
    disabledMessage: "Upload a resume or load your existing resume before continuing.",
  },
  "/jobs": {
    nextPath: "/selected-jobs",
    stateKey: "jobs",
    disabledMessage: "Search for jobs or load existing jobs before continuing.",
  },
  "/selected-jobs": {
    nextPath: "/optimizer",
    canContinue: true,
  },
  "/optimizer": {
    nextPath: "/matches",
    stateKey: "optimizedResumes",
    disabledMessage: "Create or load at least one optimized resume before continuing.",
  },
  "/matches": {
    nextPath: "/emails",
    stateKey: "matches",
    disabledMessage: "Calculate or load match results before continuing.",
  },
  "/emails": {
    nextPath: "/workflow",
    stateKey: "emailLogs",
    disabledMessage: "Generate an email summary or load existing email logs before continuing.",
  },
  "/workflow": {
    showNext: false,
  },
};

export function Page({ title, subtitle, actions, children }) {
  const app = useApp();
  const { pathname } = useLocation();
  const navigation = navigationByPath[pathname];
  const stateValue = navigation?.stateKey ? app[navigation.stateKey] : null;
  const canContinue = navigation?.canContinue ?? (Array.isArray(stateValue) ? stateValue.length > 0 : Boolean(stateValue));

  return <div className="page">
    <div className="page-heading">
      <div><h2>{title}</h2><p>{subtitle}</p></div>
      {actions && <div className="actions">{actions}</div>}
    </div>
    {children}
    {navigation && <PageNavigation {...navigation} canContinue={canContinue} />}
  </div>;
}

export function Panel({ title, subtitle, actions, children, className = "" }) {
  return <section className={`panel ${className}`}>
    <header><div><h3>{title}</h3>{subtitle && <p>{subtitle}</p>}</div>{actions}</header>
    {children}
  </section>;
}

export function Notice({ children }) {
  return children ? <div className="notice">{children}</div> : null;
}
