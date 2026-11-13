import { useEffect, useState } from "react";
import Icon from "./Icon.jsx";
import { Badge } from "./ui.jsx";
import { useApi } from "../hooks/useApi.js";

// Refreshed on a timer rather than streamed: presence is "seen in the last few
// minutes", so half a minute of staleness is invisible and costs one small GET.
const POLL_MS = 30000;

function minutesAgo(value) {
  if (!value) return "";
  const seen = new Date(value).getTime();
  if (!Number.isFinite(seen)) return "";
  const minutes = Math.max(0, Math.round((Date.now() - seen) / 60000));
  return minutes < 1 ? "just now" : `${minutes}m ago`;
}

function initials(row) {
  const letters = `${row.full_name || row.serial_number || "?"}`
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0])
    .join("");
  return letters.toUpperCase() || "?";
}

/**
 * Who has TOWEMS open right now, as a top-bar control beside Settings.
 *
 * The button carries the count; the panel lists the people by serial number —
 * the stable operational identifier — with their picture beside it. Rank, name
 * and designation are in the tooltip. The list is unit-scoped by the server, so
 * it shows exactly the people whose records the caller may already open.
 */
export default function OnlineNow() {
  const [open, setOpen] = useState(false);
  const { data, loading, error, reload } = useApi("/presence/");
  // A picture that will not load (or is outside the caller's scope) falls back
  // to initials, so the panel never shows a broken-image icon.
  const [broken, setBroken] = useState({});

  useEffect(() => {
    const timer = setInterval(() => {
      // A background tab has nothing to show; skip the poll and catch up when
      // it is looked at again.
      if (!document.hidden) reload();
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [reload]);

  const users = data?.users ?? [];
  const count = data?.count ?? 0;
  // Never name this `window`: inside the component that shadows the global, and
  // the interval above stops being a function.
  const windowMinutes = data?.window_minutes ?? 5;

  return (
    <div className="bell-wrap">
      <button
        className="icon-btn presence-btn"
        onClick={() => setOpen((value) => !value)}
        aria-label={`Online now: ${count} ${count === 1 ? "person" : "people"}`}
        title="Online now"
        aria-expanded={open}
      >
        <Icon name="personnel" size={19} />
        {count > 0 && (
          <span className="presence-count-badge">{count > 99 ? "99+" : count}</span>
        )}
      </button>

      {open && (
        <>
          <div className="bell-backdrop" onClick={() => setOpen(false)} />
          <div className="bell-panel" role="menu">
            <header className="bell-head">
              <strong className="presence-head-title">
                <span className="presence-dot" aria-hidden="true" />
                Online now
              </strong>
              <Badge tone="info">
                {count} {count === 1 ? "person" : "people"}
              </Badge>
            </header>

            <p className="presence-note">Seen in the last {windowMinutes} minutes.</p>

            {error && <p className="presence-note">{error}</p>}
            {loading && !data ? (
              <p className="presence-note">Checking…</p>
            ) : users.length ? (
              <ul className="presence-list">
                {users.map((row) => (
                  <li
                    key={row.id}
                    className="presence-row"
                    title={[row.rank, row.full_name, row.designation]
                      .filter(Boolean)
                      .join(" · ")}
                  >
                    {row.avatar_url && !broken[row.id] ? (
                      <img
                        className="presence-avatar"
                        src={row.avatar_url}
                        alt=""
                        aria-hidden="true"
                        loading="lazy"
                        onError={() =>
                          setBroken((previous) => ({ ...previous, [row.id]: true }))
                        }
                      />
                    ) : (
                      <span className="presence-avatar presence-initials" aria-hidden="true">
                        {initials(row)}
                      </span>
                    )}
                    <span className="presence-text">
                      <span className="presence-serial">{row.serial_number}</span>
                      <span className="presence-when">{minutesAgo(row.last_seen)}</span>
                    </span>
                    {row.is_self && <Badge tone="info">you</Badge>}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="presence-note" style={{ paddingBottom: 12 }}>
                No one else is online.
              </p>
            )}
          </div>
        </>
      )}
    </div>
  );
}
