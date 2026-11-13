import { useState } from "react";
import { Link } from "react-router-dom";
import { api, qs } from "../api/client.js";
import { useAuth } from "../auth/AuthContext.jsx";
import { formatDateTime } from "../utils/format.js";
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
  Tabs,
  Textarea,
} from "../components/ui.jsx";
import { useApi } from "../hooks/useApi.js";
import { refreshNotifications } from "../utils/notifications.js";
import { byRank } from "../utils/ranks.js";

const TABS = [
  { value: "outstanding", label: "Outstanding" },
  { value: "mine", label: "Addressed to me" },
  { value: "issued", label: "Issued by me" },
  { value: "", label: "All visible" },
];

const EMPTY = {
  unit_id: "",
  subject: "",
  body: "",
  recipient_ids: [],
  priority: "normal",
  requires_acknowledgement: true,
  requires_completion: false,
};

/** Instructions and action items issued by command or functional posts. */
export default function InstructionsPage() {
  const { permissions } = useAuth();
  // Receiving is universal; issuing is a command act the Creator account
  // deliberately lacks, so the button and the unit pickers stay hidden.
  const canIssue = Boolean(permissions.can_issue_instructions);
  // "All visible" lists every record in scope; the command posts and the unit's
  // Operations Center hold it. Everyone else keeps their own two views: the
  // items addressed to them and the ones they issued.
  const canSeeAll = Boolean(permissions.can_view_all_instructions);
  const tabs = canSeeAll ? TABS : TABS.filter((tab) => tab.value !== "");
  const [scope, setScope] = useState("outstanding");
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(EMPTY);
  const [peopleSearch, setPeopleSearch] = useState("");
  const [errors, setErrors] = useState({});
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  // The server offers only the caller's own unit and its subordinates: an
  // instruction is a command act and cannot reach a sister unit.
  const units = useApi("/instructions/units/", { enabled: canIssue });
  const personnel = useApi(
    `/personnel/${qs({ is_active: true, page_size: 100, search: peopleSearch })}`,
    { deps: [peopleSearch] }
  );
  const allowedUnitIds = new Set((units.data?.results ?? []).map((unit) => unit.id));
  // Highest rank first, then name; the picker reads serial, rank and surname.
  const recipientChoices = (personnel.data?.results ?? [])
    .filter((person) => allowedUnitIds.has(person.unit_id))
    .sort(byRank);

  function recipientLabel(person) {
    const name = [person.rank, person.last_name || person.full_name].filter(Boolean).join(" ");
    return person.serial_number ? `${person.serial_number}: ${name}` : name;
  }
  const { data, loading, error: loadError, reload } = useApi(
    `/instructions/${qs({ scope, page_size: 50 })}`,
    { deps: [scope] }
  );

  async function create(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setErrors({});
    try {
      await api.post("/instructions/", {
        ...form,
        unit_id: Number(form.unit_id),
        recipient_ids: form.recipient_ids.map(Number),
      });
      setOpen(false);
      setForm(EMPTY);
      setNotice("Instruction issued. Recipients have a persistent action item.");
      reload();
      refreshNotifications();
    } catch (err) {
      setErrors(err.fieldErrors || {});
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  function toggleRecipient(id) {
    const value = Number(id);
    setForm((current) => ({
      ...current,
      recipient_ids: current.recipient_ids.includes(value)
        ? current.recipient_ids.filter((item) => item !== value)
        : [...current.recipient_ids, value],
    }));
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Instructions & Action Items</h1>
          <p>
            Notes, instructions and corrective actions. Each recipient acknowledges and completes
            the work; the full history stays on the record.
          </p>
        </div>
        {canIssue && <Button onClick={() => setOpen(true)}>Issue instruction</Button>}
      </div>

      <ErrorBanner message={loadError || error} onRetry={reload} />
      {notice && <div className="alert alert-success">{notice}</div>}

      <Card>
        <Tabs tabs={tabs} active={scope} onChange={setScope} />

        {loading && !data ? (
          <Loading />
        ) : !data?.results?.length ? (
          <EmptyState message="No instructions in this view." />
        ) : (
          <ul className="notification-list">
            {data.results.map((item) => (
              <li key={item.id} className="notification">
                  <div className="notification-main">
                    <div className="notification-title">
                      <Link to={`/instructions/${item.id}`}>
                        <strong>{item.subject}</strong>
                      </Link>
                      <Badge tone={item.priority === "high" || item.priority === "urgent" ? "danger" : "muted"}>
                        {item.priority}
                      </Badge>
                      <Badge tone={item.status === "verified" ? "success" : "warning"}>
                        {item.status.replace(/_/g, " ")}
                      </Badge>
                    </div>
                    <p>{item.body}</p>
                    <div className="notification-meta">
                      <span>From @{item.sender_username}</span>
                      <span>{item.unit_acronym}</span>
                      <span>{item.recipients?.length ?? 0} recipient(s)</span>
                      <span>{formatWhen(item.created_at)}</span>
                      {item.due_at && <span>Due {formatWhen(item.due_at)}</span>}
                    </div>
                  </div>
                <div className="notification-actions">
                  <Link className="btn btn-ghost btn-sm" to={`/instructions/${item.id}`}>
                    Open
                  </Link>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {open && (
        <Modal
          title="Issue instruction"
          onClose={() => setOpen(false)}
          wide
          footer={
            <>
              <Button variant="ghost" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button onClick={create} disabled={busy}>
                {busy ? "Issuing…" : "Issue instruction"}
              </Button>
            </>
          }
        >
          <ErrorBanner message={error} />
          <form onSubmit={create}>
            <div className="form-grid">
              <Field
                label="Unit"
                required
                error={errors.unit_id}
                hint="Only your unit and its subordinate units are listed."
              >
                <Select
                  value={form.unit_id}
                  onChange={(event) => setForm({ ...form, unit_id: event.target.value })}
                  required
                >
                  <option value="">Select unit…</option>
                  {(units.data?.results ?? []).map((unit) => (
                    <option key={unit.id} value={unit.id}>
                      {unit.acronym || unit.name}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Priority">
                <Select
                  value={form.priority}
                  onChange={(event) => setForm({ ...form, priority: event.target.value })}
                >
                  <option value="low">Low</option>
                  <option value="normal">Normal</option>
                  <option value="high">High</option>
                  <option value="urgent">Urgent</option>
                </Select>
              </Field>
              <Field label="Subject" required error={errors.subject} className="span-2">
                <Input
                  value={form.subject}
                  onChange={(event) => setForm({ ...form, subject: event.target.value })}
                  required
                />
              </Field>
              <Field label="Instruction" required error={errors.body} className="span-2">
                <Textarea
                  value={form.body}
                  onChange={(event) => setForm({ ...form, body: event.target.value })}
                  rows={5}
                  required
                />
              </Field>
              <Field label="Recipients" required error={errors.recipient_ids} className="span-2">
                <Input
                  value={peopleSearch}
                  onChange={(event) => setPeopleSearch(event.target.value)}
                  placeholder="Search personnel by username, name or serial…"
                  aria-label="Search personnel"
                />
                <p className="hint">{form.recipient_ids.length} selected</p>
                <div className="checklist">
                  {recipientChoices.map((person) => (
                    <label key={person.id} className="check">
                      <input
                        type="checkbox"
                        checked={form.recipient_ids.includes(person.id)}
                        onChange={() => toggleRecipient(person.id)}
                      />
                      {recipientLabel(person)}
                    </label>
                  ))}
                </div>
              </Field>
              <Field label="Acknowledgement">
                <label className="check">
                  <input
                    type="checkbox"
                    checked={form.requires_acknowledgement}
                    onChange={(event) =>
                      setForm({ ...form, requires_acknowledgement: event.target.checked })
                    }
                  />
                  Recipient must acknowledge
                </label>
              </Field>
              <Field label="Completion">
                <label className="check">
                  <input
                    type="checkbox"
                    checked={form.requires_completion}
                    onChange={(event) =>
                      setForm({ ...form, requires_completion: event.target.checked })
                    }
                  />
                  Sender must verify completion
                </label>
              </Field>
            </div>
          </form>
        </Modal>
      )}
    </>
  );
}

// Philippine time, 24-hour military format (see utils/format.js).
const formatWhen = formatDateTime;
