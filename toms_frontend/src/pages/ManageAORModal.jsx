import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api/client.js";
import { Badge, Button, ErrorBanner, Field, Input, Modal, Select } from "../components/ui.jsx";

const LEVEL_OPTIONS = [
  { value: "all", label: "All areas" },
  { value: "province", label: "Provinces" },
  { value: "city", label: "Cities" },
  { value: "municipality", label: "Municipalities" },
  { value: "district", label: "Districts / special areas" },
];

const CONFLICT_HINTS = {
  identical: "already assigned",
  contains: "covers this area",
  contained: "lies inside this area",
  partial: "partially overlaps",
};

/**
 * Administrator AOR management (spec §1-§5).
 *
 * Search official PSGC provinces/cities, review the selection, preview the real
 * boundaries on the map, then save. Conflicts are reported by the backend with
 * the actual geometries; a same-area transfer needs an explicit confirmation and
 * a partial overlap can never be forced from here.
 */
export default function ManageAORModal({ units, initialUnitId = "", onClose, onSaved, onPreview }) {
  const [unitId, setUnitId] = useState(initialUnitId ? String(initialUnitId) : "");
  const [query, setQuery] = useState("");
  const [level, setLevel] = useState("all");
  const [results, setResults] = useState([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState("");

  const [selected, setSelected] = useState([]);
  const [coverage, setCoverage] = useState([]);
  const [removeList, setRemoveList] = useState([]);
  const [loadingCoverage, setLoadingCoverage] = useState(false);

  const selectedUnit = useMemo(
    () => (units ?? []).find((unit) => String(unit.id) === String(unitId)),
    [units, unitId]
  );

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [conflicts, setConflicts] = useState([]);
  const [canConfirmTransfer, setCanConfirmTransfer] = useState(false);

  const coveredPsgcs = useMemo(
    () => new Set(coverage.filter((area) => !removeList.includes(area.psgc)).map((area) => area.psgc)),
    [coverage, removeList]
  );

  const loadCoverage = useCallback(async (id) => {
    if (!id) {
      setCoverage([]);
      return;
    }
    setLoadingCoverage(true);
    try {
      const data = await api.get(`/aor/coverage/?unit=${id}`);
      const entry = (data.results ?? []).find((row) => String(row.unit_id) === String(id));
      setCoverage(entry?.areas ?? []);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoadingCoverage(false);
    }
  }, []);

  useEffect(() => {
    setSelected([]);
    setRemoveList([]);
    setConflicts([]);
    loadCoverage(unitId);
  }, [unitId, loadCoverage]);

  useEffect(() => {
    onPreview?.(
      selected.map((area) => ({
        type: "Feature",
        properties: { psgc: area.psgc, name: area.name, level: area.level },
        geometry: area.geometry,
      }))
    );
  }, [selected, onPreview]);

  useEffect(() => {
    const term = query.trim();
    if (term.length < 2) {
      setResults([]);
      return undefined;
    }
    let active = true;
    setSearching(true);
    setSearchError("");
    const timer = setTimeout(async () => {
      try {
        const data = await api.get(
          `/admin-areas/?q=${encodeURIComponent(term)}&level=${level}&limit=40`
        );
        if (active) setResults(data.results ?? []);
        if (active && !(data.results ?? []).length) {
          setSearchError("No province or city matches that name.");
        }
      } catch (err) {
        if (active) setSearchError(err.message);
      } finally {
        if (active) setSearching(false);
      }
    }, 250);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [query, level]);

  async function toggleArea(area) {
    setError("");
    setConflicts([]);
    const already = selected.find((item) => item.psgc === area.psgc);
    if (already) {
      setSelected(selected.filter((item) => item.psgc !== area.psgc));
      return;
    }
    try {
      const detail = await api.get(`/admin-areas/${area.psgc}/`);
      setSelected([...selected, detail]);
    } catch (err) {
      setError(err.message);
    }
  }

  function toggleRemoval(area) {
    setConflicts([]);
    setRemoveList((current) =>
      current.includes(area.psgc)
        ? current.filter((psgc) => psgc !== area.psgc)
        : [...current, area.psgc]
    );
  }

  async function save(confirmTransfer = false) {
    if (!unitId) {
      setError("Choose the unit that will own these areas.");
      return;
    }
    const additions = selected.filter((area) => !coveredPsgcs.has(area.psgc)).map((area) => area.psgc);
    if (!additions.length && !removeList.length) {
      setError("Nothing to change: select an area to add or remove.");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    setConflicts([]);
    try {
      const result = await api.post("/aor/assign/", {
        unit: Number(unitId),
        psgcs: additions,
        remove_psgcs: removeList,
        confirm_transfer: confirmTransfer,
      });
      const parts = [];
      if (result.assigned?.length) parts.push(`${result.assigned.length} assigned`);
      if (result.transferred?.length) parts.push(`${result.transferred.length} transferred`);
      if (result.removed?.length) parts.push(`${result.removed.length} removed`);
      if (result.unchanged?.length) parts.push(`${result.unchanged.length} already covered`);
      setNotice(parts.length ? `Coverage updated: ${parts.join(", ")}.` : "Coverage unchanged.");
      setSelected([]);
      setRemoveList([]);
      await loadCoverage(unitId);
      onSaved?.();
    } catch (err) {
      setError(err.message);
      const payload = err.payload || {};
      const rows = payload.conflicts || [];
      setConflicts(rows);
      const psgcs = new Set(selected.map((area) => area.psgc));
      setCanConfirmTransfer(
        rows.length > 0 && rows.every((row) => psgcs.has(row.admin_psgc))
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title="Manage AOR — administrative areas"
      onClose={onClose}
      wide
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
          <Button onClick={() => save(false)} disabled={busy || !unitId}>
            {busy ? "Saving…" : "Save coverage"}
          </Button>
        </>
      }
    >
      <ErrorBanner message={error} />
      {notice && <div className="alert alert-success">{notice}</div>}

      <div className="form-grid">
        <Field
          label="Unit"
          required
          hint="Areas are assigned to this unit's AOR and drawn in the unit's colour."
        >
          <Select value={unitId} onChange={(event) => setUnitId(event.target.value)} required>
            <option value="">Select unit…</option>
            {(units ?? []).map((unit) => (
              <option key={unit.id} value={unit.id}>
                {unit.acronym || unit.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Filter type">
          <Select value={level} onChange={(event) => setLevel(event.target.value)}>
            {LEVEL_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </Select>
        </Field>
      </div>

      {selectedUnit && (
        <p className="hint">
          <span
            className="swatch"
            style={{ background: selectedUnit.aor_color, marginRight: 6 }}
          />
          {selectedUnit.acronym} is drawn in {selectedUnit.aor_color} — every area you add or
          transfer here takes that colour, so neighbouring units stay distinct.
        </p>
      )}

      <Field label="Search provinces / cities" hint="Partial names are accepted; case is ignored.">
        <Input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="e.g. Batangas, Cebu City, Isabela"
          autoFocus
        />
      </Field>

      {searching && <p className="hint">Searching boundaries…</p>}
      {searchError && <p className="field-message">{searchError}</p>}

      {results.length > 0 && (
        <div className="selection-results" role="list">
          {results.map((area) => {
            const picked = selected.some((item) => item.psgc === area.psgc);
            const covered = coveredPsgcs.has(area.psgc);
            return (
              <button
                type="button"
                key={area.psgc}
                role="listitem"
                className={`selection-row ${picked ? "active" : ""}`}
                onClick={() => toggleArea(area)}
              >
                <span>
                  <strong>{area.name}</strong>
                  <span className="hint">
                    {area.level_label}
                    {area.parent_name ? ` · ${area.parent_name}` : ""}
                    {area.region_name ? ` · ${area.region_name}` : ""}
                  </span>
                </span>
                {covered ? <Badge tone="success">In this AOR</Badge> : picked ? <Badge tone="info">Selected</Badge> : null}
              </button>
            );
          })}
        </div>
      )}

      <div className="selection-columns">
        <section>
          <h3>Selected ({selected.length})</h3>
          {selected.length === 0 ? (
            <p className="hint">Search and select provinces or cities to add.</p>
          ) : (
            <ul className="selection-list">
              {selected.map((area) => (
                <li key={area.psgc}>
                  <span>
                    <strong>{area.name}</strong>
                    <span className="hint">
                      {area.level_label} · {area.region_name}
                    </span>
                  </span>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    onClick={() => toggleArea(area)}
                  >
                    Remove
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section>
          <h3>Current coverage</h3>
          {loadingCoverage ? (
            <p className="hint">Loading…</p>
          ) : coverage.length === 0 ? (
            <p className="hint">This unit has no administrative areas yet.</p>
          ) : (
            <ul className="selection-list">
              {coverage.map((area) => (
                <li key={area.psgc} className={removeList.includes(area.psgc) ? "removing" : ""}>
                  <span>
                    <strong>{area.name}</strong>
                    <span className="hint">
                      {area.level} {area.parent_name ? `· ${area.parent_name}` : ""}
                    </span>
                  </span>
                  <Button
                    type="button"
                    size="sm"
                    variant={removeList.includes(area.psgc) ? "ghost" : "danger"}
                    onClick={() => toggleRemoval(area)}
                  >
                    {removeList.includes(area.psgc) ? "Keep" : "Remove"}
                  </Button>
                </li>
              ))}
            </ul>
          )}
          {removeList.length > 0 && (
            <p className="hint">
              {removeList.length} area(s) will be removed when you save.
            </p>
          )}
        </section>
      </div>

      {conflicts.length > 0 && (
        <div className="alert alert-warning" style={{ display: "block" }}>
          <strong>These areas conflict with another unit's coverage.</strong>
          <table className="data-table compact">
            <thead>
              <tr>
                <th>Area</th>
                <th>Held by</th>
                <th>Relation</th>
                <th>Overlap</th>
              </tr>
            </thead>
            <tbody>
              {conflicts.map((row, index) => (
                <tr key={`${row.aor_id}-${index}`}>
                  <td>{row.candidate}</td>
                  <td>
                    {row.unit_acronym || row.unit_name} — {row.aor_name}
                  </td>
                  <td>{CONFLICT_HINTS[row.relation] || row.relation}</td>
                  <td>{row.overlap_km2 ? `${row.overlap_km2} km²` : "identical"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {canConfirmTransfer ? (
            <div className="btn-row" style={{ marginTop: 8 }}>
              <span className="hint">
                Transferring takes these areas from the other unit and is recorded in the audit
                trail.
              </span>
              <Button variant="danger" onClick={() => save(true)} disabled={busy}>
                Confirm transfer
              </Button>
            </div>
          ) : (
            <p className="hint" style={{ marginTop: 8 }}>
              Partial overlaps cannot be forced. Remove the overlapping areas from one unit's
              coverage first, or resolve the boundary with the owning unit.
            </p>
          )}
        </div>
      )}
    </Modal>
  );
}
