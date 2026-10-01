import { useEffect, useRef, useState } from "react";
import { NavLink } from "react-router-dom";
import { Icon } from "./Icons";
import NotificationBell from "./NotificationBell";
import { useApp } from "../context/AppContext";
import { userDisplayName, userInitial } from "../utils/userDisplay";

const links = [
  ["/dashboard", "dashboard", "Dashboard"],
  ["/preferences", "preferences", "Preferences"],
  ["/resume", "resume", "Resume"],
  ["/jobs", "jobs", "Job Search"],
  ["/updated-jobs", "updatedJobs", "Updated Job Search"],
  ["/selected-jobs", "selected", "Selected Jobs"],
  ["/ats-analyzer", "optimizer", "ATS Optimizer & Match"],
  ["/emails", "emails", "Email Summary"],
  ["/workflow", "workflow", "Workflow"],
];

export default function Sidebar({ open, onClose }) {
  const { currentUser, logout } = useApp();
  const name = userDisplayName(currentUser);
  const [profileOpen, setProfileOpen] = useState(false);
  const profileRef = useRef(null);
  const profileButtonRef = useRef(null);

  useEffect(() => {
    if (!profileOpen) return undefined;
    const handlePointerDown = (event) => {
      if (profileRef.current && !profileRef.current.contains(event.target)) setProfileOpen(false);
    };
    const handleKeyDown = (event) => {
      if (event.key === "Escape") {
        setProfileOpen(false);
        profileButtonRef.current?.focus();
      }
    };
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [profileOpen]);

  const closeProfile = () => {
    setProfileOpen(false);
    onClose?.();
  };

  const handleLogout = () => {
    closeProfile();
    logout();
  };

  return (
    <>
      <div className={`scrim ${open ? "show" : ""}`} onClick={onClose} />
      <aside className={`sidebar ${open ? "open" : ""}`}>
        <div className="brand"><span>CB</span><b>CareerBoost <em>AI</em></b></div>
        <nav>
          {links.map(([to, icon, label]) => (
            <NavLink key={to} to={to} onClick={onClose}>
              <Icon name={icon} /><span>{label}</span>
            </NavLink>
          ))}
        </nav>
        <div className={`sidebar-bottom${profileOpen ? " profile-expanded" : ""}`}>
          <div className="upgrade">
            <div>♛</div>
            <b>Career momentum</b>
            <p>Your AI-powered job search workspace.</p>
          </div>
          <div className="sidebar-profile" ref={profileRef}>
            {profileOpen && (
              <div id="sidebar-profile-actions" className="sidebar-profile-actions" role="group" aria-label="Profile actions">
                <NotificationBell inSidebar onNavigate={closeProfile} />
                <NavLink to="/settings" className="sidebar-profile-item" onClick={closeProfile}>
                  <Icon name="preferences" size={18} /><span>Settings</span>
                </NavLink>
                <button type="button" className="sidebar-profile-item sidebar-logout" onClick={handleLogout}>
                  <Icon name="logout" size={18} /><span>Logout</span>
                </button>
              </div>
            )}
            <button
              ref={profileButtonRef}
              type="button"
              className="user-menu"
              aria-expanded={profileOpen}
              aria-controls={profileOpen ? "sidebar-profile-actions" : undefined}
              onClick={() => setProfileOpen((current) => !current)}
            >
              <span>{userInitial(currentUser)}</span>
              <div>
                <b>{name}</b>
                <small>{currentUser?.email}</small>
              </div>
              <i aria-hidden="true">{profileOpen ? "⌃" : "⌄"}</i>
            </button>
          </div>
        </div>
      </aside>
    </>
  );
}
