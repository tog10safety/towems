/**
 * Cross-component signal for "the notification state changed".
 *
 * The bell lives in the persistent Layout, so a page that acknowledges or
 * completes an action cannot re-render it directly. Dispatching this event lets
 * the badge refresh immediately instead of waiting for its polling interval.
 */

export const NOTIFICATION_EVENT = "toms:notifications-changed";

export function refreshNotifications() {
  window.dispatchEvent(new Event(NOTIFICATION_EVENT));
}
