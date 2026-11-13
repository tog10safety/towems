import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client.js";
import { NOTIFICATION_EVENT } from "../utils/notifications.js";
import Icon from "./Icon.jsx";
import { Badge, Button } from "./ui.jsx";
import { useApi } from "../hooks/useApi.js";

const REFRESH_MS = 60_000;

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
  const summary = useApi("/notifications/summary/");
  // Show the latest notifications, not only actions: higher authorities
  // receive informational routing notices that must still be visible here.
  const notifications = useApi("/notifications/?page_size=12");
  // Pending actions come first: the panel is a work list, not a feed.
  const items = [...(notifications.data?.results ?? [])].sort(
    (a, b) => Number(Boolean(b.is_pending_action)) - Number(Boolean(a.is_pending_action))
  );

  useEffect(() => {
    let timer = null;
    const reload = () => {
      summary.reload();
      notifications.reload();
    };

    function startPolling() {
      if (timer !== null) return;
      timer = window.setInterval(reload, REFRESH_MS);
    }

    function stopPolling() {
      if (timer === null) return;
      window.clearInterval(timer);
      timer = null;
    }

    function onVisibilityChange() {
      if (document.visibilityState === "visible") {
        reload();
        startPolling();
      } else {
        stopPolling();
      }
    }

    // Pages that finish an action tell the bell to refresh straight away.
    window.addEventListener(NOTIFICATION_EVENT, reload);
    document.addEventListener("visibilitychange", onVisibilityChange);
    if (document.visibilityState === "visible") startPolling();
    return () => {
      stopPolling();
      window.removeEventListener(NOTIFICATION_EVENT, reload);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
    // The reload callbacks are stable for a fixed path.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const pending = summary.data?.pending_actions ?? 0;
  const unread = summary.data?.unread ?? 0;

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
      <button
        className="icon-btn bell-btn"
        onClick={() => setOpen((value) => !value)}
        aria-label={`Notifications: ${pending} pending action${pending === 1 ? "" : "s"}`}
        aria-expanded={open}
      >
        <Icon name="bell" size={19} />
        {pending > 0 ? (
          <span className="bell-count">{pending > 99 ? "99+" : pending}</span>
        ) : (
          unread > 0 && <span className="bell-dot" aria-hidden="true" />
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
                      className={`bell-item${item.is_pending_action ? " is-action" : ""}`}
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
                No notifications.
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
