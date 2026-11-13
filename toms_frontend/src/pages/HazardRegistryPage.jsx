import { useEffect, useMemo, useRef, useState } from "react";
import { API_BASE, api, qs } from "../api/client.js";
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
  Stat,
  Table,
  Textarea,
} from "../components/ui.jsx";
import SuperAdminDelete from "../components/SuperAdminDelete.jsx";
import Icon from "../components/Icon.jsx";
import { useApi } from "../hooks/useApi.js";
import { useAuth } from "../auth/AuthContext.jsx";
import { formatDateTime } from "../utils/format.js";

const PAGE_SIZE = 25;

const LIKELIHOODS = [
  { value: "5", label: "5 — Frequent" },
  { value: "4", label: "4 — Occasional" },
  { value: "3", label: "3 — Remote" },
  { value: "2", label: "2 — Improbable" },
  { value: "1", label: "1 — Extremely improbable" },
];

const SEVERITIES = [
  { value: "A", label: "A — Catastrophic" },
  { value: "B", label: "B — Critical" },
  { value: "C", label: "C — Moderate" },
  { value: "D", label: "D — Minor" },
  { value: "E", label: "E — Negligible" },
];

const EMPTY_FORM = {
  unit: "",
  activity_code: "",
  hazard_description: "",
  before_likelihood: "3",
  before_severity: "C",
  mitigations: "",
  after_likelihood: "2",
  after_severity: "D",
};

function ratingBadge(rating, tone) {
  if (!rating) return <Badge tone="muted">—</Badge>;
  return <Badge tone={tone || "muted"}>{rating}</Badge>;
}

function truncate(text, length = 90) {
  const value = String(text || "");
  return value.length > length ? `${value.slice(0, length)}…` : value;
}

/**
 * The unit's hazard library.
 *
 * The Unit Safety Officer (and the Administrator) maintain it here - add, edit,
 * delete, import from the template, export the current selection. Command and
 * operations posts open the same page read-only; every other account does not
 * see the module at all, and the server refuses them regardless of the URL.
 */
export default function HazardRegistryPage() {
  const { permissions } = useAuth();
  const canManage = Boolean(permissions.can_manage_hazards);
  const isAdmin = Boolean(permissions.is_admin);

  const [unit, setUnit] = useState("");
  const [activityCode, setActivityCode] = useState("");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);

  const [editor, setEditor] = useState(null); // {row} | {}
  // A read-only card of one hazard: every field, the full description and the
  // complete mitigation list, without opening the editor.
  const [viewing, setViewing] = useState(null);
  const [umrrOpen, setUmrrOpen] = useState(false);
  const [importing, setImporting] = useState(false);
  const [failure, setFailure] = useState("");
  const [message, setMessage] = useState("");

  // Typing should not fire a request per keystroke; the committed value does.
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setQuery(search.trim());
      setPage(1);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [search]);

  const path = `/hazards/${qs({
    unit,
    activity_code: activityCode,
    q: query,
    page,
    page_size: PAGE_SIZE,
  })}`;
  const { data, loading, error, reload } = useApi(path, {
    deps: [unit, activityCode, query, page],
  });
  const { data: units } = useApi("/risk-register/units/");

  const rows = data?.results ?? [];
  const summary = data?.summary ?? {};
  // The server builds these from the same scope as the rows, so the filter menu
  // can never offer a code the caller is not allowed to see.
  const codes = (data?.activity_codes ?? []).filter((row) => row.activity_code);

  const exportHref = `${API_BASE}/hazards/export/${qs({
    unit,
    activity_code: activityCode,
    q: query,
  })}`;

  function changeUnit(value) {
    setUnit(value);
    setActivityCode("");
    setPage(1);
  }

  async function remove(row) {
    const used = Number(row.uses || 0);
    const prompt = used
      ? `This hazard is used by ${used} assessment${used === 1 ? "" : "s"}; it cannot be deleted. Try anyway?`
      : `Delete "${row.hazard_description}"? This cannot be undone.`;
    if (!window.confirm(prompt)) return;
    setFailure("");
    setMessage("");
    try {
      await api.del(`/hazards/${row.id}/`);
      setMessage("Hazard deleted.");
      reload();
    } catch (err) {
      setFailure(err.message || "The hazard could not be deleted.");
    }
  }

  const columns = [
    {
      key: "activity_code",
      label: "Activity code",
      width: "170px",
      render: (row) => <strong>{row.activity_code}</strong>,
    },
    {
      key: "hazard_description",
      label: "Hazard",
      render: (row) => (
        <div>
          <div>{row.hazard_description}</div>
          <p className="hint" style={{ margin: "2px 0 0" }}>
            {truncate(row.mitigations)}
          </p>
        </div>
      ),
    },
    {
      key: "before_rating",
      label: "Before",
      width: "90px",
      render: (row) => ratingBadge(row.before_rating, row.before_tone),
    },
    {
      key: "after_rating",
      label: "After",
      width: "90px",
      render: (row) => ratingBadge(row.after_rating, row.after_tone),
    },
    {
      key: "uses",
      label: "Used by",
      width: "90px",
      render: (row) => (row.uses == null ? "—" : `${row.uses} MRM`),
    },
    {
      key: "unit_acronym",
      label: "Unit",
      render: (row) => row.unit_acronym || row.unit_name || "—",
    },
    {
      key: "date_updated",
      label: "Updated",
      width: "170px",
      render: (row) => formatDateTime(row.date_updated),
    },
    {
      key: "actions",
      label: "",
      width: canManage ? "128px" : "56px",
      render: (row) => (
        <div className="btn-row">
          {/* Reading one hazard is open to every role that may read the
              library, so View is never behind the manage check. */}
          <IconButton
            name="eye"
            label="View this hazard"
            onClick={(event) => {
              event.stopPropagation();
              setViewing(row);
            }}
          />
          {canManage && (
            <>
              <IconButton
                name="edit"
                label="Edit this hazard"
                onClick={(event) => {
                  event.stopPropagation();
                  setEditor({ row });
                }}
              />
              <IconButton
                name="trash"
                variant="danger"
                label="Delete this hazard"
                onClick={(event) => {
                  event.stopPropagation();
                  remove(row);
                }}
              />
              <SuperAdminDelete
                path={`/hazards/${row.id}/`}
                label="Delete this hazard and its assessment links"
                confirmText={`Delete "${row.hazard_description}" permanently? The assessments that use it lose the link.`}
                onDone={() => {
                  setMessage("Hazard deleted permanently.");
                  reload();
                }}
                onError={setFailure}
              />
            </>
          )}
        </div>
      ),
    },
  ];

  if (loading && !data) return <Loading label="Loading the hazard library…" />;
  if (error) return <ErrorBanner message={error} onRetry={reload} />;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Hazard Registry</h1>
          <p>
            The unit's institutional hazard library. Every MRM assessment selects its hazards
            from these rows, so the risk ratings here are the ones a crew will brief.
          </p>
        </div>
        <div className="btn-row">
          {/* The UMRR is the unit's own form, so every role that may read the
              library may print it, not only the two that maintain it. */}
          <Button variant="ghost" onClick={() => setUmrrOpen(true)}>
            Unit Mission Registry
          </Button>
          {canManage && (
            <>
              {/* Export produces the workbook that is edited and imported
                  again, so it belongs to the same two roles that may import. */}
              <a
                className="btn btn-ghost btn-md btn-icon"
                href={exportHref}
                title="Export the registry as a workbook"
                aria-label="Export the registry as a workbook"
              >
                <Icon name="download" size={18} />
              </a>
              <a
                className="btn btn-ghost btn-md btn-icon"
                href={`${API_BASE}/hazards/template/`}
                title="Download the import template"
                aria-label="Download the import template"
              >
                <Icon name="reports" size={18} />
              </a>
              <IconButton
                name="upload"
                size="md"
                label="Import a workbook"
                onClick={() => setImporting(true)}
              />
              <IconButton
                name="plus"
                variant="primary"
                size="md"
                label="Add a hazard"
                onClick={() => setEditor({})}
              />
            </>
          )}
        </div>
      </div>

      <ErrorBanner message={failure} />
      {message && (
        <div className="alert alert-success" role="status">
          {message}
        </div>
      )}

      <div className="stats">
        <Stat label="Hazards" value={summary.hazards ?? "—"} />
        <Stat label="Activity codes" value={summary.activity_codes ?? "—"} />
        <Stat
          label="Unacceptable before"
          value={summary.before_unacceptable ?? "—"}
          tone="danger"
        />
        <Stat
          label="Unacceptable after"
          value={summary.after_unacceptable ?? "—"}
          tone="success"
        />
      </div>

      <div className="inline-row hazard-filters" style={{ margin: "16px 0 12px" }}>
        <Field label="Unit">
          <Select value={unit} onChange={(event) => changeUnit(event.target.value)}>
            <option value="">All units in scope</option>
            {(units?.results ?? []).map((row) => (
              <option key={row.id} value={row.id}>
                {row.acronym || row.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Activity code">
          <Select
            value={activityCode}
            onChange={(event) => {
              setActivityCode(event.target.value);
              setPage(1);
            }}
          >
            <option value="">All activity codes</option>
            {codes.map((row) => (
              <option key={row.activity_code} value={row.activity_code}>
                {row.activity_code} ({row.hazard_count})
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Search hazard">
          <Input
            type="search"
            value={search}
            placeholder="Description contains…"
            onChange={(event) => setSearch(event.target.value)}
          />
        </Field>
      </div>

      {!canManage && (
        <p className="hint" style={{ marginBottom: 10 }}>
          Your role may read this library. Adding, editing, importing and deleting belong to the
          Unit Safety Officer.
        </p>
      )}

      <Card>
        <Table
          columns={columns}
          rows={rows}
          empty="No hazard matches these filters."
          stack
        />
      </Card>
      <Pagination
        page={page}
        pageSize={PAGE_SIZE}
        count={data?.count ?? 0}
        onPage={setPage}
      />

      {editor && (
        <HazardEditor
          row={editor.row}
          units={units?.results ?? []}
          isAdmin={isAdmin}
          onClose={() => setEditor(null)}
          onSaved={(text) => {
            setEditor(null);
            setMessage(text);
            reload();
          }}
        />
      )}

      {umrrOpen && (
        <UmrrExport
          units={units?.results ?? []}
          defaultUnit={unit}
          onClose={() => setUmrrOpen(false)}
        />
      )}

      {viewing && <HazardViewer row={viewing} onClose={() => setViewing(null)} />}

      {importing && (
        <HazardImport
          onClose={() => setImporting(false)}
          onImported={(text) => {
            setImporting(false);
            setMessage(text);
            reload();
          }}
        />
      )}
    </>
  );
}

function UmrrExport({ units, defaultUnit, onClose }) {
  const [unit, setUnit] = useState(defaultUnit || "");
  const [mode, setMode] = useState("all");
  const [selected, setSelected] = useState(() => new Set());
  const [failure, setFailure] = useState("");
  const [busy, setBusy] = useState(false);
  const [options, setOptions] = useState([]);
  const [loadingOptions, setLoadingOptions] = useState(false);

  // Specific mode needs the whole library of the chosen unit, which can run
  // past one page. The registry itself always covers the unit, so the page's
  // search and activity filters are deliberately not applied here.
  useEffect(() => {
    if (mode !== "specific") return undefined;
    let cancelled = false;
    (async () => {
      setLoadingOptions(true);
      setFailure("");
      try {
        const collected = [];
        let page = 1;
        for (;;) {
          const payload = await api.get(
            `/hazards/${qs({ unit, page, page_size: 200 })}`
          );
          collected.push(...(payload.results ?? []));
          if (!payload.next || collected.length >= (payload.count ?? 0)) break;
          page += 1;
        }
        if (!cancelled) setOptions(collected);
      } catch (err) {
        if (!cancelled) {
          setOptions([]);
          setFailure(err.message || "That unit's hazards could not be loaded.");
        }
      } finally {
        if (!cancelled) setLoadingOptions(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [mode, unit]);

  function toggle(id) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function generate() {
    setBusy(true);
    setFailure("");
    try {
      // No activities means every code stored for that unit, exactly what the
      // "All hazards" choice promises. A chosen code is always printed whole.
      const params = qs({
        unit,
        activities: mode === "specific" ? [...selected].join(",") : "",
      });
      const blob = await (await api.download(`/hazards/umrr/${params}`)).blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      const chosen = units.find((row) => String(row.id) === String(unit));
      const label = (chosen?.acronym || chosen?.name || "Unit").replace(/\s+/g, "_");
      link.download = `UMRR_${label}.pdf`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      onClose();
    } catch (err) {
      setFailure(err.message || "The registry could not be generated.");
    } finally {
      setBusy(false);
    }
  }

  // The picker is organised by activity code: one form is printed per code and
  // a form is only complete when every hazard under that code is in it.
  const codeGroups = useMemo(() => {
    const grouped = new Map();
    for (const row of options) {
      const code = row.activity_code || "Unassigned";
      if (!grouped.has(code)) grouped.set(code, []);
      grouped.get(code).push(row);
    }
    return [...grouped.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [options]);

  const selectedHazards = codeGroups
    .filter(([code]) => selected.has(code))
    .reduce((total, [, rows]) => total + rows.length, 0);

  const canGenerate = mode === "all" || selected.size > 0;

  return (
    <Modal
      title="Unit Mission Registry"
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={generate} disabled={busy || !canGenerate}>
            {busy ? "Generating…" : "Generate PDF"}
          </Button>
        </>
      }
    >
      <ErrorBanner message={failure} />
      <Field label="Unit">
        <Select
          value={unit}
          onChange={(event) => {
            setUnit(event.target.value);
            setSelected(new Set());
          }}
        >
          <option value="">All units in scope</option>
          {units.map((row) => (
            <option key={row.id} value={row.id}>
              {row.acronym || row.name}
            </option>
          ))}
        </Select>
      </Field>

      <div className="umrr-mode">
        <label className="radio-row">
          <input
            type="radio"
            name="umrr-mode"
            checked={mode === "all"}
            onChange={() => setMode("all")}
          />
          <span>
            <strong>All hazards</strong>
            <span className="hint">
              Every activity code, each on its own form, in one file.
            </span>
          </span>
        </label>
        <label className="radio-row">
          <input
            type="radio"
            name="umrr-mode"
            checked={mode === "specific"}
            onChange={() => setMode("specific")}
          />
          <span>
            <strong>Specific activity codes</strong>
            <span className="hint">
              Each chosen code prints as its own form with all of its hazards.
            </span>
          </span>
        </label>
      </div>

      {mode === "specific" && (
        <>
          {loadingOptions ? (
            <Loading label="Loading that unit's hazards…" />
          ) : (
            <div
              className="hazard-picker"
              role="group"
              aria-label="Activity codes to print"
            >
              {codeGroups.length === 0 && (
                <p className="hint">No hazards stored for that unit.</p>
              )}
              {codeGroups.map(([code, rows]) => (
                <label key={code} className="picker-row">
                  <input
                    type="checkbox"
                    checked={selected.has(code)}
                    onChange={() => toggle(code)}
                  />
                  <span>
                    <strong>{code}</strong> — {rows.length} hazard
                    {rows.length === 1 ? "" : "s"}
                    <span className="hint">
                      {" "}
                      {rows
                        .slice(0, 3)
                        .map((row) => row.hazard_description)
                        .join("; ")}
                      {rows.length > 3 ? " …" : ""}
                    </span>
                  </span>
                </label>
              ))}
            </div>
          )}
          <p className="hint" style={{ marginTop: 8 }}>
            {selected.size} activity code{selected.size === 1 ? "" : "s"} selected
            {selectedHazards ? ` — ${selectedHazards} hazards.` : "."}
          </p>
        </>
      )}
    </Modal>
  );
}


function HazardEditor({ row, units, isAdmin, onClose, onSaved }) {
  const [form, setForm] = useState(() =>
    row
      ? {
          unit: row.unit_id ?? "",
          activity_code: row.activity_code ?? "",
          hazard_description: row.hazard_description ?? "",
          before_likelihood: String(row.before_likelihood ?? "3"),
          before_severity: row.before_severity ?? "C",
          mitigations: row.mitigations ?? "",
          after_likelihood: String(row.after_likelihood ?? "2"),
          after_severity: row.after_severity ?? "D",
        }
      : { ...EMPTY_FORM }
  );
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState("");
  const [busy, setBusy] = useState(false);

  function update(key, value) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  async function save(event) {
    event.preventDefault();
    setBusy(true);
    setFailure("");
    setErrors({});
    const payload = {
      ...form,
      before_likelihood: Number(form.before_likelihood),
      after_likelihood: Number(form.after_likelihood),
    };
    if (!isAdmin) delete payload.unit;
    try {
      if (row) await api.patch(`/hazards/${row.id}/`, payload);
      else await api.post("/hazards/", payload);
      onSaved(row ? "Hazard updated." : "Hazard added to the registry.");
    } catch (err) {
      setFailure(err.message || "The hazard could not be saved.");
      setErrors(err.fieldErrors || {});
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title={row ? `Edit hazard ${row.activity_code}` : "Add a hazard"}
      onClose={onClose}
      wide
      footer={
        <div className="btn-row">
          <Button variant="ghost" onClick={onClose} type="button">
            Cancel
          </Button>
          <Button type="submit" form="hazard-form" disabled={busy}>
            {busy ? "Saving…" : row ? "Save changes" : "Add hazard"}
          </Button>
        </div>
      }
    >
      <form id="hazard-form" onSubmit={save}>
        <ErrorBanner message={failure} />
        <div className="form-grid">
          {isAdmin && (
            <Field label="Unit" required>
              <Select
                value={form.unit}
                onChange={(event) => update("unit", event.target.value)}
              >
                <option value="">Choose a unit…</option>
                {units.map((unit) => (
                  <option key={unit.id} value={unit.id}>
                    {unit.acronym || unit.name}
                  </option>
                ))}
              </Select>
            </Field>
          )}
          <Field
            label="Activity code"
            required
            error={errors.activity_code}
            hint="The library groups hazards by activity, exactly as the MRM form shows them."
          >
            <Input
              value={form.activity_code}
              onChange={(event) => update("activity_code", event.target.value)}
              placeholder="e.g. Aerodrome Operations"
            />
          </Field>
          <Field label="Hazard description" required error={errors.hazard_description}>
            <Input
              value={form.hazard_description}
              onChange={(event) => update("hazard_description", event.target.value)}
              placeholder="e.g. Foreign object debris on the runway"
            />
          </Field>
          <Field label="Before — likelihood" required error={errors.before_likelihood}>
            <Select
              value={form.before_likelihood}
              onChange={(event) => update("before_likelihood", event.target.value)}
            >
              {LIKELIHOODS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Before — severity" required error={errors.before_severity}>
            <Select
              value={form.before_severity}
              onChange={(event) => update("before_severity", event.target.value)}
            >
              {SEVERITIES.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
          </Field>
          <Field
            label="Library mitigations"
            required
            error={errors.mitigations}
            hint="One per line. The MRM shows these as the library mitigations for the hazard."
            className="span-2"
          >
            <Textarea
              rows={5}
              value={form.mitigations}
              onChange={(event) => update("mitigations", event.target.value)}
              placeholder={"Enforce marshaller and wing-walker procedures\nObserve the taxi lines and parking guidelines"}
            />
          </Field>
          <Field label="After — likelihood" required error={errors.after_likelihood}>
            <Select
              value={form.after_likelihood}
              onChange={(event) => update("after_likelihood", event.target.value)}
            >
              {LIKELIHOODS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="After — severity" required error={errors.after_severity}>
            <Select
              value={form.after_severity}
              onChange={(event) => update("after_severity", event.target.value)}
            >
              {SEVERITIES.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
          </Field>
        </div>
        <p className="hint">
          The before and after risk ratings ({form.before_likelihood}
          {form.before_severity} → {form.after_likelihood}
          {form.after_severity}) are calculated from these choices.
        </p>
      </form>
    </Modal>
  );
}

function HazardViewer({ row, onClose }) {
  // Mitigations are stored one per line; show them as the list an MRM briefs.
  const mitigations = String(row.mitigations || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);

  return (
    <Modal
      title={`Hazard ${row.activity_code}`}
      onClose={onClose}
      wide
      footer={
        <Button variant="ghost" onClick={onClose}>
          Close
        </Button>
      }
    >
      <div className="hazard-view">
        <p>
          <strong>Unit:</strong> {row.unit_acronym || row.unit_name || "—"}
        </p>
        <p>
          <strong>Before:</strong> {row.before_rating || "—"} · likelihood{" "}
          {row.before_likelihood} / severity {row.before_severity}
        </p>
        <p>
          <strong>After:</strong> {row.after_rating || "—"} · likelihood{" "}
          {row.after_likelihood} / severity {row.after_severity}
        </p>
        <p>
          <strong>Used by:</strong>{" "}
          {row.uses == null
            ? "—"
            : `${row.uses} MRM assessment${row.uses === 1 ? "" : "s"}`}
        </p>
        <p>
          <strong>Last updated:</strong> {formatDateTime(row.date_updated)}
        </p>
        <p>
          <strong>Added by:</strong> {row.created_by_name || "—"}
        </p>
        <p>
          <strong>Hazard description:</strong>{" "}
          <strong>{row.hazard_description || "—"}</strong>
        </p>
        <p style={{ marginBottom: 4 }}>
          <strong>Mitigations:</strong>
        </p>
        {mitigations.length > 0 ? (
          <ul className="hazard-view-list">
            {mitigations.map((line, index) => (
              <li key={`${index}-${line}`}>- {line}</li>
            ))}
          </ul>
        ) : (
          <p>—</p>
        )}
      </div>
    </Modal>
  );
}

function HazardImport({ onClose, onImported }) {
  const [file, setFile] = useState(null);
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState("");
  const [busy, setBusy] = useState(false);
  const inputRef = useRef(null);

  async function submit(event) {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    setFailure("");
    setErrors({});
    try {
      const data = new FormData();
      data.append("file", file);
      const body = await api.upload("/hazards/import/", data);
      onImported(body.detail || `Imported ${body.created} hazard(s).`);
    } catch (err) {
      setFailure(err.message || "The workbook could not be imported.");
      setErrors(err.fieldErrors || {});
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title="Import the hazard registry"
      onClose={onClose}
      footer={
        <div className="btn-row">
          <Button variant="ghost" onClick={onClose} type="button">
            Cancel
          </Button>
          <Button type="submit" form="hazard-import-form" disabled={busy || !file}>
            {busy ? "Importing…" : "Import workbook"}
          </Button>
        </div>
      }
    >
      <form id="hazard-import-form" onSubmit={submit}>
        <ErrorBanner message={failure} />
        <p>
          Download the template, fill in the <strong>Hazard Registry</strong> sheet, then upload
          it here. Every row is validated before anything is saved: if one row is wrong, nothing
          is imported and the rows that need attention are listed below.
        </p>
        <div className="btn-row" style={{ margin: "10px 0 14px" }}>
          <a className="btn btn-ghost btn-sm" href={`${API_BASE}/hazards/template/`}>
            Download template
          </a>
        </div>
        <Field label="Workbook (.xlsx)">
          <input
            ref={inputRef}
            className="input"
            type="file"
            accept=".xlsx"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          />
        </Field>
        {Object.keys(errors).length > 0 && (
          <div className="alert alert-warning" role="alert">
            <strong>Rows needing attention</strong>
            <ul style={{ margin: "6px 0 0 16px" }}>
              {Object.entries(errors).map(([key, value]) => (
                <li key={key}>
                  {key}: {value}
                </li>
              ))}
            </ul>
          </div>
        )}
      </form>
    </Modal>
  );
}
