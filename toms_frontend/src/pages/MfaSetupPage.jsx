import { useEffect, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { api } from "../api/client.js";
import { useAuth } from "../auth/AuthContext.jsx";
import {
  Badge,
  Button,
  Card,
  ErrorBanner,
  Field,
  Input,
  Loading,
} from "../components/ui.jsx";

/**
 * Self-service TOTP enrolment for Administrators and Commanders.
 *
 * The page is usable both during the 14-day grace window and after it has
 * closed. In the latter case the API only permits this page, /auth/me and
 * sign-out until a valid authenticator code is confirmed.
 */
export default function MfaSetupPage() {
  const { user, verifyMfaSetup, logout } = useAuth();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState(null);
  const [setup, setSetup] = useState(null);
  const [code, setCode] = useState("");
  const [recoveryCodes, setRecoveryCodes] = useState(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;

    async function prepare() {
      try {
        const current = await api.get("/auth/mfa/");
        if (!active) return;
        setStatus(current);
        if (!current.mfa_required || current.mfa_enabled) return;

        const provisioning = await api.post("/auth/mfa/setup/", {});
        if (!active) return;
        setSetup(provisioning);
      } catch (err) {
        if (active) setError(err.message || "Could not start MFA enrolment.");
      } finally {
        if (active) setLoading(false);
      }
    }

    prepare();
    return () => {
      active = false;
    };
  }, []);

  async function restartSetup() {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const provisioning = await api.post("/auth/mfa/setup/", {});
      setSetup(provisioning);
    } catch (err) {
      setError(err.message || "Could not start MFA enrolment.");
    } finally {
      setBusy(false);
    }
  }

  async function verify(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await verifyMfaSetup(code.trim());
      setRecoveryCodes(result.recovery_codes ?? []);
      setCode("");
      setStatus((current) => ({
        ...(current || {}),
        mfa_enabled: true,
        mfa_enrollment_required: false,
        mfa_setup_required: false,
      }));
    } catch (err) {
      setError(err.message || "That code could not be verified.");
    } finally {
      setBusy(false);
    }
  }

  async function copyRecoveryCodes() {
    if (!recoveryCodes?.length) return;
    const text = recoveryCodes.join("\n");
    try {
      await navigator.clipboard.writeText(text);
      setNotice("Recovery codes copied to the clipboard.");
    } catch {
      window.prompt("Copy your recovery codes and store them safely:", text);
    }
  }

  async function signOut() {
    await logout();
    navigate("/login", { replace: true });
  }

  if (!user) return <Navigate to="/login" replace />;

  return (
    <div style={{ maxWidth: 760, margin: "36px auto", padding: "0 16px" }}>
      <Card title="Two-factor authentication">
        <ErrorBanner message={error} />
        {notice && <div className="alert alert-success">{notice}</div>}

        {loading ? (
          <Loading label="Preparing your authenticator…" />
        ) : recoveryCodes ? (
          <>
            <div className="alert alert-success">
              Two-factor authentication is now enabled for @{user.username}.
            </div>
            <h3>Save your recovery codes</h3>
            <p className="hint">
              Each code works once if you lose access to your authenticator. They are shown only
              now; store them somewhere safe and offline.
            </p>
            <div
              className="grid grid-2"
              style={{ gap: 8, fontFamily: "monospace", margin: "14px 0" }}
            >
              {recoveryCodes.map((recoveryCode) => (
                <code key={recoveryCode}>{recoveryCode}</code>
              ))}
            </div>
            <div className="btn-row">
              <Button onClick={copyRecoveryCodes}>Copy all codes</Button>
              <Button variant="ghost" onClick={() => navigate("/", { replace: true })}>
                I have saved these codes
              </Button>
            </div>
          </>
        ) : status?.mfa_enabled ? (
          <>
            <div className="alert alert-success">
              Multi-factor authentication is enabled for this account.
            </div>
            <p className="hint">
              You have {status.mfa_recovery_codes_remaining ?? 0} unused recovery code
              {status.mfa_recovery_codes_remaining === 1 ? "" : "s"}.
            </p>
            <Button onClick={() => navigate("/", { replace: true })}>Return to TOWEMS</Button>
          </>
        ) : !status?.mfa_required ? (
          <>
            <p className="hint">Multi-factor authentication is not required for this account.</p>
            <Button onClick={() => navigate("/", { replace: true })}>Return to TOWEMS</Button>
          </>
        ) : setup ? (
          <>
            <div className="inline-row" style={{ gap: 8, marginBottom: 10 }}>
              <Badge tone="warning">Enrolment required</Badge>
              {typeof status.mfa_grace_days_remaining === "number" && (
                <span className="hint">
                  {status.mfa_grace_days_remaining} day
                  {status.mfa_grace_days_remaining === 1 ? "" : "s"} remaining in the grace window
                </span>
              )}
            </div>
            <p>
              Scan this QR code with Google Authenticator (or any TOTP app), then enter the
              six-digit code it shows. If you cannot scan it, enter the setup key manually.
            </p>
            <div style={{ textAlign: "center", margin: "18px 0" }}>
              <img
                src={setup.qr_data_uri}
                alt="Authenticator QR code"
                width="220"
                height="220"
                style={{ background: "#fff", padding: 10, borderRadius: 8 }}
              />
            </div>
            <p className="hint">
              The entry is created as <strong>{setup.account}</strong> in your authenticator
              app - your own Google account, not your TOWEMS username.
            </p>
            <Field label="Setup key" hint="Treat this key like a password.">
              <Input readOnly value={setup.secret} onFocus={(event) => event.target.select()} />
            </Field>
            <form onSubmit={verify}>
              <Field
                label="Authenticator code"
                htmlFor="setup-code"
                required
                hint="The code changes every 30 seconds."
              >
                <Input
                  id="setup-code"
                  autoFocus
                  autoComplete="one-time-code"
                  inputMode="numeric"
                  value={code}
                  onChange={(event) => setCode(event.target.value)}
                  placeholder="000000"
                  required
                />
              </Field>
              <div className="btn-row">
                <Button type="submit" disabled={busy}>
                  {busy ? "Verifying…" : "Verify and enable"}
                </Button>
                <Button type="button" variant="ghost" onClick={restartSetup} disabled={busy}>
                  Show a new key
                </Button>
              </div>
            </form>
            <p className="hint" style={{ marginTop: 12 }}>
              Already enrolled on another device? Sign in there or ask an administrator to reset
              this account&apos;s MFA enrolment.
            </p>
          </>
        ) : (
          <>
            <p className="hint">
              The enrolment details could not be loaded. Try starting the setup again.
            </p>
            <Button onClick={restartSetup} disabled={busy}>
              {busy ? "Preparing…" : "Start setup"}
            </Button>
          </>
        )}

        <div className="btn-row" style={{ marginTop: 20 }}>
          <Button variant="ghost" size="sm" onClick={signOut}>
            Sign out
          </Button>
        </div>
      </Card>
    </div>
  );
}
