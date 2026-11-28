import { useEffect, useMemo, useState } from "react";
import { api, qs } from "../api/client.js";
import { useAuth } from "../auth/AuthContext.jsx";
import SearchableSelect from "../components/SearchableSelect.jsx";
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
  Stat,
  StatusBadge,
  Table,
  Tabs,
  Textarea,
} from "../components/ui.jsx";
import { useApi } from "../hooks/useApi.js";
import { formatDate, formatDateTime, number, todayManila } from "../utils/format.js";

const FIREARM_TYPES = [
  { value: "rifle", label: "Rifle" },
  { value: "pistol", label: "Pistol" },
];

const FIREARM_CONDITIONS = [
  { value: "serviceable", label: "Serviceable" },
  { value: "unserviceable", label: "Unserviceable" },
  { value: "maintenance", label: "Under maintenance" },
  { value: "condemned", label: "Condemned" },
];

const AMMUNITION_CONDITIONS = [
  { value: "serviceable", label: "Serviceable" },
  { value: "unserviceable", label: "Unserviceable" },
  { value: "quarantined", label: "Quarantined" },
  { value: "expired", label: "Expired" },
];

const REPORT_OPTIONS = [
  {
    slug: "firearms",
    label: "Complete Firearm Inventory",
    detail: "Firearm ID, butt number, serial number, type, condition and custody.",
  },
  {
    slug: "ammunition",
    label: "Ammunition Inventory",
    detail: "Every lot, receipt, issue and current balance.",
  },
  {
    slug: "assignments",
    label: "Personnel Firearm Assignment",
    detail: "Active and historical designated rifles and pistols.",
  },
  {
    slug: "issuances",
    label: "Firearm Issuance Report",
    detail: "Mission-authorised releases, receipts and approving authorities.",
  },
  {
    slug: "returns",
    label: "Firearm Deposit / Return Report",
    detail: "Deposits linked to the original issue and inspection result.",
  },
  {
    slug: "mission-accountability",
    label: "Mission-Based Armory Accountability",
    detail: "Weapons and ammunition issued with Mission Order linkage when applicable.",
  },
  {
    slug: "ammunition-reconciliation",
    label: "Ammunition Consumption and Reconciliation",
    detail: "Issued, returned, consumed and on-hand quantities.",
  },
  {
    slug: "inspections",
    label: "Armory Inspection Report",
    detail: "Firearm condition and complete inspection history.",
  },
  {
    slug: "monthly-summary",
    label: "Monthly Inventory Summary",
    detail: "Holdings, movements and inspections for the chosen month.",
  },
];

const EMPTY_FIREARM = {
  serial_number: "",
  butt_number: "",
  firearm_type: "rifle",
  manufacturer: "",
  model: "",
  caliber: "",
  date_acquired: "",
  acquisition_reference: "",
  condition_status: "serviceable",
  assigned_unit: "",
  storage_location: "Armory",
  date_last_inspected: "",
  remarks: "",
};

const EMPTY_AMMUNITION = {
  nomenclature: "",
  caliber: "",
  ammunition_type: "",
  lot_number: "",
  unit_of_measure: "rounds",
  quantity_received: 0,
  serviceability: "serviceable",
  storage_location: "Armory",
  date_received: "",
  expiration_date: "",
  responsible_custodian: "",
  owning_unit: "",
  remarks: "",
};

function withoutEmpty(payload) {
  return Object.fromEntries(
    Object.entries(payload).filter(
      ([, value]) => value !== "" && value !== null && value !== undefined
    )
  );
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

async function downloadApiFile(path, filename) {
  const response = await api.download(path);
  downloadBlob(await response.blob(), filename);
}

function fieldError(errors, name) {
  return errors?.[name];
}

function personLabel(person) {
  return [person.rank, person.name].filter(Boolean).join(" ") || person.username;
}

function MissionOrderCell({ reference }) {
  return reference ? (
    <span>{reference}</span>
  ) : (
    <Badge tone="muted">Standalone</Badge>
  );
}

/** Server-side personnel lookup, so the picker searches the whole scope. */
async function searchPersonnelOptions(query) {
  const response = await api.get(
    `/armory/personnel/${qs({ search: query, limit: 50 })}`
  );
  return (response.results ?? []).map((person) => ({
    value: person.id,
    label: personLabel(person),
    hint: [person.serial_number, person.unit_acronym || person.unit_name]
      .filter(Boolean)
      .join(" · "),
    person,
  }));
}

function firearmStatusTone(row) {
  if (row.custody_status === "retired") return "muted";
  if (row.custody_status === "issued") return "warning";
  if (row.condition_status !== "serviceable") return "danger";
  return "success";
}

function BarList({ rows, labelKey, valueKey = "count", empty = "No data yet." }) {
  if (!rows?.length) return <EmptyState message={empty} />;
  const max = Math.max(1, ...rows.map((row) => Number(row[valueKey]) || 0));
  return (
    <div className="armory-bars">
      {rows.map((row) => (
        <div className="armory-bar-row" key={`${row[labelKey]}-${row[valueKey]}`}>
          <span className="armory-bar-label">{row[labelKey] || "Unspecified"}</span>
          <span className="armory-bar-track">
            <span
              className="armory-bar-fill"
              style={{ width: `${Math.max(2, ((Number(row[valueKey]) || 0) / max) * 100)}%` }}
            />
          </span>
          <span className="armory-bar-value">{number(row[valueKey])}</span>
        </div>
      ))}
    </div>
  );
}

function ErrorText({ message }) {
  if (!message) return null;
  return <div className="alert alert-danger">{message}</div>;
}

function Notice({ message }) {
  if (!message) return null;
  return <div className="alert alert-success">{message}</div>;
}

export default function ArmoryPage() {
  const { permissions } = useAuth();
  const canView = Boolean(permissions.can_view_armory);
  const canManage = Boolean(permissions.can_manage_armory);
  const canProcess = Boolean(permissions.can_process_armory);

  const [tab, setTab] = useState("overview");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const [firearmSearch, setFirearmSearch] = useState("");
  const [firearmType, setFirearmType] = useState("");
  const [firearmCustody, setFirearmCustody] = useState("");
  const [ammoSearch, setAmmoSearch] = useState("");
  const [ammoCondition, setAmmoCondition] = useState("");
  const [assignmentSearch, setAssignmentSearch] = useState("");
  const [assignmentActive, setAssignmentActive] = useState("true");
  const [txnSearch, setTxnSearch] = useState("");
  const [txnType, setTxnType] = useState("");
  const [txnStatus, setTxnStatus] = useState("");
  const [txnStart, setTxnStart] = useState("");
  const [txnEnd, setTxnEnd] = useState("");
  // A full date is accepted by every report; the monthly summary normalises it
  // to the first day of that month. A bare YYYY-MM would break the transaction
  // reports, which filter on a date field.
  const [reportStart, setReportStart] = useState(todayManila());
  const [reportEnd, setReportEnd] = useState("");

  const [firearmForm, setFirearmForm] = useState(null);
  const [firearmDetail, setFirearmDetail] = useState(null);
  const [inspectFor, setInspectFor] = useState(null);
  const [serialFor, setSerialFor] = useState(null);
  const [transferFor, setTransferFor] = useState(null);
  const [ammoForm, setAmmoForm] = useState(null);
  const [ammoDetail, setAmmoDetail] = useState(null);
  const [adjustFor, setAdjustFor] = useState(null);
  const [assignmentOpen, setAssignmentOpen] = useState(false);
  const [issueFor, setIssueFor] = useState(null);
  const [returnFor, setReturnFor] = useState(null);
  const [txnDetail, setTxnDetail] = useState(null);

  const [missionId, setMissionId] = useState("");
  const [missionSearch, setMissionSearch] = useState("");

  const dashboard = useApi("/armory/dashboard/", { enabled: canView });

  const firearmPath = `/armory/firearms/${qs({
    search: firearmSearch,
    type: firearmType,
    custody: firearmCustody,
    page_size: 200,
  })}`;
  const firearms = useApi(firearmPath, {
    enabled: canView,
    deps: [firearmSearch, firearmType, firearmCustody],
  });

  const ammoPath = `/armory/ammunition/${qs({
    search: ammoSearch,
    serviceability: ammoCondition,
    page_size: 200,
  })}`;
  const ammunition = useApi(ammoPath, {
    enabled: canView,
    deps: [ammoSearch, ammoCondition],
  });
  const availableAmmo = useApi("/armory/ammunition/?available=1&page_size=200", {
    enabled: canProcess,
  });
  // The designation picker must not inherit the register tab's search/type
  // filter. It asks for every stored, serviceable firearm in one call.
  const assignmentFirearms = useApi("/armory/firearms/?available=1&page_size=200", {
    enabled: canManage,
  });

  const assignmentPath = `/armory/assignments/${qs({
    search: assignmentSearch,
    active: assignmentActive,
    page_size: 200,
  })}`;
  const assignments = useApi(assignmentPath, {
    enabled: canView,
    deps: [assignmentSearch, assignmentActive],
  });

  const transactionPath = `/armory/transactions/${qs({
    search: txnSearch,
    type: txnType,
    status: txnStatus,
    start: txnStart,
    end: txnEnd,
    page_size: 200,
  })}`;
  const transactions = useApi(transactionPath, {
    enabled: canView,
    deps: [txnSearch, txnType, txnStatus, txnStart, txnEnd],
  });
  const openIssuances = useApi(
    "/armory/transactions/?type=issuance&status=open&page_size=200",
    { enabled: canProcess }
  );

  const missions = useApi(`/armory/missions/${qs({ search: missionSearch })}`, {
    enabled: canProcess,
    deps: [missionSearch],
  });
  const eligibility = useApi(`/armory/mission-eligibility/${qs({ mission: missionId })}`, {
    enabled: canProcess,
    deps: [missionId],
  });

  const units = useApi("/units/?is_active=true&scope=mine&page_size=200", {
    enabled: canProcess || canManage,
  });
  const myArmory = useApi("/armory/my/");

  const firearmRows = firearms.data?.results ?? [];
  const ammoRows = ammunition.data?.results ?? [];
  const assignmentRows = assignments.data?.results ?? [];
  const transactionRows = transactions.data?.results ?? [];
  const missionRows = missions.data?.results ?? [];
  const unitRows = units.data?.results ?? [];
  const availableAmmoRows = availableAmmo.data?.results ?? [];
  const assignableFirearms = assignmentFirearms.data?.results ?? [];
  const openIssuanceRows = openIssuances.data?.results ?? [];
  const my = myArmory.data ?? { assignments: [], transactions: [], open_issues: [] };

  const tabs = useMemo(() => {
    if (!canView) return [{ value: "my", label: "My Armory" }];
    const rows = [
      { value: "overview", label: "Dashboard" },
      { value: "firearms", label: "Firearms" },
      { value: "ammunition", label: "Ammunition" },
      { value: "assignments", label: "Assignments" },
    ];
    if (canProcess) {
      rows.push({ value: "issue", label: "Issue Firearms" });
      rows.push({ value: "returns", label: "Returns" });
    }
    rows.push({ value: "transactions", label: "Transactions" });
    rows.push({ value: "reports", label: "Reports" });
    rows.push({ value: "my", label: "My Armory" });
    return rows;
  }, [canView, canProcess]);

  useEffect(() => {
    if (!tabs.some((row) => row.value === tab)) {
      setTab(tabs[0].value);
    }
  }, [tab, tabs]);

  function refreshAll() {
    if (canView) {
      dashboard.reload();
      firearms.reload();
      ammunition.reload();
      assignments.reload();
      transactions.reload();
    }
    if (canProcess) {
      availableAmmo.reload();
      openIssuances.reload();
      missions.reload();
      eligibility.reload();
    }
    if (canManage) assignmentFirearms.reload();
    myArmory.reload();
  }

  function finished(message) {
    setError("");
    setNotice(message);
    refreshAll();
  }

  const busyCount =
    (dashboard.loading ? 1 : 0) +
    (firearms.loading ? 1 : 0) +
    (ammunition.loading ? 1 : 0) +
    (transactions.loading ? 1 : 0);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Armory Management</h1>
          <p>
            Firearm and ammunition accountability, personnel designations, issuance
            and deposits — with Mission Order linking when a withdrawal supports a mission.
          </p>
        </div>
        <div className="btn-row">
          {busyCount > 0 && <Loading label="Refreshing…" />}
          <Button variant="ghost" onClick={refreshAll}>
            Refresh
          </Button>
          {canManage && tab === "firearms" && (
            <Button onClick={() => setFirearmForm({ ...EMPTY_FIREARM })}>
              Register firearm
            </Button>
          )}
          {canManage && tab === "ammunition" && (
            <Button onClick={() => setAmmoForm({ ...EMPTY_AMMUNITION })}>
              Register ammunition lot
            </Button>
          )}
          {canManage && tab === "assignments" && (
            <Button onClick={() => setAssignmentOpen(true)}>Designate firearms</Button>
          )}
        </div>
      </div>

      <Notice message={notice} />
      <ErrorBanner message={error} />
      <ErrorBanner message={dashboard.error} onRetry={dashboard.reload} />
      {!canView && (
        <div className="alert alert-warning">
          You can view your designated firearms and your own armory receipts. The unit
          register is visible only to Logistics, command and the Unit Safety Office.
        </div>
      )}

      <Tabs tabs={tabs} active={tab} onChange={setTab} />

      {tab === "overview" && canView && (
        <DashboardTab
          data={dashboard.data}
          loading={dashboard.loading}
          onOpenTransaction={setTxnDetail}
        />
      )}

      {tab === "firearms" && canView && (
        <FirearmsTab
          rows={firearmRows}
          count={firearms.data?.count ?? firearmRows.length}
          loading={firearms.loading}
          error={firearms.error}
          search={firearmSearch}
          setSearch={setFirearmSearch}
          type={firearmType}
          setType={setFirearmType}
          custody={firearmCustody}
          setCustody={setFirearmCustody}
          canManage={canManage}
          onOpen={setFirearmDetail}
          onRegister={() => setFirearmForm({ ...EMPTY_FIREARM })}
          onDownload={() =>
            downloadApiFile(
              "/armory/reports/firearms/?export=xlsx",
              "TOWEMS-armory-firearms.xlsx"
            ).catch((err) => setError(err.message))
          }
        />
      )}

      {tab === "ammunition" && canView && (
        <AmmunitionTab
          rows={ammoRows}
          count={ammunition.data?.count ?? ammoRows.length}
          loading={ammunition.loading}
          error={ammunition.error}
          search={ammoSearch}
          setSearch={setAmmoSearch}
          condition={ammoCondition}
          setCondition={setAmmoCondition}
          canManage={canManage}
          onOpen={setAmmoDetail}
          onRegister={() => setAmmoForm({ ...EMPTY_AMMUNITION })}
          onDownload={() =>
            downloadApiFile(
              "/armory/reports/ammunition/?export=xlsx",
              "TOWEMS-armory-ammunition.xlsx"
            ).catch((err) => setError(err.message))
          }
        />
      )}

      {tab === "assignments" && canView && (
        <AssignmentsTab
          rows={assignmentRows}
          count={assignments.data?.count ?? assignmentRows.length}
          loading={assignments.loading}
          error={assignments.error}
          search={assignmentSearch}
          setSearch={setAssignmentSearch}
          active={assignmentActive}
          setActive={setAssignmentActive}
          canManage={canManage}
          onDesignate={() => setAssignmentOpen(true)}
          onRelease={async (row) => {
            const reason = window.prompt(
              `Release ${row.firearm?.firearm_id} from ${row.personnel_name}? Enter the reason for the audit trail.`,
              "Designation ended"
            );
            if (reason === null) return;
            try {
              await api.post(`/armory/assignments/${row.id}/release/`, { reason });
              finished("Designation released; history retained.");
            } catch (err) {
              setError(err.message);
            }
          }}
        />
      )}

      {tab === "issue" && canProcess && (
        <IssueTab
          missions={missionRows}
          missionSearch={missionSearch}
          setMissionSearch={setMissionSearch}
          missionId={missionId}
          setMissionId={setMissionId}
          eligibility={eligibility}
          onIssue={setIssueFor}
          loading={missions.loading}
          error={missions.error}
        />
      )}

      {tab === "returns" && canProcess && (
        <ReturnsTab
          rows={openIssuanceRows}
          loading={openIssuances.loading}
          error={openIssuances.error}
          onReturn={setReturnFor}
          onOpen={setTxnDetail}
        />
      )}

      {tab === "transactions" && canView && (
        <TransactionsTab
          rows={transactionRows}
          count={transactions.data?.count ?? transactionRows.length}
          loading={transactions.loading}
          error={transactions.error}
          search={txnSearch}
          setSearch={setTxnSearch}
          type={txnType}
          setType={setTxnType}
          status={txnStatus}
          setStatus={setTxnStatus}
          start={txnStart}
          setStart={setTxnStart}
          end={txnEnd}
          setEnd={setTxnEnd}
          onOpen={setTxnDetail}
        />
      )}

      {tab === "reports" && canView && (
        <ReportsTab
          start={reportStart}
          setStart={setReportStart}
          end={reportEnd}
          setEnd={setReportEnd}
          setError={setError}
        />
      )}

      {tab === "my" && (
        <MyArmoryTab
          data={my}
          loading={myArmory.loading}
          error={myArmory.error}
          onReload={myArmory.reload}
          onOpenTransaction={setTxnDetail}
        />
      )}

      {/* ----------------------------- modal layer ----------------------------- */}

      {firearmForm && (
        <FirearmFormModal
          firearm={firearmForm}
          units={unitRows}
          onClose={() => setFirearmForm(null)}
          onSaved={(editing) => {
            setFirearmForm(null);
            setFirearmDetail(null);
            finished(
              editing
                ? "Firearm register updated and audited."
                : "Firearm registered. Its PAF identifier was generated from the serial number."
            );
          }}
        />
      )}

      {firearmDetail && (
        <FirearmDetailModal
          firearm={firearmDetail}
          canManage={canManage}
          onClose={() => setFirearmDetail(null)}
          onEdit={() => {
            setFirearmForm({ ...firearmDetail });
            setFirearmDetail(null);
          }}
          onInspect={() => {
            setInspectFor(firearmDetail);
            setFirearmDetail(null);
          }}
          onSerial={() => {
            setSerialFor(firearmDetail);
            setFirearmDetail(null);
          }}
          onTransfer={() => {
            setTransferFor(firearmDetail);
            setFirearmDetail(null);
          }}
        />
      )}

      {inspectFor && (
        <InspectionModal
          firearm={inspectFor}
          onClose={() => setInspectFor(null)}
          onSaved={() => {
            setInspectFor(null);
            finished("Inspection recorded and firearm condition updated.");
          }}
        />
      )}

      {serialFor && (
        <SerialCorrectionModal
          firearm={serialFor}
          onClose={() => setSerialFor(null)}
          onSaved={() => {
            setSerialFor(null);
            finished("Serial correction recorded; the previous identifier is preserved in the audit history.");
          }}
        />
      )}

      {transferFor && (
        <TransferModal
          firearm={transferFor}
          units={unitRows}
          onClose={() => setTransferFor(null)}
          onSaved={() => {
            setTransferFor(null);
            finished("Firearm transferred and accountability transaction recorded.");
          }}
        />
      )}

      {ammoForm && (
        <AmmunitionFormModal
          ammunition={ammoForm}
          units={unitRows}
          onClose={() => setAmmoForm(null)}
          onSaved={(editing) => {
            setAmmoForm(null);
            setAmmoDetail(null);
            finished(editing ? "Ammunition lot updated." : "Ammunition lot registered with its opening stock balance.");
          }}
        />
      )}

      {ammoDetail && (
        <AmmunitionDetailModal
          ammunition={ammoDetail}
          canManage={canManage}
          onClose={() => setAmmoDetail(null)}
          onEdit={() => {
            setAmmoForm({ ...ammoDetail });
            setAmmoDetail(null);
          }}
          onAdjust={() => {
            setAdjustFor(ammoDetail);
            setAmmoDetail(null);
          }}
        />
      )}

      {adjustFor && (
        <StockAdjustmentModal
          ammunition={adjustFor}
          onClose={() => setAdjustFor(null)}
          onSaved={() => {
            setAdjustFor(null);
            finished("Physical-count adjustment recorded as an audited transaction.");
          }}
        />
      )}

      {assignmentOpen && (
        <AssignmentModal
          firearms={assignableFirearms}
          onClose={() => setAssignmentOpen(false)}
          onSaved={() => {
            setAssignmentOpen(false);
            finished("Designated firearms recorded; previous assignments were closed, not erased.");
          }}
        />
      )}

      {issueFor && (
        <IssueModal
          mission={eligibility.data?.mission}
          person={issueFor}
          ammoLots={availableAmmoRows}
          onClose={() => setIssueFor(null)}
          onSaved={(txn) => {
            setIssueFor(null);
            setTxnDetail(txn);
            finished(`Issuance ${txn.reference_number} recorded. Firearm custody and ammunition stock were updated.`);
          }}
        />
      )}

      {returnFor && (
        <ReturnModal
          issuance={returnFor}
          onClose={() => setReturnFor(null)}
          onSaved={(txn) => {
            setReturnFor(null);
            setTxnDetail(txn);
            finished(`Deposit ${txn.reference_number} recorded against ${txn.return_of_reference || "the original issue"}.`);
          }}
        />
      )}

      {txnDetail && (
        <TransactionDetailModal
          transaction={txnDetail}
          onClose={() => setTxnDetail(null)}
          setError={setError}
        />
      )}
    </>
  );
}

/* ------------------------------------------------------------------------- */
/* Dashboard                                                                  */
/* ------------------------------------------------------------------------- */

function DashboardTab({ data, loading, onOpenTransaction }) {
  if (loading && !data) return <Loading label="Loading armory dashboard…" />;
  if (!data) return <EmptyState message="No armory data is available." />;
  const recent = data.recent_transactions ?? [];
  return (
    <>
      <div className="stats armory-stats">
        <Stat label="Registered firearms" value={number(data.total_firearms)} hint={`${number(data.total_rifles)} rifles · ${number(data.total_pistols)} pistols`} />
        <Stat label="Stored in armory" value={number(data.stored_firearms)} tone="success" />
        <Stat label="Currently issued" value={number(data.issued_firearms)} tone="warning" />
        <Stat label="Pending returns" value={number(data.pending_returns)} tone={data.pending_returns ? "danger" : undefined} hint="Open issuance transactions" />
        <Stat label="Ammunition on hand" value={number(data.total_ammunition_stock)} hint={`${number(data.ammunition_lots)} lot(s)`} />
        <Stat label="Pending inspections" value={number(data.pending_inspections)} tone="warning" hint={`${data.inspection_due_days}-day inspection threshold`} />
        <Stat label="Unserviceable firearms" value={number(data.unserviceable_firearms)} tone={data.unserviceable_firearms ? "danger" : undefined} />
        <Stat label="Transactions (30 days)" value={number(data.transactions_30d)} />
      </div>

      <div className="grid grid-3 armory-overview-grid">
        <Card title="Firearms by type">
          <BarList rows={data.by_type} labelKey="label" />
        </Card>
        <Card title="Custody status">
          <BarList rows={data.by_custody} labelKey="label" />
        </Card>
        <Card title="Condition / serviceability">
          <BarList rows={data.by_condition} labelKey="label" />
        </Card>
      </div>

      <div className="grid grid-2 armory-overview-grid">
        <Card title="Ammunition stock by caliber">
          <BarList
            rows={data.ammunition_by_caliber}
            labelKey="caliber"
            valueKey="stock"
            empty="No ammunition lots registered."
          />
        </Card>
        <Card title="Transactions in the last 30 days">
          <BarList rows={data.transactions_by_type} labelKey="label" />
        </Card>
      </div>

      <Card title="Recent armory transactions">
        <Table
          rows={recent}
          rowKey="id"
          onRowClick={onOpenTransaction}
          empty="No armory transactions recorded yet."
          columns={[
            { key: "reference_number", label: "Reference" },
            { key: "type_label", label: "Type" },
            {
              key: "mission_order_reference",
              label: "Mission Order",
              render: (row) => <MissionOrderCell reference={row.mission_order_reference} />,
            },
            { key: "personnel_name", label: "Personnel" },
            { key: "unit_acronym", label: "Unit" },
            {
              key: "lines",
              label: "Firearms / ammunition",
              render: (row) => <LineSummary lines={row.lines} />,
            },
            { key: "transaction_at", label: "Date & time", render: (row) => formatDateTime(row.transaction_at) },
            { key: "status", label: "Status", render: (row) => <StatusBadge status={row.status} /> },
          ]}
        />
      </Card>
    </>
  );
}

function LineSummary({ lines }) {
  if (!lines?.length) return "—";
  return (
    <span className="armory-line-summary">
      {lines.map((line) =>
        line.item_type === "firearm" ? (
          <span key={line.id}>
            {line.firearm_id_snapshot} / {line.butt_number_snapshot}
          </span>
        ) : (
          <span key={line.id}>
            {line.quantity} {line.unit_of_measure} {line.ammunition_name} (lot {line.lot_number})
          </span>
        )
      )}
    </span>
  );
}

/* ------------------------------------------------------------------------- */
/* Registers                                                                  */
/* ------------------------------------------------------------------------- */

function FirearmsTab({
  rows,
  count,
  loading,
  error,
  search,
  setSearch,
  type,
  setType,
  custody,
  setCustody,
  canManage,
  onOpen,
  onRegister,
  onDownload,
}) {
  return (
    <Card
      title="Firearm register"
      actions={
        <div className="btn-row">
          <Button variant="ghost" size="sm" onClick={onDownload}>
            Export Excel
          </Button>
          {canManage && <Button size="sm" onClick={onRegister}>Register firearm</Button>}
        </div>
      }
    >
      <div className="armory-filters">
        <Input
          placeholder="Search Firearm ID, butt, serial, make, model, caliber or person…"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          aria-label="Search firearms"
        />
        <Select value={type} onChange={(event) => setType(event.target.value)} aria-label="Filter by firearm type">
          <option value="">All firearm types</option>
          {FIREARM_TYPES.map((row) => <option key={row.value} value={row.value}>{row.label}</option>)}
        </Select>
        <Select value={custody} onChange={(event) => setCustody(event.target.value)} aria-label="Filter by custody">
          <option value="">All custody statuses</option>
          <option value="stored">Stored in armory</option>
          <option value="issued">Issued</option>
          <option value="retired">Retired</option>
        </Select>
      </div>
      <ErrorBanner message={error} />
      {loading && !rows.length ? (
        <Loading label="Loading firearms…" />
      ) : (
        <Table
          rows={rows}
          onRowClick={onOpen}
          empty="No firearms are registered in your armory scope."
          columns={[
            { key: "firearm_id", label: "Firearm ID" },
            { key: "butt_number", label: "Butt No." },
            { key: "serial_number", label: "Serial No." },
            { key: "type_label", label: "Type" },
            {
              key: "make",
              label: "Make / model",
              render: (row) => [row.manufacturer, row.model].filter(Boolean).join(" ") || "—",
            },
            { key: "caliber", label: "Caliber" },
            {
              key: "condition_status",
              label: "Condition",
              render: (row) => <StatusBadge status={row.condition_status} />,
            },
            {
              key: "custody_status",
              label: "Custody",
              render: (row) => <Badge tone={firearmStatusTone(row)}>{row.custody_label}</Badge>,
            },
            { key: "assigned_personnel_name", label: "Designated person", render: (row) => row.assigned_personnel_name || "—" },
            { key: "storage_location", label: "Storage" },
            { key: "date_last_inspected", label: "Last inspected", render: (row) => formatDate(row.date_last_inspected) },
          ]}
        />
      )}
      <p className="hint" style={{ marginTop: 12 }}>
        {count} firearm record(s). Click a row for inspection, serial correction, transfer or full details.
      </p>
    </Card>
  );
}

function AmmunitionTab({
  rows,
  count,
  loading,
  error,
  search,
  setSearch,
  condition,
  setCondition,
  canManage,
  onOpen,
  onRegister,
  onDownload,
}) {
  return (
    <Card
      title="Ammunition inventory"
      actions={
        <div className="btn-row">
          <Button variant="ghost" size="sm" onClick={onDownload}>Export Excel</Button>
          {canManage && <Button size="sm" onClick={onRegister}>Register lot</Button>}
        </div>
      }
    >
      <div className="armory-filters">
        <Input
          placeholder="Search ammunition ID, nomenclature, caliber, type, lot or storage…"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          aria-label="Search ammunition"
        />
        <Select value={condition} onChange={(event) => setCondition(event.target.value)} aria-label="Filter by serviceability">
          <option value="">All serviceability</option>
          {AMMUNITION_CONDITIONS.map((row) => <option key={row.value} value={row.value}>{row.label}</option>)}
        </Select>
      </div>
      <ErrorBanner message={error} />
      {loading && !rows.length ? (
        <Loading label="Loading ammunition…" />
      ) : (
        <Table
          rows={rows}
          onRowClick={onOpen}
          empty="No ammunition lots are registered in your armory scope."
          columns={[
            { key: "ammunition_id", label: "Ammunition ID" },
            { key: "nomenclature", label: "Nomenclature" },
            { key: "caliber", label: "Caliber" },
            { key: "ammunition_type", label: "Type" },
            { key: "lot_number", label: "Lot / batch" },
            { key: "quantity_received", label: "Received", render: (row) => number(row.quantity_received) },
            { key: "quantity_issued", label: "Issued", render: (row) => number(row.quantity_issued) },
            { key: "stock_balance", label: "Balance", render: (row) => <strong>{number(row.stock_balance)} {row.unit_of_measure}</strong> },
            { key: "serviceability", label: "Serviceability", render: (row) => <StatusBadge status={row.serviceability} /> },
            { key: "storage_location", label: "Storage" },
            { key: "expiration_date", label: "Expires", render: (row) => formatDate(row.expiration_date) },
            { key: "custodian_name", label: "Custodian", render: (row) => row.custodian_name || "—" },
          ]}
        />
      )}
      <p className="hint" style={{ marginTop: 12 }}>
        {count} ammunition lot(s). Opening a lot lets you inspect the ledger, edit its record or record a physical-count adjustment.
      </p>
    </Card>
  );
}

function AssignmentsTab({
  rows,
  count,
  loading,
  error,
  search,
  setSearch,
  active,
  setActive,
  canManage,
  onDesignate,
  onRelease,
}) {
  return (
    <Card
      title="Personnel firearm designations"
      actions={
        canManage ? (
          <Button size="sm" onClick={onDesignate}>Designate rifle / pistol</Button>
        ) : null
      }
    >
      <div className="armory-filters">
        <Input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search name, rank, service number, unit, Firearm ID, butt or serial…"
          aria-label="Search assignments"
        />
        <Select value={active} onChange={(event) => setActive(event.target.value)} aria-label="Filter assignment history">
          <option value="true">Active designations</option>
          <option value="false">Released designations</option>
          <option value="all">Complete history</option>
        </Select>
      </div>
      <ErrorBanner message={error} />
      {loading && !rows.length ? (
        <Loading label="Loading designations…" />
      ) : (
        <Table
          rows={rows}
          empty="No personnel firearm designations recorded."
          columns={[
            {
              key: "personnel",
              label: "Personnel",
              render: (row) => (
                <span>
                  <strong>{[row.rank, row.personnel_name].filter(Boolean).join(" ")}</strong>
                  <span className="armory-subline">{row.serial_number || "No service number"} · {row.unit_acronym || row.unit_name || "No unit"}</span>
                </span>
              ),
            },
            { key: "type_label", label: "Designation" },
            { key: "firearm_id", label: "Firearm ID", render: (row) => row.firearm?.firearm_id || "—" },
            { key: "butt_number", label: "Butt No.", render: (row) => row.firearm?.butt_number || "—" },
            { key: "serial_number", label: "Serial No.", render: (row) => row.firearm?.serial_number || "—" },
            {
              key: "make",
              label: "Make / model / caliber",
              render: (row) =>
                [row.firearm?.manufacturer, row.firearm?.model, row.firearm?.caliber]
                  .filter(Boolean)
                  .join(" · ") || "—",
            },
            { key: "is_active", label: "Status", render: (row) => <Badge tone={row.is_active ? "success" : "muted"}>{row.is_active ? "Active" : "Released"}</Badge> },
            { key: "assigned_at", label: "Assigned", render: (row) => formatDateTime(row.assigned_at) },
            { key: "released_at", label: "Released", render: (row) => (row.released_at ? formatDateTime(row.released_at) : "—") },
            {
              key: "actions",
              label: "",
              render: (row) =>
                canManage && row.is_active ? (
                  <Button variant="ghost" size="sm" onClick={(event) => { event.stopPropagation(); onRelease(row); }}>
                    Release
                  </Button>
                ) : null,
            },
          ]}
        />
      )}
      <p className="hint" style={{ marginTop: 12 }}>
        {count} designation record(s). A firearm remains physically stored in the armory while designated; designation is not a mission withdrawal.
      </p>
    </Card>
  );
}

/* ------------------------------------------------------------------------- */
/* Mission issue and return                                                   */
/* ------------------------------------------------------------------------- */

function IssueTab({
  missions,
  missionSearch,
  setMissionSearch,
  missionId,
  setMissionId,
  eligibility,
  onIssue,
  loading,
  error,
}) {
  const data = eligibility.data;
  const mission = data?.mission ?? null;

  const columns = [
    {
      key: "personnel",
      label: "Personnel",
      render: (row) => (
        <span>
          <strong>{[row.rank, row.name].filter(Boolean).join(" ")}</strong>
          <span className="armory-subline">{row.serial_number || "No service number"} · {row.branch_of_service || "No BOS"} · {row.unit || "No unit"}</span>
        </span>
      ),
    },
    ...(mission ? [{ key: "mission_role", label: "Mission role" }] : []),
    {
      key: "rifle",
      label: "Designated rifle",
      render: (row) => <DesignatedWeapon weapon={row.rifle} />,
    },
    {
      key: "pistol",
      label: "Designated pistol",
      render: (row) => <DesignatedWeapon weapon={row.pistol} />,
    },
    {
      key: "ready",
      label: "Eligibility",
      render: (row) => (
        <span>
          <Badge tone={row.ready ? "success" : "danger"}>{row.ready ? "Ready" : "Blocked"}</Badge>
          {row.blockers?.length > 0 && <span className="armory-subline">{row.blockers.join(" ")}</span>}
        </span>
      ),
    },
    {
      key: "actions",
      label: "",
      render: (row) =>
        row.ready && data?.order_approved ? (
          <Button size="sm" onClick={() => onIssue(row)}>Issue</Button>
        ) : null,
    },
  ];

  return (
    <>
      <Card title="1 · Mission Order (optional)">
        <div className="armory-filters">
          <Input
            value={missionSearch}
            onChange={(event) => setMissionSearch(event.target.value)}
            placeholder="Search mission number, title or location…"
            aria-label="Search approved missions"
          />
          <Select
            value={missionId}
            onChange={(event) => setMissionId(event.target.value)}
            aria-label="Approved Mission Order"
          >
            <option value="">No Mission Order — standalone issue</option>
            {missions.map((missionRow) => (
              <option key={missionRow.id} value={missionRow.id}>
                {missionRow.mission_number} — {missionRow.title} ({missionRow.unit_acronym || missionRow.category_label})
              </option>
            ))}
          </Select>
        </div>
        <ErrorBanner message={error} />
        {loading && <Loading label="Loading Mission Orders…" />}
        <p className="hint">
          Link an approved, signed Mission Order when the withdrawal supports a mission.
          Leave it unlinked for a standalone armory issue. When a Mission Order is linked,
          only personnel included in it can withdraw against that order.
        </p>
      </Card>

      {eligibility.error && <ErrorBanner message={eligibility.error} />}
      {eligibility.loading && <Loading label="Checking designated firearms and eligibility…" />}

      {data && (
        <>
          <Card
            title={
              mission
                ? `2 · Personnel included in ${mission.mission_number}`
                : "2 · Personnel with designated firearms"
            }
            actions={
              <Badge tone={data.order_approved ? "success" : "danger"}>
                {mission
                  ? data.order_approved
                    ? "Mission Order approved"
                    : "Withdrawal blocked"
                  : "Standalone issue"}
              </Badge>
            }
          >
            {data.order_error && <div className="alert alert-danger">{data.order_error}</div>}
            <Table
              rows={data.personnel ?? []}
              rowKey="personnel"
              empty={
                mission
                  ? "No active personnel are listed in this Mission Order."
                  : "No personnel in your scope have designated firearms."
              }
              columns={columns}
            />
          </Card>
          <div className="alert alert-warning">
            Both the Firearm ID and Butt Number must be physically verified for every weapon.
            All designated firearms must be accounted for before release; ammunition is optional
            but is deducted from the lot balance when entered.
          </div>
        </>
      )}
    </>
  );
}

function DesignatedWeapon({ weapon }) {
  if (!weapon) return <span className="armory-subline">Not designated</span>;
  return (
    <span>
      <strong>{weapon.firearm_id}</strong>
      <span className="armory-subline">
        Butt {weapon.butt_number} · SN {weapon.serial_number} · {[weapon.make, weapon.model].filter(Boolean).join(" ")}
      </span>
      <Badge tone={weapon.available ? "success" : "danger"}>{weapon.custody_label}</Badge>
    </span>
  );
}

function ReturnsTab({ rows, loading, error, onReturn, onOpen }) {
  return (
    <Card title="Open issues awaiting deposit">
      <ErrorBanner message={error} />
      {loading && !rows.length ? (
        <Loading label="Loading open issues…" />
      ) : (
        <Table
          rows={rows}
          onRowClick={onOpen}
          empty="No firearms are currently out of the armory."
          columns={[
            { key: "reference_number", label: "Issue reference" },
            {
              key: "mission_order_reference",
              label: "Mission Order",
              render: (row) => <MissionOrderCell reference={row.mission_order_reference} />,
            },
            {
              key: "personnel",
              label: "Personnel",
              render: (row) => (
                <span>
                  <strong>{row.personnel_name || row.personnel_display}</strong>
                  <span className="armory-subline">{[row.rank, row.branch_of_service, row.unit_acronym].filter(Boolean).join(" · ")}</span>
                </span>
              ),
            },
            {
              key: "lines",
              label: "Outstanding items",
              render: (row) => <LineSummary lines={row.lines} />,
            },
            { key: "releasing_armorer_name", label: "Released by" },
            { key: "transaction_at", label: "Issued", render: (row) => formatDateTime(row.transaction_at) },
            {
              key: "actions",
              label: "",
              render: (row) => (
                <Button size="sm" onClick={(event) => { event.stopPropagation(); onReturn(row); }}>
                  Receive return
                </Button>
              ),
            },
          ]}
        />
      )}
      <p className="hint" style={{ marginTop: 12 }}>
        A return must link to its original issue, verify both identifiers for every firearm and
        reconcile the remaining ammunition.
      </p>
    </Card>
  );
}

/* ------------------------------------------------------------------------- */
/* Transaction register and reports                                           */
/* ------------------------------------------------------------------------- */

function TransactionsTab({
  rows,
  count,
  loading,
  error,
  search,
  setSearch,
  type,
  setType,
  status,
  setStatus,
  start,
  setStart,
  end,
  setEnd,
  onOpen,
}) {
  return (
    <Card title="Transaction and accountability register">
      <div className="armory-filters armory-filters-wrap">
        <Input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search reference, mission, person, unit, firearm, butt, serial or lot…"
          aria-label="Search transactions"
        />
        <Select value={type} onChange={(event) => setType(event.target.value)} aria-label="Filter transaction type">
          <option value="">All transaction types</option>
          <option value="issuance">Issuance</option>
          <option value="return">Return / deposit</option>
          <option value="transfer">Transfer</option>
          <option value="adjustment">Adjustment</option>
        </Select>
        <Select value={status} onChange={(event) => setStatus(event.target.value)} aria-label="Filter transaction status">
          <option value="">All statuses</option>
          <option value="open">Open — firearms out</option>
          <option value="closed">Closed</option>
        </Select>
        <Input type="date" value={start} onChange={(event) => setStart(event.target.value)} aria-label="From date" />
        <Input type="date" value={end} onChange={(event) => setEnd(event.target.value)} aria-label="To date" />
      </div>
      <ErrorBanner message={error} />
      {loading && !rows.length ? (
        <Loading label="Loading transactions…" />
      ) : (
        <Table
          rows={rows}
          onRowClick={onOpen}
          empty="No transactions match these filters."
          columns={[
            { key: "reference_number", label: "Reference" },
            { key: "type_label", label: "Type" },
            {
              key: "mission_order_reference",
              label: "Mission Order",
              render: (row) => <MissionOrderCell reference={row.mission_order_reference} />,
            },
            {
              key: "personnel",
              label: "Personnel",
              render: (row) => row.personnel_name || row.personnel_display || "—",
            },
            { key: "unit_acronym", label: "Unit" },
            {
              key: "lines",
              label: "Firearms / ammunition",
              render: (row) => <LineSummary lines={row.lines} />,
            },
            { key: "releasing_armorer_name", label: "Releasing armorer" },
            { key: "approving_authority_name", label: "Approving authority" },
            { key: "transaction_at", label: "Date & time", render: (row) => formatDateTime(row.transaction_at) },
            { key: "status", label: "Status", render: (row) => <StatusBadge status={row.status} /> },
          ]}
        />
      )}
      <p className="hint" style={{ marginTop: 12 }}>
        {count} immutable transaction record(s). Transaction lines retain the firearm and
        ammunition identity captured at the time of the movement.
      </p>
    </Card>
  );
}

function ReportsTab({ start, setStart, end, setEnd, setError }) {
  const [busy, setBusy] = useState("");
  const [done, setDone] = useState("");

  async function download(slug, exportType) {
    const key = `${slug}-${exportType}`;
    setBusy(key);
    setDone("");
    setError("");
    try {
      const response = await api.download(
        `/armory/reports/${slug}/${qs({ export: exportType, start, end })}`
      );
      const extension = exportType === "xlsx" ? "xlsx" : "pdf";
      downloadBlob(await response.blob(), `TOWEMS-armory-${slug}.${extension}`);
      setDone(`${REPORT_OPTIONS.find((row) => row.slug === slug)?.label} downloaded.`);
    } catch (err) {
      setError(err.message || "Unable to download this report.");
    } finally {
      setBusy("");
    }
  }

  return (
    <>
      <Card title="Reporting period and filters">
        <div className="armory-filters">
          <Field label="Start date" hint="The monthly summary uses the month containing this date.">
            <Input type="date" value={start} onChange={(event) => setStart(event.target.value)} />
          </Field>
          <Field label="End date" hint="Used by transaction reports when supplied.">
            <Input type="date" value={end} onChange={(event) => setEnd(event.target.value)} />
          </Field>
        </div>
        {done && <div className="alert alert-success">{done}</div>}
      </Card>

      <div className="armory-report-grid">
        {REPORT_OPTIONS.map((report) => (
          <Card key={report.slug} title={report.label}>
            <p className="hint armory-report-detail">{report.detail}</p>
            <div className="btn-row">
              <Button
                size="sm"
                onClick={() => download(report.slug, "pdf")}
                disabled={Boolean(busy)}
              >
                {busy === `${report.slug}-pdf` ? "Preparing…" : "Download PDF"}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => download(report.slug, "xlsx")}
                disabled={Boolean(busy)}
              >
                {busy === `${report.slug}-xlsx` ? "Preparing…" : "Excel"}
              </Button>
            </div>
          </Card>
        ))}
      </div>
    </>
  );
}

/* ------------------------------------------------------------------------- */
/* My armory                                                                  */
/* ------------------------------------------------------------------------- */

function MyArmoryTab({ data, loading, error, onReload, onOpenTransaction }) {
  if (loading && !data) return <Loading label="Loading your armory record…" />;
  return (
    <>
      <ErrorBanner message={error} onRetry={onReload} />
      <Card title="My designated firearms">
        {data.assignments?.length ? (
          <div className="grid grid-2 armory-my-grid">
            {data.assignments.map((row) => (
              <div className="armory-weapon-card" key={row.id}>
                <div className="armory-weapon-head">
                  <Badge tone="info">{row.type_label}</Badge>
                  <span className="armory-subline">Designated {formatDate(row.assigned_at)}</span>
                </div>
                <strong className="armory-weapon-id">{row.firearm?.firearm_id}</strong>
                <dl className="armory-key-values">
                  <div><dt>Butt number</dt><dd>{row.firearm?.butt_number}</dd></div>
                  <div><dt>Serial number</dt><dd>{row.firearm?.serial_number}</dd></div>
                  <div><dt>Make / model</dt><dd>{[row.firearm?.manufacturer, row.firearm?.model].filter(Boolean).join(" ") || "—"}</dd></div>
                  <div><dt>Caliber</dt><dd>{row.firearm?.caliber || "—"}</dd></div>
                  <div><dt>Location</dt><dd>{row.firearm?.storage_location}</dd></div>
                  <div><dt>Custody</dt><dd><Badge tone={firearmStatusTone(row.firearm ?? {})}>{row.firearm?.custody_label}</Badge></dd></div>
                </dl>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState message="No rifle or pistol is currently designated to you." />
        )}
      </Card>

      {data.open_issues?.length > 0 && (
        <div className="alert alert-warning">
          <span>
            You have {data.open_issues.length} open armory issue(s). Present the firearm(s) and
            remaining ammunition to the armory for deposit.
          </span>
        </div>
      )}

      <Card title="My armory receipts and history">
        <Table
          rows={data.transactions ?? []}
          onRowClick={onOpenTransaction}
          empty="No issuance or deposit has been recorded for you."
          columns={[
            { key: "reference_number", label: "Reference" },
            { key: "type_label", label: "Type" },
            {
              key: "mission_order_reference",
              label: "Mission Order",
              render: (row) => <MissionOrderCell reference={row.mission_order_reference} />,
            },
            { key: "lines", label: "Items", render: (row) => <LineSummary lines={row.lines} /> },
            { key: "releasing_armorer_name", label: "Releasing armorer" },
            { key: "transaction_at", label: "Date & time", render: (row) => formatDateTime(row.transaction_at) },
            { key: "status", label: "Status", render: (row) => <StatusBadge status={row.status} /> },
          ]}
        />
      </Card>
    </>
  );
}

/* ------------------------------------------------------------------------- */
/* Firearm modals                                                             */
/* ------------------------------------------------------------------------- */

function FirearmFormModal({ firearm, units, onClose, onSaved }) {
  const editing = Boolean(firearm.id);
  const [form, setForm] = useState({
    ...EMPTY_FIREARM,
    assigned_unit: units[0]?.id ?? "",
    ...firearm,
    date_acquired: firearm.date_acquired ?? "",
    date_last_inspected: firearm.date_last_inspected ?? "",
    assigned_unit: firearm.assigned_unit ?? units[0]?.id ?? "",
    storage_location: firearm.storage_location || "Armory",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [errors, setErrors] = useState({});

  function set(name, value) {
    setForm((current) => ({ ...current, [name]: value }));
  }

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setErrors({});
    const payload = withoutEmpty({
      serial_number: form.serial_number.trim(),
      butt_number: form.butt_number.trim(),
      firearm_type: form.firearm_type,
      manufacturer: form.manufacturer,
      model: form.model,
      caliber: form.caliber,
      date_acquired: form.date_acquired,
      acquisition_reference: form.acquisition_reference,
      condition_status: form.condition_status,
      assigned_unit: form.assigned_unit ? Number(form.assigned_unit) : "",
      storage_location: form.storage_location,
      date_last_inspected: form.date_last_inspected,
      remarks: form.remarks,
    });
    try {
      if (editing) {
        delete payload.serial_number;
        delete payload.assigned_unit;
        await api.patch(`/armory/firearms/${firearm.id}/`, payload);
      } else {
        await api.post("/armory/firearms/", payload);
      }
      onSaved(editing);
    } catch (err) {
      setError(err.message);
      setErrors(err.fieldErrors ?? {});
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title={editing ? `Edit ${firearm.firearm_id}` : "Register a firearm"}
      onClose={onClose}
      wide
      footer={
        <div className="btn-row">
          <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button onClick={submit} disabled={busy}>{busy ? "Saving…" : editing ? "Save changes" : "Register firearm"}</Button>
        </div>
      }
    >
      <ErrorText message={error} />
      <form onSubmit={submit}>
        <div className="form-grid form-grid-3">
          <Field label="Serial number" required error={fieldError(errors, "serial_number")} hint={editing ? "Use the authorised serial-correction action to change a serial." : "The PAF-Firearm ID is generated from this serial number."}>
            <Input value={form.serial_number} onChange={(event) => set("serial_number", event.target.value)} required disabled={editing} />
          </Field>
          <Field label="Butt number" required error={fieldError(errors, "butt_number")} hint="Unique within the selected firearm type.">
            <Input value={form.butt_number} onChange={(event) => set("butt_number", event.target.value)} required />
          </Field>
          <Field label="Firearm type" required error={fieldError(errors, "firearm_type")}>
            <Select value={form.firearm_type} onChange={(event) => set("firearm_type", event.target.value)}>
              {FIREARM_TYPES.map((row) => <option key={row.value} value={row.value}>{row.label}</option>)}
            </Select>
          </Field>
          <Field label="Manufacturer / make" error={fieldError(errors, "manufacturer")}>
            <Input value={form.manufacturer} onChange={(event) => set("manufacturer", event.target.value)} />
          </Field>
          <Field label="Model" error={fieldError(errors, "model")}>
            <Input value={form.model} onChange={(event) => set("model", event.target.value)} />
          </Field>
          <Field label="Caliber" error={fieldError(errors, "caliber")}>
            <Input value={form.caliber} onChange={(event) => set("caliber", event.target.value)} />
          </Field>
          <Field label="Date acquired" error={fieldError(errors, "date_acquired")}>
            <Input type="date" value={form.date_acquired} onChange={(event) => set("date_acquired", event.target.value)} />
          </Field>
          <Field label="Acquisition reference" error={fieldError(errors, "acquisition_reference")}>
            <Input value={form.acquisition_reference} onChange={(event) => set("acquisition_reference", event.target.value)} />
          </Field>
          <Field label="Condition / serviceability" error={fieldError(errors, "condition_status")}>
            <Select value={form.condition_status} onChange={(event) => set("condition_status", event.target.value)}>
              {FIREARM_CONDITIONS.map((row) => <option key={row.value} value={row.value}>{row.label}</option>)}
            </Select>
          </Field>
          <Field label="Assigned unit" required error={fieldError(errors, "assigned_unit")} hint={editing ? "Use Transfer to move a registered firearm between units." : "The armory unit accountable for this firearm."}>
            <Select value={form.assigned_unit} onChange={(event) => set("assigned_unit", event.target.value)} disabled={editing} required>
              <option value="">Choose unit…</option>
              {units.map((unit) => <option key={unit.id} value={unit.id}>{unit.acronym ? `${unit.acronym} — ${unit.name}` : unit.name}</option>)}
            </Select>
          </Field>
          <Field label="Storage location" error={fieldError(errors, "storage_location")}>
            <Input value={form.storage_location} onChange={(event) => set("storage_location", event.target.value)} />
          </Field>
          <Field label="Date last inspected" error={fieldError(errors, "date_last_inspected")}>
            <Input type="date" value={form.date_last_inspected} onChange={(event) => set("date_last_inspected", event.target.value)} />
          </Field>
        </div>
        <Field label="Remarks" error={fieldError(errors, "remarks")}>
          <Textarea value={form.remarks} onChange={(event) => set("remarks", event.target.value)} />
        </Field>
        {editing && (
          <div className="alert alert-warning">
            Firearm ID, serial number, custody status and designated personnel are changed only
            through their controlled actions so the accountability trail is preserved.
          </div>
        )}
      </form>
    </Modal>
  );
}

function FirearmDetailModal({ firearm, canManage, onClose, onEdit, onInspect, onSerial, onTransfer }) {
  const history = useApi(`/armory/firearms/${firearm.id}/inspections/`);
  return (
    <Modal
      title={firearm.firearm_id}
      onClose={onClose}
      wide
      footer={
        canManage ? (
          <div className="btn-row">
            <Button variant="ghost" size="sm" onClick={onInspect}>Record inspection</Button>
            <Button variant="ghost" size="sm" onClick={onSerial}>Correct serial</Button>
            <Button variant="ghost" size="sm" onClick={onTransfer}>Transfer</Button>
            <Button size="sm" onClick={onEdit}>Edit register</Button>
          </div>
        ) : null
      }
    >
      <dl className="details armory-details">
        <div className="detail"><dt>Firearm ID</dt><dd>{firearm.firearm_id}</dd></div>
        <div className="detail"><dt>Butt number</dt><dd>{firearm.butt_number}</dd></div>
        <div className="detail"><dt>Serial number</dt><dd>{firearm.serial_number}</dd></div>
        <div className="detail"><dt>Type</dt><dd>{firearm.type_label}</dd></div>
        <div className="detail"><dt>Make</dt><dd>{firearm.manufacturer || "—"}</dd></div>
        <div className="detail"><dt>Model</dt><dd>{firearm.model || "—"}</dd></div>
        <div className="detail"><dt>Caliber</dt><dd>{firearm.caliber || "—"}</dd></div>
        <div className="detail"><dt>Date acquired</dt><dd>{formatDate(firearm.date_acquired)}</dd></div>
        <div className="detail"><dt>Acquisition reference</dt><dd>{firearm.acquisition_reference || "—"}</dd></div>
        <div className="detail"><dt>Condition</dt><dd><StatusBadge status={firearm.condition_status} /></dd></div>
        <div className="detail"><dt>Custody</dt><dd><Badge tone={firearmStatusTone(firearm)}>{firearm.custody_label}</Badge></dd></div>
        <div className="detail"><dt>Designated person</dt><dd>{firearm.assigned_personnel_name || "—"}</dd></div>
        <div className="detail"><dt>Assigned unit</dt><dd>{firearm.assigned_unit_acronym || firearm.assigned_unit_name || "—"}</dd></div>
        <div className="detail"><dt>Storage location</dt><dd>{firearm.storage_location}</dd></div>
        <div className="detail"><dt>Last inspected</dt><dd>{formatDate(firearm.date_last_inspected)}</dd></div>
        <div className="detail"><dt>Registered by</dt><dd>{firearm.registered_by_name || "—"}</dd></div>
      </dl>
      {firearm.remarks && <p>{firearm.remarks}</p>}
      <h3 className="armory-section-title">Inspection history</h3>
      {history.loading ? (
        <Loading label="Loading inspections…" />
      ) : (
        <Table
          rows={history.data ?? []}
          empty="No inspections recorded."
          columns={[
            { key: "inspected_at", label: "Inspected", render: (row) => formatDateTime(row.inspected_at) },
            { key: "condition_label", label: "Condition" },
            { key: "inspected_by_name", label: "Inspector" },
            { key: "remarks", label: "Remarks" },
            { key: "next_inspection_date", label: "Next due", render: (row) => formatDate(row.next_inspection_date) },
          ]}
        />
      )}
    </Modal>
  );
}

function InspectionModal({ firearm, onClose, onSaved }) {
  const [form, setForm] = useState({ condition_status: firearm.condition_status, remarks: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit() {
    setBusy(true);
    setError("");
    try {
      await api.post(`/armory/firearms/${firearm.id}/inspect/`, form);
      onSaved();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title={`Inspect ${firearm.firearm_id}`}
      onClose={onClose}
      footer={
        <div className="btn-row">
          <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button onClick={submit} disabled={busy}>{busy ? "Recording…" : "Record inspection"}</Button>
        </div>
      }
    >
      <ErrorText message={error} />
      <dl className="armory-key-values armory-key-values-inline">
        <div><dt>Butt number</dt><dd>{firearm.butt_number}</dd></div>
        <div><dt>Serial number</dt><dd>{firearm.serial_number}</dd></div>
      </dl>
      <Field label="Condition / serviceability" required>
        <Select value={form.condition_status} onChange={(event) => setForm({ ...form, condition_status: event.target.value })}>
          {FIREARM_CONDITIONS.map((row) => <option key={row.value} value={row.value}>{row.label}</option>)}
        </Select>
      </Field>
      <Field label="Inspection remarks">
        <Textarea value={form.remarks} onChange={(event) => setForm({ ...form, remarks: event.target.value })} />
      </Field>
      <p className="hint">The inspection is time-stamped when recorded and updates the firearm's condition and last-inspected date.</p>
    </Modal>
  );
}

function SerialCorrectionModal({ firearm, onClose, onSaved }) {
  const [serial, setSerial] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit() {
    setBusy(true);
    setError("");
    try {
      await api.post(`/armory/firearms/${firearm.id}/correct-serial/`, {
        serial_number: serial,
        reason,
      });
      onSaved();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title={`Correct serial · ${firearm.firearm_id}`}
      onClose={onClose}
      footer={
        <div className="btn-row">
          <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button onClick={submit} disabled={busy || !serial.trim() || !reason.trim()}>
            {busy ? "Correcting…" : "Correct serial"}
          </Button>
        </div>
      }
    >
      <ErrorText message={error} />
      <div className="alert alert-warning">
        A serial correction changes the generated Firearm ID and is refused while the firearm is
        issued. The old serial, old ID, reason and operator are kept permanently.
      </div>
      <dl className="armory-key-values armory-key-values-inline">
        <div><dt>Current serial</dt><dd>{firearm.serial_number}</dd></div>
        <div><dt>Current Firearm ID</dt><dd>{firearm.firearm_id}</dd></div>
        <div><dt>Butt number</dt><dd>{firearm.butt_number}</dd></div>
      </dl>
      <Field label="Corrected serial number" required>
        <Input value={serial} onChange={(event) => setSerial(event.target.value)} />
      </Field>
      <Field label="Reason for correction" required>
        <Textarea value={reason} onChange={(event) => setReason(event.target.value)} />
      </Field>
    </Modal>
  );
}

function TransferModal({ firearm, units, onClose, onSaved }) {
  const [form, setForm] = useState({
    assigned_unit: "",
    storage_location: "Armory",
    reason: "",
    supporting_documents: "",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit() {
    setBusy(true);
    setError("");
    try {
      await api.post(`/armory/firearms/${firearm.id}/transfer/`, {
        ...withoutEmpty(form),
        assigned_unit: Number(form.assigned_unit),
      });
      onSaved();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title={`Transfer ${firearm.firearm_id}`}
      onClose={onClose}
      footer={
        <div className="btn-row">
          <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button onClick={submit} disabled={busy || !form.assigned_unit || !form.reason.trim()}>
            {busy ? "Transferring…" : "Transfer firearm"}
          </Button>
        </div>
      }
    >
      <ErrorText message={error} />
      <dl className="armory-key-values armory-key-values-inline">
        <div><dt>Current unit</dt><dd>{firearm.assigned_unit_acronym || firearm.assigned_unit_name}</dd></div>
        <div><dt>Butt / serial</dt><dd>{firearm.butt_number} / {firearm.serial_number}</dd></div>
      </dl>
      <Field label="Destination unit" required hint="The destination must be inside your authorised armory scope.">
        <Select value={form.assigned_unit} onChange={(event) => setForm({ ...form, assigned_unit: event.target.value })}>
          <option value="">Choose unit…</option>
          {units.filter((unit) => unit.id !== firearm.assigned_unit).map((unit) => (
            <option key={unit.id} value={unit.id}>{unit.acronym ? `${unit.acronym} — ${unit.name}` : unit.name}</option>
          ))}
        </Select>
      </Field>
      <Field label="Storage location at destination">
        <Input value={form.storage_location} onChange={(event) => setForm({ ...form, storage_location: event.target.value })} />
      </Field>
      <Field label="Reason for transfer" required>
        <Textarea value={form.reason} onChange={(event) => setForm({ ...form, reason: event.target.value })} />
      </Field>
      <Field label="Supporting document reference">
        <Input value={form.supporting_documents} onChange={(event) => setForm({ ...form, supporting_documents: event.target.value })} />
      </Field>
    </Modal>
  );
}

/* ------------------------------------------------------------------------- */
/* Ammunition modals                                                          */
/* ------------------------------------------------------------------------- */

function AmmunitionFormModal({ ammunition, units, onClose, onSaved }) {
  const editing = Boolean(ammunition.id);
  const [initialCustodian] = useState(() =>
    ammunition.responsible_custodian && ammunition.custodian_name
      ? {
          value: String(ammunition.responsible_custodian),
          label: ammunition.custodian_name,
        }
      : null
  );
  const [form, setForm] = useState({
    ...EMPTY_AMMUNITION,
    owning_unit: units[0]?.id ?? "",
    ...ammunition,
    owning_unit: ammunition.owning_unit ?? units[0]?.id ?? "",
    quantity_received: ammunition.quantity_received ?? 0,
    date_received: ammunition.date_received ?? "",
    expiration_date: ammunition.expiration_date ?? "",
    responsible_custodian: ammunition.responsible_custodian ?? "",
    storage_location: ammunition.storage_location || "Armory",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [errors, setErrors] = useState({});

  function set(name, value) {
    setForm((current) => ({ ...current, [name]: value }));
  }

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setErrors({});
    const payload = withoutEmpty({
      nomenclature: form.nomenclature.trim(),
      caliber: form.caliber.trim(),
      ammunition_type: form.ammunition_type.trim(),
      lot_number: form.lot_number.trim(),
      unit_of_measure: form.unit_of_measure.trim(),
      serviceability: form.serviceability,
      storage_location: form.storage_location,
      date_received: form.date_received,
      expiration_date: form.expiration_date,
      responsible_custodian: form.responsible_custodian ? Number(form.responsible_custodian) : "",
      owning_unit: form.owning_unit ? Number(form.owning_unit) : "",
      remarks: form.remarks,
    });
    if (!editing) payload.quantity_received = Number(form.quantity_received) || 0;
    try {
      if (editing) {
        await api.patch(`/armory/ammunition/${ammunition.id}/`, payload);
      } else {
        await api.post("/armory/ammunition/", payload);
      }
      onSaved(editing);
    } catch (err) {
      setError(err.message);
      setErrors(err.fieldErrors ?? {});
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title={editing ? `Edit ${ammunition.ammunition_id}` : "Register an ammunition lot"}
      onClose={onClose}
      wide
      footer={
        <div className="btn-row">
          <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button onClick={submit} disabled={busy}>{busy ? "Saving…" : editing ? "Save changes" : "Register lot"}</Button>
        </div>
      }
    >
      <ErrorText message={error} />
      <form onSubmit={submit}>
        <div className="form-grid form-grid-3">
          <Field label="Nomenclature" required error={fieldError(errors, "nomenclature")}>
            <Input value={form.nomenclature} onChange={(event) => set("nomenclature", event.target.value)} required />
          </Field>
          <Field label="Caliber" required error={fieldError(errors, "caliber")}>
            <Input value={form.caliber} onChange={(event) => set("caliber", event.target.value)} required />
          </Field>
          <Field label="Ammunition type" required error={fieldError(errors, "ammunition_type")}>
            <Input value={form.ammunition_type} onChange={(event) => set("ammunition_type", event.target.value)} required />
          </Field>
          <Field label="Lot / batch number" required error={fieldError(errors, "lot_number")}>
            <Input value={form.lot_number} onChange={(event) => set("lot_number", event.target.value)} required />
          </Field>
          <Field label="Unit of measurement" required error={fieldError(errors, "unit_of_measure")}>
            <Input value={form.unit_of_measure} onChange={(event) => set("unit_of_measure", event.target.value)} required />
          </Field>
          <Field label="Quantity received" required={!editing} error={fieldError(errors, "quantity_received")} hint={editing ? "Use a stock adjustment to change the balance." : "This becomes the opening stock balance."}>
            <Input
              type="number"
              min="0"
              value={form.quantity_received}
              onChange={(event) => set("quantity_received", event.target.value)}
              disabled={editing}
            />
          </Field>
          <Field label="Serviceability" error={fieldError(errors, "serviceability")}>
            <Select value={form.serviceability} onChange={(event) => set("serviceability", event.target.value)}>
              {AMMUNITION_CONDITIONS.map((row) => <option key={row.value} value={row.value}>{row.label}</option>)}
            </Select>
          </Field>
          <Field label="Owning unit" required error={fieldError(errors, "owning_unit")} hint={editing ? "A lot cannot change owning unit." : "The armory accountable for this lot."}>
            <Select value={form.owning_unit} onChange={(event) => set("owning_unit", event.target.value)} disabled={editing} required>
              <option value="">Choose unit…</option>
              {units.map((unit) => <option key={unit.id} value={unit.id}>{unit.acronym ? `${unit.acronym} — ${unit.name}` : unit.name}</option>)}
            </Select>
          </Field>
          <Field label="Storage location" error={fieldError(errors, "storage_location")}>
            <Input value={form.storage_location} onChange={(event) => set("storage_location", event.target.value)} />
          </Field>
          <Field label="Date received" error={fieldError(errors, "date_received")}>
            <Input type="date" value={form.date_received} onChange={(event) => set("date_received", event.target.value)} />
          </Field>
          <Field label="Expiration date" error={fieldError(errors, "expiration_date")}>
            <Input type="date" value={form.expiration_date} onChange={(event) => set("expiration_date", event.target.value)} />
          </Field>
          <Field label="Responsible custodian" error={fieldError(errors, "responsible_custodian")}>
            <SearchableSelect
              value={form.responsible_custodian}
              onChange={(value) => set("responsible_custodian", value)}
              search={searchPersonnelOptions}
              selectedOption={initialCustodian}
              placeholder="Search name, rank, service number or unit…"
              emptyMessage="No matching personnel."
            />
          </Field>
        </div>
        <Field label="Remarks" error={fieldError(errors, "remarks")}>
          <Textarea value={form.remarks} onChange={(event) => set("remarks", event.target.value)} />
        </Field>
      </form>
    </Modal>
  );
}

function AmmunitionDetailModal({ ammunition, canManage, onClose, onEdit, onAdjust }) {
  return (
    <Modal
      title={ammunition.ammunition_id}
      onClose={onClose}
      wide
      footer={
        canManage ? (
          <div className="btn-row">
            <Button variant="ghost" size="sm" onClick={onAdjust}>Record stock adjustment</Button>
            <Button size="sm" onClick={onEdit}>Edit lot record</Button>
          </div>
        ) : null
      }
    >
      <dl className="details armory-details">
        <div className="detail"><dt>Ammunition ID</dt><dd>{ammunition.ammunition_id}</dd></div>
        <div className="detail"><dt>Nomenclature</dt><dd>{ammunition.nomenclature}</dd></div>
        <div className="detail"><dt>Caliber</dt><dd>{ammunition.caliber}</dd></div>
        <div className="detail"><dt>Type</dt><dd>{ammunition.ammunition_type}</dd></div>
        <div className="detail"><dt>Lot / batch</dt><dd>{ammunition.lot_number}</dd></div>
        <div className="detail"><dt>Unit of measure</dt><dd>{ammunition.unit_of_measure}</dd></div>
        <div className="detail"><dt>Quantity received</dt><dd>{number(ammunition.quantity_received)}</dd></div>
        <div className="detail"><dt>Quantity issued</dt><dd>{number(ammunition.quantity_issued)}</dd></div>
        <div className="detail"><dt>Current stock</dt><dd><strong>{number(ammunition.stock_balance)} {ammunition.unit_of_measure}</strong></dd></div>
        <div className="detail"><dt>Serviceability</dt><dd><StatusBadge status={ammunition.serviceability} /></dd></div>
        <div className="detail"><dt>Storage location</dt><dd>{ammunition.storage_location}</dd></div>
        <div className="detail"><dt>Date received</dt><dd>{formatDate(ammunition.date_received)}</dd></div>
        <div className="detail"><dt>Expiration date</dt><dd>{formatDate(ammunition.expiration_date)}</dd></div>
        <div className="detail"><dt>Responsible custodian</dt><dd>{ammunition.custodian_name || "—"}</dd></div>
        <div className="detail"><dt>Owning unit</dt><dd>{ammunition.owning_unit_acronym || ammunition.owning_unit_name}</dd></div>
      </dl>
      {ammunition.remarks && <p>{ammunition.remarks}</p>}
    </Modal>
  );
}

function StockAdjustmentModal({ ammunition, onClose, onSaved }) {
  const [balance, setBalance] = useState(ammunition.stock_balance);
  const [reason, setReason] = useState("");
  const [documents, setDocuments] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit() {
    setBusy(true);
    setError("");
    try {
      await api.post(`/armory/ammunition/${ammunition.id}/adjust/`, {
        stock_balance: Number(balance),
        reason,
        supporting_documents: documents,
      });
      onSaved();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title={`Adjust stock · ${ammunition.ammunition_id}`}
      onClose={onClose}
      footer={
        <div className="btn-row">
          <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button onClick={submit} disabled={busy || !reason.trim() || Number(balance) < 0}>
            {busy ? "Recording…" : "Record adjustment"}
          </Button>
        </div>
      }
    >
      <ErrorText message={error} />
      <p className="hint">
        A physical count replaces the current balance and creates an adjustment transaction with
        your reason and identity. The quantity received and issued totals remain as ledger history.
      </p>
      <dl className="armory-key-values armory-key-values-inline">
        <div><dt>Current balance</dt><dd>{number(ammunition.stock_balance)} {ammunition.unit_of_measure}</dd></div>
        <div><dt>Lot</dt><dd>{ammunition.lot_number}</dd></div>
      </dl>
      <Field label="Counted stock balance" required>
        <Input type="number" min="0" value={balance} onChange={(event) => setBalance(event.target.value)} />
      </Field>
      <Field label="Reason for adjustment" required>
        <Textarea value={reason} onChange={(event) => setReason(event.target.value)} />
      </Field>
      <Field label="Supporting document reference">
        <Input value={documents} onChange={(event) => setDocuments(event.target.value)} />
      </Field>
    </Modal>
  );
}

/* ------------------------------------------------------------------------- */
/* Assignment modal                                                           */
/* ------------------------------------------------------------------------- */

function AssignmentModal({ firearms, onClose, onSaved }) {
  const [personnelId, setPersonnelId] = useState("");
  const [selectedPerson, setSelectedPerson] = useState(null);
  const [rifleId, setRifleId] = useState("");
  const [pistolId, setPistolId] = useState("");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const rifleOptions = useMemo(
    () =>
      firearms
        .filter((row) => row.firearm_type === "rifle" && row.custody_status === "stored" && row.condition_status === "serviceable")
        .map((row) => ({
          value: row.firearm_id,
          label: `${row.firearm_id} · Butt ${row.butt_number}`,
          hint: [row.manufacturer, row.model, row.caliber].filter(Boolean).join(" · "),
        })),
    [firearms]
  );
  const pistolOptions = useMemo(
    () =>
      firearms
        .filter((row) => row.firearm_type === "pistol" && row.custody_status === "stored" && row.condition_status === "serviceable")
        .map((row) => ({
          value: row.firearm_id,
          label: `${row.firearm_id} · Butt ${row.butt_number}`,
          hint: [row.manufacturer, row.model, row.caliber].filter(Boolean).join(" · "),
        })),
    [firearms]
  );

  useEffect(() => {
    if (!selectedPerson) return;
    const currentRifle = selectedPerson.assignments?.find((row) => row.assignment_type === "rifle");
    const currentPistol = selectedPerson.assignments?.find((row) => row.assignment_type === "pistol");
    setRifleId(currentRifle?.firearm_id ?? "");
    setPistolId(currentPistol?.firearm_id ?? "");
  }, [selectedPerson]);

  async function submit() {
    setBusy(true);
    setError("");
    try {
      await api.post("/armory/assignments/assign/", {
        personnel: Number(personnelId),
        rifle_firearm_id: rifleId,
        pistol_firearm_id: pistolId,
        notes,
      });
      onSaved();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title="Designate a rifle and pistol"
      onClose={onClose}
      wide
      footer={
        <div className="btn-row">
          <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button onClick={submit} disabled={busy || !personnelId || (!rifleId && !pistolId)}>
            {busy ? "Saving…" : "Save designation"}
          </Button>
        </div>
      }
    >
      <ErrorText message={error} />
      <div className="alert alert-warning">
        A designation is accountability only. The firearm stays in the armory until a separate
        armory issue authorises its release.
      </div>
      <Field label="Personnel" required hint="Search by name, rank, service number or unit.">
        <SearchableSelect
          value={personnelId}
          onChange={(next) => {
            setPersonnelId(next);
            if (!next) setSelectedPerson(null);
          }}
          onSelectOption={(option) => setSelectedPerson(option?.person ?? null)}
          search={searchPersonnelOptions}
          placeholder="Search name, rank, service number or unit…"
          emptyMessage="No matching personnel in your scope."
        />
      </Field>
      {selectedPerson && (
        <div className="armory-selected-person">
          <strong>{[selectedPerson.rank, selectedPerson.name].filter(Boolean).join(" ")}</strong>
          <span className="armory-subline">
            {selectedPerson.serial_number || "No service number"} · {selectedPerson.branch_of_service || "No BOS"} · {selectedPerson.unit_acronym || selectedPerson.unit_name}
          </span>
        </div>
      )}
      <div className="grid grid-2">
        <Field label="Designated rifle" hint={rifleOptions.length ? "Leave blank to keep the current rifle when only replacing the pistol." : "No serviceable rifle is registered."}>
          <SearchableSelect
            value={rifleId}
            onChange={setRifleId}
            options={rifleOptions}
            placeholder="Choose rifle…"
            emptyMessage="No serviceable rifle in scope."
            disabled={!selectedPerson || !rifleOptions.length}
          />
        </Field>
        <Field label="Designated pistol" hint={pistolOptions.length ? "Leave blank to keep the current pistol when only replacing the rifle." : "No serviceable pistol is registered."}>
          <SearchableSelect
            value={pistolId}
            onChange={setPistolId}
            options={pistolOptions}
            placeholder="Choose pistol…"
            emptyMessage="No serviceable pistol in scope."
            disabled={!selectedPerson || !pistolOptions.length}
          />
        </Field>
      </div>
      <Field label="Reassignment notes">
        <Textarea value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="Reason for the designation or reassignment…" />
      </Field>
    </Modal>
  );
}

/* ------------------------------------------------------------------------- */
/* Issue modal                                                                */
/* ------------------------------------------------------------------------- */

function IssueModal({ mission, person, ammoLots, onClose, onSaved }) {
  const [firearms, setFirearms] = useState(() =>
    ["rifle", "pistol"]
      .map((type) => ({ type, weapon: person[type] }))
      .filter((row) => row.weapon)
      .map(({ type, weapon }) => ({
        type,
        firearm_id: weapon.firearm_id,
        butt_number: weapon.butt_number,
        inspection_status: "serviceable",
        inspection_remarks: "",
      }))
  );
  const [ammoLines, setAmmoLines] = useState([]);
  const [purpose, setPurpose] = useState("");
  const [identityVerified, setIdentityVerified] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const [ackName, setAckName] = useState(person.name || "");
  const [inspectionRemarks, setInspectionRemarks] = useState("");
  const [documents, setDocuments] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  function addAmmoLine() {
    setAmmoLines((rows) => [
      ...rows,
      { ammunition: ammoLots[0]?.id ?? "", quantity: 0 },
    ]);
  }

  function updateAmmo(index, patch) {
    setAmmoLines((rows) => rows.map((row, rowIndex) => (rowIndex === index ? { ...row, ...patch } : row)));
  }

  async function submit() {
    setBusy(true);
    setError("");
    try {
      const txn = await api.post("/armory/issuances/", {
        mission: mission?.id ?? null,
        purpose: purpose.trim(),
        personnel: person.personnel,
        firearms: firearms.map((row) => ({
          firearm_id: row.firearm_id,
          butt_number: row.butt_number,
          inspection_status: row.inspection_status,
          inspection_remarks: row.inspection_remarks,
        })),
        ammunition: ammoLines
          .filter((row) => row.ammunition && Number(row.quantity) > 0)
          .map((row) => ({ ammunition: Number(row.ammunition), quantity: Number(row.quantity) })),
        identity_verified: identityVerified,
        acknowledged,
        acknowledgement_name: ackName,
        inspection_remarks: inspectionRemarks,
        supporting_documents: documents,
      });
      onSaved(txn);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title={`Issue firearms · ${mission?.mission_number || person.name}`}
      onClose={onClose}
      wide
      footer={
        <div className="btn-row">
          <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button
            onClick={submit}
            disabled={
              busy ||
              !identityVerified ||
              !acknowledged ||
              firearms.length === 0 ||
              (!mission && !purpose.trim())
            }
          >
            {busy ? "Releasing…" : "Release and generate receipt"}
          </Button>
        </div>
      }
    >
      <ErrorText message={error} />
      <div className="armory-selected-person">
        <strong>{[person.rank, person.name].filter(Boolean).join(" ")}</strong>
        <span className="armory-subline">
          {person.serial_number || "No service number"} · {person.branch_of_service || "No BOS"} · {person.unit || "No unit"}
        </span>
      </div>
      <div className="alert alert-info">
        {mission
          ? `Mission Order ${mission.mission_number} · ${mission.title} · approved and signed for the current revision.`
          : "No Mission Order linked. This will be recorded as a regular armory issue."}
      </div>

      {!mission && (
        <Field label="Purpose / reason" required hint="Required for a regular issuance without a Mission Order.">
          <Input
            value={purpose}
            onChange={(event) => setPurpose(event.target.value)}
            placeholder="e.g. Range qualification, guard duty, maintenance turn-in"
          />
        </Field>
      )}

      <h3 className="armory-section-title">3 · Physical inspection and identifiers</h3>
      {firearms.map((row, index) => (
        <div className="armory-issue-line" key={row.firearm_id}>
          <div>
            <strong>{row.firearm_id}</strong>
            <span className="armory-subline">Butt {row.butt_number}</span>
          </div>
          <Field label="Inspection result">
            <Select
              value={row.inspection_status}
              onChange={(event) =>
                setFirearms((rows) =>
                  rows.map((entry, rowIndex) =>
                    rowIndex === index ? { ...entry, inspection_status: event.target.value } : entry
                  )
                )
              }
            >
              {FIREARM_CONDITIONS.map((condition) => (
                <option key={condition.value} value={condition.value}>{condition.label}</option>
              ))}
            </Select>
          </Field>
          <Field label="Inspection remarks">
            <Input
              value={row.inspection_remarks}
              onChange={(event) =>
                setFirearms((rows) =>
                  rows.map((entry, rowIndex) =>
                    rowIndex === index ? { ...entry, inspection_remarks: event.target.value } : entry
                  )
                )
              }
            />
          </Field>
        </div>
      ))}

      <h3 className="armory-section-title">4 · Ammunition accountability</h3>
      {ammoLines.length === 0 ? (
        <p className="hint">No ammunition is being drawn. Add a lot only when it will be issued from the armory.</p>
      ) : (
        <div className="armory-ammo-lines">
          {ammoLines.map((row, index) => {
            const lot = ammoLots.find((entry) => String(entry.id) === String(row.ammunition));
            return (
              <div className="armory-ammo-line" key={`${row.ammunition}-${index}`}>
                <Field label="Ammunition lot">
                  <Select value={row.ammunition} onChange={(event) => updateAmmo(index, { ammunition: event.target.value })}>
                    <option value="">Choose lot…</option>
                    {ammoLots.map((entry) => (
                      <option key={entry.id} value={entry.id}>
                        {entry.ammunition_id} · {entry.nomenclature} · lot {entry.lot_number} · {entry.stock_balance} {entry.unit_of_measure} on hand
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label="Quantity" hint={lot ? `Maximum ${number(lot.stock_balance)} ${lot.unit_of_measure}.` : ""}>
                  <Input
                    type="number"
                    min="0"
                    max={lot?.stock_balance}
                    value={row.quantity}
                    onChange={(event) => updateAmmo(index, { quantity: event.target.value })}
                  />
                </Field>
                <Button variant="ghost" size="sm" onClick={() => setAmmoLines((rows) => rows.filter((_, rowIndex) => rowIndex !== index))}>
                  Remove
                </Button>
              </div>
            );
          })}
        </div>
      )}
      <div className="btn-row" style={{ marginBottom: 16 }}>
        <Button variant="ghost" size="sm" onClick={addAmmoLine} disabled={!ammoLots.length}>
          Add ammunition line
        </Button>
      </div>

      <h3 className="armory-section-title">5 · Acknowledgement and release</h3>
      <Field label="Ammunition and general inspection remarks">
        <Textarea value={inspectionRemarks} onChange={(event) => setInspectionRemarks(event.target.value)} />
      </Field>
      <Field label="Supporting documents">
        <Input value={documents} onChange={(event) => setDocuments(event.target.value)} placeholder="Mission Order copy (if linked), receipt book reference…" />
      </Field>
      <Field label="Receiving personnel acknowledgement name" required>
        <Input value={ackName} onChange={(event) => setAckName(event.target.value)} required />
      </Field>
      <label className="check-row" style={{ marginBottom: 10 }}>
        <input type="checkbox" checked={identityVerified} onChange={(event) => setIdentityVerified(event.target.checked)} />
        I verified the person's identity against the {mission ? "approved Mission Order" : "personnel record"}.
      </label>
      <label className="check-row">
        <input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} />
        The receiving person acknowledged receipt of every firearm and ammunition quantity listed.
      </label>
    </Modal>
  );
}

/* ------------------------------------------------------------------------- */
/* Return modal                                                               */
/* ------------------------------------------------------------------------- */

function ReturnModal({ issuance, onClose, onSaved }) {
  const firearmLines = issuance.lines?.filter((line) => line.item_type === "firearm") ?? [];
  const ammoLines = issuance.lines?.filter((line) => line.item_type === "ammunition") ?? [];
  const [lines, setLines] = useState(() =>
    firearmLines.map((line) => ({
      lineId: line.id,
      firearm_id: line.firearm_id_snapshot,
      butt_number: line.butt_number_snapshot,
      serial_number: line.serial_number_snapshot,
      condition_status: "serviceable",
      inspection_remarks: "",
    }))
  );
  const [ammo, setAmmo] = useState(() =>
    ammoLines.map((line) => ({
      lineId: line.id,
      quantity: line.quantity,
      serviceability: line.condition_status || "serviceable",
      discrepancy_note: "",
      name: line.ammunition_name,
      lot: line.lot_number,
      issued: line.quantity,
      uom: line.unit_of_measure,
    }))
  );
  const [identityVerified, setIdentityVerified] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const [ackName, setAckName] = useState(issuance.personnel_name || issuance.personnel_display || "");
  const [inspectionRemarks, setInspectionRemarks] = useState("");
  const [discrepancyNotes, setDiscrepancyNotes] = useState("");
  const [documents, setDocuments] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit() {
    setBusy(true);
    setError("");
    try {
      const txn = await api.post("/armory/returns/", {
        issuance: issuance.id,
        firearms: lines.map((line) => ({
          firearm_id: line.firearm_id,
          butt_number: line.butt_number,
          condition_status: line.condition_status,
          inspection_remarks: line.inspection_remarks,
        })),
        ammunition: ammo.map((line) => ({
          line: line.lineId,
          quantity: Number(line.quantity) || 0,
          serviceability: line.serviceability,
          discrepancy_note: line.discrepancy_note,
        })),
        identity_verified: identityVerified,
        acknowledged,
        acknowledgement_name: ackName,
        inspection_remarks: inspectionRemarks,
        discrepancy_notes: discrepancyNotes,
        supporting_documents: documents,
      });
      onSaved(txn);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title={`Receive return · ${issuance.reference_number}`}
      onClose={onClose}
      wide
      footer={
        <div className="btn-row">
          <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button onClick={submit} disabled={busy || !identityVerified || !acknowledged}>
            {busy ? "Receiving…" : "Complete deposit and generate receipt"}
          </Button>
        </div>
      }
    >
      <ErrorText message={error} />
      <div className="armory-selected-person">
        <strong>{[issuance.rank, issuance.personnel_name || issuance.personnel_display].filter(Boolean).join(" ")}</strong>
        <span className="armory-subline">
          Original issue {issuance.reference_number} · {issuance.mission_order_reference ? `Mission Order ${issuance.mission_order_reference}` : "Standalone issue"} · {issuance.unit_name}
        </span>
      </div>

      <h3 className="armory-section-title">1 · Verify every firearm</h3>
      {lines.map((line, index) => (
        <div className="armory-return-line" key={line.lineId}>
          <div>
            <strong>{line.firearm_id}</strong>
            <span className="armory-subline">Butt {line.butt_number} · Serial {line.serial_number}</span>
          </div>
          <Field label="Returned condition">
            <Select
              value={line.condition_status}
              onChange={(event) =>
                setLines((rows) =>
                  rows.map((row, rowIndex) =>
                    rowIndex === index ? { ...row, condition_status: event.target.value } : row
                  )
                )
              }
            >
              {FIREARM_CONDITIONS.map((condition) => (
                <option key={condition.value} value={condition.value}>{condition.label}</option>
              ))}
            </Select>
          </Field>
          <Field label="Inspection remarks">
            <Input
              value={line.inspection_remarks}
              onChange={(event) =>
                setLines((rows) =>
                  rows.map((row, rowIndex) =>
                    rowIndex === index ? { ...row, inspection_remarks: event.target.value } : row
                  )
                )
              }
            />
          </Field>
        </div>
      ))}

      <h3 className="armory-section-title">2 · Reconcile ammunition</h3>
      {ammo.length === 0 ? (
        <p className="hint">No ammunition was issued on this transaction.</p>
      ) : (
        ammo.map((line, index) => (
          <div className="armory-return-line" key={line.lineId}>
            <div>
              <strong>{line.name} · lot {line.lot}</strong>
              <span className="armory-subline">Issued: {line.issued} {line.uom}. Return the unexpended quantity; consumption is the difference.</span>
            </div>
            <Field label={`Returned quantity (${line.uom})`}>
              <Input
                type="number"
                min="0"
                max={line.issued}
                value={line.quantity}
                onChange={(event) =>
                  setAmmo((rows) =>
                    rows.map((row, rowIndex) =>
                      rowIndex === index ? { ...row, quantity: event.target.value } : row
                    )
                  )
                }
              />
            </Field>
            <Field label="Serviceability">
              <Select
                value={line.serviceability}
                onChange={(event) =>
                  setAmmo((rows) =>
                    rows.map((row, rowIndex) =>
                      rowIndex === index ? { ...row, serviceability: event.target.value } : row
                    )
                  )
                }
              >
                {AMMUNITION_CONDITIONS.map((condition) => (
                  <option key={condition.value} value={condition.value}>{condition.label}</option>
                ))}
              </Select>
            </Field>
            <Field label="Discrepancy note">
              <Input
                value={line.discrepancy_note}
                onChange={(event) =>
                  setAmmo((rows) =>
                    rows.map((row, rowIndex) =>
                      rowIndex === index ? { ...row, discrepancy_note: event.target.value } : row
                    )
                  )
                }
                placeholder="Expended, lost or damaged rounds and explanation…"
              />
            </Field>
          </div>
        ))
      )}

      <h3 className="armory-section-title">3 · Inspection and deposit acknowledgement</h3>
      <Field label="Return inspection remarks">
        <Textarea value={inspectionRemarks} onChange={(event) => setInspectionRemarks(event.target.value)} />
      </Field>
      <Field label="Overall discrepancy notes">
        <Textarea value={discrepancyNotes} onChange={(event) => setDiscrepancyNotes(event.target.value)} />
      </Field>
      <Field label="Supporting documents">
        <Input value={documents} onChange={(event) => setDocuments(event.target.value)} placeholder="Range expenditure certificate, receipt reference…" />
      </Field>
      <Field label="Returning personnel acknowledgement name" required>
        <Input value={ackName} onChange={(event) => setAckName(event.target.value)} required />
      </Field>
      <label className="check-row" style={{ marginBottom: 10 }}>
        <input type="checkbox" checked={identityVerified} onChange={(event) => setIdentityVerified(event.target.checked)} />
        I verified the returning person's identity and matched both identifiers against the original issue.
      </label>
      <label className="check-row">
        <input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} />
        The returning person acknowledged the deposit and the ammunition reconciliation.
      </label>
    </Modal>
  );
}

/* ------------------------------------------------------------------------- */
/* Transaction detail                                                         */
/* ------------------------------------------------------------------------- */

function TransactionDetailModal({ transaction, onClose, setError }) {
  const [downloading, setDownloading] = useState(false);

  async function downloadReceipt() {
    setDownloading(true);
    setError("");
    try {
      await downloadApiFile(
        `/armory/receipts/${transaction.id}/`,
        `${transaction.reference_number.replace(/[^\w-]/g, "_")}.pdf`
      );
    } catch (err) {
      setError(err.message || "Unable to download the receipt.");
    } finally {
      setDownloading(false);
    }
  }

  return (
    <Modal
      title={transaction.reference_number}
      onClose={onClose}
      wide
      footer={
        <div className="btn-row">
          <Button variant="ghost" onClick={onClose}>Close</Button>
          <Button onClick={downloadReceipt} disabled={downloading}>
            {downloading ? "Preparing…" : "Download electronic receipt"}
          </Button>
        </div>
      }
    >
      <dl className="details armory-details">
        <div className="detail"><dt>Transaction type</dt><dd>{transaction.type_label}</dd></div>
        <div className="detail"><dt>Status</dt><dd><StatusBadge status={transaction.status} /></dd></div>
        <div className="detail"><dt>Mission Order</dt><dd>{transaction.mission_order_reference || "—"}</dd></div>
        <div className="detail"><dt>Purpose / reason</dt><dd>{transaction.purpose || "—"}</dd></div>
        <div className="detail"><dt>Mission</dt><dd>{transaction.mission_number ? `${transaction.mission_number} — ${transaction.mission_title}` : "—"}</dd></div>
        <div className="detail"><dt>Rank / name / BOS</dt><dd>{[transaction.rank, transaction.personnel_name || transaction.personnel_display, transaction.branch_of_service].filter(Boolean).join(" / ")}</dd></div>
        <div className="detail"><dt>Unit</dt><dd>{transaction.unit_name || transaction.unit_acronym || "—"}</dd></div>
        <div className="detail"><dt>Releasing armorer</dt><dd>{transaction.releasing_armorer_name || "—"}</dd></div>
        <div className="detail"><dt>Receiving personnel</dt><dd>{transaction.receiving_personnel_name || transaction.personnel_display || "—"}</dd></div>
        <div className="detail"><dt>Approving authority</dt><dd>{transaction.approving_authority_name || "—"}</dd></div>
        <div className="detail"><dt>Date & time</dt><dd>{formatDateTime(transaction.transaction_at)}</dd></div>
        <div className="detail"><dt>Acknowledged by</dt><dd>{transaction.acknowledgement_name || "—"}</dd></div>
        <div className="detail"><dt>Acknowledged at</dt><dd>{formatDateTime(transaction.acknowledged_at)}</dd></div>
        <div className="detail"><dt>Return of</dt><dd>{transaction.return_of_reference || "—"}</dd></div>
        <div className="detail"><dt>Returned at</dt><dd>{formatDateTime(transaction.returned_at)}</dd></div>
      </dl>
      {transaction.inspection_remarks && <p><strong>Inspection remarks:</strong> {transaction.inspection_remarks}</p>}
      {transaction.discrepancy_notes && <p><strong>Discrepancies:</strong> {transaction.discrepancy_notes}</p>}
      {transaction.supporting_documents && <p><strong>Supporting documents:</strong> {transaction.supporting_documents}</p>}

      <h3 className="armory-section-title">Transaction lines</h3>
      <Table
        rows={transaction.lines ?? []}
        empty="No transaction lines."
        columns={[
          {
            key: "firearm_id_snapshot",
            label: "Firearm ID / butt / serial",
            render: (row) =>
              row.item_type === "firearm" ? (
                <span>
                  <strong>{row.firearm_id_snapshot}</strong>
                  <span className="armory-subline">Butt {row.butt_number_snapshot} · Serial {row.serial_number_snapshot}</span>
                </span>
              ) : (
                "—"
              ),
          },
          {
            key: "ammunition_name",
            label: "Ammunition / lot",
            render: (row) =>
              row.item_type === "ammunition" ? (
                <span>
                  <strong>{row.ammunition_name}</strong>
                  <span className="armory-subline">{row.ammunition_caliber} · lot {row.lot_number}</span>
                </span>
              ) : (
                "—"
              ),
          },
          { key: "quantity", label: "Quantity", render: (row) => (row.item_type === "ammunition" ? `${number(row.quantity)} ${row.unit_of_measure}` : "1") },
          { key: "condition_label", label: "Condition" },
          { key: "inspection_remarks", label: "Inspection remarks" },
        ]}
      />
    </Modal>
  );
}
