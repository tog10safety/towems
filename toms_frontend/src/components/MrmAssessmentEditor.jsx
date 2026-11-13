import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client.js";
import { useApi } from "../hooks/useApi.js";
import {
  Badge,
  Button,
  Card,
  ErrorBanner,
  Field,
  Input,
  Modal,
  Select,
  Textarea,
} from "./ui.jsx";

/**
 * Editing the body of an MRM assessment: the hazard library, the hazards this
 * mission selects and their after-mitigation values, the tagged personnel, the
 * conditions and the I'M SAFE checks.
 *
 * The legacy application's own tables back all of it, so an assessment filled
 * in here is the same record the old screens and PDF read. Risk arithmetic is
 * mirrored from the server only to show a live number; the stored values come
 * from the server on save.
 */

const SEVERITY_WEIGHT = { A: 5, B: 4, C: 3, D: 2, E: 1 };
//: The unit's risk matrix, verbatim. A rating outside these three lists is not
//: classified, so it is shown grey rather than passed off as acceptable.
const RED = new Set(["5A", "5B", "5C", "4A", "4B", "3A"]);
const YELLOW = new Set([
  "5D", "5E", "4C", "4D", "4E", "3B", "3C", "3D", "2A", "2B", "2C", "1A",
]);
const GREEN = new Set(["3E", "2D", "2E", "1B", "1C", "1D", "1E"]);
const LIKELIHOODS = [
  [5, "5 - Frequent"],
  [4, "4 - Occasional"],
  [3, "3 - Remote"],
  [2, "2 - Improbable"],
  [1, "1 - Extremely Improbable"],
];
const SEVERITIES = [
  ["A", "A - Catastrophic"],
  ["B", "B - Hazardous"],
  ["C", "C - Major"],
  ["D", "D - Minor"],
  ["E", "E - Negligible"],
];
function rating(likelihood, severity) {
  return likelihood && severity ? `${likelihood}${severity}` : "N/A";
}

function ratingTone(value) {
  if (RED.has(value)) return "danger";
  if (YELLOW.has(value)) return "warning";
  if (GREEN.has(value)) return "success";
  return "muted";
}

/** Border / background class for a selected hazard, from its risk rating. */
function ratingClass(value) {
  const tone = ratingTone(value);
  if (tone === "danger") return "hazard-row hazard-row-red";
  if (tone === "warning") return "hazard-row hazard-row-yellow";
  if (tone === "success") return "hazard-row hazard-row-green";
  return "hazard-row";
}

/** The matrix word for a rating, printed beside it: "5A Unacceptable". */
function ratingWord(value) {
  const tone = ratingTone(value);
  if (tone === "danger") return "Unacceptable";
  if (tone === "warning") return "Tolerable";
  if (tone === "success") return "Acceptable";
  return "";
}

/**
 * Library mitigations become one bullet each. They are stored one per line, but
 * some rows also carry several `* bullet` items on a single line, and some keep
 * the asterisk while others do not - all of it reads as the same list here.
 */
function mitigationLines(text) {
  return String(text || "")
    .split(/\r?\n/)
    .flatMap((line) => line.split(/\s+\*\s+/))
    .map((line) => line.replace(/^[\s*\-\u2022\u00b7]+/, "").trim())
    .filter(Boolean);
}

function points(likelihood, severity) {
  const raw = Number(likelihood) * (SEVERITY_WEIGHT[severity] || 1);
  if (raw >= 15) return 3;
  if (raw >= 6) return 2;
  return 1;
}

function effective(hazard) {
  return {
    likelihood: hazard.override_likelihood ?? hazard.after_likelihood,
    severity: hazard.override_severity ?? hazard.after_severity,
  };
}

function authorityTone(authority) {
  const value = String(authority || "").toLowerCase();
  if (value.includes("commander") && !value.includes("squadron")) return "danger";
  if (value.includes("director")) return "warning";
  if (value.includes("squadron")) return "warning";
  if (value.includes("supervisor")) return "info";
  return "success";
}

export default function MrmAssessmentEditor({ mrmId, onSaved, onStateChange }) {
  const details = useApi(`/risk-register/${mrmId}/details/`, { deps: [mrmId] });
  const [model, setModel] = useState(null);
  // Unsaved edits: the workflow above must not submit a version the server has
  // not seen, so this is reported to the page that owns the buttons.
  const [dirty, setDirty] = useState(false);
  const [search, setSearch] = useState({ activity: "" });
  const [found, setFound] = useState([]);
  const [searching, setSearching] = useState(false);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [picked, setPicked] = useState([]);
  const [forecasting, setForecasting] = useState(false);
  const [forecastNote, setForecastNote] = useState("");
  const [personQuery, setPersonQuery] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!details.data) return;
    const next = JSON.parse(JSON.stringify(details.data));
    const mission = next.mission || {};
    // Copy the mission in once, and never over a value already recorded: an
    // assessment keeps the equipment and itinerary it was signed for.
    if (!next.form.aircraft_vehicle && mission.aircraft_vehicle) {
      next.form.aircraft_vehicle = mission.aircraft_vehicle;
    }
    if (!next.form.mission_statement && mission.itinerary_text) {
      next.form.mission_statement = mission.itinerary_text;
    }
    setModel(next);
    setDirty(false);
  }, [details.data]);

  useEffect(() => {
    onStateChange?.({ dirty, hazards: model?.hazards?.length ?? 0 });
  }, [dirty, model?.hazards?.length]); // eslint-disable-line react-hooks/exhaustive-deps

  const codes = useApi("/risk-register/hazard-codes/", { enabled: Boolean(model) });

  const liveRisk = useMemo(() => {
    const hazards = model?.hazards ?? [];
    const maxRisk = hazards.length ? hazards.length * 3 : 1;
    const residual = hazards.reduce((total, hazard) => {
      const { likelihood, severity } = effective(hazard);
      return total + points(likelihood, severity);
    }, 0);
    const totalPercent = (maxRisk ? residual / maxRisk : 0) * 100;
    return { maxRisk, residual, totalPercent };
  }, [model?.hazards]);

  if (details.loading && !model) return <p className="hint">Loading the assessment…</p>;
  if (details.error) return <ErrorBanner message={details.error} onRetry={details.reload} />;
  if (!model) return null;

  const editable = Boolean(model.can_edit);
  const missionPeople = model.mission?.personnel ?? [];
  const taggedIds = new Set(model.personnel.map((row) => row.user_id));

  function patchModel(changes) {
    setModel((current) => ({ ...current, ...changes }));
  }

  function setField(key, value) {
    setDirty(true);
    setModel((current) => ({ ...current, form: { ...current.form, [key]: value } }));
  }

  function setEnvironment(value) {
    setDirty(true);
    setModel((current) => ({ ...current, environment: value }));
  }

  function toggleImsafe(key, checked) {
    setDirty(true);
    setModel((current) => ({ ...current, imsafe: { ...current.imsafe, [key]: checked } }));
  }

  async function runSearch(activityOverride) {
    const activity = (activityOverride ?? search.activity ?? "").trim();
    setSearching(true);
    setError("");
    try {
      const params = new URLSearchParams();
      if (activity) params.set("activity_code", activity);
      const result = await api.get(`/risk-register/hazards/?${params.toString()}`);
      setFound(result.results ?? []);
    } catch (err) {
      setError(err.message || "The hazard library could not be searched.");
    } finally {
      setSearching(false);
    }
  }

  function openLibrary() {
    setPicked([]);
    setLibraryOpen(true);
    runSearch();
  }

  function togglePicked(hazardId) {
    setPicked((current) =>
      current.includes(hazardId)
        ? current.filter((id) => id !== hazardId)
        : [...current, hazardId]
    );
  }

  function addPicked() {
    const chosen = found.filter((hazard) => picked.includes(hazard.hazard_id));
    if (chosen.length) setDirty(true);
    setModel((current) => {
      const known = new Set(current.hazards.map((row) => row.hazard_id));
      const additions = chosen
        .filter((hazard) => !known.has(hazard.hazard_id))
        .map((hazard) => ({
          ...hazard,
          override_likelihood: hazard.after_likelihood,
          override_severity: hazard.after_severity,
          additional_mitigations: "",
        }));
      if (!additions.length) return current;
      return { ...current, hazards: [...current.hazards, ...additions] };
    });
    setPicked([]);
    setLibraryOpen(false);
  }

  function updateHazard(hazardId, key, value) {
    setDirty(true);
    setModel((current) => ({
      ...current,
      hazards: current.hazards.map((row) =>
        row.hazard_id === hazardId ? { ...row, [key]: value } : row
      ),
    }));
  }

  function removeHazard(hazardId) {
    setDirty(true);
    setModel((current) => ({
      ...current,
      hazards: current.hazards.filter((row) => row.hazard_id !== hazardId),
    }));
  }

  /** One checkbox: ticked = tagged for feedback, unticked = not tagged. */
  function togglePerson(person) {
    setDirty(true);
    setModel((current) => {
      const tagged = current.personnel.some((row) => row.user_id === person.user_id);
      if (tagged) {
        return {
          ...current,
          personnel: current.personnel.filter((row) => row.user_id !== person.user_id),
        };
      }
      return {
        ...current,
        personnel: [
          ...current.personnel,
          {
            user_id: person.user_id,
            full_name: person.full_name,
            serial_number: person.serial_number,
            mission_role: person.mission_role,
            // The Operations Center already set the designation on the mission.
            role: person.mission_role || "Crew",
          },
        ],
      };
    });
  }

  async function loadForecast() {
    setForecasting(true);
    setForecastNote("");
    setError("");
    try {
      const result = await api.get(`/risk-register/${mrmId}/forecast/`);
      if (result.available) {
        // The forecast goes into the conditions box itself - the planner can
        // then edit it like anything else they typed.
        setEnvironment(result.summary);
        setForecastNote("");
      } else {
        setForecastNote(result.detail || "No forecast is available for this mission.");
      }
    } catch (err) {
      setForecastNote(err.message || "The forecast could not be fetched.");
    } finally {
      setForecasting(false);
    }
  }

  async function save() {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const saved = await api.patch(`/risk-register/${mrmId}/details/`, {
        form: model.form,
        environment: model.environment,
        imsafe: model.imsafe,
        hazards: model.hazards.map((hazard) => ({
          hazard_id: hazard.hazard_id,
          override_likelihood: hazard.override_likelihood,
          override_severity: hazard.override_severity,
          additional_mitigations: hazard.additional_mitigations,
        })),
        personnel: model.personnel.map((person) => ({
          user_id: person.user_id,
          // Keep the mission's designation; it is set by the Operations Center,
          // not chosen in this form.
          role:
            missionPeople.find((row) => row.user_id === person.user_id)?.mission_role ||
            person.role,
        })),
      });
      setModel(JSON.parse(JSON.stringify(saved)));
      setDirty(false);
      setNotice("Changes saved.");
      onSaved?.(saved);
    } catch (err) {
      setError(err.fieldErrors?.detail || err.message || "The assessment could not be saved.");
    } finally {
      setBusy(false);
    }
  }

  // Everyone on the mission, plus anyone tagged earlier who is no longer on it:
  // a tagged person must always be visible so they can be unticked.
  const candidates = [
    ...missionPeople,
    ...model.personnel
      .filter((tagged) => !missionPeople.some((person) => person.user_id === tagged.user_id))
      .map((tagged) => ({
        user_id: tagged.user_id,
        full_name: tagged.full_name,
        serial_number: tagged.serial_number,
        mission_role: tagged.role,
      })),
  ];
  const matches = personQuery.trim()
    ? candidates.filter((person) =>
        `${person.full_name} ${person.serial_number} ${person.mission_role}`
          .toLowerCase()
          .includes(personQuery.trim().toLowerCase())
      )
    : candidates;

  return (
    <>
      <Card title="Mission details">
        {!editable && (
          <p className="hint">
            This assessment is {model.status}; only the assigned MRM team may change a draft.
          </p>
        )}
        <div className="form-grid">
          <Field label="Activity / Mission" required>
            <Input
              value={model.form.activity_mission}
              disabled={!editable}
              onChange={(event) => setField("activity_mission", event.target.value)}
            />
          </Field>
          <Field
            label="Aircraft / Vehicle"
            hint={
              model.mission?.aircraft_vehicle
                ? `From the mission: ${model.mission.aircraft_vehicle}`
                : "Optional: equipment and vehicle condition."
            }
          >
            <Input
              value={model.form.aircraft_vehicle}
              disabled={!editable}
              onChange={(event) => setField("aircraft_vehicle", event.target.value)}
            />
            {editable && model.mission?.aircraft_vehicle && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setField("aircraft_vehicle", model.mission.aircraft_vehicle)}
              >
                Use mission aircraft / vehicle
              </Button>
            )}
          </Field>
          <Field label="Safety objectives" required className="span-2">
            <Textarea
              value={model.form.safety_objectives}
              disabled={!editable}
              onChange={(event) => setField("safety_objectives", event.target.value)}
            />
          </Field>
          <Field
            label="Mission statement — combined itinerary"
            className="span-2"
            hint="The routes this mission plans to travel, taken from its vehicle trips."
          >
            <Textarea
              value={model.form.mission_statement}
              disabled={!editable}
              onChange={(event) => setField("mission_statement", event.target.value)}
            />
            {(model.mission?.routes ?? []).map((route) => (
              <p key={route.trip_id} className="hint">
                <strong>{(route.vehicles ?? []).join(", ") || route.trip_number}</strong>:{" "}
                {route.route_text || "no itinerary stops planned"}
              </p>
            ))}
            {editable && model.mission?.itinerary_text && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setField("mission_statement", model.mission.itinerary_text)}
              >
                Use mission itinerary
              </Button>
            )}
          </Field>
        </div>
      </Card>

      <Card title="Assumptions and conditions">
        <div className="form-grid">
          <Field
            label="Environmental conditions"
            required
            className="span-2"
            hint="Weather, lighting, workspace and operational tempo — write them as you would brief them."
          >
            <Textarea
              rows={4}
              value={model.environment}
              disabled={!editable}
              placeholder="e.g. Rainy and wet, poor visibility. Overnight. Slippery trail. High tempo."
              onChange={(event) => setEnvironment(event.target.value)}
            />
            {editable && (
              <div className="inline-row">
                <Button variant="ghost" size="sm" onClick={loadForecast} disabled={forecasting}>
                  {forecasting ? "Checking the forecast…" : "Use forecast weather"}
                </Button>
                {forecastNote && <span className="hint">{forecastNote}</span>}
              </div>
            )}
            {!editable && forecastNote && <p className="hint">{forecastNote}</p>}
          </Field>
        </div>

        <h3 style={{ marginTop: 14 }}>Crew — I&apos;M SAFE</h3>
        <div className="form-grid">
          {model.options.imsafe.map((item) => (
            <label key={item.key} className="badge badge-muted" style={{ cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={Boolean(model.imsafe[item.key])}
                disabled={!editable}
                onChange={(event) => toggleImsafe(item.key, event.target.checked)}
                style={{ marginRight: 6 }}
              />
              <strong>{item.key}</strong> — {item.title}: {item.prompt}
            </label>
          ))}
        </div>
      </Card>

      <Card
        title={
          <span className="status-chip">
            Hazards
            <Badge tone="info">{model.hazards.length} selected</Badge>
          </span>
        }
        actions={
          <span className="chip-row">
            <Badge tone="muted">
              Residual {liveRisk.residual} of {liveRisk.maxRisk}
            </Badge>
            <Badge tone={liveRisk.totalPercent <= 50 ? "success" : liveRisk.totalPercent <= 65 ? "info" : liveRisk.totalPercent <= 75 ? "warning" : "danger"}>
              {liveRisk.totalPercent.toFixed(1)}%
            </Badge>
            <Badge tone={authorityTone(model.risk.authority_level)}>
              {model.risk.authority_level || "no authority yet"}
            </Badge>
            {editable && <Button onClick={openLibrary}>Open hazard library</Button>}
          </span>
        }
      >
        <p className="hint" style={{ marginTop: 0 }}>
          The rating beside a hazard is its after-mitigation rating, computed from the likelihood and
          severity you set: <strong>green</strong> acceptable, <strong>amber</strong> tolerable,{" "}
          <strong>red</strong> unacceptable. It changes with the selects below.
        </p>

        {model.hazards.length === 0 ? (
          <p className="hint">
            No hazards selected yet. {editable && "Open the hazard library to add this mission's hazards."}
          </p>
        ) : (
          model.hazards.map((hazard) => {
            const value = effective(hazard);
            const shown = rating(value.likelihood, value.severity);
            const library = mitigationLines(hazard.mitigations);
            return (
              <div key={hazard.hazard_id} className={ratingClass(shown)}>
                <h4 className="hazard-card-title">
                  {hazard.description}
                  {shown !== "N/A" && (
                    <span className={`hazard-rating-word hazard-rating-${ratingTone(shown)}`}>
                      ({shown} {ratingWord(shown)})
                    </span>
                  )}
                </h4>
                <ul className="hazard-mitigations">
                  <li>Library mitigations: {library[0] || "—"}</li>
                  {library.slice(1).map((line, index) => (
                    <li key={`${hazard.hazard_id}-${index}`}>{line}</li>
                  ))}
                </ul>
                <div className="hazard-selects">
                  <Select
                    value={value.likelihood}
                    disabled={!editable}
                    aria-label="After-mitigation likelihood"
                    onChange={(event) =>
                      updateHazard(hazard.hazard_id, "override_likelihood", Number(event.target.value))
                    }
                  >
                    {LIKELIHOODS.map(([number, label]) => (
                      <option key={number} value={number}>
                        {label}
                      </option>
                    ))}
                  </Select>
                  <Select
                    value={value.severity}
                    disabled={!editable}
                    aria-label="After-mitigation severity"
                    onChange={(event) =>
                      updateHazard(hazard.hazard_id, "override_severity", event.target.value)
                    }
                  >
                    {SEVERITIES.map(([letter, label]) => (
                      <option key={letter} value={letter}>
                        {label}
                      </option>
                    ))}
                  </Select>
                </div>
                <div className="hazard-extra">
                  <Input
                    placeholder="Additional mitigations"
                    value={hazard.additional_mitigations}
                    disabled={!editable}
                    onChange={(event) =>
                      updateHazard(hazard.hazard_id, "additional_mitigations", event.target.value)
                    }
                  />
                  {editable ? (
                    <Button variant="ghost" size="sm" onClick={() => removeHazard(hazard.hazard_id)}>
                      Remove
                    </Button>
                  ) : (
                    <span className="hint">After-mitigation rating {shown}</span>
                  )}
                </div>
              </div>
            );
          })
        )}
      </Card>

      <Card
        title={
          <span className="status-chip">
            Personnel tagged for post-mission feedback
            <Badge tone="info">{model.personnel.length} tagged</Badge>
          </span>
        }
      >
        <p className="hint" style={{ marginTop: 0 }}>
          The people assigned to this mission, with the designation the Operations Center gave them.
          Tick who will give feedback afterwards; untick anyone who will not. Tagging is optional.
        </p>

        {missionPeople.length === 0 ? (
          <p className="hint">
            This mission has no personnel recorded yet. Add them on the mission first, then come
            back to tag who gives feedback.
          </p>
        ) : (
          <>
            <Field label="Search this mission's personnel">
              <Input
                value={personQuery}
                placeholder="Name, serial number or designation"
                onChange={(event) => setPersonQuery(event.target.value)}
              />
            </Field>
            <div className="person-grid">
              {matches.map((person) => {
                const isTagged = taggedIds.has(person.user_id);
                return (
                  <label
                    key={person.user_id}
                    className={`person-check ${isTagged ? "person-check-on" : ""}`}
                  >
                    <input
                      type="checkbox"
                      checked={isTagged}
                      disabled={!editable}
                      onChange={() => togglePerson(person)}
                    />
                    <span>
                      <strong>{person.full_name}</strong>
                      <span className="hint">{person.mission_role || "Member"}</span>
                    </span>
                  </label>
                );
              })}
            </div>
          </>
        )}
      </Card>

      <ErrorBanner message={error} />
      {notice && <div className="alert alert-success">{notice}</div>}

      <div className="btn-row" style={{ marginBottom: 18 }}>
        {editable && (
          <Button onClick={save} disabled={busy}>
            {busy ? "Saving…" : "Save changes"}
          </Button>
        )}
        <span className="hint">
          Risk now: {liveRisk.residual} of {liveRisk.maxRisk} points ·{" "}
          {liveRisk.totalPercent.toFixed(1)}% · authority{" "}
          {model.risk.authority_level || "not yet computed"}
        </span>
      </div>

      {libraryOpen && (
        <Modal
          title="Hazard library"
          onClose={() => setLibraryOpen(false)}
          wide
          footer={
            <>
              <Button variant="ghost" onClick={() => setLibraryOpen(false)}>
                Close
              </Button>
              <Button onClick={addPicked} disabled={picked.length === 0}>
                {picked.length === 0
                  ? "Add selected hazards"
                  : `Add ${picked.length} selected hazard${picked.length > 1 ? "s" : ""}`}
              </Button>
            </>
          }
        >
          <div className="inline-row" style={{ marginBottom: 12 }}>
            <Field label="Activity code" hint="Only the codes in your unit's hazard registry.">
              <Select
                value={search.activity}
                onChange={(event) => {
                  const activity = event.target.value;
                  setSearch({ activity });
                  runSearch(activity);
                }}
              >
                <option value="">— All activity codes —</option>
                {(codes.data?.results ?? []).map((row) => (
                  <option key={row.activity_code} value={row.activity_code}>
                    {row.activity_code} ({row.hazard_count})
                  </option>
                ))}
              </Select>
            </Field>
            <Button onClick={() => runSearch()} disabled={searching}>
              {searching ? "Searching…" : "Search"}
            </Button>
          </div>

          {found.length === 0 ? (
            <p className="hint">
              {searching ? "Searching the library…" : "No hazards found for this activity code."}
            </p>
          ) : (
            <div className="library-list">
              {found.map((hazard) => {
                const already = model.hazards.some((row) => row.hazard_id === hazard.hazard_id);
                const checked = picked.includes(hazard.hazard_id);
                return (
                  <label
                    key={hazard.hazard_id}
                    className={`library-row ${checked ? "library-row-picked" : ""} ${
                      already ? "library-row-taken" : ""
                    }`}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={already}
                      onChange={() => togglePicked(hazard.hazard_id)}
                    />
                    <span className="library-body">
                      <span className="library-line">
                        <span className="hazard-code">{hazard.activity_code}</span>
                        {hazard.before_rating && (
                          <Badge tone="muted">Before {hazard.before_rating}</Badge>
                        )}
                        <Badge tone={ratingTone(hazard.after_rating)}>
                          After {hazard.after_rating}
                        </Badge>
                        {already && <Badge tone="info">Added</Badge>}
                      </span>
                      <strong className="library-description">{hazard.description}</strong>
                    </span>
                  </label>
                );
              })}
            </div>
          )}
        </Modal>
      )}
    </>
  );
}
