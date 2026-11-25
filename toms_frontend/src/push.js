/*
 * Closed-tab browser alerts for private messages.
 *
 * The in-app bell and the tab-open Notification path are unchanged; this module
 * only adds the Web Push subscription that lets the browser wake the device
 * when TOWEMS is closed. It runs only after the person has granted the normal
 * notification permission, so it never prompts on its own.
 */

import { api } from "./api/client.js";

const SERVICE_WORKER_URL = "/sw.js";

export function pushSupported() {
  return (
    typeof window !== "undefined" &&
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window
  );
}

/** The URL-safe base64 VAPID key, as the Push API expects it. */
function applicationServerKey(value) {
  const padding = "=".repeat((4 - (value.length % 4)) % 4);
  const base64 = (value + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = window.atob(base64);
  const bytes = new Uint8Array(raw.length);
  for (let index = 0; index < raw.length; index += 1) {
    bytes[index] = raw.charCodeAt(index);
  }
  return bytes;
}

export async function registerServiceWorker() {
  if (!("serviceWorker" in navigator)) return null;
  try {
    return await navigator.serviceWorker.register(SERVICE_WORKER_URL, {
      scope: "/",
    });
  } catch {
    return null;
  }
}

/**
 * Subscribe this browser and tell the server where to push.
 * Returns {ok:true} or {ok:false, reason} with reason one of
 * "unsupported" | "permission" | "disabled" | "server".
 */
export async function subscribeToPush() {
  if (!pushSupported()) return { ok: false, reason: "unsupported" };
  if (Notification.permission !== "granted") {
    return { ok: false, reason: "permission" };
  }

  let config;
  try {
    config = await api.get("/messaging/push/config/");
  } catch {
    return { ok: false, reason: "server" };
  }
  if (!config?.enabled || !config?.public_key) {
    return { ok: false, reason: "disabled" };
  }

  const registration = await registerServiceWorker();
  if (!registration) return { ok: false, reason: "unsupported" };

  let subscription = await registration.pushManager.getSubscription();
  if (!subscription) {
    subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: applicationServerKey(config.public_key),
    });
  }

  try {
    await api.post("/messaging/push/subscribe/", subscription.toJSON());
  } catch {
    return { ok: false, reason: "server" };
  }
  return { ok: true };
}

/**
 * Refresh the server's copy of an existing subscription without prompting.
 * Called by the shell on sign-in, so alerts keep working for people who granted
 * permission before this feature existed.
 */
export function syncPushSubscription() {
  if (!pushSupported() || Notification.permission !== "granted") return;
  subscribeToPush().catch(() => {
    // A missing service worker or an offline network must not affect the app.
  });
}
