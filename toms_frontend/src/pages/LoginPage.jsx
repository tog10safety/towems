import { useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext.jsx";
import Icon from "../components/Icon.jsx";
import { Button, ErrorBanner, Field, Input } from "../components/ui.jsx";
import { useTheme } from "../theme/ThemeContext.jsx";

export default function LoginPage() {
  const { user, login } = useAuth();
  const { resolvedTheme, toggleTheme } = useTheme();
  const navigate = useNavigate();
  const location = useLocation();
  const [form, setForm] = useState({ username: "", password: "" });
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  if (user) return <Navigate to={location.state?.from || "/"} replace />;

  async function onSubmit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await login(form.username.trim(), form.password);
      navigate(location.state?.from || "/", { replace: true });
    } catch (err) {
      setError(err.message || "Sign-in failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-shell">
      <button
        className="icon-btn login-theme-toggle"
        onClick={toggleTheme}
        aria-label={`Switch to ${resolvedTheme === "dark" ? "light" : "dark"} appearance`}
        title={`Switch to ${resolvedTheme === "dark" ? "light" : "dark"} appearance`}
      >
        <Icon name={resolvedTheme === "dark" ? "sun" : "moon"} size={20} />
      </button>

      <main className="login-panel">
        <aside className="login-identity">
          <img
            className="login-emblem"
            src="/logo.png"
            alt="Philippine Air Force seal"
            width="112"
            height="112"
          />
          <p className="login-force">Philippine Air Force</p>
          <span className="login-rule" aria-hidden="true" />
          <h1 className="login-app">TOWEMS</h1>
          <p className="login-app-sub">Tactical Operations Wings Enterprise Management System</p>
        </aside>

        <section className="login-form-panel">
          <h2 className="login-heading">Sign in</h2>
          <p className="login-copy">
        
          </p>

          <ErrorBanner message={error} />

          <form onSubmit={onSubmit} className="login-form">
            <Field label="Username" htmlFor="username" required>
              <Input
                id="username"
                autoComplete="username"
                autoFocus
                value={form.username}
                onChange={(event) => setForm({ ...form, username: event.target.value })}
                required
              />
            </Field>
            <Field label="Password" htmlFor="password" required>
              <div className="login-password">
                <Input
                  id="password"
                  type={showPassword ? "text" : "password"}
                  autoComplete="current-password"
                  value={form.password}
                  onChange={(event) => setForm({ ...form, password: event.target.value })}
                  required
                />
                <button
                  type="button"
                  className="login-password-toggle"
                  onClick={() => setShowPassword((shown) => !shown)}
                  aria-label={showPassword ? "Hide password" : "Show password"}
                  aria-pressed={showPassword}
                  tabIndex={0}
                >
                  {showPassword ? "Hide" : "Show"}
                </button>
              </div>
            </Field>
            <Button type="submit" disabled={busy} style={{ width: "100%" }}>
              {busy ? "Signing in…" : "Sign in"}
            </Button>
          </form>

          <p className="login-footnote">
            Forgotten your password? Ask an administrator to issue a reset link.
          </p>
        </section>
      </main>
    </div>
  );
}
