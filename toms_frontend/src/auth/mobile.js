/**
 * Thin wrapper around the Android shell's biometric bridge.
 *
 * The native side owns the Keystore-wrapped refresh token. JavaScript only
 * receives a short-lived access token, and only after a successful biometric
 * prompt. On the web all of these functions are inert.
 */

function bridge() {
  if (typeof window === "undefined") return null;
  return window.TOWEMSBiometric || null;
}

function parse(value) {
  if (value && typeof value === "object") return value;
  if (typeof value !== "string") return null;
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

function call(method, fallback = null) {
  const api = bridge();
  if (!api || typeof api[method] !== "function") return fallback;
  try {
    return api[method]();
  } catch {
    return fallback;
  }
}

export function isNativeShell() {
  return Boolean(bridge());
}

/** True inside the Capacitor Android app, even before the bridge attaches. */
export function isCapacitorApp() {
  if (typeof window === "undefined") return false;
  try {
    if (typeof window.Capacitor?.isNativePlatform === "function") {
      return Boolean(window.Capacitor.isNativePlatform());
    }
  } catch {
    // Fall through to the WebView marker below.
  }
  // Capacitor uses an Android WebView; its user agent carries "; wv". The
  // wait is short and harmless on a mobile browser, and prevents a cold app
  // start reading the old cookie before the native interface exists.
  return /; wv\)/.test(window.navigator?.userAgent || "");
}

/**
 * The native interface is injected a few frames after the WebView appears.
 * A cold start must not read ``hasToken`` before it exists, or the app would
 * skip the biometric gate and fall back to a stale browser cookie.
 */
export async function waitForNativeBridge(timeoutMs = 1500) {
  if (isNativeShell()) return true;
  if (!isCapacitorApp()) return false;
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 50));
    if (isNativeShell()) return true;
  }
  return false;
}

export function biometricAvailable() {
  return call("isAvailable", false) === true;
}

export function hasBiometricToken() {
  return call("hasToken", false) === true;
}

export function isBiometricLocked() {
  return call("isLocked", true) === true;
}

export function isAutoLockEnabled() {
  return call("isAutoLockEnabled", false) === true;
}

export function setAutoLockEnabled(enabled) {
  const api = bridge();
  if (!api || typeof api.setAutoLockEnabled !== "function") return false;
  try {
    return api.setAutoLockEnabled(Boolean(enabled)) === true;
  } catch {
    return false;
  }
}

/** Mark a password + MFA sign-in as stronger than a biometric unlock. */
export function markBiometricAuthenticated() {
  return call("markAuthenticated", false) === true;
}

export function biometricUsername() {
  return call("getUsername", "") || "";
}

export function mobileDeviceId() {
  return call("getDeviceId", "") || "";
}

export function storeBiometricToken(deviceId, username, refreshToken) {
  const api = bridge();
  if (!api || typeof api.storeRefreshToken !== "function") return false;
  try {
    return api.storeRefreshToken(deviceId, username, refreshToken) === true;
  } catch {
    return false;
  }
}

export function clearBiometricToken() {
  const api = bridge();
  if (!api || typeof api.clearToken !== "function") return;
  try {
    api.clearToken();
  } catch {
    /* clearing is best-effort */
  }
}

/** Prompt for fingerprint/face, then rotate the native-held refresh token. */
export function unlockWithBiometrics() {
  return parse(call("unlock", null));
}

/** Forget the decrypted token in memory but keep the Keystore copy. */
export function lockBiometricSession() {
  return call("lock", false) === true;
}

/** Renew while already unlocked; never prompts. */
export function refreshMobileAccess() {
  return parse(call("refreshAccessToken", null));
}

export function mobileBridgeError(result, fallback = "Biometric sign-in failed.") {
  if (!result) return fallback;
  if (result.reason === "no_token") return "Set up biometric sign-in from the web first.";
  if (result.reason === "locked") return "Unlock with your fingerprint or face to continue.";
  if (result.reason === "cancelled") return "Biometric authentication was cancelled.";
  if (result.reason === "device_revoked") {
    return "This phone has been revoked by an administrator. Sign in with your password.";
  }
  return result.message || fallback;
}
