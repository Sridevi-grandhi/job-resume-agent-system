/* eslint-disable react-hooks/exhaustive-deps -- reload only when the account changes */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useApp } from "../context/AppContext";
import { Notice, Page, Panel } from "../components/Page";
import StatusBadge from "../components/StatusBadge";
import NotificationBell from "../components/NotificationBell";

const SCAN_INTERVALS = [
  ["every_1_hour", "Every 1 hour"],
  ["every_6_hours", "Every 6 hours"],
  ["every_12_hours", "Every 12 hours"],
  ["daily", "Daily"],
];
const EMAIL_TYPE_OPTIONS = [
  ["job_opportunities", "Job Opportunities"],
  ["recruiter_messages", "Recruiter Messages"],
  ["application_updates", "Application Updates"],
  ["interview_invitations", "Interview Invitations"],
];
const ALL_EMAIL_TYPE_KEYS = EMAIL_TYPE_OPTIONS.map(([key]) => key);
const NOTIFICATION_OPTIONS = [
  ["job_alerts_enabled", "Job alerts"],
  ["application_updates_enabled", "Application updates"],
  ["interview_alerts_enabled", "Interview alerts"],
];

function formatScanTime(value) {
  if (!value) return "Not yet scanned";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Unavailable" : date.toLocaleString();
}

export default function SettingsPage() {
  const app = useApp();
  const [preferencesLoaded, setPreferencesLoaded] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [pendingScanEnabled, setPendingScanEnabled] = useState(null);
  const [frequencyDraft, setFrequencyDraft] = useState(null);
  const [autoSettingsMessage, setAutoSettingsMessage] = useState("");
  const [emailTypesDraft, setEmailTypesDraft] = useState(null);
  const [scanSettingsSaving, setScanSettingsSaving] = useState(false);
  const [scanSettingsMessage, setScanSettingsMessage] = useState("");
  const [notificationDraft, setNotificationDraft] = useState(null);
  const [notificationSaving, setNotificationSaving] = useState(false);
  const [notificationMessage, setNotificationMessage] = useState("");
  const [editingProfile, setEditingProfile] = useState(false);
  const [nameDraft, setNameDraft] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [profileMessage, setProfileMessage] = useState("");
  const [profileError, setProfileError] = useState("");
  const userId = app.currentUser?.user_id;

  useEffect(() => {
    let active = true;
    app.loadGmailStatus();
    Promise.resolve(app.loadEmailSummaryPreferences()).then((result) => {
      if (active) setPreferencesLoaded(Boolean(result));
    });
    return () => { active = false; };
  }, [userId]);

  const connected = app.gmailStatus?.connected === true;
  const gmailBusy = app.busy === "gmail" || scanning;
  const settingsBusy = app.busy === "email-settings";
  const scanSettings = app.emailSummaryPreferences;
  const storedFrequency = scanSettings?.gmail_scan_frequency ?? "daily";
  const frequency = frequencyDraft ?? storedFrequency;
  const storedEmailTypes = Array.isArray(scanSettings?.email_types_to_scan)
    ? scanSettings.email_types_to_scan : ALL_EMAIL_TYPE_KEYS;
  const emailTypes = emailTypesDraft ?? storedEmailTypes;
  const emailTypesDirty = emailTypes.length !== storedEmailTypes.length
    || storedEmailTypes.some((value) => !emailTypes.includes(value));
  const scanSettingsDirty = frequency !== storedFrequency || emailTypesDirty;
  const storedNotificationPreferences = Object.fromEntries(
    NOTIFICATION_OPTIONS.map(([key]) => [key, scanSettings?.[key] !== false]),
  );
  const notificationPreferences = notificationDraft ?? storedNotificationPreferences;
  const notificationDirty = NOTIFICATION_OPTIONS.some(([key]) => notificationPreferences[key] !== storedNotificationPreferences[key]);
  const profileBusy = app.busy === "profile";
  const nameDirty = nameDraft.trim() !== String(app.currentUser?.name || "").trim();
  const gmailMessage = app.messages.gmail || "";
  const gmailSuccess = /^Gmail disconnected\.|^Scan complete\b/.test(gmailMessage);

  const scanNow = async () => {
    setScanning(true);
    try { await app.scanGmailForJobs(); }
    finally { setScanning(false); }
  };
  const saveScanEnabled = async (enabled) => {
    setAutoSettingsMessage("");
    setPendingScanEnabled(enabled);
    try {
      const result = await app.saveEmailSummaryPreferences({ automatic_gmail_scan_enabled: enabled });
      setAutoSettingsMessage(result ? "Automatic scan settings saved." : "Unable to save automatic scan settings. Please try again.");
    }
    finally { setPendingScanEnabled(null); }
  };
  const toggleEmailType = (key) => {
    setScanSettingsMessage("");
    setEmailTypesDraft((draft) => {
      const current = draft ?? storedEmailTypes;
      return current.includes(key) ? current.filter((value) => value !== key) : [...current, key];
    });
  };
  const saveEmailScanSettings = async (event) => {
    event.preventDefault();
    if (!scanSettingsDirty || !connected) return;
    setScanSettingsSaving(true);
    setScanSettingsMessage("");
    try {
      const result = await app.saveEmailSummaryPreferences({ gmail_scan_frequency: frequency, email_types_to_scan: emailTypes });
      if (result) {
        setFrequencyDraft(null);
        setEmailTypesDraft(null);
        setScanSettingsMessage("Email scan settings saved.");
      } else {
        setScanSettingsMessage("Unable to save email scan settings. Please try again.");
      }
    } finally { setScanSettingsSaving(false); }
  };
  const toggleNotification = (key) => {
    setNotificationMessage("");
    setNotificationDraft((draft) => {
      const current = draft ?? storedNotificationPreferences;
      return { ...current, [key]: !current[key] };
    });
  };
  const saveNotificationSettings = async (event) => {
    event.preventDefault();
    if (!notificationDirty) return;
    setNotificationSaving(true);
    setNotificationMessage("");
    try {
      const result = await app.saveEmailSummaryPreferences(notificationPreferences);
      if (result) {
        setNotificationDraft(null);
        setNotificationMessage("Notification settings saved.");
      } else {
        setNotificationMessage("Unable to save notification settings. Please try again.");
      }
    } finally { setNotificationSaving(false); }
  };
  const startEditingProfile = () => {
    setNameDraft(app.currentUser?.name || "");
    setCurrentPassword("");
    setProfileMessage("");
    setProfileError("");
    setEditingProfile(true);
  };
  const saveProfile = async (event) => {
    event.preventDefault();
    const name = nameDraft.trim();
    if (!name || name.length > 100) {
      setProfileError("Name must be between 1 and 100 characters.");
      return;
    }
    if (!currentPassword) {
      setProfileError("Enter your current password to save your profile.");
      return;
    }
    setProfileError("");
    const result = await app.saveProfileName({ name, current_password: currentPassword });
    setCurrentPassword("");
    if (result) {
      setEditingProfile(false);
      setProfileMessage("Profile saved.");
    } else {
      setProfileError("Unable to save profile. Check your password and try again.");
    }
  };

  return (
    <Page
      className="settings-page"
      title="Settings"
      subtitle="Manage your account, integrations, notifications, and security."
    >
      <div className="settings-sections">
        <Panel title="Account & Profile">
          {editingProfile ? (
            <form className="settings-profile-form" onSubmit={saveProfile}>
              <label>Name
                <input value={nameDraft} maxLength={100} onChange={(event) => { setNameDraft(event.target.value); setProfileError(""); }} autoComplete="name" />
              </label>
              <div className="settings-readonly-field"><span>Email</span><strong>{app.currentUser?.email || "Not provided"}</strong><small>Email address is managed by your account.</small></div>
              <label>Current password
                <input type="password" value={currentPassword} onChange={(event) => { setCurrentPassword(event.target.value); setProfileError(""); }} autoComplete="current-password" />
              </label>
              <div className="settings-profile-actions">
                <button type="button" className="button secondary" disabled={profileBusy} onClick={() => { setEditingProfile(false); setProfileError(""); setCurrentPassword(""); }}>Cancel</button>
                <button type="submit" className="button primary" disabled={!nameDirty || profileBusy}>{profileBusy ? "Saving…" : "Save Profile"}</button>
              </div>
            </form>
          ) : (
            <>
              <dl className="settings-details">
                <div><dt>Name</dt><dd>{app.currentUser?.name?.trim() || "Not provided"}</dd></div>
                <div><dt>Email</dt><dd>{app.currentUser?.email || "Not provided"}</dd></div>
              </dl>
              <div className="settings-profile-actions"><button type="button" className="button secondary" onClick={startEditingProfile}>Edit Profile</button></div>
            </>
          )}
          <Notice tone="error">{profileError}</Notice>
          <Notice>{profileMessage}</Notice>
        </Panel>

        <Panel title="Gmail Integration" subtitle="Manage your connected inbox and automatic job email scans.">
          <dl className="settings-details settings-gmail-details">
            <div>
              <dt>Status</dt>
              <dd>{app.gmailStatus === null
                ? (gmailMessage ? "Connection unavailable" : "Checking connection…")
                : <StatusBadge tone={connected ? "success" : "neutral"}>{connected ? "Connected" : "Disconnected"}</StatusBadge>}
              </dd>
            </div>
            {connected && <div><dt>Connected account</dt><dd>{app.gmailStatus.email_address || "Unavailable"}</dd></div>}
            {connected && <div><dt>Last scan</dt><dd>{formatScanTime(app.gmailStatus.last_scanned_at)}</dd></div>}
          </dl>

          {connected ? (
            <div className="settings-scan-controls">
              <label className="settings-toggle">
                <span>
                  <strong>Automatic Gmail Scan</strong>
                  <small>Scan shortly after enabling, then at the selected interval.</small>
                </span>
                <span className="toggle-switch">
                  <input
                    type="checkbox"
                    checked={pendingScanEnabled ?? Boolean(scanSettings?.automatic_gmail_scan_enabled)}
                    disabled={!preferencesLoaded || settingsBusy}
                    onChange={(event) => saveScanEnabled(event.target.checked)}
                  />
                  <span className="toggle-switch-track" aria-hidden="true" />
                </span>
              </label>
              <label className="settings-interval">
                <span>Scan interval</span>
                <select
                  value={frequency}
                  disabled={!preferencesLoaded || settingsBusy || scanSettingsSaving}
                  onChange={(event) => { setFrequencyDraft(event.target.value); setScanSettingsMessage(""); }}
                >
                  {SCAN_INTERVALS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select>
              </label>
            </div>
          ) : app.gmailStatus !== null ? (
            <p className="settings-muted">Connect Gmail to manage automatic scans.</p>
          ) : null}

          <div className="settings-actions">
            {app.gmailStatus === null && gmailMessage ? (
              <button type="button" className="button secondary" disabled={gmailBusy} onClick={app.loadGmailStatus}>
                Retry connection status
              </button>
            ) : connected ? (
              <>
                <button type="button" className="button primary" disabled={gmailBusy} onClick={scanNow}>
                  {scanning ? "Scanning…" : "Scan Now"}
                </button>
                <button type="button" className="button secondary" disabled={gmailBusy} onClick={app.disconnectGmailAccount}>
                  Disconnect Gmail
                </button>
              </>
            ) : (
              <button type="button" className="button primary" disabled={gmailBusy || app.gmailStatus === null} onClick={app.connectGmailAccount}>
                Connect Gmail
              </button>
            )}
          </div>
          <Notice tone={gmailSuccess ? undefined : "error"}>
            {gmailMessage && (gmailSuccess ? gmailMessage : "Unable to complete the Gmail action. Please try again.")}
          </Notice>
          <Notice tone={autoSettingsMessage.startsWith("Unable") ? "error" : undefined}>{autoSettingsMessage}</Notice>
          {!preferencesLoaded && app.messages["email-settings"] && <Notice tone="error">Unable to load Gmail scan settings. Please try again.</Notice>}

          <div className="settings-email-types">
            <h4>Email Types to Scan</h4>
            {connected ? (
              <>
                <p>Choose which job-related messages appear in Email Summary. Save this selection and the scan interval together.</p>
                <form onSubmit={saveEmailScanSettings}>
                  <div className="settings-email-types-grid">
                    {EMAIL_TYPE_OPTIONS.map(([key, label]) => (
                      <label key={key}>
                        <input type="checkbox" checked={emailTypes.includes(key)} disabled={!preferencesLoaded || scanSettingsSaving || settingsBusy} onChange={() => toggleEmailType(key)} />
                        {label}
                      </label>
                    ))}
                  </div>
                  <div className="settings-profile-actions">
                    <button type="submit" className="button primary" disabled={!scanSettingsDirty || !preferencesLoaded || scanSettingsSaving || settingsBusy}>
                      {scanSettingsSaving ? "Saving…" : "Save Email Scan Settings"}
                    </button>
                  </div>
                </form>
              </>
            ) : (
              <p>Connect Gmail to configure automatic email scanning.</p>
            )}
            <Notice tone={scanSettingsMessage.startsWith("Unable") ? "error" : undefined}>{scanSettingsMessage}</Notice>
          </div>
        </Panel>

        <Panel title="Notifications">
          <p className="settings-muted">Choose which CareerBoost updates appear in your notifications.</p>
          <form onSubmit={saveNotificationSettings}>
            <div className="settings-notification-controls">
              {NOTIFICATION_OPTIONS.map(([key, label]) => (
                <label className="settings-toggle" key={key}>
                  <strong>{label}</strong>
                  <span className="toggle-switch">
                    <input type="checkbox" checked={notificationPreferences[key]} disabled={!preferencesLoaded || notificationSaving || settingsBusy} onChange={() => toggleNotification(key)} />
                    <span className="toggle-switch-track" aria-hidden="true" />
                  </span>
                </label>
              ))}
            </div>
            <div className="settings-notification-actions">
              <NotificationBell settingsAction />
              <button type="submit" className="button primary" disabled={!notificationDirty || !preferencesLoaded || notificationSaving || settingsBusy}>
                {notificationSaving ? "Saving…" : "Save Notification Settings"}
              </button>
            </div>
          </form>
          <Notice tone={notificationMessage.startsWith("Unable") ? "error" : undefined}>{notificationMessage}</Notice>
        </Panel>

        <Panel title="Security">
          <div className="settings-security-row">
            <div><strong>Password</strong><p>Request a secure link to change your password.</p></div>
            <Link className="button secondary" to="/forgot-password">Change Password</Link>
          </div>
        </Panel>
      </div>
    </Page>
  );
}
