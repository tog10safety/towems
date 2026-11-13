import { useMemo, useState } from "react";
import { API_BASE, api, qs } from "../api/client.js";
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
  Stat,
  StatusBadge,
  Table,
  Textarea,
} from "../components/ui.jsx";
import SuperAdminDelete from "../components/SuperAdminDelete.jsx";
import { useApi } from "../hooks/useApi.js";
import { number } from "../utils/format.js";

const PAGE_SIZE = 25;

/**
 * The asset classes the register is organised around.
 *
 * Mirrors ``InventoryCategory.KIND_CHOICES`` on the server. Air and mobility
 * assets have their own Resource pages, so they are not classes here.
 */
const CLASSES = [
  { value: "materiel", label: "Materiel" },
  { value: "equipment", label: "Equipment" },
  { value: "age", label: "Aerospace Ground Equipment (AGE)" },
  { value: "firearms", label: "Firearms" },
  { value: "ammunition", label: "Ammunition" },
  { value: "communications", label: "Communications Equipment" },
  { value: "other", label: "Other authorised assets" },
];

const CLASS_LABEL = Object.fromEntries(CLASSES.map((entry) => [entry.value, entry.label]));

const CONDITIONS = [
  { value: "serviceable", label: "Serviceable" },
  { value: "unserviceable", label: "Unserviceable" },
  { value: "maintenance", label: "Under maintenance" },
  { value: "condemned", label: "Condemned" },
];

const MOVEMENTS = [
  { value: "issue", label: "Issue" },
  { value: "return", label: "Return" },
  { value: "receipt", label: "Receipt" },
  { value: "transfer", label: "Transfer" },
  { value: "maintenance", label: "Send to maintenance" },
  { value: "inspection", label: "Record inspection" },
];

const EMPTY_ITEM = {
  name: "",
  category: "",
  description: "",
  asset_tag: "",
  serial_number: "",
  quantity_total: 0,
  unit_of_measure: "unit",
  unit: "",
  storage_location: "",
  serviceability: "serviceable",
  notes: "",
};

const EMPTY_CATEGORY = { name: "", code: "", kind: "materiel", is_sensitive: false };

export default function InventoryPage() {
  const { permissions } = useAuth();
  const canManage = Boolean(permissions.can_manage_inventory);

  const [search, setSearch] = useState("");
  const [assetClass, setAssetClass] = useState("");
  const [category, setCategory] = useState("");
  const [serviceability, setServiceability] = useState("");
  const [page, setPage] = useState(1);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  const [createOpen, setCreateOpen] = useState(false);
  const [itemForm, setItemForm] = useState(EMPTY_ITEM);

  const [categoriesOpen, setCategoriesOpen] = useState(false);
  const [categoryForm, setCategoryForm] = useState(EMPTY_CATEGORY);

  const [movementFor, setMovementFor] = useState(null);
  const [importing, setImporting] = useState(false);
  const [movement, setMovement] = useState({ kind: "issue", quantity: 1, to_unit: "", notes: "" });

  const path = `/inventory/items/${qs({
    search,
    kind: assetClass,
    category,
    serviceability,
    page,
    page_size: PAGE_SIZE,
  })}`;
  const { data, loading, error: loadError, reload } = useApi(path, {
    deps: [search, assetClass, category, serviceability, page],
  });
  const categories = useApi("/inventory/categories/?page_size=200");
  // ``scope=mine`` is the caller's own unit and its authorised subordinates,
  // so a Logistics Officer cannot offer another unit's stores in this picker
  // (the server rejects it anyway). An Administrator's scope is every unit.
  const units = useApi("/units/?is_active=true&scope=mine&page_size=200");
  // Header figures and breakdowns: the server aggregates the same scoped rows
  // the table lists, so a count can never describe something hidden from here.
  const stats = useApi("/inventory/stats/");

  const categoryList = categories.data?.results ?? [];
  const summary = stats.data ?? {};
  const byClass = summary.by_class ?? [];
  const classCounts = Object.fromEntries(byClass.map((entry) => [entry.kind, entry]));
  const maxClassItems = Math.max(1, ...byClass.map((entry) => entry.items || 0));

  /** Reload the table and the header figures together. */
  function refresh() {
    reload();
    stats.reload?.();
  }

  // The category picker is narrowed to the selected class, so materiel cannot
  // be filed under Ammunition by accident.
  const categoryChoices = useMemo(
    () => (assetClass ? categoryList.filter((row) => row.kind === assetClass) : categoryList),
    [categoryList, assetClass]
  );

  function resetPage() {
    setPage(1);
  }

  async function createItem(event) {
    event?.preventDefault?.();
    setBusy(true);
    setError("");
    try {
      await api.post("/inventory/items/", {
        ...itemForm,
        category: Number(itemForm.category),
        unit: itemForm.unit ? Number(itemForm.unit) : null,
        quantity_total: Number(itemForm.quantity_total) || 0,
      });
      setCreateOpen(false);
      setItemForm(EMPTY_ITEM);
      setNotice("Inventory item created.");
      refresh();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function createCategory(event) {
    event?.preventDefault?.();
    setBusy(true);
    setError("");
    try {
      await api.post("/inventory/categories/", {
        ...categoryForm,
        code: categoryForm.code.trim().toUpperCase(),
      });
      setCategoryForm({ ...EMPTY_CATEGORY, kind: categoryForm.kind });
      setNotice("Category added.");
      categories.reload?.();
      refresh();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function removeCategory(row) {
    setBusy(true);
    setError("");
    try {
      await api.del(`/inventory/categories/${row.id}/`);
      setNotice(`Category ${row.code} removed.`);
      categories.reload?.();
      refresh();
    } catch (err) {
      // The server explains exactly why: a category still holding items cannot
      // be removed (PROTECT), and it names the count.
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function submitMovement(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api.post(`/inventory/items/${movementFor.id}/move/`, {
        item: movementFor.id,
        kind: movement.kind,
        quantity: Number(movement.quantity),
        to_unit: movement.to_unit ? Number(movement.to_unit) : null,
        notes: movement.notes,
      });
      setMovementFor(null);
      setMovement({ kind: "issue", quantity: 1, to_unit: "", notes: "" });
      setNotice("Stock movement recorded.");
      refresh();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Inventory &amp; Logistics</h1>
          <p>
            Materiel, equipment, aerospace ground equipment, firearms and ammunition, with their
            condition and full transaction ledger. Air and mobility assets are managed in their
            own Resource pages.
          </p>
        </div>
        {canManage && (
          <div className="btn-row">
            <a
              className="btn btn-ghost btn-md"
              href={`${API_BASE}/inventory/export/${qs({
                search,
                kind: assetClass,
                category,
                serviceability,
              })}`}
            >
              Export
            </a>
            <a className="btn btn-ghost btn-md" href={`${API_BASE}/inventory/template/`}>
              Template
            </a>
            <Button variant="ghost" onClick={() => setImporting(true)}>
              Import
            </Button>
            <Button variant="ghost" onClick={() => setCategoriesOpen(true)}>
              Categories
            </Button>
            <Button
              onClick={() => {
                setItemForm({ ...EMPTY_ITEM, category: categoryChoices[0]?.id ?? "" });
                setCreateOpen(true);
              }}
            >
              Add item
            </Button>
          </div>
        )}
      </div>

      <ErrorBanner message={loadError || error} onRetry={reload} />
      {notice && <div className="alert alert-success">{notice}</div>}

      <div className="stats">
        <Stat
          label="Line items"
          value={number(summary.items ?? 0)}
          hint={`${number(summary.controlled ?? 0)} controlled · ${
            summary.can_view_sensitive ? "firearms and ammunition included" : "controlled stores hidden"
          }`}
        />
        <Stat
          label="Quantity on hand"
          value={number(summary.quantity_available ?? 0)}
          hint={`of ${number(summary.quantity_total ?? 0)} on the books`}
        />
        <Stat
          label={`Low stock (≤ ${summary.low_stock_threshold ?? 5})`}
          value={number(summary.low_stock ?? 0)}
          hint={`${number(summary.out_of_stock ?? 0)} out of stock`}
          tone={summary.out_of_stock ? "danger" : summary.low_stock ? "warning" : undefined}
        />
        <Stat
          label="Movements (30 days)"
          value={number(summary.movements_30d ?? 0)}
          hint="Issues, receipts, transfers and returns"
        />
      </div>

      <div className="grid grid-2">
        <Card title="By asset class">
          <div className="breakdown">
            {byClass.map((entry) => (
              <div className="breakdown-row" key={entry.kind}>
                <span className="breakdown-label" title={entry.label}>
                  {entry.label}
                </span>
                <span className="breakdown-value">
                  {number(entry.items)} · {number(entry.quantity_available)} on hand
                </span>
                <span className="breakdown-track">
                  <span
                    className="breakdown-fill"
                    style={{ width: `${Math.round(((entry.items || 0) / maxClassItems) * 100)}%` }}
                  />
                </span>
              </div>
            ))}
          </div>
        </Card>

        <Card title="By condition">
          <div className="breakdown">
            {(summary.by_condition ?? []).map((entry) => (
              <div className="breakdown-row" key={entry.condition}>
                <span className="breakdown-label">
                  <StatusBadge status={entry.condition} />
                </span>
                <span className="breakdown-value">{number(entry.items)} item(s)</span>
              </div>
            ))}
            {!(summary.by_condition ?? []).length && (
              <p className="hint">No items recorded yet.</p>
            )}
            {(summary.by_unit ?? []).length > 0 && (
              <p className="hint" style={{ marginTop: 10 }}>
                Held across {(summary.by_unit ?? []).map((entry) => entry.acronym || entry.unit).join(", ")}.
              </p>
            )}
          </div>
        </Card>
      </div>

      <Card title="Asset classes">
        <div className="class-chips">
          <button
            type="button"
            className={assetClass === "" ? "class-chip active" : "class-chip"}
            onClick={() => {
              setAssetClass("");
              setCategory("");
              resetPage();
            }}
          >
            All assets{summary.items ? ` · ${summary.items}` : ""}
          </button>
          {CLASSES.map((entry) => (
            <button
              key={entry.value}
              type="button"
              className={assetClass === entry.value ? "class-chip active" : "class-chip"}
              onClick={() => {
                setAssetClass(entry.value);
                setCategory("");
                resetPage();
              }}
            >
              {entry.label}
              {classCounts[entry.value]?.items ? ` · ${classCounts[entry.value].items}` : ""}
            </button>
          ))}
        </div>
      </Card>

      <Card title={assetClass ? CLASS_LABEL[assetClass] : "All inventory items"}>
        <div className="inline-row" style={{ marginBottom: 14 }}>
          <Input
            placeholder="Search item, asset tag, serial…"
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
              resetPage();
            }}
          />
          <Select
            value={category}
            onChange={(event) => {
              setCategory(event.target.value);
              resetPage();
            }}
          >
            <option value="">All categories</option>
            {categoryChoices.map((row) => (
              <option key={row.id} value={row.id}>
                {row.name}
              </option>
            ))}
          </Select>
          <Select
            value={serviceability}
            onChange={(event) => {
              setServiceability(event.target.value);
              resetPage();
            }}
          >
            <option value="">All conditions</option>
            {CONDITIONS.map((entry) => (
              <option key={entry.value} value={entry.value}>
                {entry.label}
              </option>
            ))}
          </Select>
        </div>

        {canManage && categoryList.length === 0 && (
          <div className="alert alert-warning">
            No categories exist yet, so no item can be filed. Add one from{" "}
            <button type="button" className="link-btn" onClick={() => setCategoriesOpen(true)}>
              Categories
            </button>
            .
          </div>
        )}

        {loading && !data ? (
          <Loading />
        ) : (
          <>
            <Table
              rows={data?.results ?? []}
              empty={
                assetClass
                  ? `No ${CLASS_LABEL[assetClass]} recorded yet.`
                  : "No inventory items visible for your role."
              }
              stack
              columns={[
                { key: "name", label: "Item" },
                {
                  key: "class",
                  label: "Class",
                  render: (row) => <Badge tone="info">{CLASS_LABEL[row.category_kind] ?? row.category_kind}</Badge>,
                },
                { key: "category_name", label: "Category" },
                {
                  key: "identity",
                  label: "Asset tag / serial",
                  render: (row) => row.asset_tag || row.serial_number || "—",
                },
                {
                  key: "unit_acronym",
                  label: "Unit",
                  render: (row) => row.unit_acronym || row.unit_name || "—",
                },
                {
                  key: "storage_location",
                  label: "Location",
                  render: (row) => row.storage_location || "—",
                },
                {
                  key: "quantity",
                  label: "Available / Total",
                  render: (row) => {
                    // Same threshold the statistics use, so the tile and the
                    // row never disagree about what "low" means.
                    const available = Number(row.quantity_available ?? 0);
                    const threshold = summary.low_stock_threshold ?? 5;
                    return (
                      <span className="qty-cell">
                        {`${number(row.quantity_available)} / ${number(row.quantity_total)} ${
                          row.unit_of_measure || ""
                        }`.trim()}
                        {available === 0 ? (
                          <Badge tone="danger">out</Badge>
                        ) : available <= threshold ? (
                          <Badge tone="warning">low</Badge>
                        ) : null}
                      </span>
                    );
                  },
                },
                {
                  key: "serviceability",
                  label: "Condition",
                  render: (row) => <StatusBadge status={row.serviceability} />,
                },
                {
                  key: "sensitive",
                  label: "",
                  render: (row) => (row.is_sensitive ? <Badge tone="danger">Controlled</Badge> : null),
                },
                {
                  key: "actions",
                  label: "",
                  render: (row) => (
                    <div className="btn-row">
                      {canManage && (
                        <Button variant="ghost" size="sm" onClick={() => setMovementFor(row)}>
                          Move stock
                        </Button>
                      )}
                      <SuperAdminDelete
                        path={`/inventory/items/${row.id}/?purge=true`}
                        label={`Delete ${row.name} with its ledger`}
                        confirmText={`Delete ${row.name} permanently? Its stock movements go too; a snapshot stays in the audit trail.`}
                        onDone={() => {
                          setNotice(`${row.name} deleted permanently.`);
                          refresh();
                        }}
                        onError={setError}
                      />
                    </div>
                  ),
                },
              ]}
            />
            <Pagination page={page} pageSize={PAGE_SIZE} count={data?.count ?? 0} onPage={setPage} />
          </>
        )}
      </Card>

      {createOpen && (
        <Modal
          title="Add inventory item"
          onClose={() => setCreateOpen(false)}
          wide
          footer={
            <>
              <Button variant="ghost" onClick={() => setCreateOpen(false)}>
                Cancel
              </Button>
              <Button onClick={createItem} disabled={busy}>
                Save item
              </Button>
            </>
          }
        >
          <ErrorBanner message={error} />
          <form onSubmit={createItem}>
            <div className="form-grid">
              <Field label="Name" required>
                <Input
                  value={itemForm.name}
                  onChange={(event) => setItemForm({ ...itemForm, name: event.target.value })}
                  required
                  placeholder="e.g. Ground Power Unit 28V"
                />
              </Field>
              <Field label="Category" required hint="The category sets the asset class and controlled status.">
                <Select
                  value={itemForm.category}
                  onChange={(event) => setItemForm({ ...itemForm, category: event.target.value })}
                  required
                >
                  <option value="">Select category…</option>
                  {CLASSES.map((entry) => {
                    const rows = categoryList.filter((row) => row.kind === entry.value);
                    if (!rows.length) return null;
                    return (
                      <optgroup key={entry.value} label={entry.label}>
                        {rows.map((row) => (
                          <option key={row.id} value={row.id}>
                            {row.name}
                            {row.is_sensitive ? " (controlled)" : ""}
                          </option>
                        ))}
                      </optgroup>
                    );
                  })}
                </Select>
              </Field>
              <Field label="Asset tag">
                <Input
                  value={itemForm.asset_tag}
                  onChange={(event) => setItemForm({ ...itemForm, asset_tag: event.target.value })}
                />
              </Field>
              <Field label="Serial number">
                <Input
                  value={itemForm.serial_number}
                  onChange={(event) =>
                    setItemForm({ ...itemForm, serial_number: event.target.value })
                  }
                />
              </Field>
              <Field label="Quantity">
                <Input
                  type="number"
                  min="0"
                  value={itemForm.quantity_total}
                  onChange={(event) =>
                    setItemForm({ ...itemForm, quantity_total: event.target.value })
                  }
                />
              </Field>
              <Field label="Unit of measure" hint="unit, rounds, litres, sets…">
                <Input
                  value={itemForm.unit_of_measure}
                  onChange={(event) =>
                    setItemForm({ ...itemForm, unit_of_measure: event.target.value })
                  }
                />
              </Field>
              <Field label="Condition">
                <Select
                  value={itemForm.serviceability}
                  onChange={(event) =>
                    setItemForm({ ...itemForm, serviceability: event.target.value })
                  }
                >
                  {CONDITIONS.map((entry) => (
                    <option key={entry.value} value={entry.value}>
                      {entry.label}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Unit">
                <Select
                  value={itemForm.unit}
                  onChange={(event) => setItemForm({ ...itemForm, unit: event.target.value })}
                >
                  <option value="">Unassigned</option>
                  {(units.data?.results ?? []).map((row) => (
                    <option key={row.id} value={row.id}>
                      {row.acronym}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Storage location">
                <Input
                  value={itemForm.storage_location}
                  onChange={(event) =>
                    setItemForm({ ...itemForm, storage_location: event.target.value })
                  }
                  placeholder="e.g. Warehouse B, Bay 3 (Mobility Section)"
                />
              </Field>
              <Field label="Description" className="span-2">
                <Textarea
                  value={itemForm.description}
                  onChange={(event) => setItemForm({ ...itemForm, description: event.target.value })}
                />
              </Field>
              <Field label="Notes" className="span-2">
                <Textarea
                  value={itemForm.notes}
                  onChange={(event) => setItemForm({ ...itemForm, notes: event.target.value })}
                />
              </Field>
            </div>
          </form>
        </Modal>
      )}

      {categoriesOpen && (
        <Modal
          title="Inventory categories"
          onClose={() => setCategoriesOpen(false)}
          wide
          footer={
            <Button variant="ghost" onClick={() => setCategoriesOpen(false)}>
              Close
            </Button>
          }
        >
          <ErrorBanner message={error} />
          <p className="hint">
            A category carries the asset class and whether the items are controlled. A category that
            still holds items cannot be removed.
          </p>

          <Table
            rows={categoryList}
            empty="No categories yet."
            stack
            columns={[
              { key: "code", label: "Code" },
              { key: "name", label: "Name" },
              {
                key: "kind",
                label: "Class",
                render: (row) => CLASS_LABEL[row.kind] ?? row.kind,
              },
              {
                key: "is_sensitive",
                label: "",
                render: (row) => (row.is_sensitive ? <Badge tone="danger">Controlled</Badge> : null),
              },
              {
                key: "actions",
                label: "",
                render: (row) =>
                  canManage ? (
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={busy}
                      onClick={() => removeCategory(row)}
                    >
                      Remove
                    </Button>
                  ) : null,
              },
            ]}
          />

          {canManage && (
            <form onSubmit={createCategory} style={{ marginTop: 18 }}>
              <div className="form-grid">
                <Field label="Code" required hint="Short, unique — e.g. AGE-SUPPORT">
                  <Input
                    value={categoryForm.code}
                    onChange={(event) =>
                      setCategoryForm({ ...categoryForm, code: event.target.value })
                    }
                    required
                  />
                </Field>
                <Field label="Name" required>
                  <Input
                    value={categoryForm.name}
                    onChange={(event) =>
                      setCategoryForm({ ...categoryForm, name: event.target.value })
                    }
                    required
                  />
                </Field>
                <Field label="Asset class" required>
                  <Select
                    value={categoryForm.kind}
                    onChange={(event) =>
                      setCategoryForm({ ...categoryForm, kind: event.target.value })
                    }
                  >
                    {CLASSES.map((entry) => (
                      <option key={entry.value} value={entry.value}>
                        {entry.label}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label="Controlled?" hint="Firearms and ammunition are controlled by default.">
                  <Select
                    value={categoryForm.is_sensitive ? "yes" : "no"}
                    onChange={(event) =>
                      setCategoryForm({ ...categoryForm, is_sensitive: event.target.value === "yes" })
                    }
                  >
                    <option value="no">No</option>
                    <option value="yes">Yes — command and safety roles only</option>
                  </Select>
                </Field>
              </div>
              <Button onClick={createCategory} disabled={busy}>
                Add category
              </Button>
            </form>
          )}
        </Modal>
      )}

      {movementFor && (
        <Modal
          title={`Stock movement — ${movementFor.name}`}
          onClose={() => setMovementFor(null)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setMovementFor(null)}>
                Cancel
              </Button>
              <Button onClick={submitMovement} disabled={busy}>
                Record movement
              </Button>
            </>
          }
        >
          <ErrorBanner message={error} />
          <p className="hint">
            Available: {movementFor.quantity_available} of {movementFor.quantity_total}{" "}
            {movementFor.unit_of_measure}. Movements are applied atomically.
          </p>
          <form onSubmit={submitMovement}>
            <Field label="Movement" required>
              <Select
                value={movement.kind}
                onChange={(event) => setMovement({ ...movement, kind: event.target.value })}
              >
                {MOVEMENTS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Quantity" required>
              <Input
                type="number"
                min="0"
                value={movement.quantity}
                onChange={(event) => setMovement({ ...movement, quantity: event.target.value })}
              />
            </Field>
            {movement.kind === "transfer" && (
              <Field label="Destination unit" required>
                <Select
                  value={movement.to_unit}
                  onChange={(event) => setMovement({ ...movement, to_unit: event.target.value })}
                >
                  <option value="">Select unit…</option>
                  {(units.data?.results ?? []).map((row) => (
                    <option key={row.id} value={row.id}>
                      {row.acronym}
                    </option>
                  ))}
                </Select>
              </Field>
            )}
            <Field label="Notes">
              <Textarea
                value={movement.notes}
                onChange={(event) => setMovement({ ...movement, notes: event.target.value })}
              />
            </Field>
          </form>
        </Modal>
      )}

      {importing && (
        <InventoryImport
          onClose={() => setImporting(false)}
          onImported={(text) => {
            setImporting(false);
            setNotice(text);
            refresh();
          }}
        />
      )}
    </>
  );
}

/**
 * Upload a filled-in template.
 *
 * The server validates every row before writing anything, so a failed upload
 * names the rows to fix and leaves the register untouched.
 */
function InventoryImport({ onClose, onImported }) {
  const [file, setFile] = useState(null);
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    setFailure("");
    setErrors({});
    try {
      const body = new FormData();
      body.append("file", file);
      const result = await api.upload("/inventory/import/", body);
      onImported(result.detail || `Imported ${result.created} item(s).`);
    } catch (err) {
      setFailure(err.message || "The workbook could not be imported.");
      setErrors(err.fieldErrors || {});
    } finally {
      setBusy(false);
    }
  }

  const rowErrors = Object.entries(errors).filter(([key]) => key !== "detail");

  return (
    <Modal
      title="Import inventory from Excel"
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} type="button">
            Cancel
          </Button>
          <Button type="submit" form="inventory-import-form" disabled={busy || !file}>
            {busy ? "Importing…" : "Import workbook"}
          </Button>
        </>
      }
    >
      <form id="inventory-import-form" onSubmit={submit}>
        <ErrorBanner message={failure} />
        <p>
          Download the template, fill in the <strong>Inventory</strong> sheet and upload it here.
          Asset class, category, condition, unit of measure and unit are dropdowns; the import
          re-checks each one. If any row is wrong nothing is saved, and the rows to fix are listed
          below.
        </p>
        <div className="btn-row" style={{ margin: "10px 0 14px" }}>
          <a className="btn btn-ghost btn-sm" href={`${API_BASE}/inventory/template/`}>
            Download template
          </a>
        </div>
        <Field label="Workbook (.xlsx)">
          <input
            className="input"
            type="file"
            accept=".xlsx"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          />
        </Field>
        <p className="hint">
          Up to 2000 rows, 5 MB. Stock on hand is opened as a receipt in the ledger, and an existing
          asset tag is reported rather than overwritten.
        </p>
        {rowErrors.length > 0 && (
          <div className="alert alert-warning" role="alert">
            <strong>Rows needing attention</strong>
            <ul style={{ margin: "6px 0 0 16px" }}>
              {rowErrors.map(([key, value]) => (
                <li key={key}>
                  <strong>{key}</strong>: {value}
                </li>
              ))}
            </ul>
          </div>
        )}
      </form>
    </Modal>
  );
}
