import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext.jsx";
import Icon from "../components/Icon.jsx";
import { Badge, Card, Detail, ErrorBanner, Loading } from "../components/ui.jsx";
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
export default function SettingsPage() {
  const { user } = useAuth();
  const { theme, resolvedTheme, setTheme, error } = useTheme();
  // The activity feed lives here now: the dashboard keeps the operational
  // picture and its presence rail, and this page keeps the account's paper
  // trail. The payload is the same one the dashboard used to render.
  const { data: dashboard, loading: activityLoading } = useApi("/dashboard/");
  const activity = dashboard?.recent_activity ?? [];

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
