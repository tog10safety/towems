import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client.js";
import Icon from "../components/Icon.jsx";
import {
  Button,
  EmptyState,
  ErrorBanner,
  Input,
  Loading,
} from "../components/ui.jsx";

/*
 * New group chat dialog.
 *
 * The Operations Center names a group and selects the unit personnel who should
 * join it. From then on the request lives in Messages as a routing thread: the
 * Unit Commander approves or rejects there, then the Administrator does. This
 * dialog only creates the proposal, so the thread is the single record and the
 * panel no longer repeats its list or timeline.
 *
 * It is mounted for the Operations Center alone; both authorities act in the
 * routing thread itself.
 */

function personLabel(person) {
  return [person?.rank, person?.full_name].filter(Boolean).join(" ");
}

export default function GroupChatApprovals({ onClose, onCreated }) {
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [name, setName] = useState("");
  const [roster, setRoster] = useState([]);
  const [selectedPeople, setSelectedPeople] = useState([]);
  const [rosterLoading, setRosterLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const loadRoster = useCallback(async () => {
    setRosterLoading(true);
    try {
      const data = await api.get("/messaging/unit-group/members/");
      setRoster(data.members || []);
      setError("");
    } catch (err) {
      setError(err.message || "Unable to load the unit roster.");
    } finally {
      setRosterLoading(false);
    }
  }, []);

  useEffect(() => {
    loadRoster();
  }, [loadRoster]);

  useEffect(() => {
    function onKey(event) {
      if (event.key === "Escape") onClose?.();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  function togglePerson(id) {
    setSelectedPeople((current) =>
      current.includes(id) ? current.filter((row) => row !== id) : [...current, id]
    );
  }

  async function submitRequest(event) {
    event.preventDefault();
    if (!name.trim() || !selectedPeople.length || saving) return;
    setSaving(true);
    setNotice("");
    try {
      const created = await api.post("/messaging/group-chat-requests/", {
        name: name.trim(),
        member_ids: selectedPeople,
      });
      onCreated?.(created);
    } catch (err) {
      setNotice(err.message || "Unable to submit the group chat request.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      className="gc-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="New group chat"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose?.();
      }}
    >
      <div className="gc-panel">
        <header className="gc-panel-head">
          <div>
            <h2>New group chat</h2>
            <p className="hint">
              Select only the unit personnel who should join. The Unit Commander
              reviews it first, then an Administrator gives final approval; the
              proposal and every decision are recorded in a Messages thread.
            </p>
          </div>
          <div className="gc-panel-head-actions">
            <Button variant="ghost" size="sm" type="button" onClick={onClose}>
              Close
            </Button>
          </div>
        </header>

        {notice && (
          <p className="messaging-note" role="status">
            {notice}
          </p>
        )}
        {error && <ErrorBanner message={error} onRetry={() => loadRoster()} />}

        <form onSubmit={submitRequest}>
          <Input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Group chat name"
            aria-label="Group chat name"
            maxLength={120}
            autoFocus
          />
          {rosterLoading ? (
            <Loading label="Loading unit personnel…" />
          ) : roster.length ? (
            <ul className="gc-person-list">
              {roster.map((person) => (
                <li key={person.id}>
                  <label className="check-row">
                    <input
                      type="checkbox"
                      checked={selectedPeople.includes(person.id)}
                      onChange={() => togglePerson(person.id)}
                    />
                    <span>
                      <strong>{personLabel(person)}</strong>
                      <span className="hint" style={{ display: "block" }}>
                        {[person.serial_number, person.is_admin ? "Operations Center/Admin" : ""]
                          .filter(Boolean)
                          .join(" · ")}
                      </span>
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState message="No active personnel in this unit." />
          )}
          <footer className="gc-panel-actions">
            <span className="hint">{selectedPeople.length} selected</span>
            <Button type="submit" disabled={saving || !name.trim() || !selectedPeople.length}>
              <Icon name="plus" size={14} /> Route to Unit Commander
            </Button>
          </footer>
        </form>
      </div>
    </div>
  );
}
