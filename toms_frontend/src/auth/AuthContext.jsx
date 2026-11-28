import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { api, ApiError, ensureCsrf, setAccessToken } from "../api/client.js";
import {
  biometricAvailable as nativeBiometricAvailable,
  biometricUsername as nativeBiometricUsername,
  clearBiometricToken,
  hasBiometricToken,
  isAutoLockEnabled as nativeAutoLockEnabled,
  isBiometricLocked as nativeIsBiometricLocked,
  isCapacitorApp,
  isNativeShell,
  lockBiometricSession,
  markBiometricAuthenticated,
  mobileBridgeError,
  mobileDeviceId,
  setAutoLockEnabled as nativeSetAutoLockEnabled,
  storeBiometricToken,
  unlockWithBiometrics,
  waitForNativeBridge,
} from "./mobile.js";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [biometricEnabled, setBiometricEnabled] = useState(() => nativeBiometricAvailable());
  const [biometricEnrolled, setBiometricEnrolled] = useState(() => hasBiometricToken());
  const [autoLockEnabled, setAutoLockEnabledState] = useState(() =>
    nativeAutoLockEnabled()
  );
  const unlockInFlight = useRef(false);
  const autoUnlockAttempted = useRef(false);

  // Capacitor attaches the JavaScript interface shortly after the WebView is
  // created, which can be a frame or two after React's first render. Recheck
  // briefly so a fast page load does not permanently hide biometric sign-in.
  useEffect(() => {
    let attempts = 0;
    const timer = window.setInterval(() => {
      const available = nativeBiometricAvailable();
      const enrolled = hasBiometricToken();
      if (available) setBiometricEnabled(true);
      if (enrolled) setBiometricEnrolled(true);
      if (isNativeShell()) setAutoLockEnabledState(nativeAutoLockEnabled());
      attempts += 1;
      if (attempts >= 20 || (available && enrolled)) clearInterval(timer);
    }, 250);
    return () => window.clearInterval(timer);
  }, []);

  const enrollBiometric = useCallback(async (me) => {
    if (!isNativeShell()) {
      return { ok: false, message: "Biometric sign-in is only available in the Android app." };
    }
    if (!nativeBiometricAvailable()) {
      return {
        ok: false,
        message: "Add a fingerprint or face in Android Settings first.",
      };
    }
    if (hasBiometricToken()) {
      setBiometricEnrolled(true);
      return { ok: true };
    }
    if (me?.mfa_setup_required || me?.mfa_enrollment_required) {
      return {
        ok: false,
        message: "Finish the authenticator setup before enabling biometric sign-in.",
      };
    }
    const deviceId = mobileDeviceId();
    if (!deviceId) {
      return { ok: false, message: "This Android install could not be identified." };
    }
    try {
      const result = await api.post("/auth/mobile/enroll/", {
        device_id: deviceId,
        label: "TOWEMS Android",
        platform: "android",
        app_version: "1.2",
      });
      if (
        result?.refresh_token &&
        storeBiometricToken(deviceId, me.username, result.refresh_token)
      ) {
        setAccessToken(result.access_token);
        setBiometricEnrolled(true);
        setBiometricEnabled(true);
        setAutoLockEnabledState(nativeAutoLockEnabled());
        return { ok: true };
      }
      return {
        ok: false,
        message: "The device did not accept the biometric credential.",
      };
    } catch (err) {
      return {
        ok: false,
        message: err.message || "Unable to enable biometric sign-in.",
      };
    }
  }, []);

  /** Settings action: enrol this signed-in install, or report that it already is. */
  const activateBiometrics = useCallback(async () => {
    if (!isNativeShell()) {
      return { ok: false, message: "Biometric sign-in is only available in the Android app." };
    }
    if (hasBiometricToken()) {
      setBiometricEnrolled(true);
      return { ok: true };
    }
    if (!user) {
      return { ok: false, message: "Sign in with your password first." };
    }
    return enrollBiometric(user);
  }, [enrollBiometric, user]);

  /** Prompt once, refresh the native token, and restore the API session. */
  const biometricUnlock = useCallback(async () => {
    if (!isNativeShell() || !hasBiometricToken()) {
      throw new Error("Biometric sign-in is not set up on this device.");
    }
    if (unlockInFlight.current) {
      throw new Error("A biometric prompt is already open.");
    }
    unlockInFlight.current = true;
    try {
      const result = unlockWithBiometrics();
      if (!result?.ok || !result.access_token) {
        throw new Error(mobileBridgeError(result));
      }
      setAccessToken(result.access_token);
      setBiometricEnrolled(true);
      try {
        const me = await api.get("/auth/me/");
        setUser(me);
        setError("");
        return me;
      } catch (err) {
        setAccessToken(null);
        throw err;
      }
    } finally {
      unlockInFlight.current = false;
    }
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      // The JavaScript interface is injected after the WebView appears. Wait
      // for it before reading a cookie, otherwise a cold start could restore
      // the old browser session and skip the biometric gate entirely.
      if (isCapacitorApp()) {
        await waitForNativeBridge();
      }
      if (isNativeShell()) {
        setBiometricEnabled(nativeBiometricAvailable());
        setBiometricEnrolled(hasBiometricToken());
        setAutoLockEnabledState(nativeAutoLockEnabled());
      }
      const requiresPrompt =
        isNativeShell() &&
        hasBiometricToken() &&
        nativeAutoLockEnabled() &&
        nativeIsBiometricLocked();
      if (requiresPrompt) {
        autoUnlockAttempted.current = true;
        try {
          await biometricUnlock();
          return;
        } catch {
          // Cancelling the prompt must leave the person on the login screen,
          // not fall through to a still-valid browser cookie.
          setAccessToken(null);
          setUser(null);
          setError("");
          return;
        }
      }

      try {
        const me = await api.get("/auth/me/");
        setUser(me);
        setError("");
      } catch (err) {
        // A cold WebView may have no cookie but still holds a valid Keystore
        // token. Offer biometric sign-in instead of forcing a password.
        if (isNativeShell() && hasBiometricToken()) {
          try {
            await biometricUnlock();
            return;
          } catch {
            // Fall through to the normal login screen.
          }
        }
        if (!(err instanceof ApiError) || (err.status !== 401 && err.status !== 403)) {
          setError(err.message || "Unable to reach the server.");
        }
        setUser(null);
      }
    } finally {
      setLoading(false);
    }
  }, [biometricUnlock]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (!user) return;
    enrollBiometric(user);
  }, [user, enrollBiometric]);

  useEffect(() => {
    function onAuthExpired(event) {
      setAccessToken(null);
      setUser(null);
      const reason = event?.detail?.reason;
      if (reason === "device_revoked" || reason === "reauth_required") {
        clearBiometricToken();
        setBiometricEnrolled(false);
      }
    }
    window.addEventListener("towems:auth-expired", onAuthExpired);
    return () => window.removeEventListener("towems:auth-expired", onAuthExpired);
  }, []);

  const login = useCallback(async (username, password) => {
    await ensureCsrf();
    const me = await api.post("/auth/login/", { username, password });
    // A password for an enrolled account returns a challenge, not a session.
    // Only a real challenge leaves ``user`` unset; an unenrolled account gets
    // a session plus the setup flags the shell redirects on.
    if (me?.challenge) return me;
    markBiometricAuthenticated();
    setUser(me);
    setError("");
    return me;
  }, []);

  const verifyMfa = useCallback(async (challenge, code, recovery = false) => {
    const me = await api.post("/auth/mfa/challenge/", { challenge, code, recovery });
    markBiometricAuthenticated();
    setUser(me);
    setError("");
    return me;
  }, []);

  const verifyMfaSetup = useCallback(async (code) => {
    const me = await api.post("/auth/mfa/setup/verify/", { code });
    markBiometricAuthenticated();
    setUser(me);
    setError("");
    return me;
  }, []);

  // A single-device account already signed in elsewhere: the person confirmed
  // that the other session may end, and this becomes the one active device.
  const switchDevice = useCallback(async (challenge) => {
    const me = await api.post("/auth/login/switch-device/", { challenge });
    markBiometricAuthenticated();
    setUser(me);
    setError("");
    return me;
  }, []);

  const loginWithBiometrics = biometricUnlock;

  /**
   * Lock the screen without ending the account session. The server-side web
   * session, watcher token and push registration all stay live, so alerts keep
   * arriving; only the decrypted Keystore copy is dropped.
   */
  const lockSession = useCallback(() => {
    if (!isNativeShell() || !lockBiometricSession()) {
      return false;
    }
    setAccessToken(null);
    setUser(null);
    setError("");
    setBiometricEnrolled(hasBiometricToken());
    autoUnlockAttempted.current = false;
    return true;
  }, []);

  const lockForBiometricTest = useCallback(() => lockSession(), [lockSession]);

  const logout = useCallback(async () => {
    try {
      await api.post("/auth/logout/", {});
    } catch {
      /* logging out locally is still correct if the session already expired */
    }
    clearBiometricToken();
    setBiometricEnrolled(false);
    setAccessToken(null);
    setUser(null);
    autoUnlockAttempted.current = false;
  }, []);

  const setBiometricAutoLock = useCallback((enabled) => {
    const saved = nativeSetAutoLockEnabled(Boolean(enabled));
    if (saved) setAutoLockEnabledState(Boolean(enabled));
    return saved;
  }, []);

  // If the bridge attached after the first auth request, make sure a session
  // restored from a cookie cannot bypass the lock the native shell reports.
  useEffect(() => {
    if (!user || !isNativeShell() || !biometricEnrolled || !autoLockEnabled) {
      return;
    }
    if (nativeIsBiometricLocked() && !unlockInFlight.current) {
      setAccessToken(null);
      setUser(null);
      setError("");
    }
  }, [user, biometricEnrolled, autoLockEnabled]);

  // Android calls onStop when the app leaves the foreground. The native side
  // has already dropped its decrypted token; this listener removes the page's
  // in-memory access token and asks for biometrics when the app returns.
  useEffect(() => {
    if (!isNativeShell()) return undefined;
    function onVisibilityChange() {
      if (document.visibilityState === "hidden") {
        // Do not clear the page here: a WebView navigation also reports
        // "hidden", and unmounting then would abort in-app links and reloads.
        // The native activity's onStop is the reliable signal that the app
        // actually left the foreground; the visible branch below notices it.
        if (autoLockEnabled && hasBiometricToken()) {
          autoUnlockAttempted.current = false;
        }
        return;
      }
      if (document.visibilityState !== "visible") return;
      if (!autoLockEnabled || !hasBiometricToken() || !nativeIsBiometricLocked()) {
        return;
      }
      if (unlockInFlight.current || autoUnlockAttempted.current) return;
      if (user) {
        setAccessToken(null);
        setUser(null);
        setError("");
      }
      autoUnlockAttempted.current = true;
      biometricUnlock().catch(() => {
        setAccessToken(null);
        setUser(null);
        setError("");
      });
    }
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => document.removeEventListener("visibilitychange", onVisibilityChange);
  }, [autoLockEnabled, user, lockSession, biometricUnlock]);

  const idleAction = useCallback(() => {
    if (isNativeShell() && hasBiometricToken()) {
      lockSession();
    } else {
      logout();
    }
  }, [lockSession, logout]);

  // Server polling must not keep an unattended screen signed in. This is the
  // user-facing half of the same 30-minute limit. On Android the action is a
  // lock, not a sign-out: the watcher token and refresh chain stay valid, so
  // closing the app never stops notifications or forces a password.
  useEffect(() => {
    if (!user) return undefined;
    const idleLimit = 30 * 60 * 1000;
    let timer = null;
    const reset = () => {
      if (timer) window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        idleAction();
      }, idleLimit);
    };
    reset();
    const events = ["pointerdown", "keydown", "touchstart", "scroll", "wheel"];
    for (const eventName of events) {
      window.addEventListener(eventName, reset, { passive: true });
    }
    return () => {
      if (timer) window.clearTimeout(timer);
      for (const eventName of events) {
        window.removeEventListener(eventName, reset);
      }
    };
  }, [user, idleAction]);

  const value = useMemo(
    () => ({
      user,
      loading,
      error,
      login,
      verifyMfa,
      verifyMfaSetup,
      switchDevice,
      loginWithBiometrics,
      activateBiometrics,
      autoLockEnabled,
      setBiometricAutoLock,
      lockForBiometricTest,
      logout,
      refresh,
      biometricAvailable: biometricEnabled,
      biometricEnrolled,
      biometricUsername: biometricEnrolled ? nativeBiometricUsername() : "",
      permissions: user?.permissions ?? {},
    }),
    [
      user,
      loading,
      error,
      login,
      verifyMfa,
      verifyMfaSetup,
      switchDevice,
      loginWithBiometrics,
      activateBiometrics,
      autoLockEnabled,
      setBiometricAutoLock,
      lockForBiometricTest,
      logout,
      refresh,
      biometricEnabled,
      biometricEnrolled,
    ]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}
