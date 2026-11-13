import { useState } from "react";
import { api, qs } from "../api/client.js";
import { useAuth } from "../auth/AuthContext.jsx";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorBanner,
  Field,
  Input,
  Loading,
  Modal,
  Select,
  Table,
  Tabs,
} from "../components/ui.jsx";
import { useApi } from "../hooks/useApi.js";

const EMPTY_OFFICE = {
  unit: "",
  catalogue_code: "A1",
  custom_name: "",
  name: "",
  code: "",
  kind: "office",
  function: "general",
  description: "",
};
const EMPTY_ASSIGN = { user_id: "", section_id: "", assignment_type: "primary", notes: "" };

const OTHER_CODE = "OTHER";

/**
 * Office/Section management (spec §1-§4).
 *
 * The standard staff offices (A1-A10, Safety Office) come from the backend
 * catalogue and are seeded per unit; "Specify - Other Office/Section" reveals a
 * required free-text name and is stored as a custom entry. Only the
 * Administrator and the Admin Officer may add, change or delete anything - the
 * list itself is readable so personnel and mission forms can use it. Deleting
 * an office removes the personnel assignments made to it; the audit trail keeps
 * the record of both.
 */
export default function OfficeSectionsPage() {
  const { permissions } = useAuth();
  const canManage = Boolean(permissions.can_manage_sections);

  const [unitFilter, setUnitFilter] = useState("");
  // Primary and secondary are the only two classifications, so the filter is a
  // two-option switch rather than a list that also offers an "all" state.
  const [typeFilter, setTypeFilter] = useState("primary");
  const [officeOpen, setOfficeOpen] = useState(false);
  const [assignOpen, setAssignOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [officeForm, setOfficeForm] = useState(EMPTY_OFFICE);
  const [assignForm, setAssignForm] = useState(EMPTY_ASSIGN);
  const [personSearch, setPersonSearch] = useState("");
  const [errors, setErrors] = useState({});
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  // ``scope=mine`` returns the caller's own unit plus its authorised
  // subordinates (every active unit for the Administrator). An Admin Officer
  // is never shown the organisation-wide reference list they cannot manage.
  const units = useApi("/units/?scope=mine&is_active=true&page_size=200");
  const catalogue = useApi("/offices/catalogue/");
  // Nothing is fetched until a unit is chosen: the page manages one unit's
  // structure at a time instead of aggregating every authorised unit.
  const offices = useApi(
    unitFilter ? `/offices/${qs({ unit: unitFilter, page_size: 200 })}` : null,
    { deps: [unitFilter] }
  );
  const assignments = useApi(
    unitFilter
      ? `/section-assignments/${qs({
          unit: unitFilter,
          assignment_type: typeFilter,
          page_size: 200,
        })}`
      : null,
    { deps: [unitFilter, typeFilter] }
  );
  const personnel = useApi(
    `/personnel/${qs({ is_active: true, page_size: 50, search: personSearch })}`,
    { deps: [personSearch] }
  );
  const allOffices = useApi("/offices/?is_active=true&page_size=200");

  const catalogueRows = catalogue.data?.catalogue ?? [];
  const isSpecify = officeForm.catalogue_code === OTHER_CODE;
  const unitRows = units.data?.results ?? [];
  const selectedUnit = unitRows.find((unit) => String(unit.id) === String(unitFilter));
  // Standard offices this unit already has. They stay visible in the picker but
  // disabled, so the reason is obvious instead of the entry silently vanishing.
  const existingCodes = new Set(
    (offices.data?.results ?? [])
      .filter((office) => office.is_active)
      .map((office) => office.code)
  );
  const availableCatalogue = catalogueRows.filter(
    (row) => row.custom || !existingCodes.has(row.code)
  );

  function refreshAll() {
    offices.reload();
    assignments.reload();
  }

  function openCreate() {
    // Default to A1 - Personnel / Administration, or the first standard office
    // the unit does not have yet. When every standard entry is already present,
    // fall back to Specify so the picker never preselects a duplicate.
    const firstAvailable = availableCatalogue.find((row) => !row.custom);
    setEditing(null);
    setOfficeForm({
      ...EMPTY_OFFICE,
      unit: unitFilter || "",
      catalogue_code: firstAvailable?.code || OTHER_CODE,
    });
    setErrors({});
    setError("");
    setOfficeOpen(true);
  }

  function openEdit(office) {
    setEditing(office);
    setOfficeForm({
      unit: String(office.unit),
      catalogue_code: "",
      custom_name: "",
      name: office.name,
      code: office.code,
      kind: office.kind,
      function: office.function,
      description: office.description || "",
    });
    setErrors({});
    setError("");
    setOfficeOpen(true);
  }

  async function saveOffice(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setErrors({});
    try {
      if (editing) {
        await api.patch(`/offices/${editing.id}/`, {
          name: officeForm.name,
          code: officeForm.code,
          kind: officeForm.kind,
          function: officeForm.function,
          description: officeForm.description,
        });
        setNotice("Office/section updated.");
      } else {
        const payload = {
          unit: Number(officeForm.unit),
          kind: officeForm.kind,
          function: officeForm.function,
          description: officeForm.description,
        };
        if (isSpecify) {
          payload.catalogue_code = OTHER_CODE;
          payload.custom_name = officeForm.custom_name;
        } else {
          // The picker disables what the unit already has; this is the guard
          // for a stale page or a hand-made request. The backend rejects it too.
          if (existingCodes.has(officeForm.catalogue_code)) {
            setError("This unit already has that office or section. Choose another one.");
            setBusy(false);
            return;
          }
          payload.catalogue_code = officeForm.catalogue_code;
        }
        await api.post("/offices/", payload);
        setNotice("Office/section created.");
      }
      setOfficeOpen(false);
      setOfficeForm(EMPTY_OFFICE);
      setEditing(null);
      refreshAll();
      allOffices.reload();
    } catch (err) {
      setErrors(err.fieldErrors || {});
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function seedDefaults() {
    if (!unitFilter) {
      setError("Select a unit first, then restore the standard office list.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const result = await api.post("/offices/seed-defaults/", {
        unit: Number(unitFilter),
      });
      setNotice(
        `${result.created?.length ? result.created.join(", ") : "No new offices"} created. ` +
          "Existing entries were left untouched."
      );
      refreshAll();
      allOffices.reload();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function resetCatalogue() {
    if (
      !window.confirm(
        "Delete every office/section record and its personnel assignments, then recreate " +
          "the standard A1-A10, Safety Office and Specify entries for every unit? " +
          "Missions, MRM records and the audit trail are not affected."
      )
    )
      return;
    setBusy(true);
    setError("");
    try {
      const result = await api.post("/offices/reset-catalogue/", {});
      setNotice(
        `${result.removed} office/section record(s) and ${result.assignments_removed} ` +
          `assignment(s) removed; ${result.created} standard entries created.`
      );
      refreshAll();
      allOffices.reload();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function deleteOffice(office) {
    if (
      !window.confirm(
        `Delete ${office.code} — ${office.name}? Its personnel assignments are removed with it.`
      )
    )
      return;
    setError("");
    try {
      await api.del(`/offices/${office.id}/`);
      setNotice("Office/section deleted.");
      refreshAll();
      allOffices.reload();
    } catch (err) {
      setError(err.message);
    }
  }

  async function assign(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setErrors({});
    try {
      await api.post("/section-assignments/", {
        user_id: Number(assignForm.user_id),
        section_id: Number(assignForm.section_id),
        assignment_type: assignForm.assignment_type,
        notes: assignForm.notes,
      });
      setAssignOpen(false);
      setAssignForm(EMPTY_ASSIGN);
      setNotice("Section assignment saved. The previous assignment was ended, not deleted.");
      refreshAll();
    } catch (err) {
      setErrors(err.fieldErrors || {});
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function toggleOffice(office) {
    setError("");
    try {
      await api.patch(`/offices/${office.id}/`, { is_active: !office.is_active });
      setNotice(office.is_active ? "Office/section deactivated." : "Office/section reactivated.");
      refreshAll();
    } catch (err) {
      setError(err.message);
    }
  }

  async function endAssignment(row) {
    if (!window.confirm(`End ${row.username}'s ${row.assignment_type} assignment to ${row.section_code}?`))
      return;
    setError("");
    try {
      await api.del(`/section-assignments/${row.id}/`);
      setNotice("Assignment ended. History retained.");
      refreshAll();
    } catch (err) {
      setError(err.message);
    }
  }

  const selectedPerson = (personnel.data?.results ?? []).find(
    (person) => String(person.id) === String(assignForm.user_id)
  );
  const sectionsForUnit = (allOffices.data?.results ?? []).filter(
    (office) => office.is_active && (!selectedPerson || office.unit === selectedPerson.unit_id)
  );

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Offices & Sections</h1>
          <p>
            Organise personnel under their unit with one primary and an optional secondary section.
          </p>
        </div>
        {canManage && (
          <div className="btn-row">
            {permissions.is_admin && (
              <Button variant="ghost" onClick={resetCatalogue} disabled={busy}>
                Reset to default list
              </Button>
            )}
            <Button variant="ghost" onClick={seedDefaults} disabled={busy || !unitFilter}>
              Restore defaults
            </Button>
            <Button variant="ghost" onClick={openCreate} disabled={!unitFilter}>
              New office/section
            </Button>
            <Button onClick={() => setAssignOpen(true)} disabled={!unitFilter}>
              Assign personnel
            </Button>
          </div>
        )}
      </div>

      <ErrorBanner message={offices.error || assignments.error || error} onRetry={refreshAll} />
      {notice && <div className="alert alert-success">{notice}</div>}
      {!canManage && (
        <div className="alert alert-warning">
          <span>
            You can view the structure. Adding, editing, deleting and assigning require the Admin
            Officer post or the Administrator.
          </span>
        </div>
      )}

      <Card>
        <Field
          label="Unit"
          required
          hint={
            permissions.is_admin
              ? "Select a unit from the units available to you. As Administrator every active unit is available, but the structure is managed one unit at a time."
              : "Select a unit from the units available to you — your own unit and its subordinate units."
          }
        >
          <Select
            value={unitFilter}
            onChange={(event) => setUnitFilter(event.target.value)}
            aria-label="Unit"
          >
            <option value="">Select a unit from the units available to you…</option>
            {unitRows.map((unit) => (
              <option key={unit.id} value={unit.id}>
                {unit.acronym || unit.name}
              </option>
            ))}
          </Select>
        </Field>
        {units.data && !unitRows.length && (
          <p className="hint">
            No unit is available to your account. An Administrator has to assign you to a unit
            before you can manage offices and sections.
          </p>
        )}
      </Card>

      <Card title={selectedUnit ? `Offices and sections — ${selectedUnit.acronym}` : "Offices and sections"}>
        {!unitFilter ? (
          <EmptyState message="Select a unit above to view and manage its offices and sections." />
        ) : offices.loading && !offices.data ? (
          <Loading />
        ) : !offices.data?.results?.length ? (
          <EmptyState message="No offices or sections defined for this unit yet." />
        ) : (
          <div className="section-grid">
            {offices.data.results.map((office) => (
              <div key={office.id} className={`section-card ${office.is_active ? "" : "inactive"}`}>
                <div className="section-card-head">
                  <div>
                    <strong>{office.code}</strong> {office.name}
                    <p className="hint">
                      {office.kind} · {office.unit_acronym || office.unit_name} ·{" "}
                      {office.member_count} assigned
                    </p>
                  </div>
                  {office.function === "operations_center" && (
                    <Badge tone="info">Operations Center</Badge>
                  )}
                  {office.is_custom && <Badge tone="muted">Custom</Badge>}
                  <Badge tone={office.is_active ? "success" : "muted"}>
                    {office.is_active ? "Active" : "Inactive"}
                  </Badge>
                </div>
                {office.description && <p className="hint">{office.description}</p>}
                {canManage && (
                  <div className="btn-row">
                    <Button size="sm" variant="ghost" onClick={() => openEdit(office)}>
                      Edit
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => toggleOffice(office)}>
                      {office.is_active ? "Deactivate" : "Reactivate"}
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => deleteOffice(office)}>
                      Delete
                    </Button>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card
        title="Personnel assignments"
        actions={
          <Tabs
            tabs={[
              { value: "primary", label: "Primary" },
              { value: "secondary", label: "Secondary" },
            ]}
            active={typeFilter}
            onChange={setTypeFilter}
          />
        }
      >
        {!unitFilter ? (
          <EmptyState message="Select a unit above to see its personnel assignments." />
        ) : assignments.loading && !assignments.data ? (
          <Loading />
        ) : (
          <Table
            rows={assignments.data?.results ?? []}
            empty="No section assignments match the filters."
            columns={[
              { key: "username", label: "Username" },
              { key: "user_name", label: "Name" },
              {
                key: "section_code",
                label: "Section",
                render: (row) => `${row.section_code} — ${row.section_name}`,
              },
              { key: "unit_acronym", label: "Unit" },
              {
                key: "assignment_type",
                label: "Type",
                render: (row) => (
                  <Badge tone={row.assignment_type === "primary" ? "info" : "muted"}>
                    {row.assignment_type}
                  </Badge>
                ),
              },
              {
                key: "is_active",
                label: "Status",
                render: (row) => (
                  <Badge tone={row.is_active ? "success" : "muted"}>
                    {row.is_active ? "Active" : "Ended"}
                  </Badge>
                ),
              },
              {
                key: "actions",
                label: "",
                render: (row) =>
                  canManage && row.is_active ? (
                    <Button size="sm" variant="ghost" onClick={() => endAssignment(row)}>
                      End
                    </Button>
                  ) : null,
              },
            ]}
          />
        )}
      </Card>

      {officeOpen && (
        <Modal
          title={editing ? `Edit ${editing.code}` : "New office or section"}
          onClose={() => setOfficeOpen(false)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setOfficeOpen(false)}>
                Cancel
              </Button>
              <Button onClick={saveOffice} disabled={busy}>
                {busy ? "Saving…" : editing ? "Save changes" : "Create"}
              </Button>
            </>
          }
        >
          <ErrorBanner message={error} />
          <form onSubmit={saveOffice}>
            <div className="form-grid">
              <Field
                label="Unit"
                required
                error={errors.unit}
                hint="The unit selected on the page. Change the unit there to work on another one."
              >
                <Input
                  value={selectedUnit ? selectedUnit.acronym || selectedUnit.name : ""}
                  readOnly
                  disabled
                />
              </Field>
              {!editing && (
                <Field
                  label="Office / Section"
                  required
                  error={errors.catalogue_code}
                  hint="Choose the standard office, or Specify to type your own."
                >
                  <Select
                    value={officeForm.catalogue_code}
                    onChange={(event) =>
                      setOfficeForm({
                        ...officeForm,
                        catalogue_code: event.target.value,
                        kind:
                          event.target.value === OTHER_CODE
                            ? "section"
                            : catalogueRows.find((row) => row.code === event.target.value)?.kind ||
                              officeForm.kind,
                      })
                    }
                    required
                  >
                    <option value="">Select office…</option>
                    {catalogueRows.map((row) => {
                      const taken = !row.custom && existingCodes.has(row.code);
                      return (
                        <option key={row.code} value={row.code} disabled={taken}>
                          {row.custom ? row.name : `${row.code} – ${row.name}`}
                          {taken ? " — already added" : ""}
                        </option>
                      );
                    })}
                  </Select>
                </Field>
              )}
              {editing && (
                <>
                  <Field label="Name" required error={errors.name}>
                    <Input
                      value={officeForm.name}
                      onChange={(event) => setOfficeForm({ ...officeForm, name: event.target.value })}
                      required
                    />
                  </Field>
                  <Field
                    label="Code"
                    required
                    error={errors.code}
                    hint="Unique inside the unit. Standard staff codes (A1-A10, SAFETY) should not be reused."
                  >
                    <Input
                      value={officeForm.code}
                      onChange={(event) => setOfficeForm({ ...officeForm, code: event.target.value })}
                      required
                    />
                  </Field>
                </>
              )}
              {!editing && isSpecify && (
                <Field
                  label="Specify office or section name"
                  required
                  error={errors.custom_name}
                  hint="Required because you chose Specify – Other Office/Section."
                >
                  <Input
                    value={officeForm.custom_name}
                    onChange={(event) =>
                      setOfficeForm({ ...officeForm, custom_name: event.target.value })
                    }
                    required
                  />
                </Field>
              )}
              <Field label="Kind" required>
                <Select
                  value={officeForm.kind}
                  onChange={(event) => setOfficeForm({ ...officeForm, kind: event.target.value })}
                  disabled={Boolean(editing) && !editing.is_custom}
                >
                  <option value="section">Section</option>
                  <option value="office">Office</option>
                </Select>
              </Field>
              <Field
                label="Function"
                error={errors.function}
                hint="Members of an Operations Center office hold routing and coordination authority for this unit only. One active Operations Center per unit."
              >
                <Select
                  value={officeForm.function}
                  onChange={(event) =>
                    setOfficeForm({
                      ...officeForm,
                      function: event.target.value,
                      kind: event.target.value === "operations_center" ? "office" : officeForm.kind,
                    })
                  }
                >
                  <option value="general">General office/section</option>
                  <option value="operations_center">Operations Center</option>
                </Select>
              </Field>
              <Field label="Description" className="span-2">
                <Input
                  value={officeForm.description}
                  onChange={(event) =>
                    setOfficeForm({ ...officeForm, description: event.target.value })
                  }
                />
              </Field>
            </div>
          </form>
        </Modal>
      )}

      {assignOpen && (
        <Modal
          title="Assign personnel to a section"
          onClose={() => setAssignOpen(false)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setAssignOpen(false)}>
                Cancel
              </Button>
              <Button onClick={assign} disabled={busy}>
                {busy ? "Saving…" : "Save assignment"}
              </Button>
            </>
          }
        >
          <ErrorBanner message={error} />
          <form onSubmit={assign}>
            <Field label="Find person" hint="Search by username, name or serial number.">
              <Input
                value={personSearch}
                onChange={(event) => {
                  setPersonSearch(event.target.value);
                  setAssignForm((current) => ({ ...current, user_id: "" }));
                }}
                placeholder="Type to search personnel…"
              />
            </Field>
            <div className="form-grid">
              <Field label="Person" required error={errors.user_id}>
                <Select
                  value={assignForm.user_id}
                  onChange={(event) =>
                    setAssignForm({ ...assignForm, user_id: event.target.value })
                  }
                  required
                >
                  <option value="">Select person…</option>
                  {(personnel.data?.results ?? []).map((person) => (
                    <option key={person.id} value={person.id}>
                      {person.username} — {person.full_name} ({person.unit_acronym || "no unit"})
                    </option>
                  ))}
                </Select>
              </Field>
              <Field
                label="Office / section"
                required
                error={errors.section_id}
                hint="Only sections in the person's own unit are valid."
              >
                <Select
                  value={assignForm.section_id}
                  onChange={(event) =>
                    setAssignForm({ ...assignForm, section_id: event.target.value })
                  }
                  required
                >
                  <option value="">Select section…</option>
                  {sectionsForUnit.map((office) => (
                    <option key={office.id} value={office.id}>
                      {office.code} — {office.name} ({office.unit_acronym})
                    </option>
                  ))}
                </Select>
              </Field>
              <Field
                label="Assignment type"
                required
                hint="One primary and one optional secondary per person."
              >
                <Tabs
                  tabs={[
                    { value: "primary", label: "Primary" },
                    { value: "secondary", label: "Secondary" },
                  ]}
                  active={assignForm.assignment_type}
                  onChange={(value) =>
                    setAssignForm({ ...assignForm, assignment_type: value })
                  }
                />
              </Field>
              <Field label="Notes">
                <Input
                  value={assignForm.notes}
                  onChange={(event) => setAssignForm({ ...assignForm, notes: event.target.value })}
                />
              </Field>
            </div>
          </form>
        </Modal>
      )}
    </>
  );
}
