import { useEffect, useMemo, useState } from "react";
import { useApp } from "../context/AppContext";
import { Page, Panel, Notice } from "../components/Page";
import EmptyState from "../components/EmptyState";
import StatusBadge from "../components/StatusBadge";
import MetricCard from "../components/MetricCard";

const APPLICATION_STATUSES = [
  "SAVED", "APPLIED", "APPLICATION_CONFIRMED", "UNDER_REVIEW", "RECRUITER_CONTACTED",
  "ASSESSMENT_REQUESTED", "INTERVIEW_REQUESTED", "INTERVIEW_SCHEDULED", "OFFER", "REJECTED", "WITHDRAWN",
];

// Must stay in sync with backend app/services/application_tracking_service.py's
// RELIABLE_MATCH_CONFIDENCE_THRESHOLD — the backend /resolve confirm
// endpoint is the authoritative enforcement point either way (this is a UI
// convenience so Confirm never even looks clickable for an unsafe match).
const RELIABLE_MATCH_CONFIDENCE_THRESHOLD = 0.5;

// Category mapping for the metric cards + tabs (confirmed with the user):
// "Job Opportunities" = a discovered lead (RECRUITER_OUTREACH) or generic
// job-related mail the classifier couldn't pin to a more specific type
// (OTHER) — i.e. everything that isn't already a status update on an
// application already in the pipeline or an interview invite.
const JOB_OPPORTUNITY_TYPES = ["RECRUITER_OUTREACH", "OTHER"];
const APPLICATION_UPDATE_TYPES = ["APPLICATION_CONFIRMATION", "APPLICATION_UPDATE", "ASSESSMENT_REQUEST", "OFFER", "REJECTION"];

const TAB_DEFS = [
  { key: "all", label: "All Emails", match: () => true },
  { key: "job_opportunities", label: "Job Opportunities", match: (event) => JOB_OPPORTUNITY_TYPES.includes(event.event_type) },
  { key: "recruiter_outreach", label: "Recruiter Outreach", match: (event) => event.event_type === "RECRUITER_OUTREACH" },
  { key: "application_updates", label: "Application Updates", match: (event) => APPLICATION_UPDATE_TYPES.includes(event.event_type) },
  { key: "interview", label: "Interview", match: (event) => event.event_type === "INTERVIEW_INVITE" },
];

const WEEK_MS = 7 * 24 * 60 * 60 * 1000;

function isWithinLastWeek(receivedAt) {
  if (!receivedAt) return false;
  const time = new Date(receivedAt).getTime();
  if (Number.isNaN(time)) return false;
  return Date.now() - time <= WEEK_MS;
}

function formatDateTime(value) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleString(undefined, { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" });
}

function statusLabel(status) {
  return (status || "").replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
}

// A candidate is confirmable only when every one of these holds. In
// particular match_confidence === null/undefined (legacy JobEmailEvent rows
// persisted before this field existed, which may still carry a
// matched_application_id from the old, less strict matching logic) is
// treated as NOT reliable — never inferred from the older, unrelated
// blended `confidence` field. "job-related" is not a separate field to
// check here: the backend's events list endpoint already excludes anything
// it doesn't consider job-related (see is_legacy_event_unrelated), so any
// event reaching this page is job-related by construction. Deliberately
// does NOT gate on resolution — a CONFIRMED event's matched application is
// still worth displaying as "the matched application", so both the compact
// card/drawer display and the Confirm button (which only ever renders while
// resolution is still NEEDS_CONFIRMATION anyway) share this one check.
function isReliableMatch(event) {
  return (
    Boolean(event.matched_application_id) &&
    typeof event.match_confidence === "number" &&
    event.match_confidence >= RELIABLE_MATCH_CONFIDENCE_THRESHOLD
  );
}

function GmailConnectionCard() {
  const app = useApp();
  useEffect(() => { app.loadGmailStatus(); }, [app.currentUser]);

  const status = app.gmailStatus;
  const connected = status?.connected;
  const lastSynced = formatDateTime(status?.last_scanned_at);

  return (
    <Panel title="Gmail Connection" className="gmail-connection-card">
      {connected ? (
        <>
          <p className="gmail-connected-line">
            <StatusBadge tone="success">Connected</StatusBadge> as {status.email_address}
          </p>
          <p className="gmail-last-synced">Last synced: {lastSynced || "Not yet scanned"}</p>
        </>
      ) : (
        <p className="gmail-connected-line">
          <StatusBadge tone="warning">Not connected</StatusBadge>
        </p>
      )}
      <div className="card-actions">
        {connected ? (
          <>
            <button className="button secondary" onClick={app.disconnectGmailAccount} disabled={app.busy === "gmail"}>
              Disconnect Gmail
            </button>
            <button className="button primary" onClick={app.scanGmailForJobs} disabled={app.busy === "gmail"}>
              {app.busy === "gmail" ? "Scanning…" : "Scan Job Emails"}
            </button>
          </>
        ) : (
          <button className="button primary" onClick={app.connectGmailAccount} disabled={app.busy === "gmail"}>
            Connect Gmail
          </button>
        )}
      </div>
      <Notice tone={/error|denied|not_configured/.test(app.messages.gmail || "") ? "error" : undefined}>{app.messages.gmail}</Notice>
    </Panel>
  );
}

function EmailMetricCards({ events }) {
  const counts = useMemo(() => {
    const thisWeek = events.filter((event) => isWithinLastWeek(event.received_at));
    return {
      newEmails: thisWeek.filter((event) => !event.is_read).length,
      jobOpportunities: thisWeek.filter((event) => JOB_OPPORTUNITY_TYPES.includes(event.event_type)).length,
      recruiterOutreach: thisWeek.filter((event) => event.event_type === "RECRUITER_OUTREACH").length,
      applicationUpdates: thisWeek.filter((event) => APPLICATION_UPDATE_TYPES.includes(event.event_type)).length,
    };
  }, [events]);

  return (
    <div className="metrics email-metric-cards">
      <MetricCard label="New Emails" value={counts.newEmails} detail="This Week" icon="emails" />
      <MetricCard label="Job Opportunities" value={counts.jobOpportunities} detail="Detected" icon="jobs" tone="green" />
      <MetricCard label="Recruiter Outreach" value={counts.recruiterOutreach} detail="This Week" icon="matches" tone="amber" />
      <MetricCard label="Application Updates" value={counts.applicationUpdates} detail="This Week" icon="updatedJobs" tone="purple" />
    </div>
  );
}

const SCAN_FREQUENCY_OPTIONS = [
  ["daily", "Daily (Recommended)"],
  ["every_12_hours", "Every 12 Hours"],
  ["every_6_hours", "Every 6 Hours"],
];

const EMAIL_TYPE_OPTIONS = [
  ["job_opportunities", "Job Opportunities"],
  ["recruiter_messages", "Recruiter Messages"],
  ["application_updates", "Application Updates"],
  ["interview_invitations", "Interview Invitations"],
];

const ALL_EMAIL_TYPE_KEYS = EMAIL_TYPE_OPTIONS.map(([key]) => key);

function EmailSummarySettings() {
  const app = useApp();
  const [settings, setSettings] = useState({ automatic_gmail_scan_enabled: false, gmail_scan_frequency: "daily", email_types_to_scan: ALL_EMAIL_TYPE_KEYS });
  useEffect(() => { app.loadEmailSummaryPreferences(); }, [app.currentUser]);
  useEffect(() => {
    const preferences = app.emailSummaryPreferences;
    setSettings({
      automatic_gmail_scan_enabled: Boolean(preferences?.automatic_gmail_scan_enabled),
      gmail_scan_frequency: preferences?.gmail_scan_frequency || "daily",
      email_types_to_scan: Array.isArray(preferences?.email_types_to_scan) && preferences.email_types_to_scan.length
        ? preferences.email_types_to_scan
        : ALL_EMAIL_TYPE_KEYS,
    });
  }, [app.emailSummaryPreferences]);

  const toggleCategory = (key) => setSettings((old) => ({
    ...old,
    email_types_to_scan: old.email_types_to_scan.includes(key)
      ? old.email_types_to_scan.filter((value) => value !== key)
      : [...old.email_types_to_scan, key],
  }));

  const save = (event) => {
    event.preventDefault();
    app.saveEmailSummaryPreferences(settings);
  };

  return (
    <Panel className="email-settings-panel" title="Email Summary Settings" subtitle="Control automatic scanning and email summary preferences.">
      <form className="email-settings-form" onSubmit={save}>
        <label className="toggle-switch-label">
          <span className="toggle-switch">
            <input
              type="checkbox"
              checked={settings.automatic_gmail_scan_enabled}
              onChange={(event) => setSettings((old) => ({ ...old, automatic_gmail_scan_enabled: event.target.checked }))}
            />
            <span className="toggle-switch-track" aria-hidden="true" />
          </span>
          <span className="toggle-switch-copy">
            <b>Enable Automatic Gmail Scan</b>
            <small>Automatically scan and summarize job-related emails</small>
          </span>
        </label>

        <label className="email-settings-field">
          <span>Scan Frequency</span>
          <select
            value={settings.gmail_scan_frequency}
            onChange={(event) => setSettings((old) => ({ ...old, gmail_scan_frequency: event.target.value }))}
          >
            {SCAN_FREQUENCY_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>

        <div className="email-settings-types">
          <span className="email-settings-types-label">Email Types to Scan</span>
          <div className="email-settings-checkboxes">
            {EMAIL_TYPE_OPTIONS.map(([key, label]) => (
              <label key={key}>
                <input type="checkbox" checked={settings.email_types_to_scan.includes(key)} onChange={() => toggleCategory(key)} /> {label}
              </label>
            ))}
          </div>
        </div>

        <div className="form-actions">
          <button className="button primary" disabled={app.busy === "email-settings"}>
            {app.busy === "email-settings" ? "Saving…" : "Save Settings"}
          </button>
        </div>
      </form>
      <Notice tone={/unable|invalid|error/i.test(app.messages["email-settings"] || "") ? "error" : undefined}>{app.messages["email-settings"]}</Notice>
    </Panel>
  );
}

function ApplicationCard({ application }) {
  const app = useApp();
  const [nextStatus, setNextStatus] = useState(application.status);
  // useState's initial value only applies on first mount — if this
  // application's status changes from OUTSIDE this dropdown (e.g. the user
  // clicks Confirm on a Gmail event elsewhere on the page, which refreshes
  // the applications list), the dropdown would otherwise keep showing the
  // stale pre-update value until a hard reload remounted the component.
  // Resyncing here whenever the underlying status changes keeps the
  // dropdown and the badge in agreement immediately, without discarding an
  // in-progress manual selection (this only fires when application.status
  // itself changes, not on every render).
  useEffect(() => {
    setNextStatus(application.status);
  }, [application.status]);
  return (
    <article className="application-card">
      <header>
        <div>
          <h3>{application.title}</h3>
          <p>{application.company}</p>
        </div>
        <StatusBadge tone="neutral">{statusLabel(application.status)}</StatusBadge>
      </header>
      <p className="application-meta">
        Last update: {application.last_status_update_at ? new Date(application.last_status_update_at).toLocaleString() : "—"} · Source: {application.status_source}
      </p>
      <div className="card-actions">
        <select value={nextStatus} onChange={(event) => setNextStatus(event.target.value)}>
          {APPLICATION_STATUSES.map((status) => <option key={status} value={status}>{statusLabel(status)}</option>)}
        </select>
        <button
          className="button secondary"
          onClick={() => app.updateApplicationStatusAction(application.id, nextStatus)}
          disabled={app.busy === "applications" || nextStatus === application.status}
        >
          Update Status
        </button>
      </div>
    </article>
  );
}

function EmailDetailsDrawer({ event, onClose }) {
  const app = useApp();
  const reliable = isReliableMatch(event);
  const received = formatDateTime(event.received_at);
  const needsConfirmation = event.resolution === "NEEDS_CONFIRMATION";

  return (
    <div className="uj-drawer-overlay" onClick={onClose}>
      <aside className="uj-drawer" onClick={(clickEvent) => clickEvent.stopPropagation()} role="dialog" aria-label="Email details">
        <header className="uj-drawer-head">
          <div>
            <h2>{event.subject || "Job-related email"}</h2>
            <p className="uj-company-name">From: {event.sender}</p>
          </div>
          <button className="uj-drawer-close" onClick={onClose} aria-label="Close email details">✕</button>
        </header>

        <div className="uj-drawer-meta">
          <p><b>Received:</b> {received || "Date unavailable"}</p>
          <p><b>Classification:</b> <StatusBadge tone="neutral">{statusLabel(event.event_type)}</StatusBadge></p>
          {/* Same `reliable` boolean drives both this message AND the Confirm
              button's disabled state below — they must never disagree (that
              was the bug: "No reliable match" text could show while Confirm
              stayed enabled, because the message used an extra title/company
              presence check the button didn't). Whenever reliable is true we
              always show a "Matched application" line, falling back to a
              generic label in the unlikely case both fields are blank. */}
          {reliable ? (
            <p className="possible-update-target">
              Matched application: {[event.matched_application_title, event.matched_application_company].filter(Boolean).join(" — ") || "Details unavailable"}
            </p>
          ) : (
            <p className="possible-update-no-match">No reliable application match found.</p>
          )}
        </div>

        <div className="uj-drawer-body">
          {needsConfirmation && (
            <p className="possible-update-line">
              Possible application update — confidence {Math.round((event.confidence || 0) * 100)}%
            </p>
          )}
          {needsConfirmation && !reliable && (
            <p className="possible-update-no-match confirm-unavailable-hint">
              Confirm is unavailable because no reliable application match was found.
            </p>
          )}
          {event.resolution === "CONFIRMED" && (
            <p className="draft-confirmed-line">Application update confirmed</p>
          )}
        </div>

        {needsConfirmation && (
          <footer className="uj-drawer-actions">
            <button
              className="button primary"
              onClick={() => app.resolveJobEmailEventAction(event.id, "confirm")}
              disabled={!reliable || app.busy === "applications"}
            >
              Confirm
            </button>
            <button
              className="button secondary"
              onClick={() => app.resolveJobEmailEventAction(event.id, "ignore")}
              disabled={app.busy === "applications"}
            >
              Ignore
            </button>
          </footer>
        )}

        {/* View Details ends with the professionally-formatted body (original
            line/paragraph breaks preserved) — the compact list row keeps
            using the flattened body_snippet unchanged (see EmailListCard). */}
        <div className="email-preview email-preview-full">{event.body_formatted || event.body_snippet}</div>
      </aside>
    </div>
  );
}

function EmailListCard({ event, onOpenDetails }) {
  const app = useApp();
  const received = formatDateTime(event.received_at);
  return (
    <article className={`email-row${event.is_read ? "" : " unread"}`}>
      <div className="mail-icon">✉</div>
      <div className="email-row-body">
        <h3 className="email-row-subject">{event.subject || "Job-related email"}</h3>
        <div className="email-row-meta">
          <span className="email-row-time">{received || "Date unavailable"}</span>
          <button
            type="button"
            className={`star-button${event.is_starred ? " starred" : ""}`}
            onClick={() => app.toggleJobEmailEventStarAction(event.id)}
            aria-label={event.is_starred ? "Unstar email" : "Star email"}
            aria-pressed={event.is_starred}
          >
            {event.is_starred ? "★" : "☆"}
          </button>
          <StatusBadge tone="neutral">{statusLabel(event.event_type)}</StatusBadge>
        </div>
        <p className="email-row-sender">From: {event.sender}</p>
        <div className="card-actions email-row-actions">
          <button className="button secondary" onClick={() => onOpenDetails(event)}>View Details</button>
          <button
            className="button primary"
            onClick={() => app.markJobEmailEventReadAction(event.id)}
            disabled={event.is_read || app.busy === "email-events"}
          >
            {event.is_read ? "Read" : "Mark as Read"}
          </button>
        </div>
        <p className="email-preview email-row-snippet">{event.body_snippet}</p>
      </div>
    </article>
  );
}

function EmailToolbar({ activeTab, setActiveTab, tabCounts, search, setSearch, sort, setSort }) {
  return (
    <div className="email-toolbar-row">
      <div className="email-tabs" role="tablist">
        {TAB_DEFS.map((tab) => (
          <button
            key={tab.key}
            role="tab"
            aria-selected={activeTab === tab.key}
            className={`email-tab${activeTab === tab.key ? " active" : ""}`}
            onClick={() => setActiveTab(tab.key)}
          >
            {tab.label} ({tabCounts[tab.key] || 0})
          </button>
        ))}
      </div>
      <div className="uj-toolbar">
        <input
          className="uj-search-input"
          type="search"
          placeholder="Search emails..."
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <select value={sort} onChange={(event) => setSort(event.target.value)} aria-label="Sort emails">
          <option value="newest">Newest First</option>
          <option value="oldest">Oldest First</option>
          <option value="unread">Unread First</option>
        </select>
      </div>
    </div>
  );
}

export default function EmailSummaryPage() {
  const app = useApp();
  const [activeTab, setActiveTab] = useState("all");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState("newest");
  const [detailsEventId, setDetailsEventId] = useState(null);

  useEffect(() => {
    app.loadJobEmailEvents();
    app.loadApplications();
  }, [app.currentUser]);

  const events = app.jobEmailEvents;

  const tabCounts = useMemo(() => {
    const counts = {};
    TAB_DEFS.forEach((tab) => { counts[tab.key] = events.filter(tab.match).length; });
    return counts;
  }, [events]);

  const visibleEvents = useMemo(() => {
    const activeTabDef = TAB_DEFS.find((tab) => tab.key === activeTab) || TAB_DEFS[0];
    const term = search.trim().toLowerCase();
    let list = events.filter(activeTabDef.match);
    if (term) {
      list = list.filter((event) => [
        event.subject, event.sender, event.matched_application_company, event.matched_application_title, event.body_snippet,
      ].some((field) => (field || "").toLowerCase().includes(term)));
    }
    return [...list].sort((a, b) => {
      if (sort === "unread" && Boolean(a.is_read) !== Boolean(b.is_read)) {
        return a.is_read ? 1 : -1;
      }
      const timeA = a.received_at ? new Date(a.received_at).getTime() : 0;
      const timeB = b.received_at ? new Date(b.received_at).getTime() : 0;
      return sort === "oldest" ? timeA - timeB : timeB - timeA;
    });
  }, [events, activeTab, search, sort]);

  const detailsEvent = detailsEventId ? events.find((event) => event.id === detailsEventId) || null : null;

  return (
    <Page className="email-summary-page" title="Email Summary" subtitle="Your career workflow email history, Gmail connection, and application tracking.">
      <div className="email-summary-header-grid">
        <GmailConnectionCard />
        <EmailMetricCards events={events} />
      </div>

      <EmailSummarySettings />

      <div className="emails-section">
        <EmailToolbar
          activeTab={activeTab} setActiveTab={setActiveTab} tabCounts={tabCounts}
          search={search} setSearch={setSearch} sort={sort} setSort={setSort}
        />
        <Notice tone={/unable|error/i.test(app.messages["email-events"] || "") ? "error" : undefined}>{app.messages["email-events"]}</Notice>
        {visibleEvents.length ? (
          <div className="email-grid email-row-list">
            {visibleEvents.map((event) => <EmailListCard key={event.id} event={event} onOpenDetails={(target) => setDetailsEventId(target.id)} />)}
          </div>
        ) : (
          <EmptyState title="No emails found" text="Connect Gmail and scan job emails, or adjust your search or filters." />
        )}
      </div>

      <Panel className="tracked-applications-panel" title="Application Pipeline" subtitle="Track the current stage of your job applications.">
        <Notice>{app.messages.applications}</Notice>
        {app.applications.length ? (
          <div className="application-grid">
            {app.applications.map((application) => <ApplicationCard key={application.id} application={application} />)}
          </div>
        ) : (
          <EmptyState title="No applications in your pipeline yet" text="Connect Gmail and scan job emails, or apply to a job, to start tracking status here." />
        )}
      </Panel>

      {detailsEvent && (
        <EmailDetailsDrawer event={detailsEvent} onClose={() => setDetailsEventId(null)} />
      )}
    </Page>
  );
}
