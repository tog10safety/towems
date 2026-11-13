import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client.js";
import { Badge, Button, Card, ErrorBanner, Stat, StatusBadge, Table } from "./ui.jsx";

/** A labelled proportion bar, used for both risk and hazard distributions. */
function BarRow({ label, count, max, color }) {
  const width = max > 0 ? Math.round((count / max) * 100) : 0;
  return (
    <li className="safety-bar-row">
      <span>{label}</span>
      <span className="safety-bar" aria-hidden="true">
        <span style={{ width: `${width}%`, background: color }} />
      </span>
      <strong>{count}</strong>
    </li>
  );
}

/**
 * Safety file for the Unit Safety Officer's own unit: MRM throughput, residual
 * risk profile, the hazard library's ratings and the missions still lacking an
 * approved assessment. Rendered only when the server sends the block, which it
 * does for a safety officer alone.
 */
export default function SafetyAnalytics({ data, onRefresh }) {
  const [busy, setBusy] = useState("");
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");
  const [showHidden, setShowHidden] = useState(false);

  if (!data) return null;

  const mrm = data.mrm || {};
  const risk = data.risk || {};
  const hazards = data.hazards || {};
  const missions = data.missions || {};
  const unit = data.unit || {};
  const hiddenRows = data.attention_hidden ?? [];
  const hiddenCount = data.attention_hidden_count ?? hiddenRows.length;
  const rows = showHidden ? hiddenRows : data.attention ?? [];

  async function hide(row) {
    setBusy(`hide-${row.id}`);
    setActionError("");
    setNotice("");
    try {
      await api.post(`/risk-register/${row.id}/hide-attention/`, {});
      setNotice(`${row.mrm_number} hidden from this dashboard.`);
      onRefresh?.();
    } catch (err) {
      setActionError(err.message || "Unable to hide the assessment.");
    } finally {
      setBusy("");
    }
  }

  async function restore(row) {
    setBusy(`restore-${row.id}`);
    setActionError("");
    setNotice("");
    try {
      await api.post(`/risk-register/${row.id}/restore-attention/`, {});
      setNotice(`${row.mrm_number} restored to the attention list.`);
      if (hiddenRows.length <= 1) setShowHidden(false);
      onRefresh?.();
    } catch (err) {
      setActionError(err.message || "Unable to restore the assessment.");
    } finally {
      setBusy("");
    }
  }

  async function remove(row) {
    if (
      !window.confirm(
        `Delete ${row.mrm_number}? Its hazards, tagged personnel, feedback, mission link and stored PDF are removed too. This cannot be undone.`
      )
    )
      return;
    setBusy(`delete-${row.id}`);
    setActionError("");
    setNotice("");
    try {
      await api.del(`/risk-register/${row.id}/`);
      setNotice(`${row.mrm_number} deleted.`);
      onRefresh?.();
    } catch (err) {
      setActionError(err.message || "Unable to delete the assessment.");
    } finally {
      setBusy("");
    }
  }
  const riskMax = Math.max(risk.high || 0, risk.medium || 0, risk.low || 0, 1);
  const hazardMax = Math.max(
    hazards.red || 0,
    hazards.yellow || 0,
    hazards.green || 0,
    hazards.unclassified || 0,
    1
  );
  const averageTone =
    risk.average_percent > 75 ? "danger" : risk.average_percent > 50 ? "warning" : "success";

  return (
    <Card
      title={`Safety analytics · ${unit.acronym || unit.name || "My unit"}`}
      actions={<Badge tone="info">Unit file</Badge>}
    >
      <p className="hint" style={{ marginTop: 0 }}>
        Your unit only. MRM status, residual risk, the hazard library and missions still
        awaiting an approved assessment.
      </p>

      <div className="stats stats-compact">
        <Stat
          label="MRM assessments"
          value={mrm.total ?? 0}
          hint={`${mrm.approved ?? 0} approved · ${mrm.awaiting ?? 0} awaiting`}
        />
        <Stat
          label="Average residual"
          value={`${Number(risk.average_percent || 0).toFixed(1)}%`}
          hint={`${mrm.returned ?? 0} returned for revision`}
          tone={averageTone}
        />
        <Stat
          label="Hazard library"
          value={hazards.total ?? 0}
          hint={`${hazards.activity_codes ?? 0} activity code(s)`}
        />
        <Stat
          label="Missions without approved MRM"
          value={missions.without_approved_mrm ?? 0}
          hint={`${missions.active ?? 0} active of ${missions.total ?? 0}`}
          tone={missions.without_approved_mrm ? "danger" : undefined}
        />
      </div>

      <div className="safety-grid">
        <section>
          <h3>Assessments by risk</h3>
          <ul className="safety-bars">
            <BarRow label="High" count={risk.high ?? 0} max={riskMax} color="var(--danger)" />
            <BarRow
              label="Medium"
              count={risk.medium ?? 0}
              max={riskMax}
              color="var(--warning)"
            />
            <BarRow label="Low" count={risk.low ?? 0} max={riskMax} color="var(--success)" />
            {risk.unclassified > 0 && (
              <BarRow
                label="Unrated"
                count={risk.unclassified}
                max={riskMax}
                color="var(--text-muted)"
              />
            )}
          </ul>
          <p className="hint">
            {mrm.draft ?? 0} draft · {mrm.submitted ?? 0} submitted · {mrm.reviewed ?? 0}{" "}
            reviewed
          </p>
        </section>

        <section>
          <h3>Hazard library by rating</h3>
          <ul className="safety-bars">
            <BarRow
              label="Unacceptable"
              count={hazards.red ?? 0}
              max={hazardMax}
              color="var(--danger)"
            />
            <BarRow
              label="Tolerable"
              count={hazards.yellow ?? 0}
              max={hazardMax}
              color="var(--warning)"
            />
            <BarRow
              label="Acceptable"
              count={hazards.green ?? 0}
              max={hazardMax}
              color="var(--success)"
            />
            {hazards.unclassified > 0 && (
              <BarRow
                label="Unrated"
                count={hazards.unclassified}
                max={hazardMax}
                color="var(--text-muted)"
              />
            )}
          </ul>
          <p className="hint">Ratings are the library's assessed (before-mitigation) risk.</p>
        </section>
      </div>

      <div
        className="inline-row"
        style={{ justifyContent: "space-between", marginBottom: 8 }}
      >
        <h3>
          {showHidden
            ? `Hidden assessments (${hiddenCount})`
            : "Assessments needing attention"}
        </h3>
        {(hiddenCount > 0 || showHidden) && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setShowHidden((value) => !value);
              setNotice("");
              setActionError("");
            }}
          >
            {showHidden ? "Back to open assessments" : `Show hidden (${hiddenCount})`}
          </Button>
        )}
      </div>
      <ErrorBanner message={actionError} />
      {notice && <div className="alert alert-success">{notice}</div>}
      <Table
        rows={rows}
        empty={
          showHidden
            ? "No assessments are hidden from this dashboard."
            : "No assessment is awaiting a decision."
        }
        columns={[
          {
            key: "mrm_number",
            label: "MRM #",
            render: (row) => <Link to={`/risk/${row.id}`}>{row.mrm_number}</Link>,
          },
          { key: "activity_mission", label: "Activity / Mission" },
          {
            key: "status",
            label: "Status",
            render: (row) => <StatusBadge status={row.status} />,
          },
          {
            key: "review_status",
            label: "Review",
            render: (row) => <StatusBadge status={row.review_status} />,
          },
          {
            key: "risk_category",
            label: "Risk",
            render: (row) => row.risk_category || "—",
          },
          {
            key: "total_percent",
            label: "Residual",
            render: (row) =>
              row.total_percent != null ? `${Number(row.total_percent).toFixed(1)}%` : "—",
          },
          {
            key: "actions",
            label: "Actions",
            render: (row) => (
              <span className="btn-row">
                {showHidden ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={busy === `restore-${row.id}`}
                    title={`Show ${row.mrm_number} on the attention list again`}
                    onClick={() => restore(row)}
                  >
                    {busy === `restore-${row.id}` ? "Restoring…" : "Restore"}
                  </Button>
                ) : (
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={busy === `hide-${row.id}`}
                    title={`Hide ${row.mrm_number} from this dashboard`}
                    onClick={() => hide(row)}
                  >
                    {busy === `hide-${row.id}` ? "Hiding…" : "Hide"}
                  </Button>
                )}
                {row.can_delete && (
                  <Button
                    variant="danger"
                    size="sm"
                    disabled={busy === `delete-${row.id}`}
                    title={`Delete ${row.mrm_number}`}
                    onClick={() => remove(row)}
                  >
                    {busy === `delete-${row.id}` ? "Deleting…" : "Delete"}
                  </Button>
                )}
              </span>
            ),
          },
        ]}
      />
      {showHidden && (
        <p className="hint" style={{ marginBottom: 0 }}>
          Hidden assessments stay in Risk Management. Restore one to bring it back
          to this dashboard.
        </p>
      )}
    </Card>
  );
}
