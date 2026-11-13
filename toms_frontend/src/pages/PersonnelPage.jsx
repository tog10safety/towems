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
  Pagination,
  Select,
  Table,
  Textarea,
} from "../components/ui.jsx";
import SuperAdminDelete from "../components/SuperAdminDelete.jsx";
import { useApi } from "../hooks/useApi.js";
import { formatDate } from "../utils/format.js";

const PAGE_SIZE = 25;

const DETAIL_FIELDS = {
  email: "",
  sex: "",
  date_of_birth: "",
  blood_type: "",
  home_address: "",
  emergency_contact_name: "",
  emergency_contact_number: "",
  date_enlisted: "",
  date_commissioned: "",
  afsc_primary: "",
  afsc_secondary: "",
  afsc_tertiary: "",
  highest_education: "",
  civil_status: "",
};

const EMPTY = {
  password: "",
  authority: "Creator",
  category: "Officer",
  rank: "",
  designation: "",
  first_name: "",
  last_name: "",
  middle_name: "",
  serial_number: "",
  branch_of_service: "PAF",
  unit_id: "",
  contact_number: "",
  is_active: true,
  ...DETAIL_FIELDS,
};

const DATE_FIELDS = ["date_of_birth", "date_enlisted", "date_commissioned"];

function detailsFromRow(row) {
  return Object.fromEntries(
    Object.keys(DETAIL_FIELDS).map((field) => [field, row?.[field] || ""])
  );
}

export default function PersonnelPage() {
  const { user, permissions } = useAuth();
  const [search, setSearch] = useState("");
  const [authority, setAuthority] = useState("");
  const [page, setPage] = useState(1);
  const [dialog, setDialog] = useState(null); // { mode: "create" | "edit", row? }
  const [form, setForm] = useState(EMPTY);
  const [errors, setErrors] = useState({});
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [downloading, setDownloading] = useState(false);

  const [importOpen, setImportOpen] = useState(false);
  const [importFile, setImportFile] = useState(null);
  const [importPassword, setImportPassword] = useState("");
  const [importErrors, setImportErrors] = useState([]);
  const [importError, setImportError] = useState("");
  const [importNotice, setImportNotice] = useState("");
  const [importBusy, setImportBusy] = useState(false);
  // The batch belongs to one unit's chain: the template and the import are
  // both limited to it and its subordinates.
  const [importUnit, setImportUnit] = useState(user?.unit_id ? String(user.unit_id) : "");

  const path = `/personnel/${qs({ search, authority, page, page_size: PAGE_SIZE })}`;
  const { data, loading, error: loadError, reload } = useApi(path, {
    deps: [search, authority, page],
  });
  const options = useApi("/personnel/options/");

  const pickers = options.data ?? {};
  const unitOptions = pickers.units ?? [];
  const rankOptions = [...(pickers.ranks ?? [])];
  if (form.rank && !rankOptions.includes(form.rank)) rankOptions.push(form.rank);
  const branchOptions = [...(pickers.branches ?? ["PAF"])];
  if (form.branch_of_service && !branchOptions.includes(form.branch_of_service)) {
    branchOptions.push(form.branch_of_service);
  }
  const designationOptions = [...(pickers.designations ?? [])];
  if (form.designation && !designationOptions.includes(form.designation)) {
    designationOptions.push(form.designation);
  }

  function openCreate() {
    setForm({ ...EMPTY, unit_id: user?.unit_id ? String(user.unit_id) : "" });
    setErrors({});
    setError("");
    setDialog({ mode: "create" });
  }

  function openEdit(row) {
    setForm({
      ...EMPTY,
      ...detailsFromRow(row),
      authority: row.authority || "Creator",
      category: row.category || "Officer",
      rank: row.rank || "",
      designation: row.designation || "",
      first_name: row.first_name || "",
      middle_name: row.middle_name || "",
      last_name: row.last_name || "",
      serial_number: row.serial_number || "",
      branch_of_service: row.branch_of_service || "PAF",
      unit_id: row.unit_id ? String(row.unit_id) : "",
      contact_number: row.contact_number || "",
      is_active: row.is_active !== false,
      password: "",
    });
    setErrors({});
    setError("");
    setDialog({ mode: "edit", row });
  }

  function payloadFromForm() {
    const payload = { ...form };
    delete payload.password;
    payload.unit_id = form.unit_id === "" ? null : Number(form.unit_id);
    for (const field of DATE_FIELDS) {
      payload[field] = form[field] ? form[field] : null;
    }
    if (dialog?.mode === "create") payload.password = form.password;
    return payload;
  }

  async function savePersonnel(event) {
    event?.preventDefault();
    setBusy(true);
    setError("");
    setErrors({});
    try {
      const payload = payloadFromForm();
      if (dialog.mode === "edit") {
        await api.patch(`/personnel/${dialog.row.id}/`, payload);
        setNotice(`${dialog.row.full_name || "Personnel"} updated.`);
      } else {
        await api.post("/personnel/", payload);
        setNotice(
          "Personnel registered. Username is the serial number; the person must change the initial password at first sign-in."
        );
      }
      setDialog(null);
      reload();
    } catch (err) {
      setErrors(err.fieldErrors || {});
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function deactivate(row) {
    if (!window.confirm(`Deactivate ${row.full_name}? Records and history are preserved.`)) return;
    const reason = window.prompt("Reason for deactivation (recorded in the audit trail):", "");
    if (reason === null) return;
    if (reason.trim().length < 3) {
      setError("Record a reason of at least 3 characters before deactivating.");
      return;
    }
    try {
      await api.del(`/personnel/${row.id}/`, { reason: reason.trim() });
      setNotice(`${row.full_name} deactivated.`);
      reload();
    } catch (err) {
      setError(err.message);
    }
  }

  async function downloadTemplate() {
    setDownloading(true);
    setError("");
    try {
      if (!importUnit) {
        setError("Choose the unit this template covers first.");
        return;
      }
      const response = await api.download(`/personnel/import-template/?unit=${importUnit}`);
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      const acronym = unitOptions.find((item) => String(item.id) === importUnit)?.acronym;
      link.download = acronym
        ? `TOWEMS-personnel-registration-${acronym}.xlsx`
        : "TOWEMS-personnel-registration-template.xlsx";
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err.message || "Unable to download the Excel template.");
    } finally {
      setDownloading(false);
    }
  }

  function openImport() {
    setImportFile(null);
    setImportPassword("");
    setImportErrors([]);
    setImportError("");
    setImportNotice("");
    setImportOpen(true);
  }

  async function importPersonnel(event) {
    event.preventDefault();
    setImportBusy(true);
    setImportError("");
    setImportErrors([]);
    setImportNotice("");
    try {
      const body = new FormData();
      if (importFile) body.append("file", importFile);
      if (importUnit) body.append("unit", importUnit);
      body.append("password", importPassword);
      const result = await api.upload("/personnel/import/", body);
      setImportNotice(
        `${result.created} personnel registered. They can sign in with their serial number and the initial password you set.`
      );
      reload();
    } catch (err) {
      setImportError(err.message);
      setImportErrors(Array.isArray(err.payload?.errors) ? err.payload.errors : []);
    } finally {
      setImportBusy(false);
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Personnel</h1>
          <p>
            Operational profiles, ranks, units, service details and availability. Records are
            deactivated, never deleted.
          </p>
        </div>
        {permissions.can_manage_personnel && (
          <div className="btn-row">
            <Button variant="ghost" onClick={downloadTemplate} disabled={downloading}>
              {downloading ? "Preparing…" : "Download Excel template"}
            </Button>
            <Button variant="ghost" onClick={openImport}>
              Import from Excel
            </Button>
            <Button onClick={openCreate}>Add personnel</Button>
          </div>
        )}
      </div>

      <ErrorBanner message={loadError || options.error || error} onRetry={loadError ? reload : undefined} />
      {notice && <div className="alert alert-success">{notice}</div>}

      <Card>
        <div className="inline-row" style={{ marginBottom: 14 }}>
          <Input
            placeholder="Search name, serial number, designation…"
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
              setPage(1);
            }}
          />
          <Select
            value={authority}
            onChange={(event) => {
              setAuthority(event.target.value);
              setPage(1);
            }}
          >
            <option value="">All roles</option>
            {(pickers.account_roles ?? []).map((role) => (
              <option key={role} value={role}>
                {role}
              </option>
            ))}
          </Select>
        </div>

        {loading && !data ? (
          <Loading />
        ) : (
          <>
            <Table
              rows={data?.results ?? []}
              empty="No personnel match the filters."
              columns={[
                { key: "full_name", label: "Name" },
                { key: "serial_number", label: "Serial" },
                { key: "rank", label: "Rank" },
                { key: "designation", label: "Designation" },
                { key: "email", label: "Email", render: (row) => row.email || "—" },
                {
                  key: "authority",
                  label: "Role",
                  render: (row) => <Badge tone="info">{row.authority}</Badge>,
                },
                { key: "unit_acronym", label: "Unit", render: (row) => row.unit_acronym || "—" },
                {
                  key: "sections",
                  label: "Office / Section",
                  render: (row) => {
                    const sections = row.sections ?? [];
                    if (!sections.length) return "—";
                    return sections
                      .map(
                        (entry) =>
                          `${entry.code} — ${entry.name}${
                            entry.assignment_type === "secondary" ? " (secondary)" : ""
                          }`
                      )
                      .join("; ");
                  },
                },
                {
                  key: "date_created",
                  label: "Joined",
                  render: (row) => formatDate(row.date_created),
                },
                {
                  key: "is_active",
                  label: "Status",
                  render: (row) => (
                    <Badge tone={row.is_active ? "success" : "muted"}>
                      {row.is_active ? "Active" : "Inactive"}
                    </Badge>
                  ),
                },
                {
                  key: "actions",
                  label: "",
                  render: (row) =>
                    permissions.can_manage_personnel ? (
                      <div className="btn-row">
                        <Button variant="ghost" size="sm" onClick={() => openEdit(row)}>
                          Edit
                        </Button>
                        {row.is_active && (
                          <Button variant="ghost" size="sm" onClick={() => deactivate(row)}>
                            Deactivate
                          </Button>
                        )}
                        <SuperAdminDelete
                          path={`/personnel/${row.id}/?purge=true`}
                          label={`Delete ${row.full_name} permanently`}
                          confirmText={`Delete ${row.full_name} permanently? Only an account with no missions, assessments or audit history can go.`}
                          onDone={() => {
                            setNotice(`${row.full_name} deleted permanently.`);
                            reload();
                          }}
                          onError={setError}
                        />
                      </div>
                    ) : null,
                },
              ]}
            />
            <Pagination page={page} pageSize={PAGE_SIZE} count={data?.count ?? 0} onPage={setPage} />
          </>
        )}
      </Card>

      {dialog && (
        <Modal
          title={dialog.mode === "edit" ? `Edit ${dialog.row.full_name}` : "Add personnel"}
          wide
          onClose={() => setDialog(null)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setDialog(null)}>
                Cancel
              </Button>
              <Button onClick={savePersonnel} disabled={busy}>
                {busy ? "Saving…" : dialog.mode === "edit" ? "Save changes" : "Register personnel"}
              </Button>
            </>
          }
        >
          <ErrorBanner message={error} />
          <form onSubmit={savePersonnel}>
            <div className="form-grid">
              <h3 className="span-2">Identity and assignment</h3>
              <Field label="Serial number" required error={errors.serial_number}>
                <Input
                  value={form.serial_number}
                  onChange={(event) => setForm({ ...form, serial_number: event.target.value })}
                  readOnly={dialog.mode === "edit"}
                  required
                />
              </Field>
              <Field
                label="Username"
                hint="Created automatically from the serial number."
              >
                <Input value={form.serial_number} readOnly placeholder="Uses the serial number" />
              </Field>
              <Field label="First name" required error={errors.first_name}>
                <Input
                  value={form.first_name}
                  onChange={(event) => setForm({ ...form, first_name: event.target.value })}
                  required
                />
              </Field>
              <Field label="Middle name">
                <Input
                  value={form.middle_name}
                  onChange={(event) => setForm({ ...form, middle_name: event.target.value })}
                />
              </Field>
              <Field label="Last name" required error={errors.last_name}>
                <Input
                  value={form.last_name}
                  onChange={(event) => setForm({ ...form, last_name: event.target.value })}
                  required
                />
              </Field>
              <Field label="Rank">
                <Select
                  value={form.rank}
                  onChange={(event) => setForm({ ...form, rank: event.target.value })}
                >
                  <option value="">Select rank…</option>
                  {rankOptions.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Designation" error={errors.designation}>
                <Select
                  value={form.designation}
                  onChange={(event) => setForm({ ...form, designation: event.target.value })}
                >
                  <option value="">Select designation…</option>
                  {designationOptions.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Account role" required error={errors.authority}>
                <Select
                  value={form.authority}
                  onChange={(event) => setForm({ ...form, authority: event.target.value })}
                >
                  {(pickers.account_roles ?? ["Creator"]).map((role) => (
                    <option key={role} value={role}>
                      {role}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Category" required error={errors.category}>
                <Select
                  value={form.category}
                  onChange={(event) => setForm({ ...form, category: event.target.value })}
                >
                  {(pickers.categories ?? ["Officer"]).map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Unit" required error={errors.unit_id}>
                <Select
                  value={form.unit_id}
                  onChange={(event) => setForm({ ...form, unit_id: event.target.value })}
                  required
                >
                  <option value="">Select unit…</option>
                  {unitOptions.map((unit) => (
                    <option key={unit.id} value={unit.id}>
                      {unit.label}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Branch of service" error={errors.branch_of_service}>
                <Select
                  value={form.branch_of_service}
                  onChange={(event) =>
                    setForm({ ...form, branch_of_service: event.target.value })
                  }
                >
                  {branchOptions.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Contact number" error={errors.contact_number}>
                <Input
                  value={form.contact_number}
                  onChange={(event) => setForm({ ...form, contact_number: event.target.value })}
                  placeholder="+63 917 000 0000"
                />
              </Field>
              <Field label="Email address" error={errors.email}>
                <Input
                  type="email"
                  value={form.email}
                  onChange={(event) => setForm({ ...form, email: event.target.value })}
                  placeholder="name@example.com"
                />
              </Field>

              <h3 className="span-2">Personal details</h3>
              <Field label="Sex / gender" error={errors.sex}>
                <Select
                  value={form.sex}
                  onChange={(event) => setForm({ ...form, sex: event.target.value })}
                >
                  <option value="">Select…</option>
                  {(pickers.sexes ?? ["Male", "Female"]).map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Date of birth" error={errors.date_of_birth}>
                <Input
                  type="date"
                  value={form.date_of_birth}
                  onChange={(event) => setForm({ ...form, date_of_birth: event.target.value })}
                />
              </Field>
              <Field label="Blood type" error={errors.blood_type}>
                <Select
                  value={form.blood_type}
                  onChange={(event) => setForm({ ...form, blood_type: event.target.value })}
                >
                  <option value="">Select…</option>
                  {(pickers.blood_types ?? []).map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Civil status" error={errors.civil_status}>
                <Select
                  value={form.civil_status}
                  onChange={(event) => setForm({ ...form, civil_status: event.target.value })}
                >
                  <option value="">Select…</option>
                  {(pickers.civil_statuses ?? []).map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Highest education" error={errors.highest_education}>
                <Select
                  value={form.highest_education}
                  onChange={(event) => setForm({ ...form, highest_education: event.target.value })}
                >
                  <option value="">Select…</option>
                  {(pickers.educations ?? []).map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Home address" className="span-2" error={errors.home_address}>
                <Textarea
                  rows={2}
                  value={form.home_address}
                  onChange={(event) => setForm({ ...form, home_address: event.target.value })}
                />
              </Field>
              <Field label="Emergency contact name" error={errors.emergency_contact_name}>
                <Input
                  value={form.emergency_contact_name}
                  onChange={(event) =>
                    setForm({ ...form, emergency_contact_name: event.target.value })
                  }
                />
              </Field>
              <Field
                label="Emergency contact number"
                error={errors.emergency_contact_number}
              >
                <Input
                  value={form.emergency_contact_number}
                  onChange={(event) =>
                    setForm({ ...form, emergency_contact_number: event.target.value })
                  }
                />
              </Field>

              <h3 className="span-2">Service details</h3>
              <Field label="Date enlisted" error={errors.date_enlisted}>
                <Input
                  type="date"
                  value={form.date_enlisted}
                  onChange={(event) => setForm({ ...form, date_enlisted: event.target.value })}
                />
              </Field>
              <Field label="Date commissioned" error={errors.date_commissioned}>
                <Input
                  type="date"
                  value={form.date_commissioned}
                  onChange={(event) => setForm({ ...form, date_commissioned: event.target.value })}
                />
              </Field>
              <Field label="AFSC primary" error={errors.afsc_primary}>
                <Input
                  value={form.afsc_primary}
                  onChange={(event) => setForm({ ...form, afsc_primary: event.target.value })}
                />
              </Field>
              <Field label="AFSC secondary" error={errors.afsc_secondary}>
                <Input
                  value={form.afsc_secondary}
                  onChange={(event) => setForm({ ...form, afsc_secondary: event.target.value })}
                />
              </Field>
              <Field label="AFSC tertiary" error={errors.afsc_tertiary}>
                <Input
                  value={form.afsc_tertiary}
                  onChange={(event) => setForm({ ...form, afsc_tertiary: event.target.value })}
                />
              </Field>
              {dialog.mode === "create" && (
                <Field
                  label="Initial password"
                  required
                  className="span-2"
                  error={errors.password}
                  hint="Minimum 8 characters with upper case, lower case, a digit and a symbol. The person must change it at first sign-in."
                >
                  <Input
                    type="password"
                    value={form.password}
                    onChange={(event) => setForm({ ...form, password: event.target.value })}
                    required
                    autoComplete="new-password"
                  />
                </Field>
              )}
            </div>
          </form>
        </Modal>
      )}

      {importOpen && (
        <Modal
          title="Import personnel from Excel"
          onClose={() => setImportOpen(false)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setImportOpen(false)}>
                {importNotice ? "Done" : "Cancel"}
              </Button>
              {!importNotice && (
                <Button onClick={importPersonnel} disabled={importBusy || !importFile || !importUnit}>
                  {importBusy ? "Importing…" : "Import workbook"}
                </Button>
              )}
            </>
          }
        >
          <ErrorBanner message={importError} />
          {importNotice ? (
            <div className="alert alert-success">{importNotice}</div>
          ) : (
            <form onSubmit={importPersonnel}>
              <div className="alert alert-info">
                Pick the unit this batch belongs to, then download its template. The template's
                Unit dropdown only offers that unit and its subordinate units, and rows naming
                any other unit are rejected. Usernames are created from the serial numbers, and a
                serial number can never be registered twice, in any unit.
              </div>
              <Field
                label="Unit for this batch"
                required
                hint="The template and the import are limited to this unit and its subordinate units."
              >
                <Select
                  value={importUnit}
                  onChange={(event) => setImportUnit(event.target.value)}
                >
                  <option value="">Select unit…</option>
                  {unitOptions.map((item) => (
                    <option key={item.id} value={String(item.id)}>
                      {item.label}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Excel workbook" required>
                <Input
                  type="file"
                  accept=".xlsx"
                  onChange={(event) => setImportFile(event.target.files?.[0] ?? null)}
                />
              </Field>
              <Field
                label="Initial password for every new account"
                required
                hint="Minimum 8 characters with upper case, lower case, a digit and a symbol."
              >
                <Input
                  type="password"
                  value={importPassword}
                  onChange={(event) => setImportPassword(event.target.value)}
                  required
                  autoComplete="new-password"
                />
              </Field>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={downloadTemplate}
                disabled={!importUnit}
              >
                Download the template
              </Button>
              {importErrors.length > 0 && (
                <div className="alert alert-danger" style={{ marginTop: 12 }}>
                  <strong>Nothing was imported. Fix these rows:</strong>
                  <ul>
                    {importErrors.slice(0, 30).map((row, index) => (
                      <li key={`${row.row}-${row.field}-${index}`}>
                        Row {row.row || "—"} · {row.field}: {row.message}
                      </li>
                    ))}
                  </ul>
                  {importErrors.length > 30 && (
                    <p>Showing the first 30 of {importErrors.length} issues.</p>
                  )}
                </div>
              )}
            </form>
          )}
        </Modal>
      )}
    </>
  );
}
