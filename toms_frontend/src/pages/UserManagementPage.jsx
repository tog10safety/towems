import { useMemo, useState } from "react";
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
import { formatDate, formatDateTime } from "../utils/format.js";

const PAGE_SIZE = 25;
const CATEGORIES = ["Officer", "Enlisted Personnel", "Civilian Human Resource (CivHR)"];

/**
 * Administrator user, role and appointment management (spec §3, §7).
 *
 * The role pickers are built from `GET /api/roles/`, never from a list in this
 * file: the server is the only place that knows which roles exist, what scope
 * they carry and which of them may only have one holder per unit. Every action
 * here is re-checked server-side (`manage_users` / `manage_appointments`), so
 * hiding a control is cosmetic, not the control itself.
 */
export default function UserManagementPage() {
  const { user, permissions } = useAuth();
  const [search, setSearch] = useState("");
  const [authority, setAuthority] = useState("");
  const [status, setStatus] = useState("");
  const [unit, setUnit] = useState("");
  const [page, setPage] = useState(1);

  const [dialog, setDialog] = useState(null);
  const [form, setForm] = useState({});
  const [post, setPost] = useState({ role: "", unit_id: "", notes: "" });
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [resetResult, setResetResult] = useState(null);

  const path = `/admin/users/${qs({
    search,
    authority,
    is_active: status,
    unit,
    page,
    page_size: PAGE_SIZE,
  })}`;
  const { data, loading, error: loadError, reload } = useApi(path, {
    deps: [search, authority, status, unit, page],
    enabled: Boolean(permissions.manage_users),
  });
  const units = useApi("/units/?is_active=true&page_size=200", {
    enabled: Boolean(permissions.manage_users),
  });
  const catalogue = useApi("/roles/", { enabled: Boolean(permissions.manage_users) });

  const roles = useMemo(() => catalogue.data?.roles ?? [], [catalogue.data]);
  // Account roles live in the legacy users.authority enum; posts are unit
  // appointments. "both" (Safety Officer) appears in each picker.
  const accountRoles = roles.filter((row) => row.account_value);
  const appointmentRoles = roles.filter((row) => row.appointment);

  const postUnit = dialog?.type === "posts" ? post.unit_id : "";
  const unitPosts = useApi(
    postUnit ? `/appointments/?unit=${postUnit}&is_active=true&page_size=200` : null,
    { enabled: Boolean(postUnit) }
  );

  if (!permissions.manage_users) {
    return (
      <Card title="User management">
        <p className="hint">
          You do not have permission to manage user accounts. This action is restricted to the
          Administrator role and is enforced by the server.
        </p>
      </Card>
    );
  }

  const rows = data?.results ?? [];
  const unitOptions = units.data?.results ?? [];
  const selectedRole = roles.find((row) => row.value === post.role);
  const holderOfSelected = (unitPosts.data?.results ?? []).find(
    (row) => row.role === post.role && row.user !== dialog?.user?.id
  );

  function closeDialog() {
    setDialog(null);
    setForm({});
    setPost({ role: "", unit_id: "", notes: "" });
    setReason("");
    setError("");
    setResetResult(null);
  }

  function openEdit(row) {
    setNotice("");
    setResetResult(null);
    setError("");
    setForm({
      first_name: row.first_name || "",
      middle_name: row.middle_name || "",
      last_name: row.last_name || "",
      rank: row.rank || "",
      designation: row.designation || "",
      contact_number: row.contact_number || "",
      authority: row.authority,
      category: row.category,
      unit_id: row.unit_id ?? "",
    });
    setDialog({ type: "edit", user: row });
  }

  function openPosts(row) {
    setNotice("");
    setError("");
    setPost({ role: "", unit_id: row.unit_id ?? "", notes: "" });
    setDialog({ type: "posts", user: row });
  }

  function openStatus(row) {
    setNotice("");
    setError("");
    setReason("");
    setDialog({ type: row.is_active ? "deactivate" : "reactivate", user: row });
  }

  function openReset(row) {
    setNotice("");
    setError("");
    setResetResult(null);
    setDialog({ type: "reset", user: row });
  }

  async function saveEdit() {
    setBusy(true);
    setError("");
    try {
      const payload = { ...form };
      if (payload.unit_id === "") payload.unit_id = null;
      else payload.unit_id = Number(payload.unit_id);
      const updated = await api.patch(`/admin/users/${dialog.user.id}/`, payload);
      setNotice(`Updated @${updated.username}.`);
      closeDialog();
      reload();
    } catch (err) {
      setError(err.fieldErrors ? Object.values(err.fieldErrors).join(" ") : err.message);
    } finally {
      setBusy(false);
    }
  }

  /** Re-read the account so the dialog reflects what the server now holds. */
  async function refreshDialogUser(userId) {
    const fresh = await api.get(`/admin/users/${userId}/`);
    setDialog((current) => (current ? { ...current, user: fresh } : current));
    return fresh;
  }

  async function assignPost() {
    setBusy(true);
    setError("");
    try {
      await api.post("/appointments/", {
        unit_id: Number(post.unit_id),
        user_id: dialog.user.id,
        role: post.role,
        notes: post.notes,
      });
      const label = selectedRole?.label ?? post.role;
      setNotice(`Assigned ${label} to @${dialog.user.username}.`);
      setPost({ ...post, role: "", notes: "" });
      await refreshDialogUser(dialog.user.id);
      reload();
      unitPosts.reload();
    } catch (err) {
      setError(err.fieldErrors?.detail || err.fieldErrors?.role || err.message);
    } finally {
      setBusy(false);
    }
  }

  async function endPost(appointment) {
    setBusy(true);
    setError("");
    try {
      await api.del(`/appointments/${appointment.id}/`);
      setNotice(`Ended ${appointment.role_label} at ${appointment.unit_acronym || "the unit"}.`);
      await refreshDialogUser(dialog.user.id);
      reload();
      unitPosts.reload();
    } catch (err) {
      setError(err.fieldErrors?.detail || err.message);
    } finally {
      setBusy(false);
    }
  }

  async function saveStatus() {
    setBusy(true);
    setError("");
    try {
      const action = dialog.type === "deactivate" ? "deactivate" : "reactivate";
      await api.post(`/admin/users/${dialog.user.id}/${action}/`, { reason });
      setNotice(
        `${dialog.user.full_name || dialog.user.username} ${action === "deactivate" ? "deactivated" : "reactivated"}.`
      );
      closeDialog();
      reload();
    } catch (err) {
      setError(err.fieldErrors?.reason || err.fieldErrors?.detail || err.message);
    } finally {
      setBusy(false);
    }
  }

  async function issueReset() {
    setBusy(true);
    setError("");
    try {
      const result = await api.post(`/admin/users/${dialog.user.id}/reset-password/`, {});
      setResetResult(result);
      reload();
    } catch (err) {
      setError(err.fieldErrors?.detail || err.message);
    } finally {
      setBusy(false);
    }
  }

  async function copyLink() {
    const absolute = `${window.location.origin}${resetResult.reset_url}`;
    try {
      await navigator.clipboard.writeText(absolute);
      setNotice("Reset link copied to the clipboard.");
    } catch {
      window.prompt("Copy the reset link:", absolute);
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>User management</h1>
          <p>
            Accounts, roles and unit appointments. An account role sets what a person may do;
            a unit appointment places a post inside one unit. Accounts and their records are never
            deleted, and every change is audited.
          </p>
        </div>
      </div>

      <ErrorBanner message={loadError || error} onRetry={loadError ? reload : undefined} />
      {notice && <div className="alert alert-success">{notice}</div>}

      <Card>
        <div className="form-grid" style={{ marginBottom: 14 }}>
          <Input
            placeholder="Search name, username, serial number…"
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
              setPage(1);
            }}
          />
          <div className="inline-row">
            <Select
              value={authority}
              onChange={(event) => {
                setAuthority(event.target.value);
                setPage(1);
              }}
              aria-label="Filter by account role"
            >
              <option value="">All account roles</option>
              {accountRoles.map((row) => (
                <option key={row.value} value={row.account_value}>
                  {row.label}
                </option>
              ))}
            </Select>
            <Select
              value={status}
              onChange={(event) => {
                setStatus(event.target.value);
                setPage(1);
              }}
              aria-label="Filter by account status"
            >
              <option value="">All statuses</option>
              <option value="true">Active</option>
              <option value="false">Inactive</option>
            </Select>
            <Select
              value={unit}
              onChange={(event) => {
                setUnit(event.target.value);
                setPage(1);
              }}
              aria-label="Filter by unit"
            >
              <option value="">All units</option>
              {unitOptions.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.acronym || row.name}
                </option>
              ))}
            </Select>
          </div>
        </div>

        {loading && !data ? (
          <Loading label="Loading accounts…" />
        ) : (
          <>
            <Table
              rows={rows}
              empty="No accounts match the current filters."
              columns={[
                {
                  key: "name",
                  label: "Name",
                  render: (row) => (
                    <span>
                      <strong>{row.full_name || row.username}</strong>
                      <span className="hint" style={{ display: "block" }}>
                        @{row.username} · {row.serial_number}
                      </span>
                    </span>
                  ),
                },
                { key: "unit_acronym", label: "Unit", render: (row) => row.unit_acronym || "—" },
                {
                  key: "roles",
                  label: "Roles",
                  render: (row) => (
                    <span className="badge-stack">
                      <Badge tone="info">{row.authority}</Badge>
                      {(row.appointments ?? []).map((appointment) => (
                        <Badge key={appointment.id} tone="success">
                          {appointment.role_label}
                          {appointment.unit_acronym ? ` · ${appointment.unit_acronym}` : ""}
                          {appointment.is_detached_service ? " (DS)" : ""}
                        </Badge>
                      ))}
                      {(row.sections ?? [])
                        .filter((section) => section.function === "operations_center")
                        .map((section) => (
                          <Badge key={`office-${section.id}`} tone="info">
                            Operations Center
                            {section.unit_acronym ? ` · ${section.unit_acronym}` : ""}
                          </Badge>
                        ))}
                    </span>
                  ),
                },
                {
                  key: "is_active",
                  label: "Status",
                  render: (row) => (
                    <Badge tone={row.is_active ? "success" : "danger"}>
                      {row.is_active ? "Active" : "Inactive"}
                    </Badge>
                  ),
                },
                {
                  key: "password_expiry_date",
                  label: "Password",
                  render: (row) =>
                    row.force_password_change ? (
                      <Badge tone="warning">Change due</Badge>
                    ) : (
                      formatDate(row.password_expiry_date)
                    ),
                },
                {
                  key: "actions",
                  label: "Actions",
                  render: (row) => (
                    <div className="btn-row">
                      <Button size="sm" variant="ghost" onClick={() => openEdit(row)}>
                        Edit
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => openPosts(row)}>
                        Roles &amp; posts
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => openReset(row)}>
                        Reset password
                      </Button>
                      <Button
                        size="sm"
                        variant={row.is_active ? "danger" : "ghost"}
                        onClick={() => openStatus(row)}
                        disabled={row.id === user?.id}
                        title={row.id === user?.id ? "You cannot change your own status" : undefined}
                      >
                        {row.is_active ? "Deactivate" : "Reactivate"}
                      </Button>
                      <SuperAdminDelete
                        path={`/admin/users/${row.id}/`}
                        label={`Delete @${row.username} permanently`}
                        confirmText={`Delete @${row.username} permanently? Only an account with no missions, assessments, inventory movements or audit history can go.`}
                        disabled={row.id === user?.id || row.is_system_owner}
                        onDone={() => {
                          setNotice(`@${row.username} deleted permanently.`);
                          reload();
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

      {dialog && (
        <Modal
          title={
            dialog.type === "edit"
              ? `Edit @${dialog.user.username}`
              : dialog.type === "posts"
                ? `Roles & appointments · @${dialog.user.username}`
                : dialog.type === "reset"
                  ? `Reset password · @${dialog.user.username}`
                  : `${dialog.type === "deactivate" ? "Deactivate" : "Reactivate"} @${dialog.user.username}`
          }
          onClose={closeDialog}
          footer={
            <>
              <Button variant="ghost" onClick={closeDialog}>
                {resetResult ? "Close" : "Done"}
              </Button>
              {dialog.type === "edit" && (
                <Button onClick={saveEdit} disabled={busy}>
                  {busy ? "Saving…" : "Save changes"}
                </Button>
              )}
              {(dialog.type === "deactivate" || dialog.type === "reactivate") && (
                <Button
                  variant={dialog.type === "deactivate" ? "danger" : "primary"}
                  onClick={saveStatus}
                  disabled={busy}
                >
                  {busy ? "Saving…" : dialog.type === "deactivate" ? "Deactivate account" : "Reactivate account"}
                </Button>
              )}
              {dialog.type === "reset" && !resetResult && (
                <Button onClick={issueReset} disabled={busy || !dialog.user.is_active}>
                  {busy ? "Issuing…" : "Issue reset link"}
                </Button>
              )}
            </>
          }
        >
          <ErrorBanner message={error} />

          {dialog.type === "edit" && (
            <div className="form-grid">
              <Field label="First name" required>
                <Input
                  value={form.first_name}
                  onChange={(event) => setForm({ ...form, first_name: event.target.value })}
                />
              </Field>
              <Field label="Middle name">
                <Input
                  value={form.middle_name}
                  onChange={(event) => setForm({ ...form, middle_name: event.target.value })}
                />
              </Field>
              <Field label="Last name" required>
                <Input
                  value={form.last_name}
                  onChange={(event) => setForm({ ...form, last_name: event.target.value })}
                />
              </Field>
              <Field label="Rank">
                <Input
                  value={form.rank}
                  onChange={(event) => setForm({ ...form, rank: event.target.value })}
                />
              </Field>
              <Field label="Designation">
                <Input
                  value={form.designation}
                  onChange={(event) => setForm({ ...form, designation: event.target.value })}
                />
              </Field>
              <Field label="Contact number">
                <Input
                  value={form.contact_number}
                  onChange={(event) => setForm({ ...form, contact_number: event.target.value })}
                />
              </Field>
              <Field
                label="Account role"
                hint={
                  dialog.user.id === user?.id
                    ? "You cannot change your own role."
                    : "Account roles apply everywhere the person works. Command and staff posts are assigned under Roles & appointments."
                }
              >
                <Select
                  value={form.authority}
                  onChange={(event) => setForm({ ...form, authority: event.target.value })}
                  disabled={dialog.user.id === user?.id}
                >
                  {accountRoles.map((row) => (
                    <option key={row.value} value={row.account_value}>
                      {row.label}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Category">
                <Select
                  value={form.category}
                  onChange={(event) => setForm({ ...form, category: event.target.value })}
                >
                  {CATEGORIES.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Assigned unit" className="span-2">
                <Select
                  value={form.unit_id ?? ""}
                  onChange={(event) => setForm({ ...form, unit_id: event.target.value })}
                >
                  <option value="">Unassigned</option>
                  {unitOptions.map((row) => (
                    <option key={row.id} value={row.id}>
                      {row.acronym || row.name}
                    </option>
                  ))}
                </Select>
              </Field>
              <p className="hint span-2">
                Username, serial number and password are not editable here. The username and serial
                number are stable identifiers used by audit records.
              </p>
            </div>
          )}

          {dialog.type === "posts" && (
            <div>
              <dl className="details">
                <div className="detail">
                  <dt>Account role</dt>
                  <dd>
                    <Badge tone="info">{dialog.user.authority}</Badge>
                  </dd>
                </div>
                <div className="detail">
                  <dt>Assigned unit</dt>
                  <dd>{dialog.user.unit_acronym || dialog.user.unit_name || "Unassigned"}</dd>
                </div>
                <div className="detail">
                  <dt>Effective roles</dt>
                  <dd>
                    {(dialog.user.role_labels ?? [dialog.user.authority]).join(", ")}
                  </dd>
                </div>
              </dl>

              <h3 style={{ marginTop: 16 }}>Office memberships</h3>
              {(dialog.user.sections ?? []).length === 0 ? (
                <p className="hint">No office or section assignment.</p>
              ) : (
                <Table
                  rows={dialog.user.sections}
                  empty="No office memberships."
                  columns={[
                    {
                      key: "code",
                      label: "Office / section",
                      render: (row) => `${row.code} — ${row.name}`,
                    },
                    {
                      key: "function",
                      label: "Function",
                      render: (row) =>
                        row.function === "operations_center" ? (
                          <Badge tone="info">Operations Center</Badge>
                        ) : (
                          "General"
                        ),
                    },
                    { key: "assignment_type", label: "Type" },
                    {
                      key: "unit_acronym",
                      label: "Unit",
                      render: (row) => row.unit_acronym || row.unit_id,
                    },
                  ]}
                />
              )}
              <p className="hint">
                The Operations Center is an office, not a post: personnel are assigned to it under
                Offices &amp; Sections, and every active member then holds its routing authority for
                that unit.
              </p>

              <h3 style={{ marginTop: 16 }}>Active appointments</h3>
              {(dialog.user.appointments ?? []).length === 0 ? (
                <p className="hint">No unit appointments. This person holds only their account role.</p>
              ) : (
                <Table
                  rows={dialog.user.appointments}
                  empty="No active appointments."
                  columns={[
                    { key: "role_label", label: "Post" },
                    {
                      key: "unit_acronym",
                      label: "Unit",
                      render: (row) => row.unit_acronym || row.unit_name || row.unit_id,
                    },
                    {
                      key: "actions",
                      label: "",
                      render: (row) => (
                        <Button
                          size="sm"
                          variant="danger"
                          onClick={() => endPost(row)}
                          disabled={busy}
                        >
                          End
                        </Button>
                      ),
                    },
                  ]}
                />
              )}

              <h3 style={{ marginTop: 16 }}>Assign a post</h3>
              <div className="form-grid">
                <Field label="Post" required>
                  <Select
                    value={post.role}
                    onChange={(event) => setPost({ ...post, role: event.target.value })}
                  >
                    <option value="">Select a post…</option>
                    {appointmentRoles.map((row) => (
                      <option key={row.value} value={row.value}>
                        {row.label}
                        {row.exclusive ? " (one per unit)" : ""}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label="Unit" required>
                  <Select
                    value={post.unit_id}
                    onChange={(event) => setPost({ ...post, unit_id: event.target.value })}
                  >
                    <option value="">Select a unit…</option>
                    {unitOptions.map((row) => (
                      <option key={row.id} value={row.id}>
                        {row.acronym || row.name}
                      </option>
                    ))}
                  </Select>
                </Field>
                {selectedRole && (
                  <div className="span-2">
                    <p className="hint">{selectedRole.description}</p>
                    <p className="hint">
                      <strong>Authority scope:</strong> {selectedRole.scope_label}
                      {selectedRole.exclusive ? " · One active holder per unit." : ""}
                    </p>
                    <p className="hint">
                      <strong>Records visible:</strong> {selectedRole.read_scope_label}
                    </p>
                    <p className="hint">
                      <strong>Grants:</strong> {selectedRole.capability_labels.join(", ")}
                    </p>
                    {holderOfSelected && (
                      <div className="alert alert-warning">
                        {holderOfSelected.role_label} is already held at this unit by{" "}
                        @{holderOfSelected.username}. End that appointment before assigning a
                        replacement.
                      </div>
                    )}
                  </div>
                )}
                <Field label="Reason / notes (optional)" className="span-2">
                  <Textarea
                    rows={2}
                    value={post.notes}
                    onChange={(event) => setPost({ ...post, notes: event.target.value })}
                    placeholder="Authority for this appointment; recorded in the audit trail."
                  />
                </Field>
              </div>
              <div className="btn-row" style={{ marginTop: 10 }}>
                <Button
                  onClick={assignPost}
                  disabled={busy || !post.role || !post.unit_id || Boolean(holderOfSelected)}
                >
                  {busy ? "Assigning…" : "Assign post"}
                </Button>
              </div>
              <p className="hint" style={{ marginTop: 10 }}>
                Posts are separate from the account role: a Commander in one unit has no command
                authority over another unit, and no post grants Administrator access.
              </p>
            </div>
          )}

          {(dialog.type === "deactivate" || dialog.type === "reactivate") && (
            <>
              <p className="hint">
                {dialog.type === "deactivate"
                  ? "The account can no longer sign in. Their missions, assessments and audit history are preserved."
                  : "The account will be able to sign in again with its existing credentials."}
              </p>
              <Field
                label="Reason"
                required={dialog.type === "deactivate"}
                hint="Recorded in the audit trail with your username and the time."
              >
                <Textarea
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  rows={3}
                />
              </Field>
            </>
          )}

          {dialog.type === "reset" && !resetResult && (
            <>
              <p className="hint">
                This issues a single-use reset link. No existing password is displayed or sent.
                Share the link with the user through an approved channel; it expires after 60
                minutes and stops working after one use.
              </p>
              {!dialog.user.is_active && (
                <div className="alert alert-warning">
                  This account is inactive. Reactivate it before issuing a reset.
                </div>
              )}
            </>
          )}

          {resetResult && (
            <>
              <div className="alert alert-success">{resetResult.detail}</div>
              <Field label="One-time reset link" hint={`Expires ${formatDateTime(resetResult.expires_at)}.`}>
                <Input readOnly value={`${window.location.origin}${resetResult.reset_url}`} onFocus={(event) => event.target.select()} />
              </Field>
              <div className="btn-row">
                <Button size="sm" onClick={copyLink}>
                  Copy link
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => window.open(resetResult.reset_url, "_blank", "noopener")}
                >
                  Open in new tab
                </Button>
              </div>
              <p className="hint" style={{ marginTop: 10 }}>
                The token is shown only once. Closing this dialog discards it; issue a new link if
                it is lost.
              </p>
            </>
          )}
        </Modal>
      )}
    </>
  );
}
