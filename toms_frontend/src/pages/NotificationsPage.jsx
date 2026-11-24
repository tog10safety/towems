import { useState } from "react";
import { Link } from "react-router-dom";
import { api, qs } from "../api/client.js";
import { formatDateTime } from "../utils/format.js";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorBanner,
  Loading,
  Tabs,
} from "../components/ui.jsx";
import { useApi } from "../hooks/useApi.js";
import { refreshNotifications } from "../utils/notifications.js";

const TABS = [
  { value: "action", label: "Action required" },
  { value: "unread", label: "Unread" },
  { value: "history", label: "History" },
  { value: "", label: "All" },
];

const WORKFLOW_BACKED = new Set([
  "instruction",
  "mission",
  "mrm_form",
  "inventory_request",
  "unit_change_request",
]);

export default function NotificationsPage() {
  const [scope, setScope] = useState("action");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const summary = useApi("/notifications/summary/");
  const { data, loading, error: loadError, reload } = useApi(
    `/notifications/${qs({ scope, page_size: 50 })}`,
    { deps: [scope] }
  );

  function refreshAll() {
    reload();
    summary.reload();
    refreshNotifications();
  }

  async function markRead(item) {
    // Opening the same item twice must not fire a second write.
    if (item.is_read) return;
    setError("");
    try {
      await api.post(`/notifications/${item.id}/mark-read/`);
      refreshAll();
    } catch (err) {
      setError(err.message);
    }
  }

  async function markAllRead() {
    setError("");
    try {
      const result = await api.post("/notifications/mark-all-read/", {});
      setNotice(`${result.updated} notification(s) marked read. Pending actions remain pending.`);
      refreshAll();
    } catch (err) {
      setError(err.message);
    }
  }

  async function acknowledge(item) {
    setError("");
    try {
      await api.post(`/notifications/${item.id}/acknowledge/`, {});
      setNotice("Acknowledged. The task stays open until the work is completed.");
      refreshAll();
    } catch (err) {
      setError(err.message);
    }
  }

  async function complete(item) {
    setError("");
    try {
      await api.post(`/notifications/${item.id}/complete/`, {});
      setNotice("Action recorded as addressed.");
      refreshAll();
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Notifications</h1>
          <p>
            Reading an item clears the unread badge only. A task remains here until the required
            work is completed and recorded in its own workflow.
          </p>
        </div>
        <Button variant="ghost" onClick={markAllRead}>
          Mark all read
        </Button>
      </div>

      <ErrorBanner message={loadError || error} onRetry={reload} />
      {notice && <div className="alert alert-success">{notice}</div>}

      <div className="stats stats-compact">
        <div className="stat">
          <span className="stat-label">Pending actions</span>
          <span className="stat-value">{summary.data?.pending_actions ?? "—"}</span>
        </div>
        <div className="stat">
          <span className="stat-label">In progress</span>
          <span className="stat-value">{summary.data?.in_progress ?? "—"}</span>
        </div>
        <div className="stat">
          <span className="stat-label">Addressed</span>
          <span className="stat-value">{summary.data?.completed ?? "—"}</span>
        </div>
        <div className="stat">
          <span className="stat-label">Unread</span>
          <span className="stat-value">{summary.data?.unread ?? "—"}</span>
        </div>
      </div>

      <Card>
        <Tabs tabs={TABS} active={scope} onChange={setScope} />

        {loading && !data ? (
          <Loading />
        ) : !data?.results?.length ? (
          <EmptyState
            message={
              scope === "action"
                ? "No outstanding actions. Everything assigned to you is addressed."
                : "No notifications in this view."
            }
          />
        ) : (
          <ul className="notification-list">
            {data.results.map((item) => (
              <li key={item.id} className={`notification ${item.is_read ? "" : "unread"}`}>
                {/* Opening a notice reads it. The action itself stays open. */}
                <div
                  className={`notification-main${item.is_read ? "" : " is-unread"}`}
                  role={item.is_read ? undefined : "button"}
                  tabIndex={item.is_read ? undefined : 0}
                  title={item.is_read ? undefined : "Mark this notification read"}
                  onClick={() => markRead(item)}
                  onKeyDown={(event) => {
                    if (item.is_read) return;
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      markRead(item);
                    }
                  }}
                >
                  <div className="notification-title">
                    <strong>{item.title}</strong>
                    {!item.is_read && <Badge tone="info">New</Badge>}
                    <Badge
                      tone={
                        item.priority === "urgent"
                          ? "danger"
                          : item.priority === "high"
                            ? "warning"
                            : "muted"
                      }
                    >
                      {item.priority}
                    </Badge>
                    <Badge tone={item.action_status === "completed" ? "success" : "warning"}>
                      {labelForStatus(item.action_status)}
                    </Badge>
                  </div>
                  {item.message && <p>{item.message}</p>}
                  <div className="notification-meta">
                    {item.sender && <span>From @{item.sender}</span>}
                    <span>{formatWhen(item.created_at)}</span>
                    {item.related_type && (
                      <span>
                        {item.related_type}
                        {item.related_id ? ` #${item.related_id}` : ""}
                      </span>
                    )}
                    {item.due_at && <span>Due {formatWhen(item.due_at)}</span>}
                    {item.completed_at && (
                      <span>Completed {formatWhen(item.completed_at)}</span>
                    )}
                  </div>
                  {item.completion_note && (
                    <p className="hint">Completion note: {item.completion_note}</p>
                  )}
                </div>

                <div className="notification-actions">
                  {item.action_url && (
                    <Link
                      className="btn btn-ghost btn-sm"
                      to={item.action_url}
                      onClick={() => markRead(item)}
                    >
                      {item.action_status === "completed"
                        ? "Open record"
                        : item.action_label || "Open record"}
                    </Link>
                  )}
                  {item.requires_action && item.action_status === "open" && (
                    <Button size="sm" variant="ghost" onClick={() => acknowledge(item)}>
                      Acknowledge
                    </Button>
                  )}
                  {!item.is_read && (
                    <Button size="sm" variant="ghost" onClick={() => markRead(item)}>
                      Mark read
                    </Button>
                  )}
                  {item.requires_action &&
                    item.action_status !== "completed" &&
                    !WORKFLOW_BACKED.has(item.related_type) && (
                      <Button size="sm" onClick={() => complete(item)}>
                        Mark addressed
                      </Button>
                    )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </>
  );
}

function labelForStatus(status) {
  const map = {
    none: "informational",
    open: "open",
    in_progress: "in progress",
    completed: "addressed",
  };
  return map[status] ?? status ?? "—";
}

// Philippine time, 24-hour military format (see utils/format.js).
const formatWhen = formatDateTime;
