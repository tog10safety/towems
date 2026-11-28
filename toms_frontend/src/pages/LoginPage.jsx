import { useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext.jsx";
import Icon from "../components/Icon.jsx";
import { Button, ErrorBanner, Field, Input } from "../components/ui.jsx";
import { useTheme } from "../theme/ThemeContext.jsx";

export default function LoginPage() {
  const {
    user,
    login,
    verifyMfa,
    switchDevice,
    loginWithBiometrics,
    biometricAvailable,
    biometricEnrolled,
    biometricUsername: savedBiometricUsername,
  } = useAuth();
  const { resolvedTheme, toggleTheme } = useTheme();
  const navigate = useNavigate();
  const location = useLocation();
  const [form, setForm] = useState({ username: "", password: "" });
  const [challenge, setChallenge] = useState(null);
  const [code, setCode] = useState("");
  const [recoveryMode, setRecoveryMode] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // Set when a single-device account is already signed in elsewhere.
  const [conflict, setConflict] = useState(null);
  const [biometricBusy, setBiometricBusy] = useState(false);

  if (user) return <Navigate to={location.state?.from || "/"} replace />;

  const destination = location.state?.from || "/";

  /**
   * Show the device dialog when the server refused a second sign-in, and any
   * other failure as the usual banner. Returns true when it was handled here.
   */
  function handleAuthError(err, fallback = "Sign-in failed.") {
    // The API wraps every error as {detail, errors}; the device payload is the
    // normalised error body.
    const payload = err?.payload?.errors ?? err?.payload;
    if (payload?.code === "device_in_use") {
      setConflict(payload);
      setChallenge(null);
      setError("");
      return true;
    }
    setError(err.message || fallback);
    return false;
  }

  async function onSubmit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await login(form.username.trim(), form.password);
      if (result?.challenge) {
        setChallenge(result);
        setCode("");
        setRecoveryMode(false);
        return;
      }
      if (result?.mfa_setup_required) {
        navigate("/mfa-setup", { replace: true });
        return;
      }
      navigate(destination, { replace: true });
    } catch (err) {
      handleAuthError(err);
    } finally {
      setBusy(false);
    }
  }

  async function onVerify(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await verifyMfa(challenge.challenge, code.trim(), recoveryMode);
      navigate(destination, { replace: true });
    } catch (err) {
      handleAuthError(err, "Verification failed.");
    } finally {
      setBusy(false);
    }
  }

  async function onSwitchDevice() {
    setBusy(true);
    setError("");
    try {
      const result = await switchDevice(conflict.challenge);
      setConflict(null);
      if (result?.mfa_setup_required) {
        navigate("/mfa-setup", { replace: true });
        return;
      }
      navigate(destination, { replace: true });
    } catch (err) {
      setConflict(null);
      handleAuthError(err);
    } finally {
      setBusy(false);
    }
  }

  async function onBiometric() {
    setBiometricBusy(true);
    setError("");
    try {
      const result = await loginWithBiometrics();
      if (result?.mfa_setup_required) {
        navigate("/mfa-setup", { replace: true });
        return;
      }
      navigate(destination, { replace: true });
    } catch (err) {
      setError(err.message || "Biometric sign-in failed.");
    } finally {
      setBiometricBusy(false);
    }
  }

  function backToPassword() {
    setChallenge(null);
    setCode("");
    setRecoveryMode(false);
    setError("");
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
          <h2 className="login-heading">
            {conflict
              ? "Already signed in"
              : challenge
                ? "Two-factor verification"
                : "Sign in"}
          </h2>
          <p className="login-copy">
            {challenge
              ? `Enter the code for @${challenge.username} from your authenticator app.`
              : ""}
          </p>

          <ErrorBanner message={error} />

          {conflict ? (
            <div className="login-conflict" role="alertdialog" aria-labelledby="device-conflict-title">
              <h3 id="device-conflict-title">This account is open on another device</h3>
              <p>{conflict.detail}</p>
              <p className="login-footnote">
                {conflict.device?.label ? `${conflict.device.label}` : "Another device"}
                {conflict.device?.since
                  ? ` · signed in ${new Date(conflict.device.since).toLocaleString()}`
                  : ""}
              </p>
              <p className="login-footnote">
                Continuing signs that device out immediately. Ask the person using it to save
                their work first.
              </p>
              <div className="btn-row" style={{ marginTop: 12 }}>
                <Button type="button" onClick={onSwitchDevice} disabled={busy}>
                  {busy ? "Switching…" : "Sign out that device and continue"}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => setConflict(null)}
                  disabled={busy}
                >
                  Cancel
                </Button>
              </div>
            </div>
          ) : challenge ? (
            <>
              <form onSubmit={onVerify} className="login-form">
                <Field
                  label={recoveryMode ? "Recovery code" : "Authenticator code"}
                  htmlFor="mfa-code"
                  required
                  hint={
                    recoveryMode
                      ? "Each recovery code works once."
                      : "The six-digit code changes every 30 seconds."
                  }
                >
                  <Input
                    id="mfa-code"
                    autoFocus
                    autoComplete="one-time-code"
                    inputMode={recoveryMode ? "text" : "numeric"}
                    value={code}
                    onChange={(event) => setCode(event.target.value)}
                    placeholder={recoveryMode ? "XXXX-XXXX" : "000000"}
                    required
                  />
                </Field>
                <Button type="submit" disabled={busy} style={{ width: "100%" }}>
                  {busy ? "Verifying…" : "Verify and sign in"}
                </Button>
              </form>

              <div className="btn-row" style={{ marginTop: 12 }}>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    setRecoveryMode((current) => !current);
                    setCode("");
                    setError("");
                  }}
                >
                  {recoveryMode ? "Use authenticator code" : "Use a recovery code"}
                </Button>
                <Button type="button" size="sm" variant="ghost" onClick={backToPassword}>
                  Back
                </Button>
              </div>

              {challenge.recovery_codes_remaining === 0 && (
                <p className="login-footnote">
                  No recovery codes remain. Ask an administrator to reset your MFA enrolment if
                  you lose your authenticator.
                </p>
              )}
            </>
          ) : (
            <>
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
                {biometricAvailable && biometricEnrolled && (
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={onBiometric}
                    disabled={busy || biometricBusy}
                    style={{ width: "100%" }}
                  >
                    <Icon name="shield" size={16} />
                    {biometricBusy
                      ? "Waiting for biometrics…"
                      : `Use fingerprint or face${savedBiometricUsername ? ` (@${savedBiometricUsername})` : ""}`}
                  </Button>
                )}
              </form>

              <p className="login-footnote">
                Forgotten your password? Ask an administrator to issue a reset link.
              </p>
            </>
          )}
        </section>
      </main>

      <p className="login-copyright">
        © 2026 TOWEMS – Developed by SSg Noche PAF, 2026 Metrobank Foundation
        Outstanding Filipino | All Rights Reserved.
      </p>
    </div>
  );
}
