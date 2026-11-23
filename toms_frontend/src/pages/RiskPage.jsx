import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, qs } from "../api/client.js";
import { useAuth } from "../auth/AuthContext.jsx";
import {
  Badge,
  Button,
  Card,
  ErrorBanner,
  Field,
  IconButton,
  Input,
  Loading,
  Modal,
  Pagination,
  Select,
  StatusBadge,
  Table,
  Tabs,
  Textarea,
} from "../components/ui.jsx";
import Icon from "../components/Icon.jsx";
import { useApi } from "../hooks/useApi.js";
import { formatDate, formatDateRange } from "../utils/format.js";
import { byRank } from "../utils/ranks.js";

const PAGE_SIZE = 25;

/**
 * Raise an MRM by hand instead of from an Operations Center mission.
 *
 * Only the title and the evaluator are needed to create the draft; hazards and
 * conditions are filled in on the assessment form it opens.
 */
function ManualMrmModal({ onClose, onCreated }) {
  const [title, setTitle] = useState("");
  const [statement, setStatement] = useState("");
  const [evaluatorId, setEvaluatorId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const { data: evaluators, loading: loadingEvaluators, error: evaluatorError } = useApi(
    "/risk-register/evaluators/"
  );

  async function submit(event) {
    event.preventDefault();
    if (!title.trim()) {
      setError("Enter the activity or mission this assessment covers.");
      return;
    }
    if (!statement.trim()) {
      setError("Enter the purpose or scope this assessment must cover.");
      return;
    }
    if (!evaluatorId) {
      setError("Select the evaluator who will review this assessment.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const created = await api.post("/risk-register/", {
        activity_mission: title.trim(),
        mission_statement: statement.trim(),
        evaluator_id: Number(evaluatorId),
      });
      onCreated(created);
    } catch (err) {
      const fieldErrors = err.fieldErrors
        ? Object.values(err.fieldErrors).flat().join(" ")
        : "";
      setError(fieldErrors || err.message || "Unable to create the MRM.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title="New MRM"
      onClose={onClose}
      footer={
        <div className="btn-row">
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button
            type="submit"
            form="manual-mrm-form"
            disabled={
              busy ||
              loadingEvaluators ||
              !title.trim() ||
              !statement.trim() ||
              !evaluatorId
            }
          >
            {busy ? "Creating…" : "Create draft"}
          </Button>
        </div>
      }
    >
      <form id="manual-mrm-form" onSubmit={submit}>
        <p className="hint" style={{ marginTop: 0 }}>
          This assessment is raised manually. It is not linked to a mission from the
          Operations Center.
        </p>
        <Field label="Activity / mission" required htmlFor="manual-mrm-title">
          <Input
            id="manual-mrm-title"
            value={title}
            autoFocus
            maxLength={255}
            placeholder="e.g. Battalion road march"
            onChange={(event) => setTitle(event.target.value)}
          />
        </Field>
        <Field
          label="Purpose / scope"
          required
          htmlFor="manual-mrm-statement"
          hint="What this assessment covers and why; the hazards and conditions are added in the draft."
        >
          <Textarea
            id="manual-mrm-statement"
            value={statement}
            onChange={(event) => setStatement(event.target.value)}
          />
        </Field>
        <Field label="Evaluator" required htmlFor="manual-mrm-evaluator">
          <Select
            id="manual-mrm-evaluator"
            value={evaluatorId}
            disabled={loadingEvaluators}
            onChange={(event) => setEvaluatorId(event.target.value)}
          >
            <option value="">
              {loadingEvaluators ? "Loading evaluators…" : "Select an evaluator"}
            </option>
            {[...(evaluators?.results ?? [])].sort(byRank).map((row) => (
              <option key={row.id} value={row.id}>
                {row.full_name}
              </option>
            ))}
          </Select>
        </Field>
        <ErrorBanner message={evaluatorError || error} />
      </form>
    </Modal>
  );
}

/** The dates the assessment covers: its mission window, else when it was raised. */
function coveredDates(row) {
  const start = row.date_covered_start;
  const end = row.date_covered_end;
  // A multi-day mission prints its window; a one-day one prints the day. An
  // assessment raised by hand covers the day it was raised.
  if (!start && !end) return formatDateRange(row.date_created, row.date_created);
  return formatDateRange(start, end);
}

export default function RiskPage() {
  const navigate = useNavigate();
  const { user } = useAuth();
  // The two register managers may open any PDF they can already see in the
  // list; everyone else reaches the PDF from the assessment page.
  const canViewPdf =
    user?.authority === "Administrator" || user?.authority === "Unit Safety Officer";
  // A completed assessment opens its own read-only viewer page. Everything
  // still in flight opens the assessment page, where the work happens.

  function isCompleted(row) {
    // The decision authority's signature is the completion marker; the legacy
    // rows keep status "reviewed" even after it, so status alone is not used.
    return Boolean(row.authority_approved) && row.review_status === "approved";
  }

  function openRow(row) {
    navigate(isCompleted(row) ? `/risk/${row.id}/view` : `/risk/${row.id}`);
  }
  const [scope, setScope] = useState("");
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const [unit, setUnit] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [page, setPage] = useState(1);

  const canCreate =
    user?.authority === "Creator" || user?.authority === "Administrator";
  const [createOpen, setCreateOpen] = useState(false);

  const [selected, setSelected] = useState(new Set());
  const [deleting, setDeleting] = useState(null);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [actionError, setActionError] = useState("");
  const [message, setMessage] = useState("");

  const path = `/risk-register/${qs({
    scope,
    status,
    search,
    unit,
    date_from: dateFrom,
    date_to: dateTo,
    page,
    page_size: PAGE_SIZE,
  })}`;
  const { data, loading, error, reload } = useApi(path, {
    deps: [scope, status, search, unit, dateFrom, dateTo, page],
  });
  // Filter choices are scoped by the server to the units this caller may read.
  const { data: units } = useApi("/risk-register/units/");

  const rows = data?.results ?? [];
  const deletable = rows.filter((row) => row.can_delete);
  const canDeleteAny = deletable.length > 0;
  const allSelected = deletable.length > 0 && deletable.every((row) => selected.has(row.id));

  // A selection belongs to the page and filters it was made on.
  useEffect(() => {
    setSelected(new Set());
  }, [scope, status, search, unit, dateFrom, dateTo, page]);

  function toggle(id) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function togglePage() {
    setSelected(allSelected ? new Set() : new Set(deletable.map((row) => row.id)));
  }

  async function remove(row) {
    if (
      !window.confirm(
        `Delete ${row.mrm_number}? Its hazards, tagged personnel, feedback, mission link and stored PDF are removed too. This cannot be undone.`
      )
    )
      return;
    setDeleting(row.id);
    setActionError("");
    setMessage("");
    try {
      await api.del(`/risk-register/${row.id}/`);
      setSelected((current) => {
        const next = new Set(current);
        next.delete(row.id);
        return next;
      });
      setMessage(`${row.mrm_number} deleted.`);
      reload();
    } catch (err) {
      setActionError(err.message || "Unable to delete the assessment.");
    } finally {
      setDeleting(null);
    }
  }

  async function removeSelected() {
    const ids = [...selected];
    if (!ids.length) return;
    if (
      !window.confirm(
        `Delete ${ids.length} assessment${ids.length === 1 ? "" : "s"}? Their hazards, personnel, feedback and mission links are removed too. This cannot be undone.`
      )
    )
      return;
    setBulkBusy(true);
    setActionError("");
    setMessage("");
    try {
      const result = await api.post("/risk-register/bulk-delete/", { ids });
      const refused = result?.skipped ?? [];
      setSelected(new Set());
      if (refused.length) {
        setActionError(
          `Deleted ${result.deleted.length}. Not deleted: ${refused
            .map((row) => row.reason)
            .filter((reason, index, all) => all.indexOf(reason) === index)
            .join(" ")}`
        );
      } else {
        setMessage(`Deleted ${result.deleted.length} assessment${result.deleted.length === 1 ? "" : "s"}.`);
      }
      reload();
    } catch (err) {
      setActionError(err.message || "Unable to delete the selected assessments.");
    } finally {
      setBulkBusy(false);
    }
  }

  const selectColumn = {
    key: "select",
    label: "",
    width: 36,
    render: (row) =>
      row.can_delete ? (
        <span onClick={(event) => event.stopPropagation()}>
          <input
            type="checkbox"
            aria-label={`Select ${row.mrm_number}`}
            checked={selected.has(row.id)}
            onChange={() => toggle(row.id)}
          />
        </span>
      ) : null,
  };

  return (
    <div className="risk-page">
      <div className="page-head">
        <div>
          <h1>Mission Risk Management</h1>
          <p>
            Preserved MRMS workflow: evaluator assignment, review, authority approval and the
            original risk scale.
          </p>
        </div>
        {canCreate && (
          <div className="btn-row">
            <Button onClick={() => setCreateOpen(true)}>
              <Icon name="plus" size={16} /> New MRM
            </Button>
          </div>
        )}
      </div>

      <Tabs
        tabs={[
          { value: "", label: "All assessments" },
          { value: "mine", label: "Raised by me" },
          { value: "to_review", label: "Assigned to me" },
          { value: "pending_approval", label: "Pending approval" },
        ]}
        active={scope}
        onChange={(value) => {
          setScope(value);
          setPage(1);
        }}
      />

      <Card>
        <div className="risk-filters">
          <div className="risk-filter-search">
            <Input
              placeholder="Search MRM number, activity or name…"
              aria-label="Search risk assessments"
              value={search}
              onChange={(event) => {
                setSearch(event.target.value);
                setPage(1);
              }}
            />
          </div>
          <Select
            aria-label="Unit"
            value={unit}
            onChange={(event) => {
              setUnit(event.target.value);
              setPage(1);
            }}
          >
            <option value="">All units</option>
            {(units?.results ?? []).map((row) => (
              <option key={row.id} value={row.id}>
                {row.acronym || row.name}
              </option>
            ))}
          </Select>
          <Select
            aria-label="Status"
            value={status}
            onChange={(event) => {
              setStatus(event.target.value);
              setPage(1);
            }}
          >
            <option value="">All statuses</option>
            <option value="draft">Draft</option>
            <option value="submitted">Submitted</option>
            <option value="reviewed">Reviewed</option>
          </Select>
          <label className="risk-date-field">
            <span>Covered from</span>
            <Input
              type="date"
              aria-label="Covered from"
              title="MRM date covered, from"
              value={dateFrom}
              onChange={(event) => {
                setDateFrom(event.target.value);
                setPage(1);
              }}
            />
          </label>
          <label className="risk-date-field">
            <span>Covered to</span>
            <Input
              type="date"
              aria-label="Covered to"
              title="MRM date covered, to"
              value={dateTo}
              onChange={(event) => {
                setDateTo(event.target.value);
                setPage(1);
              }}
            />
          </label>
          {(dateFrom || dateTo) && (
            <IconButton
              name="close"
              label="Clear the date filters"
              className="risk-clear-dates"
              onClick={() => {
                setDateFrom("");
                setDateTo("");
                setPage(1);
              }}
            />
          )}
        </div>

        {(canDeleteAny || selected.size > 0) && (
          <div className="btn-row risk-selection-actions" style={{ marginBottom: 12 }}>
            {canDeleteAny && (
              <IconButton
                name={allSelected ? "close" : "check"}
                label={
                  allSelected
                    ? "Clear the selection"
                    : "Select every deletable MRM on this page"
                }
                onClick={togglePage}
              />
            )}
            {selected.size > 0 && (
              <IconButton
                name="trash"
                variant="danger"
                disabled={bulkBusy}
                label={`Delete the ${selected.size} selected MRM${
                  selected.size === 1 ? "" : "s"
                }`}
                onClick={removeSelected}
              />
            )}
          </div>
        )}

        <ErrorBanner message={actionError || error} onRetry={error ? reload : undefined} />
        {message && <div className="alert alert-success">{message}</div>}
        {loading && !data ? (
          <Loading />
        ) : (
          <>
            <Table
              rows={rows}
              onRowClick={openRow}
              empty="No risk assessments found."
              stack
              columns={[
                ...(canDeleteAny ? [selectColumn] : []),
                { key: "mrm_number", label: "MRM #" },
                { key: "activity_mission", label: "Activity / Mission" },
                {
                  key: "unit_name",
                  label: "Unit",
                  render: (row) => row.unit_acronym || row.unit_name || row.original_unit_name || "—",
                },
                {
                  key: "date_covered",
                  label: "Date covered",
                  render: (row) => coveredDates(row),
                },
                { key: "status", label: "Status", render: (row) => <StatusBadge status={row.status} /> },
                {
                  key: "review_status",
                  label: "Review",
                  render: (row) => <StatusBadge status={row.review_status} />,
                },
                {
                  key: "risk_category",
                  label: "Risk",
                  render: (row) => (
                    <Badge
                      tone={
                        row.risk_category === "High"
                          ? "danger"
                          : row.risk_category === "Medium"
                            ? "warning"
                            : row.risk_category
                              ? "success"
                              : "muted"
                      }
                    >
                      {row.risk_category || "—"}
                    </Badge>
                  ),
                },
                { key: "authority_level", label: "Authority" },
                { key: "evaluator_name", label: "Evaluator", render: (row) => row.evaluator_name || "—" },
                { key: "date_created", label: "Raised", render: (row) => formatDate(row.date_created) },
                {
                  key: "actions",
                  label: "Actions",
                  render: (row) => {
                    if (!canViewPdf && !row.can_delete && !row.can_edit) return "—";
                    return (
                      <span
                        className="btn-row"
                        onClick={(event) => event.stopPropagation()}
                      >
                        {canViewPdf && (
                          <IconButton
                            name="eye"
                            label={`View ${row.mrm_number}`}
                            onClick={() => navigate(`/risk/${row.id}/view`)}
                          />
                        )}
                        {row.can_edit && (
                          <IconButton
                            name="edit"
                            label={`Edit draft ${row.mrm_number}`}
                            onClick={() => navigate(`/risk/${row.id}/edit`)}
                          />
                        )}
                        {row.can_delete && (
                          <IconButton
                            name="trash"
                            variant="danger"
                            disabled={deleting === row.id}
                            label={`Delete ${row.mrm_number}`}
                            onClick={() => remove(row)}
                          />
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

      {createOpen && (
        <ManualMrmModal
          onClose={() => setCreateOpen(false)}
          // Straight into the assessment form: a fresh draft has nothing to
          // show on the workflow page until its hazards are scored.
          onCreated={(created) => navigate(`/risk/${created.id}/edit`)}
        />
      )}

    </div>
  );
}
