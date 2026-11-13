import { useMemo, useState } from "react";
import { api, qs } from "../api/client.js";
import { useAuth } from "../auth/AuthContext.jsx";
import {
  Badge,
  Button,
  ErrorBanner,
  Field,
  Input,
  Loading,
  Modal,
  Select,
  StatusBadge,
  Table,
} from "./ui.jsx";
import { useApi } from "../hooks/useApi.js";
import OperationalLocationPickerModal from "./OperationalLocationPickerModal.jsx";

const AIR_ASSET_LOCATION_TYPES = ["friendly_troop"];

const AIRCRAFT_TYPES = [
  { value: "rotary_wing", label: "Rotary-Wing Aircraft" },
  { value: "fixed_wing", label: "Fixed-Wing Aircraft" },
  { value: "uav", label: "Unmanned Aerial Vehicle (UAV)" },
  { value: "tiltrotor", label: "Tiltrotor Aircraft" },
  { value: "other", label: "Other Aircraft" },
];

const ASSIGNED_UNITS = [
  { value: "5fw", label: "5th Fighter Wing" },
  { value: "205th_thw", label: "205th Tactical Helicopter Wing" },
  { value: "15sw", label: "15th Strike Wing" },
  { value: "220aw", label: "220th Airlift Wing" },
  { value: "250paw", label: "250th Presidential Airlift Wing" },
  { value: "300aisw", label: "300th Air Intelligence Surveillance Wing" },
  { value: "505sarg", label: "505th Search and Rescue Group" },
];

const CATEGORIES = [
  { value: "fighter_interceptor", label: "Fighter / Interceptor" },
  { value: "attack_cas", label: "Attack / Close Air Support" },
  { value: "light_attack", label: "Light Attack" },
  { value: "transport_cargo", label: "Transport / Cargo" },
  { value: "utility", label: "Utility" },
  { value: "search_rescue", label: "Search and Rescue (SAR)" },
  { value: "combat_search_rescue", label: "Combat Search and Rescue (CSAR)" },
  { value: "reconnaissance_surveillance", label: "Reconnaissance / Surveillance" },
  { value: "maritime_patrol", label: "Maritime Patrol" },
  { value: "training", label: "Training" },
  { value: "air_ambulance_medevac", label: "Air Ambulance / Medical Evacuation (MEDEVAC)" },
  { value: "unmanned_aerial_vehicle", label: "Unmanned Aerial Vehicle (UAV)" },
  { value: "aerial_refueling", label: "Aerial Refueling" },
  { value: "special_operations", label: "Special Operations" },
  { value: "other_unclassified", label: "Other / Unclassified" },
];

const OPERATIONAL_STATUSES = [
  {
    value: "available",
    label: "Available",
    description: "Ready for mission assignment.",
  },
  {
    value: "scheduled",
    label: "Scheduled",
    description: "Assigned to an upcoming mission.",
  },
  {
    value: "in_flight",
    label: "In Flight",
    description: "Currently airborne.",
  },
  {
    value: "maintenance",
    label: "Maintenance",
    description: "Under maintenance.",
  },
  {
    value: "grounded",
    label: "Grounded",
    description: "Restricted from flight operations.",
  },
  {
    value: "unserviceable",
    label: "Unserviceable",
    description: "Not operationally capable.",
  },
  {
    value: "reserved",
    label: "Reserved",
    description: "Reserved for another task.",
  },
];

const FIREPOWER_CAPABILITIES = [
  { value: "gun_system", label: "Gun System", description: "Aircraft-mounted gun capability." },
  { value: "air_to_air", label: "Air-to-Air Capability", description: "Air combat capability." },
  {
    value: "air_to_ground",
    label: "Air-to-Ground Capability",
    description: "Ground attack capability.",
  },
  {
    value: "precision_strike",
    label: "Precision Strike Capability",
    description: "Precision-guided engagement capability.",
  },
  {
    value: "defensive_systems",
    label: "Defensive Systems",
    description: "Aircraft defensive capability.",
  },
  {
    value: "electronic_warfare",
    label: "Electronic Warfare",
    description: "Electronic warfare capability.",
  },
  {
    value: "multi_role",
    label: "Multi-Role Capability",
    description: "Multiple mission capabilities.",
  },
  {
    value: "none",
    label: "No Firepower Capability",
    description: "Non-combat aircraft.",
  },
  {
    value: "other_unclassified",
    label: "Other / Unclassified",
    description: "Other capabilities.",
  },
];

const MUNITIONS_CATEGORIES = [
  {
    value: "air_to_air",
    label: "Air-to-Air Munitions",
    description: "Munitions intended for aerial targets.",
  },
  {
    value: "air_to_ground",
    label: "Air-to-Ground Munitions",
    description: "Munitions intended for ground targets.",
  },
  { value: "guided", label: "Guided Munitions", description: "Guided ordnance." },
  { value: "unguided", label: "Unguided Munitions", description: "Unguided ordnance." },
  { value: "gun_ammunition", label: "Gun Ammunition", description: "Aircraft gun ammunition." },
  {
    value: "training",
    label: "Training Munitions",
    description: "Training-specific munitions.",
  },
  {
    value: "practice",
    label: "Practice Munitions",
    description: "Practice or inert training items.",
  },
  {
    value: "other_unclassified",
    label: "Other / Unclassified",
    description: "Other munitions categories.",
  },
];

const EMPTY = {
  aircraft_type: "",
  categories: [],
  model: "",
  tail_number: "",
  callsign: "",
  cruising_speed_knots: "",
  current_location: "",
  assigned_unit: "",
  opcon_unit: "",
  operational_status: "available",
  firepower_capabilities: [],
  munitions_categories: [],
};

function descriptionFor(options, value) {
  return options.find((entry) => entry.value === value)?.description ?? "";
}

/**
 * The air-asset register.
 *
 * It is rendered on its own Air Assets page because an aircraft is an
 * accountable asset. The Administrator is the only account allowed to add,
 * edit or delete a row; every resource-menu account reads the rows in its unit
 * scope. The server enforces that same rule, so hiding the controls here is
 * only presentation.
 */
export default function AirAssetsSection() {
  const { permissions } = useAuth();
  const canManage = Boolean(permissions.is_admin);

  const [search, setSearch] = useState("");
  const [aircraftType, setAircraftType] = useState("");
  const [aircraftModel, setAircraftModel] = useState("");
  const [operationalStatus, setOperationalStatus] = useState("");
  const [assignedUnit, setAssignedUnit] = useState("");
  const [open, setOpen] = useState(false);
  const [locationPickerOpen, setLocationPickerOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(EMPTY);
  const [deleting, setDeleting] = useState(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");

  const path = `/inventory/air-assets/${qs({
    search,
    aircraft_type: aircraftType,
    model: aircraftModel,
    operational_status: operationalStatus,
    assigned_unit: assignedUnit,
    page_size: 200,
  })}`;
  const { data, loading, error, reload } = useApi(path, {
    deps: [search, aircraftType, aircraftModel, operationalStatus, assignedUnit],
  });
  const {
    data: filterCatalog,
    reload: reloadFilterCatalog,
  } = useApi("/inventory/air-assets/?page_size=200");
  const units = useApi("/units/?is_active=true&page_size=200", { enabled: canManage });

  const rows = data?.results ?? [];
  const catalogRows = filterCatalog?.results ?? [];
  const modelOptions = useMemo(() => {
    const models = new Set();
    catalogRows.forEach((row) => {
      if (aircraftType && row.aircraft_type !== aircraftType) return;
      if (row.model) models.add(row.model);
    });
    return [...models].sort((left, right) => left.localeCompare(right));
  }, [catalogRows, aircraftType]);

  function changeAircraftType(value) {
    setAircraftType(value);
    setAircraftModel("");
  }

  function openCreate() {
    setEditing(null);
    setForm(EMPTY);
    setActionError("");
    setNotice("");
    setOpen(true);
  }

  function openEdit(row) {
    setEditing(row);
    setForm({
      aircraft_type: row.aircraft_type || "",
      categories: Array.isArray(row.categories) ? row.categories : [],
      model: row.model || "",
      tail_number: row.tail_number || "",
      callsign: row.callsign || "",
      cruising_speed_knots: row.cruising_speed_knots ?? "",
      current_location: row.current_location || "",
      assigned_unit: row.assigned_unit || "",
      opcon_unit: row.opcon_unit ? String(row.opcon_unit) : "",
      operational_status: row.operational_status || "available",
      firepower_capabilities: Array.isArray(row.firepower_capabilities)
        ? row.firepower_capabilities
        : [],
      munitions_categories: Array.isArray(row.munitions_categories)
        ? row.munitions_categories
        : [],
    });
    setActionError("");
    setNotice("");
    setOpen(true);
  }

  function applyCurrentLocation(picked) {
    const detail = picked.detail || {};
    const value = [detail.label || picked.location_name, detail.address, detail.mgrs || picked.mgrs]
      .filter(Boolean)
      .join(", ");
    setForm((current) => ({ ...current, current_location: value }));
    setLocationPickerOpen(false);
  }

  function toggleList(field, value) {
    setForm((current) => {
      const values = Array.isArray(current[field]) ? current[field] : [];
      return {
        ...current,
        [field]: values.includes(value)
          ? values.filter((entry) => entry !== value)
          : [...values, value],
      };
    });
  }

  async function save(event) {
    event?.preventDefault?.();
    setBusy(true);
    setActionError("");
    try {
      const payload = {
        ...form,
        cruising_speed_knots:
          form.cruising_speed_knots === ""
            ? null
            : Number(form.cruising_speed_knots),
        opcon_unit: form.opcon_unit ? Number(form.opcon_unit) : null,
      };
      if (editing) {
        await api.patch(`/inventory/air-assets/${editing.id}/`, payload);
      } else {
        await api.post("/inventory/air-assets/", payload);
      }
      setOpen(false);
      setEditing(null);
      setForm(EMPTY);
      setNotice(editing ? "Air asset updated." : "Air asset added.");
      reload();
      reloadFilterCatalog();
    } catch (err) {
      const fieldError = Object.values(err.fieldErrors || {})[0];
      setActionError(fieldError || err.message || "The air asset could not be saved.");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!deleting) return;
    setBusy(true);
    setActionError("");
    try {
      await api.del(`/inventory/air-assets/${deleting.id}/`);
      setNotice(`${deleting.model} (${deleting.tail_number}) deleted.`);
      setDeleting(null);
      reload();
      reloadFilterCatalog();
    } catch (err) {
      setActionError(err.message || "The air asset could not be deleted.");
    } finally {
      setBusy(false);
    }
  }

  const columns = [
    {
      key: "aircraft",
      label: "Aircraft",
      render: (row) => (
        <div className="air-asset-name">
          <strong>{row.model}</strong>
          <span className="air-asset-sub">{row.aircraft_type_label}</span>
        </div>
      ),
    },
    {
      key: "identity",
      label: "Tail / callsign",
      render: (row) => (
        <div className="air-asset-stack">
          <strong>{row.tail_number}</strong>
          {row.callsign && <span className="air-asset-sub">{row.callsign}</span>}
        </div>
      ),
    },
    {
      key: "categories",
      label: "Category",
      render: (row) => {
        const labels = row.category_labels ?? [];
        return (
          <div className="air-categories" title={labels.join(", ")}>
            {labels.slice(0, 2).map((label) => (
              <Badge key={label} tone="info">
                {label}
              </Badge>
            ))}
            {labels.length > 2 && <Badge tone="muted">+{labels.length - 2}</Badge>}
          </div>
        );
      },
    },
    {
      key: "cruising_speed",
      label: "Cruising speed",
      render: (row) =>
        row.cruising_speed_knots ? (
          <span>{row.cruising_speed_knots} kn</span>
        ) : (
          <span className="air-asset-sub">Not set</span>
        ),
    },
    {
      key: "assignment",
      label: "Assignment",
      render: (row) => (
        <div className="air-asset-stack">
          <span>{row.assigned_unit_label || row.assigned_unit || "Unassigned"}</span>
          <span className="air-asset-sub">
            {row.opcon_unit_acronym || row.opcon_unit_name
              ? `OPCON: ${row.opcon_unit_acronym || row.opcon_unit_name}`
              : "No OPCON unit"}
          </span>
        </div>
      ),
    },
    {
      key: "current_location",
      label: "Current location",
      render: (row) =>
        row.current_location ? (
          <span title={row.current_location}>{row.current_location}</span>
        ) : (
          <span className="air-asset-sub">Not recorded</span>
        ),
    },
    {
      key: "operational_status",
      label: "Status",
      render: (row) => (
        <span title={descriptionFor(OPERATIONAL_STATUSES, row.operational_status)}>
          <StatusBadge status={row.operational_status} />
        </span>
      ),
    },
    {
      key: "capability",
      label: "Firepower / munitions",
      render: (row) => {
        const firepower = row.firepower_capability_labels ?? [];
        const munitions = row.munitions_category_labels ?? [];
        return (
          <div className="air-asset-stack">
            <span title={firepower.join(", ")}>
              {firepower.length ? firepower.join(" · ") : "—"}
            </span>
            <span className="air-asset-sub" title={munitions.join(", ")}>
              {munitions.length ? munitions.join(" · ") : "Munitions not recorded"}
            </span>
          </div>
        );
      },
    },
  ];

  if (canManage) {
    columns.push({
      key: "actions",
      label: "",
      render: (row) => (
        <div className="btn-row">
          <Button variant="ghost" size="sm" onClick={() => openEdit(row)}>
            Edit
          </Button>
          <Button variant="ghost" size="sm" onClick={() => setDeleting(row)}>
            Delete
          </Button>
        </div>
      ),
    });
  }

  return (
    <>
      <div className="air-assets-toolbar">
        <div className="air-assets-filters">
          <Input
            placeholder="Search model, tail number, callsign or MGRS…"
            aria-label="Search air assets"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          <Select
            aria-label="Aircraft type"
            value={aircraftType}
            onChange={(event) => changeAircraftType(event.target.value)}
          >
            <option value="">All aircraft types</option>
            {AIRCRAFT_TYPES.map((entry) => (
              <option key={entry.value} value={entry.value}>
                {entry.label}
              </option>
            ))}
          </Select>
          <Select
            aria-label="Aircraft model"
            value={aircraftModel}
            onChange={(event) => setAircraftModel(event.target.value)}
          >
            <option value="">All aircraft models</option>
            {modelOptions.map((model) => (
              <option key={model} value={model}>
                {model}
              </option>
            ))}
          </Select>
          <Select
            aria-label="Operational status"
            value={operationalStatus}
            onChange={(event) => setOperationalStatus(event.target.value)}
          >
            <option value="">All operational statuses</option>
            {OPERATIONAL_STATUSES.map((entry) => (
              <option key={entry.value} value={entry.value}>
                {entry.label}
              </option>
            ))}
          </Select>
          <Select
            aria-label="Aircraft unit"
            value={assignedUnit}
            onChange={(event) => setAssignedUnit(event.target.value)}
          >
            <option value="">All aircraft units</option>
            {ASSIGNED_UNITS.map((unit) => (
              <option key={unit.value} value={unit.value}>
                {unit.label}
              </option>
            ))}
          </Select>
        </div>
        {canManage && <Button onClick={openCreate}>Add air asset</Button>}
      </div>

      <p className="hint air-assets-note">
        {canManage
          ? "The Administrator records aircraft identity, current location, assignment, operational status and capability here."
          : "The Administrator maintains this register. You can read the air assets in your unit scope."}
      </p>

      <ErrorBanner message={error || actionError} onRetry={reload} />
      {notice && <div className="alert alert-success">{notice}</div>}

      {loading && !data ? (
        <Loading />
      ) : (
        <Table
          rows={rows}
          empty="No air assets recorded in your scope."
          stack
          columns={columns}
        />
      )}

      {open && (
        <Modal
          title={editing ? `Edit air asset — ${editing.tail_number}` : "Add air asset"}
          onClose={() => setOpen(false)}
          wide
          footer={
            <>
              <Button variant="ghost" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button onClick={save} disabled={busy}>
                {busy ? "Saving…" : editing ? "Save changes" : "Add air asset"}
              </Button>
            </>
          }
        >
          <ErrorBanner message={actionError} />
          <form onSubmit={save}>
            <div className="form-grid">
              <Field label="Aircraft type" required>
                <Select
                  value={form.aircraft_type}
                  onChange={(event) =>
                    setForm({ ...form, aircraft_type: event.target.value })
                  }
                  required
                >
                  <option value="">Select aircraft type…</option>
                  {AIRCRAFT_TYPES.map((entry) => (
                    <option key={entry.value} value={entry.value}>
                      {entry.label}
                    </option>
                  ))}
                </Select>
              </Field>

              <Field label="Aircraft model" required>
                <Input
                  value={form.model}
                  onChange={(event) => setForm({ ...form, model: event.target.value })}
                  placeholder="e.g. FA-50PH"
                  required
                />
              </Field>

              <Field
                label="Aircraft tail number"
                required
                hint="The same tail number is allowed for a different aircraft type or model."
              >
                <Input
                  value={form.tail_number}
                  onChange={(event) =>
                    setForm({ ...form, tail_number: event.target.value })
                  }
                  placeholder="e.g. 001"
                  required
                />
              </Field>

              <Field label="Aircraft callsign">
                <Input
                  value={form.callsign}
                  onChange={(event) => setForm({ ...form, callsign: event.target.value })}
                  placeholder="e.g. EAGLE 01"
                />
              </Field>

              <Field
                label="Cruising speed (knots)"
                hint="Used to calculate air mission ETAs from the MGRS route distance."
              >
                <Input
                  type="number"
                  inputMode="decimal"
                  min="1"
                  max="2000"
                  step="0.1"
                  value={form.cruising_speed_knots}
                  onChange={(event) =>
                    setForm({
                      ...form,
                      cruising_speed_knots: event.target.value,
                    })
                  }
                  placeholder="e.g. 140"
                />
              </Field>

              <Field
                label="Current location (MGRS)"
                hint="Search unit locations, airports, air strips or friendly troops. The selection fills the name, place and MGRS."
              >
                <div className="inline-row">
                  <Input
                    value={form.current_location}
                    onChange={(event) =>
                      setForm({ ...form, current_location: event.target.value })
                    }
                    placeholder="e.g. HTOG10, Bancasi, Butuan City, 51PYK7316589902"
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => setLocationPickerOpen(true)}
                  >
                    Search map
                  </Button>
                </div>
              </Field>

              <Field
                label="Assigned unit"
                required
                hint="The air unit that owns this aircraft."
              >
                <Select
                  value={form.assigned_unit}
                  onChange={(event) =>
                    setForm({ ...form, assigned_unit: event.target.value })
                  }
                  required
                >
                  <option value="">Select assigned unit…</option>
                  {ASSIGNED_UNITS.map((unit) => (
                    <option key={unit.value} value={unit.value}>
                      {unit.label}
                    </option>
                  ))}
                </Select>
              </Field>

              <Field
                label="Operational Control (OPCON) Unit"
                hint="The inventory unit that will employ this aircraft within its AOR."
              >
                <Select
                  value={form.opcon_unit}
                  onChange={(event) =>
                    setForm({ ...form, opcon_unit: event.target.value })
                  }
                >
                  <option value="">No OPCON unit</option>
                  {(units.data?.results ?? []).map((unit) => (
                    <option key={unit.id} value={unit.id}>
                      {unit.acronym || unit.name}
                    </option>
                  ))}
                </Select>
              </Field>

              <Field
                label="Operational status"
                required
                hint={descriptionFor(OPERATIONAL_STATUSES, form.operational_status)}
              >
                <Select
                  value={form.operational_status}
                  onChange={(event) =>
                    setForm({ ...form, operational_status: event.target.value })
                  }
                  required
                >
                  {OPERATIONAL_STATUSES.map((entry) => (
                    <option key={entry.value} value={entry.value}>
                      {entry.label}
                    </option>
                  ))}
                </Select>
              </Field>

              <Field
                label="Firepower capability"
                className="span-2"
                hint="Tick every capability this airframe has."
              >
                <div className="air-category-grid">
                  {FIREPOWER_CAPABILITIES.map((entry) => (
                    <label
                      className="check-row"
                      key={entry.value}
                      title={entry.description}
                    >
                      <input
                        type="checkbox"
                        checked={form.firepower_capabilities.includes(entry.value)}
                        onChange={() =>
                          toggleList("firepower_capabilities", entry.value)
                        }
                      />
                      <span>{entry.label}</span>
                    </label>
                  ))}
                </div>
              </Field>

              <Field
                label="Air munitions category"
                className="span-2"
                hint="Tick every munitions category this airframe can carry."
              >
                <div className="air-category-grid">
                  {MUNITIONS_CATEGORIES.map((entry) => (
                    <label
                      className="check-row"
                      key={entry.value}
                      title={entry.description}
                    >
                      <input
                        type="checkbox"
                        checked={form.munitions_categories.includes(entry.value)}
                        onChange={() =>
                          toggleList("munitions_categories", entry.value)
                        }
                      />
                      <span>{entry.label}</span>
                    </label>
                  ))}
                </div>
              </Field>

              <Field
                label="Aircraft category"
                required
                className="span-2"
                hint="Tick every role this airframe can perform."
              >
                <div className="air-category-grid">
                  {CATEGORIES.map((entry) => (
                    <label className="check-row" key={entry.value}>
                      <input
                        type="checkbox"
                        checked={form.categories.includes(entry.value)}
                        onChange={() => toggleList("categories", entry.value)}
                      />
                      <span>{entry.label}</span>
                    </label>
                  ))}
                </div>
              </Field>
            </div>
          </form>
        </Modal>
      )}

      {locationPickerOpen && (
        <OperationalLocationPickerModal
          title="Select aircraft current location"
          includeUnits
          includeAirfields
          featureTypes={AIR_ASSET_LOCATION_TYPES}
          onClose={() => setLocationPickerOpen(false)}
          onApply={applyCurrentLocation}
        />
      )}

      {deleting && (
        <Modal
          title="Delete air asset"
          onClose={() => setDeleting(null)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setDeleting(null)}>
                Cancel
              </Button>
              <Button variant="danger" onClick={remove} disabled={busy}>
                {busy ? "Deleting…" : "Delete air asset"}
              </Button>
            </>
          }
        >
          <ErrorBanner message={actionError} />
          <p>
            Delete <strong>{deleting.model}</strong> ({deleting.tail_number}) from the air-asset
            register? This cannot be undone.
          </p>
        </Modal>
      )}
    </>
  );
}
