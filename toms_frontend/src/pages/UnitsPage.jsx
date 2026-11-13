import { useEffect, useRef, useState } from "react";
import { api } from "../api/client.js";
import LocationPickerModal from "../components/LocationPickerModal.jsx";
import { useAuth } from "../auth/AuthContext.jsx";
import {
  Badge,
  Button,
  Card,
  ErrorBanner,
  Field,
  Input,
  Loading,
  Modal,
  Select,
  Table,
} from "../components/ui.jsx";
import { useApi } from "../hooks/useApi.js";

const CATEGORIES = ["Command", "Wing", "Group", "Center", "Squadron"];

/**
 * Unit Management — Administrator only.
 *
 * The menu item and the server agree on who may enter: writes are gated by
 * ``system_config`` (the Administrator capability no delegation can grant), and
 * the page itself refuses to render its controls for anyone else.
 *
 * Units are never deleted: missions, MRM forms, personnel and audit history
 * reference them by id, so a unit is deactivated instead, with a reason.
 */
export default function UnitsPage() {
  const { permissions } = useAuth();
  const canManage = Boolean(permissions?.capabilities?.system_config);
  if (!canManage) return <AdminOnly />;
  return <UnitRegister />;
}

function AdminOnly() {
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Unit Management</h1>
          <p>Adding, editing and deactivating units is an Administrator function.</p>
        </div>
      </div>
      <Card title="Restricted">
        <p className="hint">
          Your account does not hold the Administrator capability. The unit register is still used
          by the unit selectors elsewhere in TOWEMS; ask an Administrator if a unit needs to be
          created, edited or deactivated.
        </p>
      </Card>
    </>
  );
}

function UnitRegister() {
  const [scope, setScope] = useState("active");
  const [formTarget, setFormTarget] = useState(null); // {mode: "create" | "edit", unit}
  const [markerTarget, setMarkerTarget] = useState(null);
  const [deactivateTarget, setDeactivateTarget] = useState(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [actionError, setActionError] = useState("");

  const path = `/units/?page_size=200${
    scope === "active" ? "&is_active=true" : scope === "inactive" ? "&is_active=false" : ""
  }`;
  const { data, loading, error, reload } = useApi(path);
  const tree = useApi(`/units/tree/`);
  // The parent picker must offer every unit, not only the filter currently shown.
  const everyUnit = useApi("/units/?page_size=500");

  function refresh() {
    reload();
    tree.reload();
    everyUnit.reload();
  }

  const rows = data?.results ?? [];
  const allUnits = everyUnit.data?.results ?? rows;

  async function reactivate(unit) {
    if (!window.confirm(`Reactivate ${unit.acronym}? It becomes selectable again.`)) return;
    setBusy(true);
    setActionError("");
    setNotice("");
    try {
      await api.post(`/units/${unit.id}/activate/`, {});
      setNotice(`${unit.acronym} reactivated.`);
      refresh();
    } catch (err) {
      setActionError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Unit Management</h1>
          <p>
            Unit hierarchy, leadership, status, map location and seal. A unit is deactivated rather
            than deleted, so its missions, personnel and audit history stay intact.
          </p>
        </div>
        <Button onClick={() => setFormTarget({ mode: "create", unit: null })}>New unit</Button>
      </div>

      <ErrorBanner message={error || actionError} onRetry={reload} />
      {notice && <div className="alert alert-success">{notice}</div>}

      <Card
        title="Unit register"
        actions={
          <Select
            value={scope}
            onChange={(event) => setScope(event.target.value)}
            aria-label="Show units"
          >
            <option value="active">Active units</option>
            <option value="all">All units</option>
            <option value="inactive">Deactivated units</option>
          </Select>
        }
      >
        {loading && !data ? (
          <Loading />
        ) : (
          <Table
            rows={rows}
            empty={scope === "inactive" ? "No deactivated units." : "No units configured."}
            columns={[
              {
                key: "map_logo_url",
                label: "Seal",
                width: 64,
                render: (row) =>
                  row.map_logo_url ? (
                    <img className="unit-seal-thumb" src={row.map_logo_url} alt="" />
                  ) : (
                    <span className="unit-seal-thumb unit-seal-empty" aria-hidden="true">
                      {(row.acronym || "?").slice(0, 3)}
                    </span>
                  ),
              },
              {
                key: "acronym",
                label: "Acronym",
                render: (row) => (
                  <span className="inline-row" style={{ gap: 6 }}>
                    <strong>{row.acronym}</strong>
                    {row.is_active === false && <Badge tone="danger">Deactivated</Badge>}
                  </span>
                ),
              },
              { key: "name", label: "Name" },
              {
                key: "category",
                label: "Category",
                render: (row) => <Badge tone="muted">{row.category}</Badge>,
              },
              { key: "parent_acronym", label: "Parent", render: (row) => row.parent_acronym || "—" },
              {
                key: "map_mgrs",
                label: "Grid (MGRS)",
                render: (row) =>
                  row.map_mgrs ? (
                    <span className="mono">{row.map_mgrs}</span>
                  ) : (
                    <Badge tone="warning">Not pinned</Badge>
                  ),
              },
              {
                key: "map_place_name",
                label: "Location",
                render: (row) => row.map_place_name || row.location || "—",
              },
              {
                key: "commanding_officer",
                label: "Commander",
                render: (row) => row.commanding_officer || "—",
              },
              {
                key: "actions",
                label: "",
                render: (row) => (
                  <div className="btn-row" style={{ gap: 4 }}>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setFormTarget({ mode: "edit", unit: row })}
                    >
                      Edit
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setMarkerTarget(row)}>
                      {row.map_mgrs ? "Edit pin" : "Set pin"}
                    </Button>
                    {row.is_active === false ? (
                      <Button size="sm" variant="ghost" disabled={busy} onClick={() => reactivate(row)}>
                        Reactivate
                      </Button>
                    ) : (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => setDeactivateTarget(row)}
                      >
                        Deactivate
                      </Button>
                    )}
                  </div>
                ),
              },
            ]}
          />
        )}
      </Card>

      <Card title="Hierarchy">
        {tree.loading ? (
          <Loading />
        ) : tree.error ? (
          <ErrorBanner message={tree.error} onRetry={tree.reload} />
        ) : (
          <UnitTree nodes={tree.data?.tree ?? []} depth={0} />
        )}
      </Card>

      {formTarget && (
        <UnitFormModal
          mode={formTarget.mode}
          unit={formTarget.unit}
          units={allUnits}
          onClose={() => setFormTarget(null)}
          onSaved={(unit) => {
            setFormTarget(null);
            setNotice(
              formTarget.mode === "create"
                ? `${unit.acronym} added.`
                : `${unit.acronym} updated.`
            );
            refresh();
          }}
        />
      )}

      {markerTarget && (
        <UnitMarkerModal
          unit={markerTarget}
          onClose={() => setMarkerTarget(null)}
          onSaved={() => {
            setMarkerTarget(null);
            refresh();
          }}
          onChanged={refresh}
        />
      )}

      {deactivateTarget && (
        <DeactivateModal
          unit={deactivateTarget}
          onClose={() => setDeactivateTarget(null)}
          onSaved={() => {
            setDeactivateTarget(null);
            setNotice(`${deactivateTarget.acronym} deactivated. Its records are unchanged.`);
            refresh();
          }}
        />
      )}
    </>
  );
}

function UnitTree({ nodes, depth }) {
  if (!nodes.length) return <p className="hint">No subordinate units.</p>;
  return (
    <ul style={{ listStyle: "none", paddingLeft: depth ? 16 : 0, margin: 0 }}>
      {nodes.map((node) => (
        <li key={node.id} style={{ marginBottom: 6 }}>
          <strong>{node.acronym}</strong>{" "}
          <span className="hint">
            {node.name} · {node.category}
          </span>
          {node.children?.length > 0 && <UnitTree nodes={node.children} depth={depth + 1} />}
        </li>
      ))}
    </ul>
  );
}

/** Add or edit a unit's descriptive fields (name, acronym, category, parent). */
function UnitFormModal({ mode, unit, units, onClose, onSaved }) {
  const [form, setForm] = useState({
    name: unit?.name || "",
    acronym: unit?.acronym || "",
    category: unit?.category || "Squadron",
    location: unit?.location || "",
    parent: unit?.parent ? String(unit.parent) : "",
    commanding_officer: unit?.commanding_officer || "",
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [fieldErrors, setFieldErrors] = useState({});

  function set(field, value) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  async function submit() {
    setSaving(true);
    setError("");
    setFieldErrors({});
    const body = {
      name: form.name.trim(),
      acronym: form.acronym.trim(),
      category: form.category,
      location: form.location.trim(),
      parent: form.parent ? Number(form.parent) : null,
      commanding_officer: form.commanding_officer.trim() || null,
    };
    try {
      const saved =
        mode === "create"
          ? await api.post("/units/", body)
          : await api.patch(`/units/${unit.id}/`, body);
      onSaved(saved);
    } catch (err) {
      setFieldErrors(err.fieldErrors || {});
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  // Never offer the unit itself, or a unit already below it, as its own parent.
  const descendants = new Set(
    mode === "edit" ? subtreeIds(units, unit?.id) : new Set()
  );
  const parentChoices = units.filter(
    (row) => row.id !== unit?.id && !descendants.has(row.id) && row.is_active !== false
  );

  return (
    <Modal
      title={mode === "create" ? "New unit" : `Edit ${unit.acronym}`}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={saving}>
            {saving ? "Saving…" : mode === "create" ? "Create unit" : "Save changes"}
          </Button>
        </>
      }
    >
      {error && <div className="alert alert-danger">{error}</div>}

      <div className="form-grid">
        <Field label="Unit name" required error={fieldErrors.name}>
          <Input value={form.name} onChange={(event) => set("name", event.target.value)} />
        </Field>
        <Field label="Acronym" required error={fieldErrors.acronym} hint="Shown on maps, reports and selectors.">
          <Input
            value={form.acronym}
            onChange={(event) => set("acronym", event.target.value.toUpperCase())}
          />
        </Field>
        <Field label="Category" required error={fieldErrors.category}>
          <Select value={form.category} onChange={(event) => set("category", event.target.value)}>
            {CATEGORIES.map((category) => (
              <option key={category} value={category}>
                {category}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Location" required error={fieldErrors.location} hint="Camp, base or municipality.">
          <Input value={form.location} onChange={(event) => set("location", event.target.value)} />
        </Field>
        <Field label="Parent unit" error={fieldErrors.parent} hint="Leave empty for a top-level unit.">
          <Select value={form.parent} onChange={(event) => set("parent", event.target.value)}>
            <option value="">No parent (top level)</option>
            {parentChoices.map((row) => (
              <option key={row.id} value={row.id}>
                {row.acronym} — {row.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Commanding officer" error={fieldErrors.commanding_officer}>
          <Input
            value={form.commanding_officer}
            onChange={(event) => set("commanding_officer", event.target.value)}
          />
        </Field>
      </div>
    </Modal>
  );
}

/** Ids of a unit and everything below it, for excluding parents that would loop. */
function subtreeIds(units, rootId) {
  const byParent = new Map();
  for (const row of units) {
    const key = row.parent ?? null;
    if (!byParent.has(key)) byParent.set(key, []);
    byParent.get(key).push(row.id);
  }
  const out = new Set();
  const stack = [rootId];
  while (stack.length) {
    const id = stack.pop();
    if (out.has(id)) continue;
    out.add(id);
    for (const childId of byParent.get(id) ?? []) stack.push(childId);
  }
  return out;
}

/** Deactivation asks for a reason and surfaces the server's refusal, if any. */
function DeactivateModal({ unit, onClose, onSaved }) {
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function submit() {
    if (!reason.trim()) {
      setError("Give the reason for deactivating this unit.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await api.post(`/units/${unit.id}/deactivate/`, { reason: reason.trim() });
      onSaved();
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      title={`Deactivate ${unit.acronym}?`}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={saving}>
            {saving ? "Deactivating…" : "Deactivate unit"}
          </Button>
        </>
      }
    >
      <p className="hint">
        {unit.name} stays in the database with its missions, MRM forms, personnel and audit history.
        It stops appearing in unit selectors and cannot receive new work until it is reactivated.
      </p>
      {error && <div className="alert alert-danger">{error}</div>}
      <Field label="Reason" required hint="Recorded in the audit trail with your account and the time.">
        <Input value={reason} onChange={(event) => setReason(event.target.value)} autoFocus />
      </Field>
    </Modal>
  );
}

/**
 * Location + seal for one unit.
 *
 * The grid reference is authoritative: it can be typed, derived from a point
 * picked on the map, or both - the backend keeps the pair consistent and
 * re-derives the MGRS from the clicked point, so a unit can never be pinned at
 * coordinates that contradict its printed grid.
 */
function UnitMarkerModal({ unit, onClose, onSaved, onChanged }) {
  const [mgrs, setMgrs] = useState(unit.map_mgrs || "");
  const [place, setPlace] = useState(unit.map_place_name || "");
  const [picked, setPicked] = useState(
    unit.map_latitude != null && unit.map_longitude != null
      ? { latitude: unit.map_latitude, longitude: unit.map_longitude, mgrs: unit.map_mgrs || "" }
      : null
  );
  const [picking, setPicking] = useState(false);
  const [logoUrl, setLogoUrl] = useState(unit.map_logo_url || "");
  const [logoVersion, setLogoVersion] = useState(0);
  const [saving, setSaving] = useState(false);
  const [busyLogo, setBusyLogo] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const logoInput = useRef(null);

  useEffect(() => {
    setError("");
  }, [mgrs, place]);

  async function save() {
    setError("");
    setNotice("");
    const body = { mgrs: mgrs.trim().toUpperCase(), place_name: place.trim() };
    // An empty grid would clear the pin; ask for a point instead of silently
    // unpinning the unit from every map.
    if (!body.mgrs) {
      setError("Enter the grid coordinate or pick the unit's location on the map.");
      return;
    }
    setSaving(true);
    // Send the point only when the typed grid still matches what was picked;
    // otherwise the grid wins and the backend converts it.
    if (picked && picked.mgrs && picked.mgrs === body.mgrs) {
      body.latitude = picked.latitude;
      body.longitude = picked.longitude;
    }
    try {
      const saved = await api.patch(`/units/${unit.id}/marker/`, body);
      setMgrs(saved.mgrs || "");
      setPlace(saved.place_name || "");
      setNotice("Unit location saved. The pin is now shown on the maps.");
      onChanged?.();
    } catch (err) {
      setError(err.fieldErrors?.mgrs || err.fieldErrors?.latitude || err.message);
    } finally {
      setSaving(false);
    }
  }

  async function uploadLogo(file) {
    if (!file) return;
    setBusyLogo(true);
    setError("");
    setNotice("");
    const form = new FormData();
    form.append("logo", file);
    try {
      const saved = await api.upload(`/units/${unit.id}/marker-logo/`, form);
      setLogoUrl(saved.logo_url);
      setLogoVersion(Date.now());
      setNotice("Seal uploaded. It is now the unit's map pin.");
      onChanged?.();
    } catch (err) {
      setError(err.fieldErrors?.logo || err.message);
    } finally {
      setBusyLogo(false);
      if (logoInput.current) logoInput.current.value = "";
    }
  }

  async function removeLogo() {
    setBusyLogo(true);
    setError("");
    try {
      await api.del(`/units/${unit.id}/marker-logo/`);
      setLogoUrl("");
      setLogoVersion(Date.now());
      setNotice("Seal removed. The pin falls back to the unit acronym.");
      onChanged?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusyLogo(false);
    }
  }

  return (
    <>
      <Modal
        title={`${unit.acronym} — map location and seal`}
        onClose={onClose}
        wide
        footer={
          <>
            <Button variant="ghost" onClick={onClose}>
              Close
            </Button>
            <Button onClick={save} disabled={saving}>
              {saving ? "Saving…" : "Save location"}
            </Button>
          </>
        }
      >
        {error && <div className="alert alert-danger">{error}</div>}
        {notice && <div className="alert alert-success">{notice}</div>}

        <p className="hint">
          {unit.name}
          {unit.parent_acronym ? ` · under ${unit.parent_acronym}` : ""}
        </p>

        <div className="form-grid">
          <Field
            label="Grid coordinate (MGRS)"
            hint="Type the grid reference, or pick the exact spot on the map. The map pin uses this point."
          >
            <div className="inline-row">
              <Input
                value={mgrs}
                onChange={(event) => setMgrs(event.target.value.toUpperCase())}
                placeholder="e.g. 51PTS8284814992"
              />
              <Button variant="ghost" size="sm" onClick={() => setPicking(true)}>
                Pick on map
              </Button>
            </div>
          </Field>

          <Field label="Location description" hint="Landmark, camp or municipality the unit occupies.">
            <Input
              value={place}
              onChange={(event) => setPlace(event.target.value)}
              placeholder="e.g. Camp Evangelista, Butuan City"
            />
          </Field>
        </div>

        {picked && (
          <p className="hint">
            Point: {Number(picked.latitude).toFixed(6)}, {Number(picked.longitude).toFixed(6)}
            {picked.mgrs ? ` · ${picked.mgrs}` : ""}
          </p>
        )}

        <div className="unit-seal-editor">
          <div className="unit-seal-preview">
            {logoUrl ? (
              <img src={`${logoUrl}${logoUrl.includes("?") ? "&" : "?"}v=${logoVersion}`} alt="" />
            ) : (
              <span className="unit-pin unit-pin-text">
                {(unit.acronym || "?").slice(0, 5)}
              </span>
            )}
          </div>
          <div>
            <h3>Unit logo / seal</h3>
            <p className="hint">
              PNG, JPEG or WebP up to 2 MiB. The seal is drawn as the unit's pin; without one the
              pin shows the acronym instead.
            </p>
            <div className="btn-row">
              <input
                ref={logoInput}
                type="file"
                accept="image/png,image/jpeg,image/webp"
                hidden
                onChange={(event) => uploadLogo(event.target.files?.[0])}
              />
              <Button
                size="sm"
                variant="ghost"
                disabled={busyLogo}
                onClick={() => logoInput.current?.click()}
              >
                {logoUrl ? "Replace seal" : "Upload seal"}
              </Button>
              {logoUrl && (
                <Button size="sm" variant="ghost" disabled={busyLogo} onClick={removeLogo}>
                  Remove seal
                </Button>
              )}
            </div>
          </div>
        </div>
      </Modal>

      {picking && (
        <LocationPickerModal
          title={`Pin ${unit.acronym} on the map`}
          initial={{
            latitude: picked?.latitude ?? "",
            longitude: picked?.longitude ?? "",
            mgrs: mgrs,
            location_name: place,
          }}
          onClose={() => setPicking(false)}
          onApply={(point) => {
            setPicked(point);
            setMgrs(point.mgrs || mgrs);
            if (point.location_name) setPlace(point.location_name);
            setPicking(false);
          }}
        />
      )}
    </>
  );
}
