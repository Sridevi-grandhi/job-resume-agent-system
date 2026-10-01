import { useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import Sidebar from "./Sidebar";
import Topbar from "./Topbar";

export default function Layout() {
  const [open, setOpen] = useState(false);
  const { pathname } = useLocation();
  return (
    <div className="shell">
      <Sidebar key={pathname} open={open} onClose={() => setOpen(false)} />
      <main><Topbar showWelcome={pathname === "/dashboard"} onMenu={() => setOpen(true)} /><Outlet /></main>
    </div>
  );
}
