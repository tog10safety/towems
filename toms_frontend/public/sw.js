/*
 * TOWEMS service worker: closed-tab alerts for private messages.
 *
 * The server sends a small, body-free payload (who wrote, which thread). This
 * worker turns it into a system notification and, when clicked, focuses an
 * open TOWEMS window on that conversation - or opens a new one. It deliberately
 * caches nothing: every page request still comes from the network, so this file
 * cannot serve stale application code after a deployment.
 */

self.addEventListener("install", () => {
  // A new worker takes over immediately; there is no offline cache to migrate.
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("push", (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch {
    payload = { body: event.data ? event.data.text() : "" };
  }

  const title = payload.title || "New TOWEMS message";
  const options = {
    body: payload.body || "Open TOWEMS to read it.",
    icon: "/logo.png",
    badge: "/logo.png",
    // One notification per thread: a burst of messages replaces itself instead
    // of stacking a dozen alerts on the lock screen.
    tag: payload.tag || "towems-message",
    renotify: true,
    data: { url: payload.url || "/messages" },
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = new URL(
    event.notification.data?.url || "/messages",
    self.location.origin
  ).href;

  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({
        type: "window",
        includeUncontrolled: true,
      });
      for (const client of windows) {
        if (new URL(client.url).origin !== self.location.origin) continue;
        await client.focus();
        if ("navigate" in client) {
          try {
            await client.navigate(target);
          } catch {
            // A window that refuses navigation still received focus.
          }
        }
        return;
      }
      await self.clients.openWindow(target);
    })()
  );
});
