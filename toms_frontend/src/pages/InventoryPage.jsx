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
  Tabs,
  Textarea,
} from "../components/ui.jsx";
import PolStockMap from "../components/PolStockMap.jsx";
import FuelTransferModal from "../components/FuelTransferModal.jsx";
import SuperAdminDelete from "../components/SuperAdminDelete.jsx";
import { useApi } from "../hooks/useApi.js";
import { formatDateTime, number } from "../utils/format.js";

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
  { value: "fuel", label: "Fuel & Lubricants" },
  { value: "communications", label: "Communications Equipment" },
  { value: "other", label: "Other authorised assets" },
];

const CLASS_LABEL = Object.fromEntries(CLASSES.map((entry) => [entry.value, entry.label]));

/**
 * The sections the register is split into, in the order a store is walked.
 *
 * A tab is the shelf an officer works in, not a raw category: POL is the fuel
 * store and the general shelves are grouped by class. ``kinds`` is therefore a
 * list, and the register asks the server for all of them at once. Firearms and
 * ammunition have their own register under Armory Management.
 */
/**
 * The grades the two graded POL categories are stocked in: POL-01 Aviation Fuel
 * is AVGas or Jet A1, POL-02 Automotive Fuel is Diesel or Gasoline. Every other
 * POL category is a single product, so it gets no grade picker at all.
 * Mirrors ``GRADES_BY_CATEGORY`` on the server.
 */
/**
 * What a POL category prefills. Aviation and automotive fuel come from Petron
 * Corporation, so the store opens named after the supplier; every category
 * except aviation fuel is measured in litres (bulk), never in drums.
 */
const PETRON_CATEGORIES = new Set(["POL-01", "POL-02"]);

function polDefaults(code) {
  return {
    name: PETRON_CATEGORIES.has(code) ? "Petron Corporation" : "",
    form: code === "POL-01" ? "drum" : "bulk",
  };
}

const POL_GRADES = {
  "POL-01": [
    { value: "avgas", label: "AVGas" },
    { value: "jet_a1", label: "Jet A1" },
  ],
  "POL-02": [
    { value: "diesel", label: "Diesel" },
    { value: "gasoline", label: "Gasoline" },
  ],
};

// Firearms and ammunition have moved to Armory Management. Listing the
// remaining shelves explicitly keeps the general store from showing armory
// rows through the unfiltered "all stock" request.
const GENERAL_STOCK_KINDS = [
  "materiel",
  "equipment",
  "age",
  "fuel",
  "communications",
  "other",
];

const TABS = [
  { value: "", label: "All stock", kinds: GENERAL_STOCK_KINDS },
  { value: "materiel", label: "Materiel", kinds: ["materiel"] },
  { value: "equipment", label: "Equipment", kinds: ["equipment"] },
  { value: "age", label: "AGE", kinds: ["age"] },
  // Firearms and ammunition are no longer stock shelves: they are maintained
  // in the dedicated Armory Management register.
  { value: "communications", label: "Communications", kinds: ["communications"] },
  {
    value: "fuel",
    label: "POL (Petroleum, Oil & Lubricants)",
    kinds: ["fuel"],
  },
  { value: "other", label: "Other assets", kinds: ["other"] },
];

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

const MOVEMENT_LABEL = {
  ...Object.fromEntries(MOVEMENTS.map((entry) => [entry.value, entry.label])),
  adjustment: "Adjustment",
};

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

/**
 * Fuel is its own store record: a drum bay or a bulk tank, never a materiel row.
 * The quantity counts drums for drum stock and litres for a bulk tank, and the
 * unit of measure is set from that choice rather than typed in.
 */
const EMPTY_FUEL = {
  name: "",
  category: "",
  grade: "",
  form: "drum",
  drum_capacity_litres: 200,
  quantity_total: 0,
  // A POL batch is dated: issued and expiry for every category, testing for
  // aviation fuel only.
  date_issued: "",
  testing_date: "",
  expiry_date: "",
  // Bulk aviation deliveries are quoted by delivery advice number.
  da_number: "",
  // Bulk stock is sited on an airport inside the unit AOR; drums are not.
  airfield: "",
  serviceability: "serviceable",
  notes: "",
};

export default function InventoryPage() {
  const { user, permissions } = useAuth();
  const canManage = Boolean(permissions.can_manage_inventory);
  // Only the System Administrator maintains the category catalogue; everyone
  // else uses the codes they are given.
  const isAdmin = Boolean(permissions.is_admin);

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

  const [fuelOpen, setFuelOpen] = useState(false);
  const [fuelForm, setFuelForm] = useState(EMPTY_FUEL);
  const [airfieldSearch, setAirfieldSearch] = useState("");

  const [categoriesOpen, setCategoriesOpen] = useState(false);
  const [categoryForm, setCategoryForm] = useState(EMPTY_CATEGORY);

  const [movementFor, setMovementFor] = useState(null);
  // The item whose ledger is open for reading.
  const [ledgerFor, setLedgerFor] = useState(null);
  // "board" is the register table; "map" pins the POL-01 stores on their airports.
  const [polView, setPolView] = useState("board");
  const [transferOpen, setTransferOpen] = useState(false);
  const [importing, setImporting] = useState(false);
  const [movement, setMovement] = useState({ kind: "issue", quantity: 1, to_unit: "", notes: "" });

  // The tab is the shelf; its kinds are what the register and export filter
  // by. No kinds means the tab is the whole store.
  const activeTab = TABS.find((entry) => entry.value === assetClass) ?? TABS[0];
  const activeKinds = activeTab.kinds;
  // POL is its own screen: the fuel panel replaces the generic register there,
  // because the same fuel rows in two tables would only invite double counting.
  const showFuel = assetClass === "" || assetClass === "fuel";
  const showRegister = assetClass !== "fuel";

  const path = `/inventory/items/${qs({
    search,
    kind: activeKinds.join(","),
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
  // Each tab monitors its own shelf: the same aggregates, narrowed to the
  // tab's classes. The unscoped call above stays for the tab badges, which
  // must count every shelf even while another one is on screen.
  const tabStats = useApi(`/inventory/stats/${qs({ kind: activeKinds.join(",") })}`, {
    deps: [assetClass],
  });
  // The airports a bulk store may sit on: the server narrows them to the
  // caller's own AOR, and the call only runs while the fuel form is open.
  const fuelLocations = useApi("/inventory/fuel-locations/", { enabled: fuelOpen });

  const categoryList = categories.data?.results ?? [];
  const summary = tabStats.data ?? {};
  const totals = stats.data ?? {};
  // Jet A1 monitoring lives in the same stats payload as the header tiles, so
  // the fuel panel and the register can never disagree about the stock.
  const fuel = summary.fuel ?? {};
  // Drum batches with drums on hand: the only stock a transfer can move.
  const drumBatches = useMemo(
    () =>
      (fuel.rows ?? []).filter(
        (row) => row.form === "drum" && (Number(row.quantity_available) || 0) > 0
      ),
    [fuel.rows]
  );
  // The board lists only stock that sits somewhere and still has some on hand:
  // a record with no location cannot be found on the ground, counted or moved,
  // and a store emptied of its drums or its bulk is no longer stock to monitor
  // (its trail stays on the ledger), so neither is shown as a row here.
  const boardRows = useMemo(
    () =>
      (fuel.rows ?? []).filter(
        (row) =>
          String(row.storage_location || "").trim() &&
          Number(row.quantity_available) > 0
      ),
    [fuel.rows]
  );
  // The POL catalogue in code order: POL-01 Aviation Fuel to POL-09 Cleaning
  // and Solvents. Each category is a distinct stock line in the same panel.
  const polCategories = useMemo(
    () =>
      categoryList
        .filter((row) => row.kind === "fuel")
        .sort((left, right) => String(left.code).localeCompare(String(right.code))),
    [categoryList]
  );
  const selectedPol = polCategories.find(
    (row) => String(row.id) === String(fuelForm.category)
  );
  const gradeChoices = POL_GRADES[selectedPol?.code] ?? [];
  // A bulk aviation tank is dated by its delivery advice instead of a shelf
  // life: it is topped up and drawn down, never expiring as a batch.
  const aviationBulk = selectedPol?.code === "POL-01" && fuelForm.form === "bulk";
  // The officer's own unit: fuel is filed there, and it names the POL dump.
  const unitLabel = user?.unit_acronym || user?.unit_name || "";
  const airfieldChoices = useMemo(() => {
    const rows = fuelLocations.data?.results ?? [];
    const term = airfieldSearch.trim().toLowerCase();
    if (!term) return rows;
    return rows.filter((row) =>
      `${row.name} ${row.code} ${row.municipality} ${row.province}`
        .toLowerCase()
        .includes(term)
    );
  }, [fuelLocations.data, airfieldSearch]);
  const byClass = summary.by_class ?? [];
  const classCounts = Object.fromEntries(
    (totals.by_class ?? []).map((entry) => [entry.kind, entry])
  );
  const maxClassItems = Math.max(1, ...byClass.map((entry) => entry.items || 0));

  /** A tab's figure is the sum of its kinds. */
  function tabCount(entry) {
    if (!entry.kinds.length) return totals.items ?? 0;
    return entry.kinds.reduce((total, kind) => total + (classCounts[kind]?.items || 0), 0);
  }

  /** Reload the table and the header figures together. */
  function refresh() {
    reload();
    stats.reload?.();
    tabStats.reload?.();
  }

  // The category picker is narrowed to the selected class, so materiel cannot
  // be filed under Ammunition by accident.
  const categoryChoices = useMemo(
    () =>
      activeKinds.length
        ? categoryList.filter((row) => activeKinds.includes(row.kind))
        : categoryList,
    [categoryList, activeKinds]
  );

  // Fuel stock is recorded from its own panel, so the materiel form never
  // offers a fuel category and can never file fuel as a numbered item.
  const itemCategories = useMemo(
    () => categoryChoices.filter((row) => row.kind !== "fuel"),
    [categoryChoices]
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

  async function createFuel(event) {
    event?.preventDefault?.();
    if (!selectedPol) return;
    if (gradeChoices.length && !fuelForm.grade) {
      setError("Choose the grade this POL category is stocked in.");
      return;
    }
    const drums = fuelForm.form === "drum";
    const quantity = Number(fuelForm.quantity_total) || 0;
    const airfield = (fuelLocations.data?.results ?? []).find(
      (row) => String(row.id) === String(fuelForm.airfield)
    );
    setError("");
    // A receipt of nothing would close the form without changing the tank, so
    // the quantity is checked here as well as by the required field.
    if (quantity <= 0) {
      setError("Enter the quantity being added.");
      return;
    }
    if (aviationBulk && !fuelForm.da_number.trim()) {
      setError("Enter the DA number for this bulk delivery.");
      return;
    }
    if (!drums && !airfield) {
      setError("Choose the airport that holds this bulk fuel.");
      return;
    }
    setBusy(true);
    try {
      const saved = await api.post("/inventory/items/", {
        name: fuelForm.name,
        category: selectedPol.id,
        quantity_total: quantity,
        unit_of_measure: drums ? "drum" : "litre",
        // No unit is sent: the server files the stock against the officer's own
        // unit. Drums go to that unit's POL dump; bulk stock is sited on the
        // chosen airport inside the unit AOR.
        storage_location: drums
          ? unitLabel
            ? `${unitLabel} POL Dump`
            : "POL Dump"
          : airfield.display,
        serviceability: fuelForm.serviceability,
        notes: fuelForm.notes,
        fuel_grade: fuelForm.grade || "",
        date_issued: fuelForm.date_issued || null,
        testing_date: fuelForm.testing_date || null,
        expiry_date: fuelForm.expiry_date || null,
        da_number: aviationBulk ? fuelForm.da_number : "",
        fuel_form: fuelForm.form,
        drum_capacity_litres: drums
          ? Number(fuelForm.drum_capacity_litres) || null
          : null,
      });
      setFuelOpen(false);
      setFuelForm(EMPTY_FUEL);
      setNotice(
        aviationBulk && saved?.id
          ? `${number(quantity)} L added to ${saved.name}${
              saved.storage_location ? ` at ${saved.storage_location}` : ""
            }${saved.da_number ? ` (DA ${saved.da_number})` : ""}.`
          : "POL stock added."
      );
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
            Materiel, equipment, aerospace ground equipment and POL, with their condition and
            full transaction ledger. Firearms and ammunition are maintained separately in
            Armory Management; air and mobility assets have their own Resource pages.
          </p>
        </div>
        {isAdmin && (
          <div className="inv-head-actions">
            <Button variant="yellow" onClick={() => setCategoriesOpen(true)}>
              Categories
            </Button>
          </div>
        )}
      </div>

      <div className="inv-tabs">
        <Tabs
          tabs={TABS.map((entry) => ({
            value: entry.value,
            label: tabCount(entry)
              ? `${entry.label} · ${tabCount(entry)}`
              : entry.label,
          }))}
          active={assetClass}
          onChange={(value) => {
            setAssetClass(value);
            setCategory("");
            resetPage();
          }}
        />
      </div>

      <ErrorBanner message={loadError || error} onRetry={reload} />
      {notice && <div className="alert alert-success">{notice}</div>}

      {/* One line names the section and carries the tools that act on it: the
          export and template carry the tab's classes and an import is refused
          outside them, so nothing is filed in the wrong shelf by accident. */}
      <div className="inv-monitor-head">
        <p className="inv-monitor-label">
          Monitoring · {assetClass ? activeTab.label : "All stock"}
        </p>
        {canManage && (
          <div className="btn-row">
            <a
              className="btn btn-ghost btn-md"
              href={`${API_BASE}/inventory/export/${qs({
                search,
                kind: activeKinds.join(","),
                category,
                serviceability,
              })}`}
            >
              Export
            </a>
            <a
              className="btn btn-ghost btn-md"
              href={`${API_BASE}/inventory/template/${qs({
                kind: activeKinds.join(","),
              })}`}
            >
              Template
            </a>
            <Button variant="ghost" onClick={() => setImporting(true)}>
              Import
            </Button>
            {/* Adding stock belongs to the section on screen: the form offers
                only that tab's categories. POL has its own "Add fuel stock"
                button on the fuel panel. */}
            {assetClass !== "fuel" && (
              <Button
                onClick={() => {
                  setItemForm({ ...EMPTY_ITEM, category: itemCategories[0]?.id ?? "" });
                  setCreateOpen(true);
                }}
              >
                Add item
              </Button>
            )}
          </div>
        )}
      </div>
      <div className="stats">
        <Stat
          label="Line items"
          value={number(summary.items ?? 0)}
          hint={`${number(summary.controlled ?? 0)} controlled · ${
            summary.can_view_sensitive ? "controlled stores included" : "controlled stores hidden"
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

      {showFuel && (
      <Card
        title="POL stock"
        actions={
          canManage ? (
            <>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setTransferOpen(true)}
                disabled={!drumBatches.length}
              >
                Transfer
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  const first = polCategories[0];
                  const defaults = polDefaults(first?.code);
                  setFuelForm({
                    ...EMPTY_FUEL,
                    category: first?.id ?? "",
                    name: defaults.name,
                    form: defaults.form,
                  });
                  setAirfieldSearch("");
                  setFuelOpen(true);
                }}
              >
                Add fuel stock
              </Button>
            </>
          ) : null
        }
      >
        <div className="pol-view-bar">
          <div
            className="flight-view-switch"
            role="group"
            aria-label="POL stock view"
          >
            <button
              type="button"
              className={`flight-view-button ${polView === "board" ? "is-active" : ""}`}
              aria-pressed={polView === "board"}
              onClick={() => setPolView("board")}
            >
              Board
            </button>
            <button
              type="button"
              className={`flight-view-button ${polView === "map" ? "is-active" : ""}`}
              aria-pressed={polView === "map"}
              onClick={() => setPolView("map")}
            >
              Map View
            </button>
          </div>
          <p className="hint">
            {polView === "map"
              ? "POL-01 Aviation Fuel only, pinned at the airport each store is held on."
              : "Every POL category, in the form it is held."}
          </p>
        </div>

        {polView === "map" ? (
          <PolStockMap rows={fuel.rows ?? []} />
        ) : (
          <>
        <div className="fuel-totals">
          <div className="fuel-total">
            <span className="hint">Available</span>
            <strong>{number(fuel.litres_available ?? 0)} L</strong>
          </div>
          <div className="fuel-total">
            <span className="hint">On the books</span>
            <strong>{number(fuel.litres_total ?? 0)} L</strong>
          </div>
          <div className="fuel-total">
            <span className="hint">Movements (30 days)</span>
            <strong>{number(fuel.movements_30d ?? 0)}</strong>
          </div>
        </div>

        <div className="fuel-forms">
          {(fuel.by_form ?? []).map((entry) => {
            const share = entry.litres_total
              ? Math.round((entry.litres_available / entry.litres_total) * 100)
              : 0;
            return (
              <section className="fuel-form" key={entry.form}>
                <header>
                  <strong>{entry.label}</strong>
                  <span className="hint">
                    {entry.form === "drum"
                      ? `${number(entry.units_available)} of ${number(entry.units_total)} drums`
                      : "Measured in litres"}
                  </span>
                </header>
                <p className="fuel-litres">{number(entry.litres_available)} L</p>
                <span className="breakdown-track">
                  <span className="breakdown-fill" style={{ width: `${share}%` }} />
                </span>
                <p className="hint">
                  {entry.litres_total
                    ? `${number(entry.litres_total)} L on the books · ${share}% remaining`
                    : "Nothing stocked in this form yet."}
                </p>
              </section>
            );
          })}
        </div>

        <Table
          rows={boardRows}
          stack
          empty="No POL stock recorded yet. Add one record for the drums (quantity in drums, plus litres per drum) and one for the bulk tank (quantity in litres)."
          columns={[
            { key: "name", label: "Fuel store" },
            {
              key: "category",
              label: "POL item",
              render: (row) => `${row.category_code} · ${row.category_name}`,
            },
            {
              key: "grade",
              label: "Grade",
              render: (row) => row.grade_label || "—",
            },
            {
              key: "form_label",
              label: "Held as",
              render: (row) =>
                row.form === "drum" && row.drum_capacity_litres
                  ? `${row.form_label} · ${number(row.drum_capacity_litres)} L/drum`
                  : row.form_label,
            },
            {
              key: "quantity",
              label: "Quantity",
              render: (row) =>
                `${number(row.quantity_available)} / ${number(row.quantity_total)} ${
                  row.unit_of_measure || ""
                }`.trim(),
            },
            {
              key: "litres",
              label: "Litres available",
              render: (row) =>
                `${number(row.litres_available)} L of ${number(row.litres_total)} L`,
            },
            {
              key: "storage_location",
              label: "Location",
              render: (row) => row.storage_location || "—",
            },
            {
              key: "actions",
              label: "",
              render: (row) => (
                <div className="btn-row">
                  <Button variant="ghost" size="sm" onClick={() => setLedgerFor(row)}>
                    Trail
                  </Button>
                  {canManage && (
                    <Button variant="ghost" size="sm" onClick={() => setMovementFor(row)}>
                      Move stock
                    </Button>
                  )}
                </div>
              ),
            },
          ]}
        />
          </>
        )}
      </Card>
      )}

      {assetClass === "" && (
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
      )}

      {showRegister && (
      <Card title={assetClass ? activeTab.label : "All inventory items"}>
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
            No categories exist yet, so no item can be filed.{" "}
            {isAdmin ? (
              <>
                Add one from{" "}
                <button
                  type="button"
                  className="link-btn"
                  onClick={() => setCategoriesOpen(true)}
                >
                  Categories
                </button>
                .
              </>
            ) : (
              "Ask the System Administrator to add one."
            )}
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
                  ? `No ${activeTab.label} recorded yet.`
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
                        {row.litres_available != null && (
                          <span className="hint">· {number(row.litres_available)} L</span>
                        )}
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
                      <Button variant="ghost" size="sm" onClick={() => setLedgerFor(row)}>
                        Trail
                      </Button>
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
      )}

      {ledgerFor && (
        <LedgerModal item={ledgerFor} onClose={() => setLedgerFor(null)} />
      )}

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
                  {/* Fuel is added from its own panel, never as a materiel row. */}
                  {CLASSES.filter((entry) => entry.value !== "fuel").map((entry) => {
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
              <Field label="Unit of measure" hint="unit, rounds, sets…">
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

      {fuelOpen && (
        <Modal
          title="Add POL stock"
          onClose={() => setFuelOpen(false)}
          wide
          footer={
            <>
              <Button variant="ghost" type="button" onClick={() => setFuelOpen(false)}>
                Cancel
              </Button>
              <Button
                type="button"
                onClick={createFuel}
                disabled={busy || !polCategories.length}
              >
                Save fuel stock
              </Button>
            </>
          }
        >
          <ErrorBanner message={error} />
          <p className="hint">
            Fuel is a store of its own, not a materiel item. One record describes one
            store: a drum bay (counted as drums, each with its own capacity) or a bulk
            tank (counted in litres). Aviation fuel (POL-01) and automotive fuel
            (POL-02) also record their grade; lubricants and the other POL categories
            are single products. Receipts and issues still write to the same ledger the
            register uses.
          </p>
          {!polCategories.length && (
            <div className="alert alert-warning">
              The POL categories (POL-01 to POL-09) are missing. Restore them under
              Categories before recording POL stock.
            </div>
          )}
          <form onSubmit={createFuel}>
            <div className="form-grid">
              <Field
                label="POL category"
                required
                hint="POL-01 to POL-09; the code says what is being stocked."
              >
                <Select
                  value={fuelForm.category}
                  onChange={(event) => {
                    const value = event.target.value;
                    const next = polCategories.find(
                      (row) => String(row.id) === String(value)
                    );
                    const defaults = polDefaults(next?.code);
                    setFuelForm((current) => ({
                      ...current,
                      category: value,
                      // Only POL-01 and POL-02 carry a grade, and it must
                      // belong to the new category, so it is cleared.
                      grade: "",
                      // Diesel, oils and the rest are measured in litres; a
                      // name the officer typed is kept, a default is replaced.
                      form: defaults.form,
                      name:
                        !current.name || current.name === "Petron Corporation"
                          ? defaults.name
                          : current.name,
                    }));
                  }}
                  required
                >
                  <option value="">Select a POL category…</option>
                  {polCategories.map((row) => (
                    <option key={row.id} value={row.id}>
                      {row.code} · {row.name}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field
                label="Fuel store"
                required
                hint="e.g. POL Store drum bay, Bulk farm tank 1"
              >
                <Input
                  value={fuelForm.name}
                  onChange={(event) =>
                    setFuelForm({ ...fuelForm, name: event.target.value })
                  }
                  required
                  placeholder="Jet A1 drums — POL store"
                />
              </Field>
              {gradeChoices.length > 0 && (
                <Field
                  label="Grade"
                  required
                  hint={`${selectedPol?.name ?? "This category"} is bought in grades.`}
                >
                  <Select
                    value={fuelForm.grade}
                    onChange={(event) =>
                      setFuelForm({ ...fuelForm, grade: event.target.value })
                    }
                    required
                  >
                    <option value="">Select the grade…</option>
                    {gradeChoices.map((entry) => (
                      <option key={entry.value} value={entry.value}>
                        {entry.label}
                      </option>
                    ))}
                  </Select>
                </Field>
              )}
              <Field label="Held as" required hint="This sets the unit the quantity is counted in.">
                <Select
                  value={fuelForm.form}
                  onChange={(event) =>
                    setFuelForm({
                      ...fuelForm,
                      form: event.target.value,
                      // The DA number only exists on a bulk aviation delivery.
                      da_number:
                        event.target.value === "bulk" ? fuelForm.da_number : "",
                    })
                  }
                >
                  <option value="drum">Drums</option>
                  <option value="bulk">Bulk tank (litres)</option>
                </Select>
              </Field>
              {fuelForm.form === "drum" && (
                <Field label="Litres per drum" required hint="e.g. 200 for a standard drum.">
                  <Input
                    type="number"
                    min="1"
                    value={fuelForm.drum_capacity_litres}
                    onChange={(event) =>
                      setFuelForm({
                        ...fuelForm,
                        drum_capacity_litres: event.target.value,
                      })
                    }
                    required
                  />
                </Field>
              )}
              <Field
                label={fuelForm.form === "drum" ? "Drums on hand" : "Litres on hand"}
                required
                hint={
                  fuelForm.form === "drum"
                    ? "Full and part-filled drums both count as one drum."
                    : "Measured in litres; the per-drum capacity is not used."
                }
              >
                <Input
                  type="number"
                  min="1"
                  required
                  placeholder={fuelForm.form === "drum" ? "e.g. 20" : "e.g. 5000"}
                  value={fuelForm.quantity_total}
                  onChange={(event) =>
                    setFuelForm({ ...fuelForm, quantity_total: event.target.value })
                  }
                />
              </Field>
              {fuelForm.form === "drum" ? (
                <Field
                  label="Storage location"
                  hint="Drums are held at your unit's POL dump."
                >
                  <Input
                    readOnly
                    value={unitLabel ? `${unitLabel} POL Dump` : "POL Dump"}
                  />
                </Field>
              ) : (
                <>
                  <Field
                    label="Bulk storage"
                    required
                    className="span-2"
                    hint={
                      fuelLocations.data?.aor_scoped
                        ? `Airports inside the ${unitLabel || "unit"} AOR only.`
                        : "Your unit has no AOR recorded yet, so every airport is listed."
                    }
                  >
                    <Input
                      placeholder="Search airport, code or town…"
                      value={airfieldSearch}
                      onChange={(event) => setAirfieldSearch(event.target.value)}
                    />
                  </Field>
                  <Field label="Airport" required className="span-2">
                    <Select
                      value={fuelForm.airfield}
                      onChange={(event) =>
                        setFuelForm({ ...fuelForm, airfield: event.target.value })
                      }
                      required
                    >
                      <option value="">
                        {airfieldChoices.length
                          ? "Select an airport…"
                          : "No airport found"}
                      </option>
                      {airfieldChoices.map((row) => (
                        <option key={row.id} value={row.id}>
                          {row.display}
                          {row.municipality ? ` · ${row.municipality}` : ""}
                        </option>
                      ))}
                    </Select>
                  </Field>
                  {fuelLocations.loading && (
                    <p className="hint">Loading airports…</p>
                  )}
                  {!fuelLocations.loading && airfieldChoices.length === 0 && (
                    <p className="field-message">
                      No airport is recorded in your unit AOR. Ask the Administrator
                      to assign it, or hold this stock as drums.
                    </p>
                  )}
                </>
              )}
              <Field label="Date issued" hint="When the unit received this batch.">
                <Input
                  type="date"
                  value={fuelForm.date_issued}
                  onChange={(event) =>
                    setFuelForm({ ...fuelForm, date_issued: event.target.value })
                  }
                />
              </Field>
              {selectedPol?.code === "POL-01" && !aviationBulk && (
                <Field label="Testing date" hint="When the batch was last tested.">
                  <Input
                    type="date"
                    value={fuelForm.testing_date}
                    onChange={(event) =>
                      setFuelForm({ ...fuelForm, testing_date: event.target.value })
                    }
                  />
                </Field>
              )}
              {!aviationBulk && (
                <Field label="Expiry date" hint="The batch's shelf life ends here.">
                  <Input
                    type="date"
                    value={fuelForm.expiry_date}
                    onChange={(event) =>
                      setFuelForm({ ...fuelForm, expiry_date: event.target.value })
                    }
                  />
                </Field>
              )}
              {aviationBulk && (
                <Field
                  label="DA number"
                  required
                  hint="The delivery advice behind this bulk receipt."
                >
                  <Input
                    value={fuelForm.da_number}
                    onChange={(event) =>
                      setFuelForm({ ...fuelForm, da_number: event.target.value })
                    }
                    required
                    placeholder="e.g. DA-2026-0012"
                  />
                </Field>
              )}
              <Field label="Condition">
                <Select
                  value={fuelForm.serviceability}
                  onChange={(event) =>
                    setFuelForm({ ...fuelForm, serviceability: event.target.value })
                  }
                >
                  {CONDITIONS.map((entry) => (
                    <option key={entry.value} value={entry.value}>
                      {entry.label}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Notes" className="span-2">
                <Textarea
                  value={fuelForm.notes}
                  onChange={(event) =>
                    setFuelForm({ ...fuelForm, notes: event.target.value })
                  }
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
                  isAdmin ? (
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

          {isAdmin && (
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
                <Field label="Controlled?" hint="Controlled stores are restricted to authorised logistics, command and safety roles.">
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

      {transferOpen && (
        <FuelTransferModal
          batches={drumBatches}
          onClose={() => setTransferOpen(false)}
          onDone={(message) => {
            setTransferOpen(false);
            setNotice(message);
            // The board and the map both read these figures, so the drums
            // appear at their new location as soon as the call returns.
            refresh();
          }}
        />
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
          kinds={activeKinds.join(",")}
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
 * The trail of one item: every receipt, issue and transfer that moved it.
 *
 * The ledger is the explanation for the balance, so it is read exactly as the
 * server recorded it - including the DA number of a bulk delivery.
 */
function LedgerModal({ item, onClose }) {
  const { data, loading, error } = useApi(`/inventory/items/${item.id}/transactions/`);
  const rows = Array.isArray(data) ? data : data?.results ?? [];

  return (
    <Modal
      title={`Trail — ${item.name}`}
      onClose={onClose}
      wide
      footer={
        <Button variant="ghost" type="button" onClick={onClose}>
          Close
        </Button>
      }
    >
      <ErrorBanner message={error} />
      <p className="hint">
        {item.storage_location || "No location recorded"} · every movement that
        explains the balance, newest first.
      </p>
      {loading && !rows.length ? (
        <Loading />
      ) : (
        <Table
          rows={rows}
          stack
          empty="No movements recorded for this item yet."
          columns={[
            {
              key: "created_at",
              label: "When",
              render: (row) => formatDateTime(row.created_at),
            },
            {
              key: "kind",
              label: "Movement",
              render: (row) => MOVEMENT_LABEL[row.kind] ?? row.kind,
            },
            {
              key: "quantity",
              label: "Quantity",
              render: (row) => number(row.quantity),
            },
            {
              key: "reference",
              label: "Reference / DA",
              render: (row) => row.reference || "—",
            },
            {
              key: "actor_name",
              label: "By",
              render: (row) => row.actor_name || "—",
            },
            {
              key: "notes",
              label: "Notes",
              render: (row) => row.notes || "—",
            },
          ]}
        />
      )}
    </Modal>
  );
}

/**
 * Upload a filled-in template.
 *
 * The server validates every row before writing anything, so a failed upload
 * names the rows to fix and leaves the register untouched.
 */
function InventoryImport({ kinds, onClose, onImported }) {
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
      const result = await api.upload(`/inventory/import/${qs({ kind: kinds })}`, body);
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
          <a
            className="btn btn-ghost btn-sm"
            href={`${API_BASE}/inventory/template/${qs({ kind: kinds })}`}
          >
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
