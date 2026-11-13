import { useState } from "react";
import { api, qs } from "../api/client.js";
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
  StatusBadge,
  Table,
  Textarea,
} from "../components/ui.jsx";
import SuperAdminDelete from "../components/SuperAdminDelete.jsx";
import { useApi } from "../hooks/useApi.js";
import { formatDateTime } from "../utils/format.js";

const EMPTY = {
  registration_number: "",
  wheels: 4,
  make: "",
  model: "",
  registration_no: "",
  official_receipt_no: "",
  unit: "",
  capacity: 0,
  notes: "",
};

const WHEELS = [2, 4, 6, 8, 10, 12];

/**
 * The vehicle register.
 *
 * It has its own Mobility Assets page under Resources because a vehicle is an
 * accountable asset like any other, but it stays a single record: dispatch,
 * trip planning and tracking all read these rows. ``embedded`` remains available
 * for compact reuse without the page heading.
 */
export default function VehiclesPage({ embedded = false }) {
  const { permissions } = useAuth();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const path = `/vehicles/${qs({ search, status, page_size: 200 })}`;
  const { data, loading, error, reload } = useApi(path, { deps: [search, status] });
  const units = useApi("/units/?is_active=true&page_size=200");
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(EMPTY);
  const [actionError, setActionError] = useState("");
  const [busy, setBusy] = useState(false);

  async function create(event) {
    event.preventDefault();
    setBusy(true);
    setActionError("");
    try {
      await api.post("/vehicles/", {
        ...form,
        unit: form.unit ? Number(form.unit) : null,
        capacity: Number(form.capacity) || 0,
        wheels: Number(form.wheels) || 0,
      });
      setOpen(false);
      setForm(EMPTY);
      reload();
    } catch (err) {
      setActionError(err.message);
    } finally {
      setBusy(false);
    }
  }

  const table = (
    <>
      <div className="inline-row" style={{ marginBottom: 14 }}>
        <Input
          placeholder="Search make, model or plate…"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          aria-label="Search vehicles"
        />
        <Select
          value={status}
          onChange={(event) => setStatus(event.target.value)}
          aria-label="Vehicle status"
        >
          <option value="">Any status</option>
          <option value="available">Available</option>
          <option value="on_trip">On Trip</option>
          <option value="maintenance">Under Maintenance</option>
          <option value="unserviceable">Unserviceable</option>
        </Select>
      </div>

      <ErrorBanner message={error || actionError} onRetry={reload} />

      {loading && !data ? (
        <Loading />
      ) : (
        <Table
          rows={data?.results ?? []}
          empty="No vehicles registered."
          stack
          columns={[
            {
              key: "display_name",
              label: "Vehicle",
              render: (row) =>
                row.display_name ||
                [row.make, row.model, row.registration_number].filter(Boolean).join(" "),
            },
            {
              key: "wheels",
              label: "Type",
              render: (row) => (row.wheels ? `${row.wheels}-wheel` : "—"),
            },
            { key: "registration_no", label: "Registration (CR)", render: (row) => row.registration_no || "—" },
            {
              key: "official_receipt_no",
              label: "Official receipt (OR)",
              render: (row) => row.official_receipt_no || "—",
            },
            {
              key: "unit_acronym",
              label: "Unit",
              render: (row) => row.unit_acronym || row.unit_name || "—",
            },
            {
              key: "location",
              label: "Location",
              render: (row) => {
                const report = row.last_position;
                if (!report) return <span className="hint">No report</span>;
                const where =
                  report.mgrs ||
                  `${Number(report.latitude).toFixed(4)}, ${Number(report.longitude).toFixed(4)}`;
                return (
                  <div>
                    <div>{where}</div>
                    <div className="hint">
                      {formatDateTime(report.reported_at)}
                      {report.is_stale ? " · stale" : ""}
                    </div>
                  </div>
                );
              },
            },
            { key: "capacity", label: "Capacity" },
            { key: "status", label: "Status", render: (row) => <StatusBadge status={row.status} /> },
            {
              key: "actions",
              label: "",
              render: (row) => (
                <SuperAdminDelete
                  path={`/vehicles/${row.id}/`}
                  label={`Delete ${row.display_name || row.registration_number}`}
                  confirmText={`Delete ${
                    row.display_name || row.registration_number
                  } permanently? The trips that name it go with it.`}
                  onDone={reload}
                  onError={setActionError}
                />
              ),
            },
          ]}
        />
      )}
    </>
  );

  const modal = open && (
    <Modal
      title="Add vehicle"
      onClose={() => setOpen(false)}
      wide
      footer={
        <>
          <Button variant="ghost" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button onClick={create} disabled={busy}>
            Save vehicle
          </Button>
        </>
      }
    >
      <ErrorBanner message={actionError} />
      <form onSubmit={create}>
        <div className="form-grid">
          <Field label="Type (no. of wheels)" required>
            <Select
              value={form.wheels}
              onChange={(event) => setForm({ ...form, wheels: event.target.value })}
            >
              {WHEELS.map((count) => (
                <option key={count} value={count}>
                  {count}-wheel
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Make" required hint="The manufacturer — e.g. KIA, Toyota, Honda.">
            <Input
              value={form.make}
              onChange={(event) => setForm({ ...form, make: event.target.value })}
              placeholder="KIA"
              required
            />
          </Field>
          <Field label="Model" required hint="The model — e.g. KM450. Never the same as the make.">
            <Input
              value={form.model}
              onChange={(event) => setForm({ ...form, model: event.target.value })}
              placeholder="KM450"
              required
            />
          </Field>
          <Field label="Plate number" required hint="Shown after the make and model: KIA KM450 12389.">
            <Input
              value={form.registration_number}
              onChange={(event) =>
                setForm({ ...form, registration_number: event.target.value })
              }
              placeholder="12389"
              required
            />
          </Field>
          <Field label="Registration no. (CR)" hint="Certificate of Registration number.">
            <Input
              value={form.registration_no}
              onChange={(event) => setForm({ ...form, registration_no: event.target.value })}
            />
          </Field>
          <Field label="Official receipt (OR)" hint="Official Receipt number of the registration.">
            <Input
              value={form.official_receipt_no}
              onChange={(event) =>
                setForm({ ...form, official_receipt_no: event.target.value })
              }
            />
          </Field>
          <Field label="Unit">
            <Select
              value={form.unit}
              onChange={(event) => setForm({ ...form, unit: event.target.value })}
            >
              <option value="">Unassigned</option>
              {(units.data?.results ?? []).map((unit) => (
                <option key={unit.id} value={unit.id}>
                  {unit.acronym || unit.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Capacity" hint="Seats or payload, as the unit counts it.">
            <Input
              type="number"
              min="0"
              value={form.capacity}
              onChange={(event) => setForm({ ...form, capacity: event.target.value })}
            />
          </Field>
          <Field label="Notes" className="span-2">
            <Textarea
              value={form.notes}
              onChange={(event) => setForm({ ...form, notes: event.target.value })}
            />
          </Field>
        </div>
      </form>
    </Modal>
  );

  if (embedded) {
    return (
      <>
        <div className="btn-row" style={{ marginBottom: 12 }}>
          {permissions.can_manage_vehicles && (
            <Button onClick={() => setOpen(true)}>Add vehicle</Button>
          )}
          <span className="hint">
            A vehicle is accounted for here and in dispatch, trip planning and tracking — one record,
            not a second copy.
          </span>
        </div>
        {table}
        {modal}
      </>
    );
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Mobility Assets</h1>
          <p>Unit vehicle register with availability used by dispatch and trip planning.</p>
        </div>
        {permissions.can_manage_vehicles && (
          <Button onClick={() => setOpen(true)}>Add vehicle</Button>
        )}
      </div>

      <Card>{table}</Card>
      {modal}
    </>
  );
}
