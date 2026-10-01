import { useApp } from "../context/AppContext";
import { userDisplayName } from "../utils/userDisplay";

export default function Topbar({ onMenu, showWelcome = true }) {
  const { currentUser } = useApp();
  const name = userDisplayName(currentUser);
  if (!showWelcome) {
    return (
      <header className="module-mobile-topbar">
        <button className="mobile-menu" type="button" aria-label="Open navigation" onClick={onMenu}>☰</button>
      </header>
    );
  }
  return (
    <header className="topbar">
      <button className="mobile-menu" onClick={onMenu}>☰</button>
      <div className="topbar-copy">
        <h1>Welcome back, {name} <span>👋</span></h1>
        <p>Track jobs, optimize resumes and boost your career with AI.</p>
      </div>
    </header>
  );
}
