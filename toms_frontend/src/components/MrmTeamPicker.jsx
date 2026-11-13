import { useState } from "react";
import { qs } from "../api/client.js";
import { useApi } from "../hooks/useApi.js";
import { Button, Select } from "./ui.jsx";

const ROLES = [
  { value: "lead", label: "MRM lead" },
  { value: "assessor", label: "MRM assessor" },
  { value: "reviewer", label: "Safety reviewer" },
];

/**
 * Selects the personnel who will prepare a mission's MRM assessment (spec §2A).
 *
 * Candidates come from the server filtered to the mission unit's command tree,
 * so personnel from an unrelated unit cannot be selected. The server re-checks
 * activity, unit and MRM capability when the mission is saved.
 */
export default function MrmTeamPicker({ unitId, value = [], onChange, disabled = false }) {
  const [selectedId, setSelectedId] = useState("");
  const path = unitId ? `/personnel/assignable/${qs({ mrm_unit: unitId })}` : "";
  const { data, loading, error } = useApi(path, { enabled: Boolean(unitId), deps: [unitId] });

  const people = data?.results ?? [];
  const chosen = new Set(value.map((row) => Number(row.user)));
  const candidates = people.filter((person) => !chosen.has(person.id));

  function add() {
    const person = people.find((row) => String(row.id) === String(selectedId));
    if (!person) return;
    onChange([...value, { user: person.id, role: "assessor", name: person.full_name }]);
    setSelectedId("");
  }

  function setRole(userId, role) {
    onChange(value.map((row) => (row.user === userId ? { ...row, role } : row)));
  }

  function remove(userId) {
    onChange(value.filter((row) => row.user !== userId));
  }

  if (!unitId) {
    return <p className="hint">Select the responsible unit first; MRM candidates are scoped to it.</p>;
  }

  return (
    <div>
      <div className="inline-row">
        <Select
          value={selectedId}
          onChange={(event) => setSelectedId(event.target.value)}
          disabled={disabled || loading}
          aria-label="Add MRM team member"
        >
          <option value="">
            {loading ? "Loading personnel…" : "Select personnel…"}
          </option>
          {candidates.map((person) => (
            <option key={person.id} value={person.id}>
              {person.full_name} {person.rank ? `(${person.rank})` : ""} — {person.serial_number}
              {person.unit_acronym ? ` · ${person.unit_acronym}` : ""}
            </option>
          ))}
        </Select>
        <Button type="button" variant="ghost" onClick={add} disabled={disabled || !selectedId}>
          Add to MRM team
        </Button>
      </div>

      {error && <p className="field-message">{error}</p>}
      {!loading && candidates.length === 0 && people.length === 0 && (
        <p className="hint">No active personnel are available in this unit's command tree.</p>
      )}

      {value.length === 0 ? (
        <p className="hint" style={{ marginTop: 10 }}>
          No MRM team assigned yet. The mission can be created now and the team added before the
          assessment starts.
        </p>
      ) : (
        <div className="team-list">
          {value.map((row) => {
            const person = people.find((item) => item.id === Number(row.user));
            return (
              <div className="team-row" key={row.user}>
                <span className="team-person">
                  <strong>{person?.full_name || row.name || `Personnel #${row.user}`}</strong>
                  <span className="hint">
                    {person?.serial_number || ""}
                    {person?.mrm_eligible === false ? " · not MRM qualified" : ""}
                  </span>
                </span>
                <Select
                  value={row.role}
                  onChange={(event) => setRole(row.user, event.target.value)}
                  disabled={disabled}
                  aria-label={`MRM responsibility for ${person?.full_name || row.user}`}
                >
                  {ROLES.map((role) => (
                    <option key={role.value} value={role.value}>
                      {role.label}
                    </option>
                  ))}
                </Select>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => remove(row.user)}
                  disabled={disabled}
                >
                  Remove
                </Button>
              </div>
            );
          })}
        </div>
      )}
      <p className="hint" style={{ marginTop: 8 }}>
        Assigned personnel are notified and must prepare the MRM before the mission can proceed
        through its approval stages.
      </p>
    </div>
  );
}
