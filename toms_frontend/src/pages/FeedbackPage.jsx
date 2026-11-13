import { useEffect, useState } from "react";
import { api, qs } from "../api/client.js";
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
  Table,
  Tabs,
  Textarea,
} from "../components/ui.jsx";
import { useApi } from "../hooks/useApi.js";
import { useAuth } from "../auth/AuthContext.jsx";
import { formatDate, formatDateTime } from "../utils/format.js";

const PAGE_SIZE = 25;

const RATING_FIELDS = [
  {
    key: "risk_assessment_accuracy",
    label: "How accurate was the risk assessment?",
    low: "very inaccurate",
    high: "very accurate",
  },
  {
    key: "hazard_identification_rating",
    label: "How well were the hazards identified?",
    low: "missed most",
    high: "identified all",
  },
  {
    key: "mitigation_effectiveness",
    label: "How effective were the mitigations?",
    low: "ineffective",
    high: "very effective",
  },
];

const TEXT_FIELDS = [
  {
    key: "what_went_well",
    label: "What went well?",
    placeholder: "Anything the plan got right and should keep doing.",
  },
  {
    key: "what_could_be_improved",
    label: "What could be improved?",
    placeholder: "Where the plan or the briefing fell short.",
  },
  {
    key: "new_hazards_identified",
    label: "New hazards identified",
    placeholder: "Hazards the assessment did not list but the crew met.",
  },
  {
    key: "lessons_learned",
    label: "Lessons learned",
    placeholder: "What the next crew should know before it rolls.",
  },
  {
    key: "additional_comments",
    label: "Additional comments",
    placeholder: "Optional.",
  },
];

const EMPTY_FEEDBACK = {
  risk_assessment_accuracy: "",
  hazard_identification_rating: "",
  mitigation_effectiveness: "",
  what_went_well: "",
  what_could_be_improved: "",
  new_hazards_identified: "",
  lessons_learned: "",
  additional_comments: "",
  recommend_add_to_hazard_registry: false,
  recommended_hazard_description: "",
  recommended_mitigations: "",
  is_anonymous: false,
};

function ratingCell(value) {
  return value ? <Badge tone={value >= 4 ? "success" : value <= 2 ? "danger" : "warning"}>{value}/5</Badge> : <span className="hint">—</span>;
}

function statusTone(status) {
  if (status === "pending") return "warning";
  if (status === "actioned") return "success";
  if (status === "reviewed") return "info";
  return "muted";
}

/**
 * Post-mission feedback.
 *
 * Everyone sees the assessments they were tagged on: the ones already filed,
 * the ones waiting on them, and the ones whose mission has not finished yet.
 * The Unit Safety Officer additionally gets the audit queue, where each
 * submission is read, actioned or dismissed, and a recommended hazard can be
 * adopted straight into the unit's registry.
 */
export default function FeedbackPage() {
  const { permissions } = useAuth();
  const canAudit = Boolean(permissions.can_audit_feedback);
  const [tab, setTab] = useState("requests");
  const [message, setMessage] = useState("");
  const [failure, setFailure] = useState("");
  const [filing, setFiling] = useState(null);
  const [auditing, setAuditing] = useState(null);

  const [unit, setUnit] = useState("");
  const [status, setStatus] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);

  const requests = useApi("/feedback/requests/");
  const { data: units } = useApi("/risk-register/units/", { enabled: canAudit });

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setQuery(search.trim());
      setPage(1);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [search]);

  const auditPath = `/feedback/audit/${qs({
    unit,
    status,
    date_from: dateFrom,
    date_to: dateTo,
    q: query,
    page,
    page_size: PAGE_SIZE,
  })}`;
  const audit = useApi(auditPath, {
    enabled: canAudit && tab === "audit",
    deps: [canAudit, tab, unit, status, dateFrom, dateTo, query, page],
  });

  const body = requests.data ?? {};
  const requested = body.requested ?? [];
  const upcoming = body.upcoming ?? [];
  const submitted = body.submitted ?? [];
  const summary = audit.data?.summary ?? {};
  const rows = audit.data?.results ?? [];

  function refreshAll() {
    requests.reload();
    if (canAudit) audit.reload();
  }

  function saved(text) {
    setFiling(null);
    setAuditing(null);
    setMessage(text);
    setFailure("");
    refreshAll();
  }

  const tabs = [
    { value: "requests", label: `My feedback${requested.length ? ` (${requested.length})` : ""}` },
    ...(canAudit ? [{ value: "audit", label: "Safety audit" }] : []),
  ];

  const columns = [
    {
      key: "submitted_at",
      label: "Filed",
      width: "170px",
      render: (row) => formatDateTime(row.submitted_at),
    },
    {
      key: "mrm_number",
      label: "Assessment",
      render: (row) => (
        <div>
          <strong>{row.mrm_number}</strong>
          <p className="hint" style={{ margin: "2px 0 0" }}>
            {row.activity_mission}
          </p>
        </div>
      ),
    },
    { key: "unit_acronym", label: "Unit" },
    { key: "submitter", label: "Submitted by" },
    {
      key: "mitigation_effectiveness",
      label: "Mitigations",
      width: "120px",
      render: (row) => ratingCell(row.mitigation_effectiveness),
    },
    {
      key: "recommend_add_to_hazard_registry",
      label: "Recommends",
      width: "120px",
      render: (row) =>
        row.recommend_add_to_hazard_registry ? (
          <Badge tone="info">New hazard</Badge>
        ) : (
          <span className="hint">—</span>
        ),
    },
    {
      key: "status",
      label: "Status",
      width: "150px",
      render: (row) => (
        <Badge tone={statusTone(row.status)}>{row.status_label || row.status}</Badge>
      ),
    },
  ];

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Post-Mission Feedback</h1>
          <p>
            What actually happened out there. People tagged on an MRM report it after the
            mission; the unit's Safety Officer audits every answer and can turn a
            recommendation into a hazard-library entry.
          </p>
        </div>
        <Button variant="ghost" size="sm" onClick={refreshAll}>
          Refresh
        </Button>
      </div>

      <ErrorBanner message={failure} />
      {message && (
        <div className="alert alert-success" role="status">
          {message}
        </div>
      )}

      <Tabs tabs={tabs} active={tab} onChange={setTab} />

      {tab === "requests" && (
        <>
          {requests.loading && !requests.data ? (
            <Loading label="Loading your feedback tasks…" />
          ) : requests.error ? (
            <ErrorBanner message={requests.error} onRetry={requests.reload} />
          ) : (
            <>
              <Card
                title={`Waiting for your feedback${requested.length ? ` (${requested.length})` : ""}`}
              >
                {requested.length === 0 ? (
                  <p className="hint">
                    Nothing is waiting on you. You are asked for feedback once an assessment you
                    were tagged on is approved and its mission is completed.
                  </p>
                ) : (
                  <div className="grid grid-2">
                    {requested.map((row) => (
                      <section className="card" key={row.mrm}>
                        <header className="card-head">
                          <div>
                            <h2>{row.mrm_number}</h2>
                            <p className="hint" style={{ margin: 0 }}>
                              {row.activity_mission}
                            </p>
                          </div>
                          <Badge tone="warning">Feedback due</Badge>
                        </header>
                        <div className="card-body">
                          <dl className="details">
                            <div className="detail">
                              <dt>Unit</dt>
                              <dd>{row.unit_acronym || row.unit_name || "—"}</dd>
                            </div>
                            <div className="detail">
                              <dt>Mission</dt>
                              <dd>
                                {row.mission
                                  ? `${row.mission.mission_number} — ${row.mission.title}`
                                  : "Not linked to a mission"}
                              </dd>
                            </div>
                            <div className="detail">
                              <dt>Approved</dt>
                              <dd>{formatDate(row.date_approved)}</dd>
                            </div>
                          </dl>
                          <Button size="sm" onClick={() => setFiling(row)}>
                            File feedback
                          </Button>
                        </div>
                      </section>
                    ))}
                  </div>
                )}
              </Card>

              {upcoming.length > 0 && (
                <Card title={`Not open yet (${upcoming.length})`}>
                  <ul className="recipient-list">
                    {upcoming.map((row) => (
                      <li key={row.mrm}>
                        <div>
                          <strong>{row.mrm_number}</strong>
                          <p className="hint" style={{ margin: "2px 0 0" }}>
                            {row.activity_mission}
                          </p>
                        </div>
                        <span className="hint">{row.reason}</span>
                      </li>
                    ))}
                  </ul>
                </Card>
              )}

              <Card title={`Filed (${submitted.length})`}>
                {submitted.length === 0 ? (
                  <p className="hint">You have not filed feedback yet.</p>
                ) : (
                  <div className="grid grid-2">
                    {submitted.map((row) => (
                      <section className="card" key={row.mrm}>
                        <header className="card-head">
                          <div>
                            <h2>{row.mrm_number}</h2>
                            <p className="hint" style={{ margin: 0 }}>
                              {row.activity_mission}
                            </p>
                          </div>
                          <Badge tone={statusTone(row.feedback.status)}>
                            {row.feedback.status_label}
                          </Badge>
                        </header>
                        <div className="card-body">
                          <p className="hint">
                            Filed {formatDateTime(row.feedback.submitted_at)}
                            {row.feedback.is_anonymous ? " · anonymous" : ""}
                          </p>
                          {row.feedback.review_notes && (
                            <p>
                              <strong>Safety Officer:</strong> {row.feedback.review_notes}
                            </p>
                          )}
                          {row.feedback.action_taken && (
                            <p>
                              <strong>Action taken:</strong> {row.feedback.action_taken}
                            </p>
                          )}
                        </div>
                      </section>
                    ))}
                  </div>
                )}
              </Card>
            </>
          )}
        </>
      )}

      {tab === "audit" && canAudit && (
        <>
          <div className="stats">
            <Stat label="Awaiting audit" value={summary.awaiting_audit ?? "—"} tone="warning" />
            <Stat label="Reviewed" value={summary.reviewed ?? "—"} />
            <Stat label="Actioned" value={summary.actioned ?? "—"} tone="success" />
            <Stat label="Dismissed" value={summary.dismissed ?? "—"} />
            <Stat
              label="Avg mitigation"
              value={
                summary.average_mitigation_effectiveness == null
                  ? "—"
                  : `${summary.average_mitigation_effectiveness}/5`
              }
            />
            <Stat
              label="Hazard recommendations"
              value={summary.recommendations_pending ?? "—"}
              tone="info"
            />
          </div>

          <div className="inline-row" style={{ margin: "16px 0 12px" }}>
            <Field label="Unit">
              <Select
                value={unit}
                onChange={(event) => {
                  setUnit(event.target.value);
                  setPage(1);
                }}
              >
                <option value="">All units in scope</option>
                {(units?.results ?? []).map((row) => (
                  <option key={row.id} value={row.id}>
                    {row.acronym || row.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Status">
              <Select
                value={status}
                onChange={(event) => {
                  setStatus(event.target.value);
                  setPage(1);
                }}
              >
                <option value="">All statuses</option>
                <option value="pending">Awaiting audit</option>
                <option value="reviewed">Reviewed</option>
                <option value="actioned">Actioned</option>
                <option value="dismissed">Dismissed</option>
              </Select>
            </Field>
            <Field label="Filed from">
              <Input
                type="date"
                value={dateFrom}
                onChange={(event) => {
                  setDateFrom(event.target.value);
                  setPage(1);
                }}
              />
            </Field>
            <Field label="Filed to">
              <Input
                type="date"
                value={dateTo}
                onChange={(event) => {
                  setDateTo(event.target.value);
                  setPage(1);
                }}
              />
            </Field>
            <Field label="Search">
              <Input
                type="search"
                value={search}
                placeholder="MRM, mission or answer…"
                onChange={(event) => setSearch(event.target.value)}
              />
            </Field>
          </div>

          {audit.loading && !audit.data ? (
            <Loading label="Loading the feedback queue…" />
          ) : audit.error ? (
            <ErrorBanner message={audit.error} onRetry={audit.reload} />
          ) : (
            <>
              <Card>
                <Table
                  columns={columns}
                  rows={rows}
                  empty="No feedback matches these filters."
                  onRowClick={(row) => setAuditing(row)}
                />
              </Card>
              <Pagination
                page={page}
                pageSize={PAGE_SIZE}
                count={audit.data?.count ?? 0}
                onPage={setPage}
              />
            </>
          )}
        </>
      )}

      {filing && (
        <FeedbackForm
          request={filing}
          onClose={() => setFiling(null)}
          onSaved={() => saved("Feedback filed. The Unit Safety Officer has been notified.")}
        />
      )}

      {auditing && (
        <FeedbackAuditDialog
          row={auditing}
          onClose={() => setAuditing(null)}
          onSaved={saved}
        />
      )}
    </>
  );
}

function FeedbackForm({ request, onClose, onSaved }) {
  const [form, setForm] = useState({ ...EMPTY_FEEDBACK });
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState("");
  const [busy, setBusy] = useState(false);

  function update(key, value) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setFailure("");
    setErrors({});
    try {
      await api.post(`/feedback/requests/${request.mrm}/`, form);
      onSaved();
    } catch (err) {
      setFailure(err.message || "The feedback could not be filed.");
      setErrors(err.fieldErrors || {});
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title={`Feedback · ${request.mrm_number}`}
      onClose={onClose}
      wide
      footer={
        <div className="btn-row">
          <Button variant="ghost" onClick={onClose} type="button">
            Cancel
          </Button>
          <Button type="submit" form="feedback-form" disabled={busy}>
            {busy ? "Filing…" : "File feedback"}
          </Button>
        </div>
      }
    >
      <form id="feedback-form" onSubmit={submit}>
        <ErrorBanner message={failure} />
        <p className="hint">
          {request.activity_mission} · {request.unit_acronym || request.unit_name}
          {request.mission ? ` · ${request.mission.mission_number}` : ""}
        </p>

        <div className="form-grid">
          {RATING_FIELDS.map((field) => (
            <Field key={field.key} label={field.label} error={errors[field.key]}>
              <Select
                value={form[field.key]}
                onChange={(event) => update(field.key, event.target.value)}
              >
                <option value="">Not rated</option>
                {[1, 2, 3, 4, 5].map((value) => (
                  <option key={value} value={value}>
                    {value} —{" "}
                    {value === 1 ? field.low : value === 5 ? field.high : `${value} of 5`}
                  </option>
                ))}
              </Select>
            </Field>
          ))}
        </div>

        {TEXT_FIELDS.map((field) => (
          <Field key={field.key} label={field.label} error={errors[field.key]}>
            <Textarea
              rows={3}
              value={form[field.key]}
              placeholder={field.placeholder}
              onChange={(event) => update(field.key, event.target.value)}
            />
          </Field>
        ))}

        <div className="card">
          <div className="card-body">
            <label className="check-row">
              <input
                type="checkbox"
                checked={form.recommend_add_to_hazard_registry}
                onChange={(event) =>
                  update("recommend_add_to_hazard_registry", event.target.checked)
                }
              />
              <span>Recommend a hazard for the unit's registry</span>
            </label>
            <p className="hint">
              The Safety Officer can adopt this straight into the library so the next crew
              plans for it.
            </p>
            {form.recommend_add_to_hazard_registry && (
              <>
                <Field
                  label="Hazard description"
                  required
                  error={errors.recommended_hazard_description}
                >
                  <Input
                    value={form.recommended_hazard_description}
                    onChange={(event) =>
                      update("recommended_hazard_description", event.target.value)
                    }
                    placeholder="What should be in the library?"
                  />
                </Field>
                <Field label="Proposed mitigations">
                  <Textarea
                    rows={3}
                    value={form.recommended_mitigations}
                    onChange={(event) =>
                      update("recommended_mitigations", event.target.value)
                    }
                    placeholder="What would control it?"
                  />
                </Field>
              </>
            )}
            <label className="check-row" style={{ marginTop: 10 }}>
              <input
                type="checkbox"
                checked={form.is_anonymous}
                onChange={(event) => update("is_anonymous", event.target.checked)}
              />
              <span>File this anonymously</span>
            </label>
            <p className="hint">
              The Safety Officer always sees who filed feedback that is not anonymous; the
              unit report hides names when you choose this.
            </p>
          </div>
        </div>
      </form>
    </Modal>
  );
}

function FeedbackAuditDialog({ row, onClose, onSaved }) {
  const [status, setStatus] = useState(row.status === "pending" ? "reviewed" : row.status);
  const [notes, setNotes] = useState(row.review_notes || "");
  const [action, setAction] = useState(row.action_taken || "");
  const [busy, setBusy] = useState("");
  const [failure, setFailure] = useState("");
  const [errors, setErrors] = useState({});

  const [hazard, setHazard] = useState({
    activity_code: "",
    before_likelihood: "4",
    before_severity: "C",
    after_likelihood: "2",
    after_severity: "D",
    mitigations: row.recommended_mitigations || "",
  });

  async function review(event) {
    event.preventDefault();
    setBusy("review");
    setFailure("");
    setErrors({});
    try {
      await api.post(`/feedback/audit/${row.id}/review/`, {
        status,
        review_notes: notes,
        action_taken: action,
      });
      onSaved("Audit recorded. The submitter has been notified.");
    } catch (err) {
      setFailure(err.message || "The audit could not be saved.");
      setErrors(err.fieldErrors || {});
    } finally {
      setBusy("");
    }
  }

  async function adopt(event) {
    event.preventDefault();
    setBusy("adopt");
    setFailure("");
    setErrors({});
    try {
      await api.post(`/feedback/audit/${row.id}/adopt/`, {
        ...hazard,
        before_likelihood: Number(hazard.before_likelihood),
        after_likelihood: Number(hazard.after_likelihood),
      });
      onSaved("Hazard adopted into the unit registry and the feedback marked actioned.");
    } catch (err) {
      setFailure(err.message || "The hazard could not be adopted.");
      setErrors(err.fieldErrors || {});
    } finally {
      setBusy("");
    }
  }

  return (
    <Modal
      title={`${row.mrm_number} · ${row.submitter}`}
      onClose={onClose}
      wide
      footer={
        <div className="btn-row">
          <Button variant="ghost" onClick={onClose} type="button">
            Close
          </Button>
          <Button type="submit" form="feedback-review-form" disabled={Boolean(busy)}>
            {busy === "review" ? "Saving…" : "Save audit"}
          </Button>
        </div>
      }
    >
      <ErrorBanner message={failure} />

      <p className="hint">
        {row.activity_mission} · {row.unit_acronym || row.unit_name} · filed{" "}
        {formatDateTime(row.submitted_at)}
        {row.is_anonymous ? " · anonymous submission" : ""}
      </p>

      <dl className="details">
        {RATING_FIELDS.map((field) => (
          <div className="detail" key={field.key}>
            <dt>{field.label}</dt>
            <dd>{ratingCell(row[field.key])}</dd>
          </div>
        ))}
      </dl>

      {TEXT_FIELDS.map((field) =>
        row[field.key] ? (
          <div key={field.key}>
            <h3 className="hint" style={{ marginBottom: 4 }}>
              {field.label}
            </h3>
            <p className="instruction-body">{row[field.key]}</p>
          </div>
        ) : null
      )}

      {row.recommend_add_to_hazard_registry && (
        <div className="card" style={{ marginTop: 12 }}>
          <header className="card-head">
            <h2>Recommended hazard</h2>
            <Badge tone="info">Registry candidate</Badge>
          </header>
          <div className="card-body">
            <p>
              <strong>{row.recommended_hazard_description}</strong>
            </p>
            {row.recommended_mitigations && (
              <p className="instruction-body">{row.recommended_mitigations}</p>
            )}
            {row.status !== "actioned" ? (
              <form id="feedback-adopt-form" onSubmit={adopt}>
                <div className="form-grid">
                  <Field label="Activity code" required error={errors.activity_code}>
                    <Input
                      value={hazard.activity_code}
                      onChange={(event) =>
                        setHazard((current) => ({
                          ...current,
                          activity_code: event.target.value,
                        }))
                      }
                      placeholder="Which activity does this belong to?"
                    />
                  </Field>
                  <Field label="Before — likelihood" error={errors.before_likelihood}>
                    <Select
                      value={hazard.before_likelihood}
                      onChange={(event) =>
                        setHazard((current) => ({
                          ...current,
                          before_likelihood: event.target.value,
                        }))
                      }
                    >
                      {[5, 4, 3, 2, 1].map((value) => (
                        <option key={value} value={value}>
                          {value}
                        </option>
                      ))}
                    </Select>
                  </Field>
                  <Field label="Before — severity" error={errors.before_severity}>
                    <Select
                      value={hazard.before_severity}
                      onChange={(event) =>
                        setHazard((current) => ({
                          ...current,
                          before_severity: event.target.value,
                        }))
                      }
                    >
                      {["A", "B", "C", "D", "E"].map((value) => (
                        <option key={value} value={value}>
                          {value}
                        </option>
                      ))}
                    </Select>
                  </Field>
                  <Field label="After — likelihood" error={errors.after_likelihood}>
                    <Select
                      value={hazard.after_likelihood}
                      onChange={(event) =>
                        setHazard((current) => ({
                          ...current,
                          after_likelihood: event.target.value,
                        }))
                      }
                    >
                      {[5, 4, 3, 2, 1].map((value) => (
                        <option key={value} value={value}>
                          {value}
                        </option>
                      ))}
                    </Select>
                  </Field>
                  <Field label="After — severity" error={errors.after_severity}>
                    <Select
                      value={hazard.after_severity}
                      onChange={(event) =>
                        setHazard((current) => ({
                          ...current,
                          after_severity: event.target.value,
                        }))
                      }
                    >
                      {["A", "B", "C", "D", "E"].map((value) => (
                        <option key={value} value={value}>
                          {value}
                        </option>
                      ))}
                    </Select>
                  </Field>
                  <Field label="Mitigations" className="span-2">
                    <Textarea
                      rows={3}
                      value={hazard.mitigations}
                      onChange={(event) =>
                        setHazard((current) => ({
                          ...current,
                          mitigations: event.target.value,
                        }))
                      }
                    />
                  </Field>
                </div>
                <Button
                  type="button"
                  variant="ghost"
                  disabled={Boolean(busy)}
                  onClick={adopt}
                >
                  {busy === "adopt" ? "Adding…" : "Adopt into the hazard registry"}
                </Button>
              </form>
            ) : (
              <p className="hint">{row.action_taken}</p>
            )}
          </div>
        </div>
      )}

      <form id="feedback-review-form" onSubmit={review}>
        <Field label="Audit outcome">
          <Select value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="reviewed">Reviewed — noted, no further action</option>
            <option value="actioned">Actioned — something was done</option>
            <option value="dismissed">Dismissed — not taken forward</option>
          </Select>
        </Field>
        <Field
          label="Audit notes"
          error={errors.review_notes}
          hint="What you concluded. Required when dismissing."
        >
          <Textarea
            rows={3}
            value={notes}
            onChange={(event) => setNotes(event.target.value)}
          />
        </Field>
        <Field label="Action taken" hint="Recorded on the feedback and shown to the submitter.">
          <Textarea
            rows={2}
            value={action}
            onChange={(event) => setAction(event.target.value)}
            placeholder="e.g. Added to the quarterly safety brief"
          />
        </Field>
      </form>
    </Modal>
  );
}
