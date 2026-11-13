import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, qs } from "../api/client.js";
import { useAuth } from "../auth/AuthContext.jsx";
import { Badge, Button, Card, EmptyState, ErrorBanner, IconButton, Input, Loading, Modal, Pagination, Select, StatusBadge, Table } from "../components/ui.jsx";
import Icon from "../components/Icon.jsx";
import PdfViewer from "../components/PdfViewer.jsx";
import SuperAdminDelete from "../components/SuperAdminDelete.jsx";
import AirOperationsBoard from "../components/AirOperationsBoard.jsx";
import { useApi } from "../hooks/useApi.js";
import { formatDateTime } from "../utils/format.js";

const PAGE_SIZE = 25;
const POV_GENERIC_TYPES = new Set([
  "Motorcycle",
  "Car",
  "SUV",
  "Pickup",
  "Van",
  "Truck",
  "Bus",
  "Other",
]);

/** A POV reads as ``POV- Toyota Hilux PN-12315`` in the mission list. */
function povLabel(item) {
  const type = (item.vehicle_type || "").trim();
  const model = (item.make_model || "").trim();
  // Older POV rows kept the make in vehicle_type and the model in make_model;
  // newer rows keep a generic type there instead.
  const description =
    model && type && !POV_GENERIC_TYPES.has(type) ? `${type} ${model}` : model || type;
  const plate = (item.plate_number || "").trim();
  return `POV- ${[description, plate].filter(Boolean).join(" ")}`.trim();
}

const APPROVAL_TONES = {
  approved: "success",
  pending: "warning",
  returned: "warning",
  rejected: "danger",
  not_routed: "muted",
};

const READINESS_TONES = {
  ready: "success",
  blocked: "danger",
  in_progress: "info",
  completed: "success",
  cancelled: "muted",
};

export default function MissionsPage({ category: categoryProp = "ground" }) {
  const navigate = useNavigate();
  const { permissions } = useAuth();
  const canPlan = Boolean(permissions?.can_plan_missions);
  const canView = Boolean(permissions?.can_view_missions);
  // Erasing a mission is the Super Administrator's alone; the server enforces
  // the same rule, so this only decides whether the button is offered.
  const isAdmin = Boolean(permissions?.is_system_owner);
  // Ground and air are separate pages: the route decides which register this
  // is, so the list never mixes the two.
  const category = categoryProp === "air" ? "air" : "ground";
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const [approvalStatus, setApprovalStatus] = useState("");
  const [mrmStatus, setMrmStatus] = useState("");
  const [readiness, setReadiness] = useState("");
  const [startAfter, setStartAfter] = useState("");
  const [startBefore, setStartBefore] = useState("");
  const [page, setPage] = useState(1);

  const path = `/missions/${qs({
    category,
    status,
    search,
    approval_status: approvalStatus,
    mrm_status: mrmStatus,
    readiness,
    start_after: startAfter,
    start_before: startBefore,
    page,
    page_size: PAGE_SIZE,
  })}`;
  const { data, loading, error, reload } = useApi(path, {
    deps: [
      category,
      status,
      search,
      approvalStatus,
      mrmStatus,
      readiness,
      startAfter,
      startBefore,
      page,
    ],
  });

  const rows = data?.results ?? [];

  const [deleting, setDeleting] = useState(null);
  const [deleteError, setDeleteError] = useState("");
  const [ending, setEnding] = useState(null);
  const [endError, setEndError] = useState("");
  const [dispatching, setDispatching] = useState(null);
  const [dispatchError, setDispatchError] = useState("");
  // { kind: "mrm" | "order", row } while a PDF preview is open.
  const [preview, setPreview] = useState(null);

  /**
   * Close a mission the Operations Center is allowed to end.
   *
   * The server decides when a mission is endable (planned end passed, or every
   * vehicle trip completed); this only offers the action where that rule holds.
   * Closing cancels any run still open, so the confirmation says how many - the
   * override is then recorded in the movement log rather than being silent.
   */
  async function endMission(row) {
    const closing = row.closing || {};
    const open = Number(closing.open_runs || 0);
    const override = open
      ? `\n\n${open} vehicle run${open === 1 ? " is" : "s are"} still open; ending the mission cancels ${
          open === 1 ? "it" : "them"
        } and records the override.`
      : "";
    if (
      !window.confirm(
        `End mission ${row.mission_number}?\n\n${closing.reason || ""}${override}\n\nThe mission and its trips will be marked completed.`
      )
    )
      return;
    setEnding(row.id);
    setEndError("");
    try {
      await api.post(`/missions/${row.id}/tracking/complete/`, {
        force: open > 0,
        note: `Ended from the mission list by the Operations Center (${
          closing.reasons?.includes("trips_completed") ? "vehicle trips completed" : "planned end passed"
        }).`,
      });
      reload();
    } catch (err) {
      setEndError(err.message || "The mission could not be ended.");
    } finally {
      setEnding(null);
    }
  }

  /** Move an approved, unblocked mission into execution from the list. */
  async function dispatchMission(row) {
    if (
      !window.confirm(
        `Dispatch mission ${row.mission_number}?\n\nThis starts the mission and its planned vehicle runs.`
      )
    )
      return;
    setDispatching(row.id);
    setDispatchError("");
    try {
      await api.post(`/missions/${row.id}/dispatch/`, {});
      reload();
    } catch (err) {
      setDispatchError(err.fieldErrors?.detail || err.message || "The mission could not be dispatched.");
    } finally {
      setDispatching(null);
    }
  }

  async function removeMission(row) {
    if (
      !window.confirm(
        `Delete mission ${row.mission_number}? This removes the record and any drafts linked to it, and cannot be undone.`
      )
    )
      return;
    setDeleting(row.id);
    setDeleteError("");
    try {
      await api.del(`/missions/${row.id}/`);
      reload();
    } catch (err) {
      setDeleteError(err.message || "Unable to delete the mission.");
    } finally {
      setDeleting(null);
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{category === "air" ? "Air Operations" : "Ground Operations"}</h1>
          <p>
            {category === "air"
              ? "Daily Flight Operations: schedule first, then record actual takeoff and landing times."
              : "Ground missions, with risk linkage, approval status and execution readiness."}
          </p>
        </div>
        {canPlan && (
          <Link
            className="btn btn-primary btn-md btn-icon"
            to={`/missions/new?category=${category}`}
            title={category === "air" ? "Plan an air mission" : "Plan a ground mission"}
            aria-label={category === "air" ? "Plan an air mission" : "Plan a ground mission"}
          >
            <Icon name="plus" size={18} />
          </Link>
        )}
      </div>

      {!canView ? (
        <Card>
          <EmptyState message="Missions are visible to the unit's command, its staff officers and personnel attached to a mission. You have no mission records in scope." />
        </Card>
      ) : (
      <>
      {category === "air" ? (
        <AirOperationsBoard />
      ) : (
        <Card>
        <div className="inline-row" style={{ marginBottom: 10 }}>
          <Input
            placeholder="Search mission number, title, location…"
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
              setPage(1);
            }}
          />
          <Select
            value={status}
            onChange={(event) => {
              setStatus(event.target.value);
              setPage(1);
            }}
          >
            <option value="">All statuses</option>
            {["draft", "planned", "approved", "active", "completed", "cancelled"].map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </Select>
        </div>

        <div className="inline-row" style={{ marginBottom: 14 }}>
          {category === "ground" && (
            <Select
              aria-label="Approval status"
              value={approvalStatus}
              onChange={(event) => {
                setApprovalStatus(event.target.value);
                setPage(1);
              }}
            >
              <option value="">All approval statuses</option>
              <option value="not_routed">Not routed</option>
              <option value="pending">Pending approval</option>
              <option value="returned">Returned for revision</option>
              <option value="approved">Approved</option>
              <option value="rejected">Rejected</option>
            </Select>
          )}
          <Select
            aria-label="MRM status"
            value={mrmStatus}
            onChange={(event) => {
              setMrmStatus(event.target.value);
              setPage(1);
            }}
          >
            <option value="">All MRM statuses</option>
            <option value="approved">MRM approved</option>
            <option value="submitted">Submitted / under evaluation</option>
            <option value="in_progress">Draft</option>
            <option value="returned">Returned for revision</option>
            <option value="pending">Awaiting assessment</option>
            <option value="not_created">No MRM</option>
          </Select>
          <Select
            aria-label="Execution readiness"
            value={readiness}
            onChange={(event) => {
              setReadiness(event.target.value);
              setPage(1);
            }}
          >
            <option value="">All readiness states</option>
            <option value="ready">Ready for dispatch</option>
            <option value="blocked">Blocked</option>
            <option value="in_progress">In progress</option>
            <option value="completed">Completed</option>
          </Select>
          <Input
            type="date"
            aria-label="Missions starting on or after"
            title="Missions starting on or after"
            value={startAfter}
            onChange={(event) => {
              setStartAfter(event.target.value);
              setPage(1);
            }}
          />
          <Input
            type="date"
            aria-label="Missions starting on or before"
            title="Missions starting on or before"
            value={startBefore}
            onChange={(event) => {
              setStartBefore(event.target.value);
              setPage(1);
            }}
          />
        </div>

        <ErrorBanner message={error} onRetry={reload} />
        {deleteError && <ErrorBanner message={deleteError} />}
        {endError && <ErrorBanner message={endError} />}
        {dispatchError && <ErrorBanner message={dispatchError} />}
        {loading && !data ? (
          <Loading />
        ) : (
          <>
            <Table
              rows={rows}
              onRowClick={(row) => navigate(`/missions/${row.id}`)}
              empty="No missions match the current filters."
              stack
              columns={[
                { key: "mission_number", label: "Mission #" },
                { key: "title", label: "Mission" },
                {
                  key: "category",
                  label: "Category",
                  render: (row) => (
                    <Badge tone={row.category === "air" ? "info" : "muted"}>
                      {row.category === "air" ? "Air" : "Ground"}
                    </Badge>
                  ),
                },
                { key: "unit_acronym", label: "Unit", render: (row) => row.unit_acronym || "—" },
                { key: "start_at", label: "Starts", render: (row) => formatDateTime(row.start_at) },
                {
                  key: "mrm_risk",
                  label: "MRM / Risk",
                  render: (row) => {
                    const mrm = row.mrm || {};
                    const assessment = row.risk_assessment;
                    const status = (mrm.label || "Not yet created").toUpperCase();
                    const risk = assessment?.risk_category
                      ? `${assessment.risk_category.toUpperCase()}${
                          assessment.total_percent != null
                            ? `-${Number(assessment.total_percent).toFixed(1)}%`
                            : ""
                        }`
                      : "NOT ASSESSED";
                    // The oval badges follow the matrix: green low, amber
                    // medium, red high - the same shape as Approval Status.
                    const statusTone =
                      mrm.status === "approved"
                        ? "success"
                        : mrm.status === "returned"
                          ? "danger"
                          : mrm.status === "not_created"
                            ? "muted"
                            : "warning";
                    const riskTone =
                      assessment?.risk_category === "High"
                        ? "danger"
                        : assessment?.risk_category === "Medium"
                          ? "warning"
                          : assessment?.risk_category
                            ? "success"
                            : "muted";
                    return (
                      <span className="mrm-risk-cell">
                        <span className="mrm-risk-status">
                          <Badge tone={statusTone}>{status}</Badge>
                          {mrm.status === "approved" && mrm.mrm_form && (
                            <IconButton
                              name="eye"
                              variant="info"
                              label={`View ${mrm.mrm_number || "the MRM"} PDF`}
                              onClick={(event) => {
                                event.stopPropagation();
                                setPreview({ kind: "mrm", row });
                              }}
                            />
                          )}
                        </span>
                        <Badge tone={riskTone}>{risk}</Badge>
                        {mrm.overdue && <Badge tone="danger">OVERDUE</Badge>}
                      </span>
                    );
                  },
                },
                ...(category === "ground"
                  ? [
                      {
                        key: "approving_authority",
                        label: "Approving authority",
                        render: (row) => {
                          const approval = row.approval || {};
                          return approval.authority_role_label || "—";
                        },
                      },
                      {
                        key: "approval",
                        label: "Approval Status",
                        render: (row) => {
                          const approval = row.approval || {};
                          const tone = approval.revision_changed
                            ? "danger"
                            : APPROVAL_TONES[approval.status] ?? "muted";
                          return (
                            <Badge tone={tone}>
                              {approval.label || approval.status || "Not routed"}
                            </Badge>
                          );
                        },
                      },
                    ]
                  : []),
                {
                  key: "execution",
                  label: "Execution readiness",
                  render: (row) => {
                    const execution = row.execution || {};
                    const blockers = (execution.blockers || []).map((item) => item.detail || item.label);
                    return (
                      <span title={blockers.length ? blockers.join(" ") : undefined}>
                        <Badge tone={READINESS_TONES[execution.state] ?? "muted"}>
                          {execution.label || "—"}
                        </Badge>
                      </span>
                    );
                  },
                },
                {
                  key: "status",
                  label: "Mission status",
                  render: (row) => {
                    const order = row.mission_order || {};
                    return (
                      <span
                        className="status-chip"
                        onClick={(event) => event.stopPropagation()}
                      >
                        <StatusBadge status={row.status} />
                        {order.status === "approved" && (
                          <IconButton
                            name="eye"
                            variant="info"
                            label={`View Mission Order PDF for ${row.mission_number}`}
                            onClick={() => setPreview({ kind: "order", row })}
                          />
                        )}
                      </span>
                    );
                  },
                },
                {
                  key: "vehicles",
                  label: "Vehicles",
                  render: (row) => {
                    const labels = (row.vehicles || [])
                      .map((item) => {
                        if (!item.is_pov) return item.registration_number;
                        return povLabel(item);
                      })
                      .filter(Boolean);
                    return labels.length ? labels.join(", ") : "—";
                  },
                },
                {
                  key: "actions",
                  label: "Actions",
                  render: (row) => {
                    // A blocked mission has not met its execution conditions
                    // (the MRM assessment in particular), so the map is not
                    // offered until those conditions are cleared.
                    const mapBlocked = row.execution?.state === "blocked";
                    return (
                      <span
                        className="status-chip"
                        onClick={(event) => event.stopPropagation()}
                      >
                        <Link
                          className="btn btn-info btn-sm"
                          to={`/missions/${row.id}`}
                          title={`Open ${row.mission_number}`}
                          aria-label={`Open ${row.mission_number}`}
                        >
                          View
                        </Link>
                        {canPlan && row.execution?.state === "ready" && (
                          <Button
                            variant="success"
                            size="sm"
                            disabled={dispatching === row.id}
                            title={`Dispatch mission ${row.mission_number}`}
                            onClick={() => dispatchMission(row)}
                          >
                            {dispatching === row.id ? "Dispatching…" : "Dispatch"}
                          </Button>
                        )}
                        {!mapBlocked && (
                          <a
                            className="btn btn-primary btn-sm"
                            href={`/missions/${row.id}/track`}
                            target="_blank"
                            rel="noreferrer"
                            title={`Open ${row.mission_number} on the map`}
                            aria-label={`Open ${row.mission_number} on the map`}
                          >
                            Map
                          </a>
                        )}
                        {canPlan && row.closing?.available && (
                          <Button
                            variant="warning"
                            size="sm"
                            disabled={ending === row.id}
                            title={`End this mission - ${row.closing.reason}`}
                            onClick={() => endMission(row)}
                          >
                            {ending === row.id ? "Ending…" : "End"}
                          </Button>
                        )}
                        {isAdmin && (
                          <Button
                            variant="danger"
                            size="sm"
                            disabled={deleting === row.id}
                            title={`Delete mission ${row.mission_number} permanently`}
                            onClick={() => removeMission(row)}
                          >
                            {deleting === row.id ? "Deleting…" : "Delete"}
                          </Button>
                        )}
                      </span>
                    );
                  },
                },
              ]}
            />
            <Pagination page={page} pageSize={PAGE_SIZE} count={data?.count ?? 0} onPage={setPage} />
          </>
        )}
        </Card>
      )}

      {preview && (
        <Modal
          title={
            preview.kind === "mrm"
              ? `MRM ${preview.row.mrm?.mrm_number || preview.row.mission_number} · ${preview.row.title}`
              : `Mission Order · ${preview.row.mission_number}`
          }
          onClose={() => setPreview(null)}
          wide
          footer={
            <Button variant="ghost" size="sm" onClick={() => setPreview(null)}>
              Close
            </Button>
          }
        >
          <PdfViewer
            path={
              preview.kind === "mrm"
                ? `/risk-register/${preview.row.mrm.mrm_form}/pdf/`
                : `/missions/${preview.row.id}/mission-order/`
            }
            title={
              preview.kind === "mrm"
                ? `MRM ${preview.row.mrm?.mrm_number || ""}`.trim()
                : `Mission Order ${preview.row.mission_number}`
            }
          />
        </Modal>
      )}
      </>
      )}
    </>
  );
}
