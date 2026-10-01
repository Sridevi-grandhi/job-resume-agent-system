/* eslint-disable react-hooks/set-state-in-effect -- reset form state when auth mode changes */
import { useEffect, useState } from "react";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { useApp } from "../context/AppContext";

const MIN_PASSWORD_LENGTH = 8;

function AuthShell({ title, subtitle, children, footer }) {
  return (
    <div className="auth">
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
        <div className="auth-card">
          <div className="auth-mini-brand"><span>CB</span></div>
          <h2>{title}</h2>
          <p>{subtitle}</p>
          {children}
          {footer}
        </div>
      </section>
    </div>
  );
}

function PasswordVisibilityButton({ visible, label, onClick }) {
  return (
    <button
      type="button"
      className="password-visibility-toggle"
      aria-label={`${visible ? "Hide" : "Show"} ${label}`}
      onClick={onClick}
    >
      {visible ? (
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m3 3 18 18M10.6 10.7a2 2 0 0 0 2.7 2.7M9.9 4.2A10.8 10.8 0 0 1 12 4c5.5 0 9.3 4.3 10 8-.3 1.5-1.2 3.1-2.6 4.4M6.2 6.2C4.3 7.6 2.8 9.8 2 12c.7 3.7 4.5 8 10 8 1.2 0 2.3-.2 3.3-.6" /></svg>
      ) : (
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.6-8 10-8 10 8 10 8-3.6 8-10 8S2 12 2 12Z" /><circle cx="12" cy="12" r="3" /></svg>
      )}
    </button>
  );
}

export default function AuthPage({ mode }) {
  const app = useApp();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const resetToken = searchParams.get("token") || "";

  const [form, setForm] = useState({ name: "", email: "", password: "", confirmPassword: "" });
  const [localError, setLocalError] = useState("");
  const [signupConfirmError, setSignupConfirmError] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [forgotSent, setForgotSent] = useState(false);
  const [resetDone, setResetDone] = useState(false);
  const [devResetUrl, setDevResetUrl] = useState("");

  useEffect(() => {
    app.clearAuthMessages?.();
    setLocalError("");
    setSignupConfirmError("");
    setShowPassword(false);
    setShowConfirmPassword(false);
    setForgotSent(false);
    setResetDone(false);
    setDevResetUrl("");
    setForm({ name: "", email: "", password: "", confirmPassword: "" });
  }, [mode]);

  // An existing account can use the supported email reset flow from Settings.
  if (app.currentUser && mode !== "reset" && mode !== "forgot") {
    return <Navigate to="/dashboard" replace />;
  }

  const loginBusy = app.busy === "authLogin" || app.busy === "auth";
  const signupBusy = app.busy === "authSignup" || app.busy === "auth";
  const forgotBusy = app.busy === "authForgot";
  const resetBusy = app.busy === "authReset";

  const loginError = app.messages.authLogin || (mode === "login" ? app.messages.auth : "");
  const signupError = app.messages.authSignup || (mode === "signup" ? app.messages.auth : "");
  const forgotMessage = app.messages.authForgot || "";
  const resetMessage = app.messages.authReset || "";

  const submitLoginOrSignup = async (event) => {
    event.preventDefault();
    setLocalError("");
    if (mode === "signup" && (form.password || "").length < MIN_PASSWORD_LENGTH) {
      setLocalError(`Password must be at least ${MIN_PASSWORD_LENGTH} characters.`);
      return;
    }
    if (mode === "signup") {
      const confirmError = !form.confirmPassword
        ? "Please confirm your password."
        : form.password !== form.confirmPassword
          ? "Passwords do not match."
          : "";
      if (confirmError) {
        setSignupConfirmError(confirmError);
        return;
      }
      setSignupConfirmError("");
      const signupPayload = { name: form.name, email: form.email, password: form.password };
      if (await app.authenticate(mode, signupPayload)) navigate("/dashboard");
      return;
    }
    if (await app.authenticate(mode, form)) navigate("/dashboard");
  };

  const updateSignupPassword = (field, value) => {
    const nextForm = { ...form, [field]: value };
    setForm(nextForm);
    if (signupConfirmError) {
      setSignupConfirmError(!nextForm.confirmPassword
        ? "Please confirm your password."
        : nextForm.password !== nextForm.confirmPassword
          ? "Passwords do not match."
          : "");
    }
  };

  const submitForgot = async (event) => {
    event.preventDefault();
    setLocalError("");
    setForgotSent(false);
    setDevResetUrl("");
    const result = await app.requestPasswordReset(form.email);
    if (result) {
      setForgotSent(true);
      // Only ever set when the backend explicitly opts in (APP_ENV=development
      // AND SHOW_DEV_RESET_LINK=true) — null in every normal/production response.
      if (result.dev_reset_url) setDevResetUrl(result.dev_reset_url);
    } else {
      // The request itself failed (network/server error) — a safe, generic
      // message, never the raw backend exception/error text.
      setLocalError("Unable to process the request right now. Please try again.");
    }
  };

  const submitReset = async (event) => {
    event.preventDefault();
    setLocalError("");
    if (!resetToken) {
      setLocalError("This password reset link is invalid or has expired. Please request a new one.");
      return;
    }
    if ((form.password || "").length < MIN_PASSWORD_LENGTH) {
      setLocalError(`Password must be at least ${MIN_PASSWORD_LENGTH} characters.`);
      return;
    }
    if (form.password !== form.confirmPassword) {
      setLocalError("Passwords do not match.");
      return;
    }
    const result = await app.submitPasswordReset({ token: resetToken, new_password: form.password });
    if (result) setResetDone(true);
  };

  if (mode === "forgot") {
    return (
      <AuthShell
        title="Forgot your password?"
        subtitle="Enter the email address associated with your CareerBoost account and we'll send you instructions to reset your password."
        footer={(
          <p className="auth-switch">
            <Link to="/login" onClick={() => app.clearAuthMessages?.()}>Back to Sign in</Link>
          </p>
        )}
      >
        <form onSubmit={submitForgot}>
          <label>Email address
            <input
              type="email"
              value={form.email}
              onChange={(event) => setForm({ ...form, email: event.target.value })}
              placeholder="you@example.com"
              required
              autoComplete="email"
            />
          </label>
          <button className="button primary wide" disabled={forgotBusy} type="submit">
            {forgotBusy ? "Please wait…" : "Send Reset Link"}
          </button>
        </form>
        {forgotSent && (
          <div className="notice">
            {forgotMessage || "If an account exists for this email, password reset instructions have been sent."}
            {" "}Please check your inbox. Check your spam or junk folder if you don't see the email.
          </div>
        )}
        {localError && <div className="notice error">{localError}</div>}
        {/* Only ever populated behind an explicit backend opt-in
            (APP_ENV=development AND SHOW_DEV_RESET_LINK=true) — never
            shown in normal/production use. Raw reset link is never
            displayed otherwise. */}
        {devResetUrl && (
          <div className="notice">
            Development reset link:{" "}
            <a href={devResetUrl}>{devResetUrl}</a>
          </div>
        )}
      </AuthShell>
    );
  }

  if (mode === "reset") {
    const invalidTokenUi = !resetToken || (resetMessage && /invalid or has expired/i.test(resetMessage));
    if (resetDone) {
      return (
        <AuthShell
          title="Password updated"
          subtitle="Your password was changed successfully. You can now sign in with your new password."
          footer={(
            <p className="auth-switch">
              <Link to="/login" onClick={() => app.clearAuthMessages?.()}>Continue to Sign in</Link>
            </p>
          )}
        >
          <div className="notice">{resetMessage || "Password updated successfully."}</div>
          <button className="button primary wide" type="button" onClick={() => navigate("/login")}>
            Continue to Sign in
          </button>
        </AuthShell>
      );
    }

    return (
      <AuthShell
        title="Reset your password"
        subtitle="Choose a new password for your CareerBoost account."
        footer={(
          <p className="auth-switch">
            {invalidTokenUi && !resetToken ? (
              <Link to="/forgot-password" onClick={() => app.clearAuthMessages?.()}>Request New Reset Link</Link>
            ) : (
              <Link to="/login" onClick={() => app.clearAuthMessages?.()}>Back to Sign in</Link>
            )}
          </p>
        )}
      >
        {!resetToken ? (
          <>
            <div className="notice error">This password reset link is invalid or has expired. Please request a new one.</div>
            <button className="button primary wide" type="button" onClick={() => navigate("/forgot-password")}>
              Request New Reset Link
            </button>
          </>
        ) : (
          <form onSubmit={submitReset}>
            <label>New password
              <input
                type="password"
                value={form.password}
                onChange={(event) => setForm({ ...form, password: event.target.value })}
                placeholder="••••••••"
                required
                minLength={MIN_PASSWORD_LENGTH}
                autoComplete="new-password"
              />
            </label>
            <label>Confirm new password
              <input
                type="password"
                value={form.confirmPassword}
                onChange={(event) => setForm({ ...form, confirmPassword: event.target.value })}
                placeholder="••••••••"
                required
                minLength={MIN_PASSWORD_LENGTH}
                autoComplete="new-password"
              />
            </label>
            <p className="auth-hint">Password must be at least {MIN_PASSWORD_LENGTH} characters.</p>
            <button className="button primary wide" disabled={resetBusy} type="submit">
              {resetBusy ? "Please wait…" : "Reset Password"}
            </button>
          </form>
        )}
        {localError && <div className="notice error">{localError}</div>}
        {resetMessage && !resetDone && (
          <div className="notice error">
            {resetMessage}
            {/invalid or has expired/i.test(resetMessage) && (
              <div className="auth-inline-action">
                <Link to="/forgot-password" onClick={() => app.clearAuthMessages?.()}>Request New Reset Link</Link>
              </div>
            )}
          </div>
        )}
      </AuthShell>
    );
  }

  return (
    <AuthShell
      title={mode === "login" ? "Welcome back" : "Create your account"}
      subtitle={mode === "login" ? "Sign in to continue your career workflow." : "Start building better applications today."}
      footer={(
        <p className="auth-switch">
          {mode === "login" ? "New to CareerBoost? " : "Already have an account? "}
          <Link
            to={mode === "login" ? "/signup" : "/login"}
            onClick={() => app.clearAuthMessages?.()}
          >
            {mode === "login" ? "Create an account" : "Sign in"}
          </Link>
        </p>
      )}
    >
      <form onSubmit={submitLoginOrSignup}>
        {mode === "signup" && (
          <label>Full name
            <input
              name="name"
              value={form.name}
              onChange={(event) => setForm({ ...form, name: event.target.value })}
              placeholder="Your name"
              required
              autoComplete="name"
            />
          </label>
        )}
        <label>Email address
          <input
            type="email"
            value={form.email}
            onChange={(event) => setForm({ ...form, email: event.target.value })}
            placeholder="you@example.com"
            required
            autoComplete="email"
          />
        </label>
        <label className={mode === "signup" ? "signup-password-field" : ""}>Password
          <input
            className={mode === "signup" ? "auth-password-input" : undefined}
            type={mode === "signup" && showPassword ? "text" : "password"}
            value={form.password}
            onChange={(event) => mode === "signup"
              ? updateSignupPassword("password", event.target.value)
              : setForm({ ...form, password: event.target.value })}
            placeholder="••••••••"
            required
            minLength={mode === "signup" ? MIN_PASSWORD_LENGTH : undefined}
            autoComplete={mode === "login" ? "current-password" : "new-password"}
          />
          {mode === "signup" && <PasswordVisibilityButton visible={showPassword} label="password" onClick={() => setShowPassword((visible) => !visible)} />}
        </label>
        {mode === "signup" && (
          <label className="signup-password-field">Confirm password
            <input
              className="auth-password-input"
              type={showConfirmPassword ? "text" : "password"}
              value={form.confirmPassword}
              onChange={(event) => updateSignupPassword("confirmPassword", event.target.value)}
              placeholder="Re-enter your password"
              aria-required="true"
              aria-invalid={Boolean(signupConfirmError)}
              aria-describedby={signupConfirmError ? "signup-confirm-password-error" : undefined}
              minLength={MIN_PASSWORD_LENGTH}
              autoComplete="new-password"
            />
            <PasswordVisibilityButton visible={showConfirmPassword} label="confirm password" onClick={() => setShowConfirmPassword((visible) => !visible)} />
            {signupConfirmError && (
              <span id="signup-confirm-password-error" className="auth-field-error" role="alert">
                {signupConfirmError}
              </span>
            )}
          </label>
        )}
        {mode === "login" && (
          <div className="auth-forgot-row">
            <Link
              to="/forgot-password"
              className="auth-forgot-link"
              onClick={() => app.clearAuthMessages?.()}
            >
              Forgot password?
            </Link>
          </div>
        )}
        <button
          className="button primary wide"
          disabled={mode === "login" ? loginBusy : signupBusy}
          type="submit"
        >
          {(mode === "login" ? loginBusy : signupBusy)
            ? "Please wait…"
            : mode === "login" ? "Sign in" : "Create account"}
        </button>
      </form>
      {localError && <div className="notice error">{localError}</div>}
      {mode === "login" && loginError && <div className="notice error">{loginError}</div>}
      {mode === "signup" && signupError && <div className="notice error">{signupError}</div>}
    </AuthShell>
  );
}
