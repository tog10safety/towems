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
import { useApi } from "../hooks/useApi.js";
import { formatDateTime } from "../utils/format.js";

const PAGE_SIZE = 25;

/**
 * Administrator register of Android installs enrolled for biometric refresh.
 * Revoking a row immediately invalidates every access and refresh token that
 * device holds; authorising only lets the person enrol again after a new
 * password + MFA sign-in.
 */
export default function MobileDevicesPage() {
  const { permissions } = useAuth();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [dialog, setDialog] = useState(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const path = `/admin/mobile-devices/${qs({
    search,
    is_authorized: status,
    page,
    page_size: PAGE_SIZE,
  })}`;
  const { data, loading, error: loadError, reload } = useApi(path, {
    deps: [search, status, page],
    enabled: Boolean(permissions.manage_users),
  });

  if (!permissions.manage_users) {
    return (
      <Card title="Android devices">
        <p className="hint">
          Device management is restricted to the Administrator role and enforced
          by the server.
        </p>
      </Card>
    );
  }

  const rows = data?.results ?? [];

  function openAction(type, row) {
    setError("");
    setNotice("");
    setReason("");
    setDialog({ type, row });
  }

  async function submitAction() {
    if (!dialog) return;
    setBusy(true);
    setError("");
    try {
      if (dialog.type === "revoke") {
        await api.post(`/admin/mobile-devices/${dialog.row.id}/revoke/`, { reason });
        setNotice(`Revoked ${dialog.row.username}'s ${dialog.row.label || "Android device"}.`);
      } else {
        await api.post(`/admin/mobile-devices/${dialog.row.id}/authorize/`, {});
        setNotice(`Authorised ${dialog.row.username}'s ${dialog.row.label || "Android device"}.`);
      }
      setDialog(null);
      reload();
    } catch (err) {
      setError(err.fieldErrors?.reason || err.fieldErrors?.detail || err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Android devices</h1>
          <p>
            Installs enrolled for fingerprint or face sign-in. Revocation ends
            the device&apos;s access and refresh tokens immediately. The person
            must sign in with their password and authenticator before enrolling
            again.
          </p>
        </div>
      </div>

      <ErrorBanner message={loadError || error} onRetry={loadError ? reload : undefined} />
      {notice && <div className="alert alert-success">{notice}</div>}

      <Card>
        <div className="form-grid" style={{ marginBottom: 14 }}>
          <Input
            placeholder="Search name, username, serial number, device…"
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
            aria-label="Filter by device authorisation"
          >
            <option value="">All devices</option>
            <option value="true">Authorised</option>
            <option value="false">Revoked</option>
          </Select>
        </div>

        {loading && !data ? (
          <Loading label="Loading Android devices…" />
        ) : (
          <>
            <Table
              rows={rows}
              empty="No Android devices match the current filters."
              columns={[
                {
                  key: "user",
                  label: "Account",
                  render: (row) => (
                    <span>
                      <strong>{row.full_name || row.username}</strong>
                      <span className="hint" style={{ display: "block" }}>
                        @{row.username} · {row.serial_number || "No serial"}
                      </span>
                    </span>
                  ),
                },
                {
                  key: "device",
                  label: "Device",
                  render: (row) => (
                    <span>
                      {row.label || "Android device"}
                      <span className="hint" style={{ display: "block" }}>
                        {row.platform} {row.app_version ? `· ${row.app_version}` : ""}
                      </span>
                    </span>
                  ),
                },
                { key: "unit_name", label: "Unit", render: (row) => row.unit_name || "—" },
                {
                  key: "status",
                  label: "Status",
                  render: (row) => (
                    <Badge tone={row.status === "active" ? "success" : row.status === "revoked" ? "danger" : "warning"}>
                      {row.status === "active"
                        ? "Active session"
                        : row.status === "revoked"
                          ? "Revoked"
                          : "Authorised"}
                    </Badge>
                  ),
                },
                {
                  key: "last_seen_at",
                  label: "Last seen",
                  render: (row) => (row.last_seen_at ? formatDateTime(row.last_seen_at) : "Never"),
                },
                {
                  key: "active_sessions",
                  label: "Sessions",
                  render: (row) => row.active_sessions ?? 0,
                },
                {
                  key: "actions",
                  label: "Actions",
                  render: (row) => (
                    <div className="btn-row">
                      {row.is_authorized ? (
                        <Button
                          size="sm"
                          variant="danger"
                          onClick={() => openAction("revoke", row)}
                        >
                          Revoke
                        </Button>
                      ) : (
                        <Button size="sm" onClick={() => openAction("authorize", row)}>
                          Authorise
                        </Button>
                      )}
                    </div>
                  ),
                },
              ]}
            />
            <Pagination
              page={page}
              pageSize={PAGE_SIZE}
              count={data?.count ?? 0}
              onPage={setPage}
            />
          </>
        )}
      </Card>

      {dialog && (
        <Modal
          title={
            dialog.type === "revoke"
              ? `Revoke ${dialog.row.label || "Android device"}`
              : `Authorise ${dialog.row.label || "Android device"}`
          }
          onClose={() => setDialog(null)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setDialog(null)} disabled={busy}>
                Cancel
              </Button>
              <Button
                variant={dialog.type === "revoke" ? "danger" : "primary"}
                onClick={submitAction}
                disabled={busy || (dialog.type === "revoke" && reason.trim().length < 3)}
              >
                {busy
                  ? "Saving…"
                  : dialog.type === "revoke"
                    ? "Revoke device"
                    : "Authorise device"}
              </Button>
            </>
          }
        >
          <ErrorBanner message={error} />
          <p className="hint">
            Account: @{dialog.row.username} ({dialog.row.full_name || "no name"})
          </p>
          {dialog.type === "revoke" ? (
            <>
              <p className="hint">
                The device&apos;s access token and refresh token stop working on the
                next request. The person must sign in with password + authenticator
                and be authorised again.
              </p>
              <Field label="Reason" required>
                <Textarea
                  rows={3}
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  placeholder="Why is this device being revoked?"
                />
              </Field>
            </>
          ) : (
            <p className="hint">
              This allows the install to enrol again, but does not create a session.
              The person must complete the password and authenticator steps first.
            </p>
          )}
        </Modal>
      )}
    </>
  );
}
