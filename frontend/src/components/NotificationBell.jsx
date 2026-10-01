import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useApp } from "../context/AppContext";
import { Icon } from "./Icons";
import * as api from "../api";

const READ_STORAGE_PREFIX = "cb_notifications_read_v1_";

function loadReadIds(userId) {
  if (!userId) return new Set();
  try {
    const raw = localStorage.getItem(READ_STORAGE_PREFIX + userId);
    const parsed = raw ? JSON.parse(raw) : [];
    return new Set(Array.isArray(parsed) ? parsed : []);
  } catch {
    return new Set();
  }
}

function saveReadIds(userId, ids) {
  if (!userId) return;
  try {
    localStorage.setItem(READ_STORAGE_PREFIX + userId, JSON.stringify([...ids]));
  } catch {
    // Storage unavailable (private browsing, quota, etc.) — read state
    // just won't persist across reloads; never a crash.
  }
}

function timeAgo(dateString) {
  if (!dateString) return "";
  const date = new Date(dateString);
  if (Number.isNaN(date.getTime())) return "";
  const minutes = Math.round((Date.now() - date.getTime()) / 60000);
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hr ago`;
  const days = Math.round(hours / 24);
  if (days === 1) return "Yesterday";
  if (days < 7) return `${days} days ago`;
  return date.toLocaleDateString();
}

/** Real, existing data only — never a fabricated notification. */
const EVENT_NOTIFICATION_KIND = {
  RECRUITER_OUTREACH: ["job_alerts_enabled", "Job alert"],
  OTHER: ["job_alerts_enabled", "Job alert"],
  INTERVIEW_INVITE: ["interview_alerts_enabled", "Interview invitation"],
  APPLICATION_CONFIRMATION: ["application_updates_enabled", "Application update"],
  APPLICATION_UPDATE: ["application_updates_enabled", "Application update"],
  ASSESSMENT_REQUEST: ["application_updates_enabled", "Application update"],
  OFFER: ["application_updates_enabled", "Application update"],
  REJECTION: ["application_updates_enabled", "Application update"],
};

function buildNotifications({ jobEmailEvents, dailySummaryLog, readIds, preferences }) {
  const items = [];

  for (const event of jobEmailEvents || []) {
    if (event.resolution !== "NEEDS_CONFIRMATION") continue;
    const [preferenceKey, title] = EVENT_NOTIFICATION_KIND[event.event_type] || EVENT_NOTIFICATION_KIND.APPLICATION_UPDATE;
    if (preferences?.[preferenceKey] === false) continue;
    items.push({
      id: `application_update:${event.id}`,
      type: event.event_type,
      title,
      message: event.subject || "An application-related email needs your review.",
      timestamp: event.received_at || event.created_at,
      target: "/emails",
    });
  }

  if (dailySummaryLog) {
    items.push({
      id: `daily_summary:${dailySummaryLog.email_log_id}`,
      type: "DAILY_SUMMARY",
      title: "Daily job summary",
      message: dailySummaryLog.subject || "Your latest job summary is ready.",
      timestamp: dailySummaryLog.created_at,
      target: "/emails",
      alwaysRead: true, // informational only — no natural unread/read transition exists for it
    });
  }

  items.sort((a, b) => new Date(b.timestamp || 0) - new Date(a.timestamp || 0));
  return items.map((item) => ({ ...item, unread: !item.alwaysRead && !readIds.has(item.id) }));
}

export default function NotificationBell({ inSidebar = false, onNavigate, settingsAction = false }) {
  const app = useApp();
  const navigate = useNavigate();
  const userId = app.currentUser?.user_id;
  const containerRef = useRef(null);
  const buttonRef = useRef(null);

  const [open, setOpen] = useState(false);
  const [readIds, setReadIds] = useState(() => loadReadIds(userId));
  const [dailySummaryLog, setDailySummaryLog] = useState(null);

  useEffect(() => { setReadIds(loadReadIds(userId)); }, [userId]);

  useEffect(() => {
    if (!userId) return;
    app.loadJobEmailEvents();
    app.loadEmailSummaryPreferences();
    // Deliberately bypasses the shared loadEmailLogs() action: that 404s
    // (and would set app.messages.emails — which the Daily Summary tab
    // displays) whenever no summary has ever been generated, the common
    // case here. This is a background/best-effort lookup only.
    api.getEmailLogs(userId)
      .then((data) => {
        const logs = Array.isArray(data.logs) ? data.logs : [];
        const latest = logs.find((log) => (log.event_type || "summary_generated") === "summary_generated");
        setDailySummaryLog(latest || null);
      })
      .catch(() => setDailySummaryLog(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId]);

  useEffect(() => {
    if (!open) return undefined;
    const handlePointerDown = (event) => {
      if (containerRef.current && !containerRef.current.contains(event.target)) setOpen(false);
    };
    const handleKeyDown = (event) => {
      if (event.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      }
    };
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  const notifications = useMemo(
    () => buildNotifications({ jobEmailEvents: app.jobEmailEvents, dailySummaryLog, readIds, preferences: app.emailSummaryPreferences }),
    [app.jobEmailEvents, app.emailSummaryPreferences, dailySummaryLog, readIds],
  );
  const unreadCount = notifications.filter((item) => item.unread).length;
  const badgeText = unreadCount > 9 ? "9+" : String(unreadCount);

  const markRead = (id) => {
    setReadIds((current) => {
      if (current.has(id)) return current;
      const next = new Set(current);
      next.add(id);
      saveReadIds(userId, next);
      return next;
    });
  };

  const markAllRead = () => {
    setReadIds((current) => {
      const next = new Set(current);
      notifications.forEach((item) => next.add(item.id));
      saveReadIds(userId, next);
      return next;
    });
  };

  const handleRowClick = (item) => {
    markRead(item.id);
    setOpen(false);
    if (item.target) {
      navigate(item.target);
      onNavigate?.();
    }
  };

  const toggleOpen = () => setOpen((current) => !current);
  const handleButtonKeyDown = (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      toggleOpen();
    }
  };

  return (
    <div className={`notification-wrap${inSidebar ? " sidebar-notification-wrap" : ""}${settingsAction ? " settings-notification-wrap" : ""}`} ref={containerRef}>
      <button
        type="button"
        ref={buttonRef}
        className={inSidebar ? "sidebar-profile-item sidebar-notification-trigger" : settingsAction ? "button secondary settings-notification-trigger" : "notification"}
        aria-label={settingsAction ? "View Notifications" : "Notifications"}
        aria-haspopup="true"
        aria-expanded={open}
        onClick={toggleOpen}
        onKeyDown={handleButtonKeyDown}
      >
        <Icon name="bell" size={18} />
        {inSidebar && <span>Notifications</span>}
        {settingsAction && <span>View Notifications</span>}
        {unreadCount > 0 && <b className={inSidebar || settingsAction ? "sidebar-notification-badge" : undefined}>{badgeText}</b>}
      </button>
      {open && (
        <div className={`notification-dropdown${inSidebar ? " sidebar-notification-dropdown" : ""}`} role="menu">
          <div className="notification-dropdown-header">
            <span>Notifications</span>
            {unreadCount > 0 && (
              <button type="button" className="notification-mark-all" onClick={markAllRead}>
                Mark all as read
              </button>
            )}
          </div>
          {notifications.length ? (
            <ul className="notification-list">
              {notifications.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    className={`notification-row${item.unread ? " unread" : ""}`}
                    onClick={() => handleRowClick(item)}
                  >
                    <span className="notification-row-title">{item.title}</span>
                    <span className="notification-row-message">{item.message}</span>
                    <span className="notification-row-time">{timeAgo(item.timestamp)}</span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <div className="notification-empty">You're all caught up.</div>
          )}
        </div>
      )}
    </div>
  );
}
