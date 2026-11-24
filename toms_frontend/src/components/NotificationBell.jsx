import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client.js";
import { NOTIFICATION_EVENT } from "../utils/notifications.js";
import Icon from "./Icon.jsx";
import { Badge, Button } from "./ui.jsx";
import { useApi } from "../hooks/useApi.js";
import { readStored, writeStored } from "../map/config.js";

// The pulse endpoint is three counts and, at most, five short rows, so a desk
// can afford to ask often. Ten seconds is the difference between "the alert is
// in the bell" and "we heard about the emergency while it was happening".
const POLL_MS = 5_000;
const HIDDEN_POLL_MS = 20_000;
//: Whether this device has already been asked. Set before the prompt is shown,
//: so a remount, a second sign-in or a refused prompt never asks again.
const DESKTOP_ALERTS_ASKED = "toms.desktop-alerts.asked";

/**
 * A short two-tone chime for an urgent alert. Browsers only allow sound after
 * the user has interacted with the page; every control on a console is a
 * click, and a browser that refuses simply stays silent.
 */
function chime() {
  try {
    const Audio = window.AudioContext || window.webkitAudioContext;
    if (!Audio) return;
    const context = new Audio();
    const gain = context.createGain();
    gain.gain.value = 0.08;
    gain.connect(context.destination);
    [880, 660].forEach((frequency, index) => {
      const tone = context.createOscillator();
      tone.type = "sine";
      tone.frequency.value = frequency;
      tone.connect(gain);
      tone.start(context.currentTime + index * 0.22);
      tone.stop(context.currentTime + index * 0.22 + 0.18);
    });
    window.setTimeout(() => context.close?.(), 1500);
  } catch {
    // No audio is not a failed alert: the card and the bell still report it.
  }
}

/**
 * Notification bell (spec §4).
 *
 * The badge counts *pending actions*, not just unread rows, so clearing the
 * unread state can never make an outstanding task disappear. Opening an item
 * marks it read and takes the recipient to the record where the work is done.
 */
export default function NotificationBell() {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  // Urgent items that arrived while this screen was open. They stay on screen
  // until somebody deals with them, because a desk may be looking elsewhere.
  const [alerts, setAlerts] = useState([]);
  const lastSeen = useRef(null);
  const summary = useApi("/notifications/summary/");
  // Show the latest notifications, not only actions: higher authorities
  // receive informational routing notices that must still be visible here.
  const notifications = useApi("/notifications/?page_size=30");
  // The panel is a work list, not a feed: an action stays until the work is
  // done, while a notice that has been read has no reason to sit here any
  // longer - it remains on the Notifications page.
  const items = [...(notifications.data?.results ?? [])]
    .filter((item) => !item.is_read || item.is_pending_action)
    .sort(
      (a, b) => Number(Boolean(b.is_pending_action)) - Number(Boolean(a.is_pending_action))
    )
    .slice(0, 12);

  // Ask once, straight after sign-in. The desktop alert is what reaches an
  // operator whose TOWEMS tab is buried behind another window, so it cannot be
  // hidden behind a button nobody presses. The browser remembers the answer;
  // this flag makes sure a refusal or a dismissal is not asked about again.
  useEffect(() => {
    if (typeof Notification === "undefined") return;
    if (Notification.permission !== "default") return;
    if (readStored(DESKTOP_ALERTS_ASKED, false) === true) return;
    writeStored(DESKTOP_ALERTS_ASKED, true);
    try {
      const answer = Notification.requestPermission();
      // Older Safari returns nothing; nothing to do then.
      Promise.resolve(answer).catch(() => {});
    } catch {
      // A browser that refuses the request simply stays without OS alerts.
    }
  }, []);

  useEffect(() => {
    let timer = null;
    let stopped = false;

    const reload = () => {
      summary.reload();
      notifications.reload();
    };

    function announce(rows) {
      setAlerts((current) => {
        const known = new Set(current.map((row) => row.id));
        const fresh = rows.filter((row) => !known.has(row.id));
        return [...fresh.reverse(), ...current].slice(0, 3);
      });
      if (rows.some((row) => row.priority === "urgent")) chime();
      if (typeof Notification === "undefined" || Notification.permission !== "granted") {
        return;
      }
      rows.forEach((row) => {
        try {
          const note = new Notification(row.title, {
            body: row.message || "",
            tag: `towems-notification-${row.id}`,
            requireInteraction: row.priority === "urgent",
          });
          note.onclick = () => {
            window.focus();
            navigate(row.action_url || "/notifications");
            note.close();
          };
        } catch {
          // Phones only allow notifications from a service worker. The in-app
          // alert above is always shown, so the message is never lost.
        }
      });
    }

    async function check() {
      // 0 is a real mark ("this inbox was empty"), so presence is tested with
      // null, not truthiness. An inbox that started empty must still refresh
      // when its first notification lands.
      const marked = lastSeen.current !== null;
      let body;
      try {
        body = await api.get(
          marked
            ? `/notifications/pulse/?since=${lastSeen.current}`
            : "/notifications/pulse/"
        );
      } catch {
        // A dropped poll is retried by the next tick; the link is lossy.
        return;
      }
      if (stopped || !body) return;
      const latest = Number(body.latest_id) || 0;
      const changed = marked && latest !== lastSeen.current;
      lastSeen.current = latest;
      if (changed || body.urgent?.length) reload();
      if (body.urgent?.length) announce(body.urgent);
    }

    function schedule(delay) {
      if (stopped) return;
      window.clearTimeout(timer);
      timer = window.setTimeout(async () => {
        await check();
        schedule(
          document.visibilityState === "visible" ? POLL_MS : HIDDEN_POLL_MS
        );
      }, delay);
    }

    function onVisibilityChange() {
      if (document.visibilityState === "visible") {
        check();
        schedule(POLL_MS);
      } else {
        // Keep a slow watch: a background tab still has to hear an emergency.
        schedule(HIDDEN_POLL_MS);
      }
    }

    // Pages that finish an action tell the bell to refresh straight away.
    window.addEventListener(NOTIFICATION_EVENT, reload);
    document.addEventListener("visibilitychange", onVisibilityChange);
    check();
    schedule(document.visibilityState === "visible" ? POLL_MS : HIDDEN_POLL_MS);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      window.removeEventListener(NOTIFICATION_EVENT, reload);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
    // The reload callbacks are stable for a fixed path.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const pending = summary.data?.pending_actions ?? 0;
  const unread = summary.data?.unread ?? 0;
  // The badge counts what is waiting for this account. Pending work wins when
  // it exists, but an unread emergency is a number too - a bare blue dot hides
  // how much is sitting in the inbox.
  const badge = Math.max(pending, unread);

  function dismiss(item) {
    setAlerts((current) => current.filter((row) => row.id !== item.id));
  }

  async function openItem(item) {
    setOpen(false);
    if (!item.is_read) {
      try {
        await api.post(`/notifications/${item.id}/mark-read/`);
        summary.reload();
        notifications.reload();
      } catch {
        /* navigating is still useful even if the read flag failed */
      }
    }
    navigate(item.action_url || "/notifications");
  }

  return (
    <div className="bell-wrap">
      {/* A portal, not a child of the top bar: the bar's backdrop-filter makes
          it the containing block for fixed children, which put the alert in
          the header instead of at the foot of the screen. */}
      {alerts.length > 0 &&
        createPortal(
          <div className="bell-alerts" role="alert" aria-live="assertive">
            {alerts.map((item) => (
              <div
                key={item.id}
                className={`bell-alert${
                  item.priority === "urgent" ? " is-urgent" : ""
                }`}
              >
                <div className="bell-alert-copy">
                  <strong>{item.title}</strong>
                  {item.message ? <p>{item.message}</p> : null}
                </div>
                <div className="bell-alert-actions">
                  <Button
                    size="sm"
                    onClick={() => {
                      dismiss(item);
                      openItem(item);
                    }}
                  >
                    Open
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => dismiss(item)}>
                    Dismiss
                  </Button>
                </div>
              </div>
            ))}
          </div>,
          document.body
        )}

      <button
        className="icon-btn bell-btn"
        onClick={() => setOpen((value) => !value)}
        aria-label={`Notifications: ${unread} unread, ${pending} pending action${
          pending === 1 ? "" : "s"
        }`}
        aria-expanded={open}
      >
        <Icon name="bell" size={19} />
        {badge > 0 && (
          <span className="bell-count">{badge > 99 ? "99+" : badge}</span>
        )}
      </button>

      {open && (
        <>
          <div className="bell-backdrop" onClick={() => setOpen(false)} />
          <div className="bell-panel" role="menu">
            <header className="bell-head">
              <strong>Notifications</strong>
              <Badge tone={pending ? "warning" : "muted"}>
                {pending} pending action{pending === 1 ? "" : "s"}
              </Badge>
            </header>

            {notifications.loading && !notifications.data ? (
              <p className="hint" style={{ padding: 12 }}>
                Loading…
              </p>
            ) : items.length ? (
              <ul className="bell-list">
                {items.map((item) => (
                  <li key={item.id}>
                    <button
                      className={`bell-item${item.is_pending_action ? " is-action" : ""}${
                        item.is_read ? "" : " is-unread"
                      }`}
                      onClick={() => openItem(item)}
                    >
                      <span className="bell-item-head">
                        <span className={item.is_read ? "" : "unread"}>{item.title}</span>
                        <Badge
                          tone={
                            item.is_pending_action
                              ? "warning"
                              : item.priority === "urgent" || item.priority === "high"
                                ? "danger"
                                : "info"
                          }
                        >
                          {item.is_pending_action ? "Action needed" : item.priority}
                        </Badge>
                      </span>
                      {item.message && <span className="bell-message">{item.message}</span>}
                      <span className="bell-meta">
                        {item.sender ? `From @${item.sender} · ` : ""}
                        {item.action_label ||
                          (item.requires_action ? "Action required" : "Notification")}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="hint" style={{ padding: 12 }}>
                Nothing waiting. Read notices stay on the Notifications page.
              </p>
            )}

            <footer className="bell-foot">
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  setOpen(false);
                  navigate("/notifications");
                }}
              >
                View all notifications
              </Button>
            </footer>
          </div>
        </>
      )}
    </div>
  );
}
