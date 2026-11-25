import { useState } from "react";
import { api } from "../api/client.js";
import { formatDateTime } from "../utils/format.js";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorBanner,
  Loading,
  Tabs,
} from "../components/ui.jsx";
import { useApi } from "../hooks/useApi.js";
import { refreshNotifications } from "../utils/notifications.js";

/**
 * Unit transfers requested from the profile page.
 *
 * A move is never applied when the person asks for it: the server records a
 * pending request and notifies Administrators through the bell. Approving here
 * ends every post held in the old unit and leaves the Creator role only;
 * declining leaves the account exactly as it is.
 */
const TABS = [
  { value: "pending", label: "Pending" },
  { value: "", label: "All requests" },
];

export default function UnitChangeRequestsPage() {
  const [scope, setScope] = useState("pending");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busyId, setBusyId] = useState(null);
  const {
    data,
    loading,
    error: loadError,
    reload,
  } = useApi(scope ? `/unit-changes/?status=${scope}` : "/unit-changes/", {
    deps: [scope],
  });
  const rows = data?.results ?? [];

  async function decide(row, approve) {
    let note = "";
    if (approve) {
      const answer = window.prompt(
        `Approve ${row.requester_name}'s move to ${row.to_unit_acronym}?\n\n` +
          `Every post held in ${row.from_unit_acronym || "the current unit"} will be ended ` +
          "and the account will hold the Creator role only in the new unit.\n\n" +
          "Approval note (optional):",
        ""
      );
      if (answer === null) return;
      note = answer.trim();
    } else {
      const answer = window.prompt(
        `Decline ${row.requester_name}'s move to ${row.to_unit_acronym}?\n\n` +
          "Record the reason (required):",
        ""
      );
      if (answer === null) return;
      note = answer.trim();
      if (!note) {
        setError("A reason is required to decline a unit change request.");
        return;
      }
    }

    setBusyId(row.id);
    setError("");
    setNotice("");
    try {
      await api.post(`/unit-changes/${row.id}/${approve ? "approve" : "reject"}/`, {
        note,
      });
      setNotice(
        approve
          ? `Approved. ${row.requester_name} was moved to ${row.to_unit_acronym}.`
          : `Declined. ${row.requester_name} keeps their current unit and posts.`
      );
      reload();
      refreshNotifications();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusyId(null);
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Unit Change Requests</h1>
          <p>
            Moving a person to another unit ends every post they hold where they
            were and leaves them with the Creator role only. Only an Administrator
            may approve a move; each request is also
            delivered to the notification bell.
          </p>
        </div>
      </div>

      <ErrorBanner message={loadError || error} onRetry={reload} />
      {notice && <div className="alert alert-success">{notice}</div>}

      <Card>
        <Tabs tabs={TABS} active={scope} onChange={setScope} />

        {loading && !data ? (
          <Loading />
        ) : !rows.length ? (
          <EmptyState
            message={
              scope === "pending"
                ? "No unit change requests are waiting for a decision."
                : "No unit change requests have been raised."
            }
          />
        ) : (
          <ul className="notification-list">
            {rows.map((row) => (
              <li key={row.id} className="notification">
                <div className="notification-main">
                  <div className="notification-title">
                    <strong>
                      {row.requester_rank ? `${row.requester_rank} ` : ""}
                      {row.requester_name || row.requester_username}
                    </strong>
                    <Badge tone={toneFor(row.status)}>{row.status_label}</Badge>
                  </div>
                  <p>
                    {row.from_unit_acronym || row.from_unit_name || "No unit"} →{" "}
                    <strong>
                      {row.to_unit_acronym}
                      {row.to_unit_name ? ` - ${row.to_unit_name}` : ""}
                    </strong>
                  </p>
                  <div className="notification-meta">
                    <span>@{row.requester_username}</span>
                    {row.requester_serial_number && (
                      <span>Serial {row.requester_serial_number}</span>
                    )}
                    <span>Requested {formatDateTime(row.created_at)}</span>
                    {row.decided_by_username && (
                      <span>Decided by @{row.decided_by_username}</span>
                    )}
                  </div>
                  {row.requester_note && (
                    <p className="hint">Request note: {row.requester_note}</p>
                  )}
                  {row.decision_note && (
                    <p className="hint">Decision note: {row.decision_note}</p>
                  )}
                </div>

                {row.status === "pending" && (
                  <div className="notification-actions">
                    <Button
                      size="sm"
                      onClick={() => decide(row, true)}
                      disabled={busyId === row.id}
                    >
                      {busyId === row.id ? "Saving…" : "Approve"}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => decide(row, false)}
                      disabled={busyId === row.id}
                    >
                      Decline
                    </Button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </>
  );
}

function toneFor(status) {
  if (status === "approved") return "success";
  if (status === "rejected") return "danger";
  if (status === "pending") return "warning";
  return "muted";
}
