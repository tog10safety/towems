import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client.js";
import Icon from "../components/Icon.jsx";
import { Button, ErrorBanner, Field, Input } from "../components/ui.jsx";
import { useTheme } from "../theme/ThemeContext.jsx";

/**
 * Completes an administrator-issued password reset (spec §3B).
 *
 * The link carries a single-use token. No password is ever displayed; the user
 * chooses a new one here, and their existing sessions stop working because the
 * password digest changes.
 */
export default function ResetPasswordPage() {
  const [params] = useSearchParams();
  const token = params.get("token") || "";
  const { resolvedTheme, toggleTheme } = useTheme();

  const [state, setState] = useState({ checking: true, valid: false, expiresAt: null });
  const [form, setForm] = useState({ new_password: "", confirm: "" });
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    async function check() {
      if (!token) {
        setState({ checking: false, valid: false, expiresAt: null });
        return;
      }
      try {
        const result = await api.get(`/auth/password-reset/?token=${encodeURIComponent(token)}`);
        if (active) setState({ checking: false, valid: Boolean(result.valid), expiresAt: result.expires_at });
      } catch {
        if (active) setState({ checking: false, valid: false, expiresAt: null });
      }
    }
    check();
    return () => {
      active = false;
    };
  }, [token]);

  async function submit(event) {
    event.preventDefault();
    setError("");
    if (form.new_password !== form.confirm) {
      setError("The new passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      await api.post("/auth/password-reset/", {
        token,
        new_password: form.new_password,
      });
      setDone(true);
    } catch (err) {
      setError(err.fieldErrors?.new_password || err.message);
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
      >
        <Icon name={resolvedTheme === "dark" ? "sun" : "moon"} size={20} />
      </button>
      <div className="login-card">
        <div className="brand">
          <span className="brand-mark">TOWEMS</span>
          <span className="brand-sub">Password reset</span>
        </div>

        {state.checking ? (
          <p className="lead">Checking your reset link…</p>
        ) : done ? (
          <>
            <div className="alert alert-success">Your password has been updated.</div>
            <p className="lead">Sign in with the new password. Previous sessions were invalidated.</p>
            <Link className="btn btn-primary btn-md" to="/login" style={{ width: "100%" }}>
              Go to sign in
            </Link>
          </>
        ) : !state.valid ? (
          <>
            <div className="alert alert-danger">
              This reset link is invalid, expired or already used.
            </div>
            <p className="lead">Ask an administrator to issue a new password reset link.</p>
            <Link className="btn btn-ghost btn-md" to="/login" style={{ width: "100%" }}>
              Back to sign in
            </Link>
          </>
        ) : (
          <>
            <p className="lead">
              Choose a new password. It must include upper and lower case letters, a digit and a
              symbol, and be at least 8 characters long.
            </p>
            <ErrorBanner message={error} />
            <form onSubmit={submit}>
              <Field label="New password" required>
                <Input
                  type="password"
                  value={form.new_password}
                  onChange={(event) => setForm({ ...form, new_password: event.target.value })}
                  autoComplete="new-password"
                  required
                  autoFocus
                />
              </Field>
              <Field label="Confirm new password" required>
                <Input
                  type="password"
                  value={form.confirm}
                  onChange={(event) => setForm({ ...form, confirm: event.target.value })}
                  autoComplete="new-password"
                  required
                />
              </Field>
              <Button type="submit" disabled={busy} style={{ width: "100%" }}>
                {busy ? "Updating…" : "Set new password"}
              </Button>
            </form>
          </>
        )}
      </div>
    </div>
  );
}
