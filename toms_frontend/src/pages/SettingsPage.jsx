import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client.js";
import { useAuth } from "../auth/AuthContext.jsx";
import Icon from "../components/Icon.jsx";
import {
  Badge,
  Card,
  Detail,
  ErrorBanner,
  Loading,
  Select,
} from "../components/ui.jsx";
import { useApi } from "../hooks/useApi.js";
import { useTheme } from "../theme/ThemeContext.jsx";
import { formatDateTime } from "../utils/format.js";

const OPTIONS = [
  {
    value: "system",
    label: "System default",
    hint: "Follows your operating system's light or dark setting and changes with it.",
    icon: "settings",
  },
  {
    value: "light",
    label: "Light",
    hint: "High-contrast light surfaces for daylight and printed-briefing environments.",
    icon: "sun",
  },
  {
    value: "dark",
    label: "Dark / night",
    hint: "Low-glare dark surfaces for watch floors and low-light operations.",
    icon: "moon",
  },
];

/**
 * User settings (spec §1).
 *
 * Appearance is the only preference the system stores today. It is written to
 * the account, so the same choice applies on every device the user signs in on.
 */
function GroupChatOversight() {
  const { data, loading, error } = useApi("/messaging/admin/group-chats/");
  const [rows, setRows] = useState([]);
  const [unitFilter, setUnitFilter] = useState("");
  const [savingId, setSavingId] = useState(null);
  const [saveError, setSaveError] = useState("");

  useEffect(() => {
    if (data?.groups) setRows(data.groups);
  }, [data]);

  async function toggle(row, field, value) {
    setSavingId(row.id);
    setSaveError("");
    try {
      const updated = await api.patch(`/messaging/admin/group-chats/${row.id}/`, {
        [field]: value,
      });
      setRows((current) =>
        current.map((item) => (item.id === row.id ? updated : item))
      );
    } catch (err) {
      setSaveError(err.message || "Unable to save the group chat setting.");
    } finally {
      setSavingId(null);
    }
  }

  const filtered = unitFilter
    ? rows.filter((row) => String(row.unit_id) === unitFilter)
    : rows;

  return (
    <Card title="Group chat oversight">
      <p className="hint" style={{ marginBottom: 14 }}>
        Approved group chats appear here after the Unit Commander and an Administrator
        approve them. Filter by unit to disable an entire group (it disappears from its
        members' Messages) or only its chat (members keep reading it).
      </p>
      {loading && !data ? (
        <Loading label="Loading approved group chats…" />
      ) : (
        <>
          <div className="settings-filter">
            <Select
              value={unitFilter}
              onChange={(event) => setUnitFilter(event.target.value)}
              aria-label="Filter group chats by unit"
            >
              <option value="">All units</option>
              {(data?.units || []).map((unit) => (
                <option key={unit.id} value={String(unit.id)}>
                  {unit.acronym || unit.name}
                </option>
              ))}
            </Select>
            <span className="hint">{filtered.length} group chat(s)</span>
          </div>

          {filtered.length ? (
            <ul className="settings-group-list">
              {filtered.map((row) => (
                <li className="settings-group-row" key={row.id}>
                  <span className="settings-group-text">
                    <strong>{row.title}</strong>
                    <span className="hint">
                      {row.unit_acronym || row.unit_name} · {row.member_count} members
                    </span>
                  </span>
                  <label className="check-row">
                    <input
                      type="checkbox"
                      checked={row.is_enabled}
                      disabled={savingId === row.id}
                      onChange={(event) => toggle(row, "is_enabled", event.target.checked)}
                    />
                    Group enabled
                  </label>
                  <label className="check-row">
                    <input
                      type="checkbox"
                      checked={row.chat_enabled}
                      disabled={savingId === row.id || !row.is_enabled}
                      onChange={(event) => toggle(row, "chat_enabled", event.target.checked)}
                    />
                    Chat enabled
                  </label>
                </li>
              ))}
            </ul>
          ) : (
            <p className="hint">No approved group chats for this filter.</p>
          )}
        </>
      )}
      {saveError && <ErrorBanner message={saveError} />}
      {error && <ErrorBanner message={error} />}
    </Card>
  );
}

export default function SettingsPage() {
  const { user } = useAuth();
  const { theme, resolvedTheme, setTheme, error } = useTheme();
  // The activity feed lives here now: the dashboard keeps the operational
  // picture and its presence rail, and this page keeps the account's paper
  // trail. The payload is the same one the dashboard used to render.
  const { data: dashboard, loading: activityLoading } = useApi("/dashboard/");
  const activity = dashboard?.recent_activity ?? [];
  const { data: messagingSettings, reload: reloadMessaging } = useApi("/messaging/settings/");
  const [privateEnabled, setPrivateEnabled] = useState(true);
  const [savingMessaging, setSavingMessaging] = useState(false);
  const [messagingError, setMessagingError] = useState("");

  useEffect(() => {
    if (messagingSettings) {
      setPrivateEnabled(messagingSettings.private_messaging_enabled !== false);
    }
  }, [messagingSettings]);

  async function togglePrivateMessaging(event) {
    const enabled = event.target.checked;
    setSavingMessaging(true);
    setMessagingError("");
    try {
      const saved = await api.patch("/messaging/settings/", {
        private_messaging_enabled: enabled,
      });
      setPrivateEnabled(saved.private_messaging_enabled !== false);
      reloadMessaging();
    } catch (err) {
      setMessagingError(err.message || "Unable to save the messaging setting.");
    } finally {
      setSavingMessaging(false);
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Settings</h1>
          <p>Appearance and account preferences. Changes apply immediately.</p>
        </div>
        <Badge tone="info">{resolvedTheme === "dark" ? "Dark active" : "Light active"}</Badge>
      </div>

      <ErrorBanner message={error} />

      <Card title="Appearance">
        <p className="hint" style={{ marginBottom: 14 }}>
          Your choice is saved to your account and restored the next time you sign in, on any
          device. It applies to the dashboard, forms, maps, notifications and the sign-in page.
        </p>
        <div className="theme-options" role="radiogroup" aria-label="Appearance">
          {OPTIONS.map((option) => {
            const active = theme === option.value;
            return (
              <button
                key={option.value}
                type="button"
                role="radio"
                aria-checked={active}
                className={`theme-option ${active ? "active" : ""}`}
                onClick={() => setTheme(option.value)}
              >
                <span className={`theme-swatch theme-swatch-${option.value}`} aria-hidden="true">
                  <span className="theme-swatch-bar" />
                  <span className="theme-swatch-line" />
                  <span className="theme-swatch-line short" />
                </span>
                <span className="theme-option-text">
                  <span className="theme-option-label">
                    <Icon name={option.icon} size={16} /> {option.label}
                    {active && <Icon name="check" size={16} className="theme-check" />}
                  </span>
                  <span className="hint">{option.hint}</span>
                </span>
              </button>
            );
          })}
        </div>
      </Card>

      {messagingSettings?.can_manage && (
        <Card title="Messaging security">
          <p className="hint" style={{ marginBottom: 14 }}>
            This is the system-wide switch for private one-to-one messages. Turning it
            off blocks starting and opening private conversations for every account; the
            unit group chat keeps working, because each unit's Operations Center controls
            who may see and post there.
          </p>
          <label className="check-row">
            <input
              type="checkbox"
              checked={privateEnabled}
              disabled={savingMessaging}
              onChange={togglePrivateMessaging}
            />
            <span>
              <strong>Allow private one-to-one messages</strong>
              <span className="hint" style={{ display: "block" }}>
                {privateEnabled
                  ? "Private conversations are available to signed-in accounts."
                  : "Private conversations are disabled. Unit chat remains available."}
              </span>
            </span>
          </label>
          {savingMessaging && <p className="hint">Saving…</p>}
          <ErrorBanner message={messagingError} />
        </Card>
      )}

      {messagingSettings?.can_manage && <GroupChatOversight />}

      <Card title="Account">
        <dl className="details">
          <Detail label="Name" value={user?.full_name} />
          <Detail label="Username" value={`@${user?.username ?? ""}`} />
          <Detail label="Unit" value={user?.unit_acronym || user?.unit_name || "Unassigned"} />
          <Detail label="Legacy role" value={user?.authority} />
          <Detail label="Serial number" value={user?.serial_number} />
          <Detail label="Password status">
            {user?.password_change_required ? (
              <Badge tone="warning">Change required</Badge>
            ) : (
              <Badge tone="success">Current</Badge>
            )}
          </Detail>
        </dl>
        <div className="btn-row" style={{ marginTop: 14 }}>
          <Link className="btn btn-ghost btn-md" to="/profile">
            Open my profile
          </Link>
          <Link className="btn btn-ghost btn-md" to="/notifications">
            Notification inbox
          </Link>
          {user?.permissions?.manage_users && (
            <Link className="btn btn-ghost btn-md" to="/admin/users">
              User management
            </Link>
          )}
        </div>
      </Card>

      <Card
        title="Recent activity"
        actions={<Link to="/audit">Full audit trail →</Link>}
      >
        <p className="hint" style={{ marginBottom: 12 }}>
          The latest actions recorded in your units. The list moved here from the dashboard so
          the operational picture stays focused.
        </p>
        {activityLoading && !dashboard ? (
          <Loading label="Loading recent activity…" />
        ) : activity.length ? (
          <ul className="timeline">
            {activity.map((entry) => (
              <li key={entry.id}>
                <time>{formatDateTime(entry.created_at)}</time>
                <span>
                  <strong title={entry.actor_name || undefined}>
                    @{entry.actor || "system"}
                  </strong>{" "}
                  {entry.action} {entry.entity_type}
                  {entry.entity_id ? ` #${entry.entity_id}` : ""}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="hint">No recorded activity yet.</p>
        )}
      </Card>
    </>
  );
}
