import { useState } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { useApp } from "../context/AppContext";

export default function AuthPage({ mode }) {
  const app = useApp();
  const navigate = useNavigate();
  const [form, setForm] = useState({ name: "", email: "", password: "" });
  if (app.currentUser) return <Navigate to="/dashboard" replace />;

  const submit = async (event) => {
    event.preventDefault();
    if (await app.authenticate(mode, form)) navigate("/dashboard");
  };

  return <div className="auth">
    <section className="auth-hero">
      <div className="auth-brand"><span>CB</span> CareerBoost AI</div>
      <div>
        <i>AI-powered career workspace</i>
        <h1>Turn every application into your strongest one.</h1>
        <p>Search smarter, tailor every resume and understand exactly why each role matches your experience.</p>
        <div className="auth-benefits"><span>✓ Personalized matches</span><span>✓ Resume optimization</span><span>✓ Automated workflow</span></div>
      </div>
      <small>Built for focused, confident job searches.</small>
    </section>
    <section className="auth-form-wrap">
      <form className="auth-card" onSubmit={submit}>
        <div className="auth-mini-brand"><span>CB</span></div>
        <h2>{mode === "login" ? "Welcome back" : "Create your account"}</h2>
        <p>{mode === "login" ? "Sign in to continue your career workflow." : "Start building better applications today."}</p>
        {mode === "signup" && <label>Full name<input name="name" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="Your name" required /></label>}
        <label>Email address<input type="email" value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} placeholder="you@example.com" required /></label>
        <label>Password<input type="password" value={form.password} onChange={(event) => setForm({ ...form, password: event.target.value })} placeholder="••••••••" required /></label>
        <button className="button primary wide" disabled={app.busy === "auth"}>{app.busy === "auth" ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}</button>
        {app.messages.auth && <div className="notice error">{app.messages.auth}</div>}
        <p className="auth-switch">{mode === "login" ? "New to CareerBoost? " : "Already have an account? "}<Link to={mode === "login" ? "/signup" : "/login"}>{mode === "login" ? "Create an account" : "Sign in"}</Link></p>
      </form>
    </section>
  </div>;
}
