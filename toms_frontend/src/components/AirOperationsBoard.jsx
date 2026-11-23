import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api, API_BASE, qs } from "../api/client.js";
import { Button, Card, EmptyState, ErrorBanner, Input, Loading, Modal } from "./ui.jsx";
import { useApi } from "../hooks/useApi.js";
import { useAuth } from "../auth/AuthContext.jsx";
import { toInputDateTime } from "../utils/format.js";
import { legRouteLabel } from "../utils/airRoute.js";
import AirSimulationMap from "./AirSimulationMap.jsx";
import AirFuelDialog from "./AirFuelDialog.jsx";

const STATUS_LABELS = {
  gray: "Not started",
  yellow: "In progress",
  green: "Complete",
};

function statusLabel(status) {
  return STATUS_LABELS[status] || STATUS_LABELS.gray;
}

function recordTitle(event) {
  if (event === "takeoff") return "Record actual takeoff (ATD)";
  if (event === "landing") return "Record actual landing (ATA)";
  if (event === "tot") return "Record arrival over target (TOT)";
  if (event === "toot") return "Record departure from target (TOOT)";
  if (event === "se") return "Record engine start (SE)";
  if (event === "sd") return "Record engine shutdown (SD)";
  return "Record actual time";
}

function recordHint(event) {
  if (event === "tot") {
    return "Record the lead aircraft's arrival in the area over the overflight target. It is applied to every aircraft in the element.";
  }
  if (event === "toot") {
    return "Record the lead aircraft's departure from the target. The next leg's ATA unlocks after this.";
  }
  if (event === "se") {
    return "Start engines before takeoff. It is applied to every aircraft in the element and must not follow the ATD.";
  }
  if (event === "sd") {
    return "Shut the engines down after landing. Record the ATA first; the shutdown must not precede it.";
  }
  return "Record the lead aircraft's time. It is applied to every aircraft in the element. Planned ETD/ETA stay unchanged.";
}

function todayInput() {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 10);
}

function timeLabel(value, actual) {
  if (!value) return actual ? "—" : "TBD";
  const text = String(value);
  const match = text.match(/T(\d{2}):(\d{2})/);
  if (match) return `${match[1]}${match[2]}`;
  return text;
}

function aircraftLabel(aircraft) {
  if (!aircraft) return "—";
  return [
    aircraft.aircraft_type,
    aircraft.tail_number ? `# ${aircraft.tail_number}` : "",
    aircraft.callsign,
  ]
    .filter(Boolean)
    .join(" · ");
}

function crewLabel(aircraft) {
  return (aircraft?.crew || [])
    .map((row) => `[${row.role}] ${row.name}`)
    .join("\n");
}

function crewLabelInline(aircraft) {
  return (aircraft?.crew || [])
    .map((row) => `[${row.role}] ${row.name}`)
    .join(" / ");
}

function actualRouteLabel(leg, previous = null) {
  return legRouteLabel(leg, previous);
}

function nextLandingLeg(planes, legIndex) {
  const legCount = Math.max(
    1,
    ...planes.map((plane) => Math.max(1, (plane.itineraries || []).length))
  );
  for (let index = legIndex + 1; index < legCount; index += 1) {
    const candidate = leadLeg(planes, index);
    if (candidate && !candidate.overflight) return candidate;
  }
  return null;
}

function legStatus(leg, planes, legIndex) {
  if (leg?.status === "cancelled" && !leg?.atd && !leg?.ata) return "cancelled";
  const previous = legIndex > 0 ? leadLeg(planes, legIndex - 1) : null;
  if (previous?.overflight && previous?.toot && !leg?.ata) return "yellow";
  if (leg?.overflight) {
    const nextLanding = nextLandingLeg(planes, legIndex);
    if (nextLanding?.ata) return "green";
    if (leg?.atd || leg?.tot || leg?.toot) return "yellow";
    return "gray";
  }
  if (leg?.ata) return "green";
  if (leg?.atd || leg?.tot || leg?.toot) return "yellow";
  return "gray";
}

function aircraftShort(aircraft, index) {
  return aircraft?.tail_number
    ? `# ${aircraft.tail_number}`
    : `ACFT #${index + 1}`;
}

function legShutdown(leg) {
  // Fuel belongs to the leg that ends with a shutdown, so the option appears
  // on that leg only. An overflight leg ends over the target and never shuts
  // down, so it never carries a figure.
  if (!leg || leg.status === "cancelled" || leg.overflight) return false;
  return Boolean(leg.sd);
}

function fuelDrawFor(mission, aircraftId, legId) {
  return (
    (mission?.air_fuel?.draws || []).find(
      (draw) => draw.aircraft === aircraftId && draw.leg === legId
    ) || null
  );
}

function fuelLabel(draw) {
  if (!draw) return "";
  if (draw.no_consumption) return "No fuel";
  if (draw.source === "bulk") return `${Math.round(draw.litres)} L`;
  return `${draw.quantity} drum${draw.quantity === 1 ? "" : "s"}`;
}

function formationTimes(planes, legIndex, field, actual) {
  const rows = planes.map((plane, index) => ({
    plane,
    index,
    leg: plane.itineraries?.[legIndex],
  }));
  const values = rows.map(({ leg }) => timeLabel(leg?.[field], actual));
  if (!values.length) return actual ? "—" : "TBD";
  const same = values.every((value) => value === values[0]);
  if (rows.length === 1 || same) return values[0];
  return rows
    .map(
      ({ plane, index }, position) =>
        `${aircraftShort(plane, index)}  ${values[position]}`
    )
    .join("\n");
}

function leadPlane(planes) {
  return (planes || []).find((plane) => plane.is_lead) || planes?.[0] || null;
}

function elementLegs(planes, legIndex) {
  return (planes || [])
    .map((plane) => plane.itineraries?.[legIndex])
    .filter(Boolean);
}

function leadLeg(planes, legIndex) {
  const lead = leadPlane(planes);
  return (
    lead?.itineraries?.[legIndex] ||
    elementLegs(planes, legIndex)[0] ||
    {}
  );
}

function leadTime(planes, legIndex, field) {
  return timeLabel(leadLeg(planes, legIndex)[field], true);
}

function elementItineraries(planes) {
  const lead = leadPlane(planes);
  return (
    lead?.itineraries ||
    (planes || []).find((plane) => (plane.itineraries || []).length)?.itineraries ||
    []
  );
}

function routeStops(planes) {
  const legs = elementItineraries(planes);
  const stops = [];
  if (legs[0]?.origin) stops.push(legs[0].origin);
  legs.forEach((leg) => {
    if (!leg.destination) return;
    stops.push(
      leg.overflight
        ? `${leg.destination} (overflight)`
        : leg.destination
    );
  });
  return stops;
}

function routeStatus(planes) {
  const legs = elementItineraries(planes);
  if (!legs.length) return "gray";
  const first = legs[0];
  const last = legs[legs.length - 1];
  const departed = Boolean(
    first?.atd || last?.atd || first?.tot || last?.tot || first?.toot || last?.toot
  );
  const completed = Boolean(first?.atd && last?.ata);
  if (completed) return "green";
  if (departed || last?.ata) return "yellow";
  return "gray";
}

function legLocked(leg) {
  return Boolean(
    leg?.atd ||
      leg?.ata ||
      leg?.tot ||
      leg?.toot ||
      leg?.status === "cancelled"
  );
}

function missionElements(mission) {
  const detail = mission.air_detail || {};
  return (detail.elements || []).length
    ? detail.elements
    : fallbackElements(detail);
}

function remainingItineraryLegs(mission) {
  const rows = [];
  missionElements(mission).forEach((element, elementIndex) => {
    const lead = leadPlane(element.aircraft);
    const legs = lead?.itineraries || [];
    legs.forEach((leg, legIndex) => {
      if (!leg?.id || !/^\d+$/.test(String(leg.id))) return;
      const formationLegs = (element.aircraft || [])
        .map((plane) => plane.itineraries?.[legIndex])
        .filter(Boolean);
      if (formationLegs.some(legLocked)) return;
      rows.push({ element, elementIndex, legIndex, leg });
    });
  });
  return rows;
}

function fallbackElements(detail) {
  const assigned = detail?.assigned_aircraft || [];
  if (!assigned.length && !detail?.aircraft_type) return [];
  return [
    {
      sequence: 1,
      mission: "",
      aircraft: (assigned.length ? assigned : [null]).map((row, index) => ({
        id: `legacy-${index}`,
        aircraft_type: row?.aircraft_model || detail?.aircraft_type || "",
        tail_number: row?.tail_number || detail?.aircraft_tail_number || "",
        callsign: row?.callsign || detail?.flight_identification || "",
        crew: [
          ...(row?.pilot_in_command
            ? [{ role: "PIC", name: row.pilot_in_command }]
            : []),
          ...(row?.co_pilot ? [{ role: "CP", name: row.co_pilot }] : []),
        ],
        itineraries: [
          {
            id: `legacy-leg-${index}`,
            etd: detail?.scheduled_departure,
            eta: detail?.scheduled_arrival,
            atd: null,
            ata: null,
            origin: detail?.departure_location || "",
            destination: detail?.destination_location || "",
            route: detail?.route || "",
            mission: "",
            overflight: false,
            return_to_origin: false,
            route_label: detail?.route || "",
            display_mission: "",
          },
        ],
      })),
    },
  ];
}

export default function AirOperationsBoard() {
  const navigate = useNavigate();
  const location = useLocation();
  const { permissions } = useAuth();
  const canPlan = Boolean(permissions?.can_plan_missions);
  const isAdmin = Boolean(permissions?.is_system_owner);
  // Fuel is the Logistics post's figure: only it sees the record button.
  const canFuel = Boolean(permissions?.can_record_air_fuel);
  const showActions = canPlan || isAdmin;
  const [date, setDate] = useState(
    () => location.state?.airDate || todayInput()
  );
  const [notice, setNotice] = useState(() => {
    if (!location.state?.itinerarySaved) return "";
    return location.state.missionNumber
      ? `Itinerary changes saved for ${location.state.missionNumber}.`
      : "Itinerary changes saved.";
  });
  const [mode, setMode] = useState(() =>
    todayInput() < new Date().toISOString().slice(0, 10) ? "actual" : "schedule"
  );
  const [view, setView] = useState("board");
  const [busy, setBusy] = useState("");
  const [actionError, setActionError] = useState("");
  const [recordTarget, setRecordTarget] = useState(null);
  const [recordAt, setRecordAt] = useState("");
  const [cancelTarget, setCancelTarget] = useState(null);
  const [fuelTarget, setFuelTarget] = useState(null);
  const path = `/missions/${qs({
    category: "air",
    air_date: date,
    page_size: 100,
  })}`;
  const { data, loading, error, reload } = useApi(path, {
    deps: [date],
  });
  // Landed sorties that still owe a POL-01 figure, for this operations date.
  const fuelOutstanding = useApi(
    `/missions/fuel-unrecorded/${qs({ air_date: date })}`,
    { deps: [date], enabled: canFuel }
  );
  const missions = data?.results ?? [];
  const actual = mode === "actual";
  const timingWarnings = missions.flatMap((mission) => mission.timing_warnings || []);
  const summary = missions.reduce(
    (totals, mission) => {
      const detail = mission.air_detail || {};
      const elements = (detail.elements || []).length
        ? detail.elements
        : fallbackElements(detail);
      elements.forEach((element) => {
        const planes = element.aircraft || [];
        if (!planes.length) return;
        totals.elements += 1;
        totals.aircraft += planes.length;
        totals[routeStatus(planes)] += 1;
      });
      return totals;
    },
    { elements: 0, aircraft: 0, green: 0, yellow: 0, gray: 0 }
  );

  function changeDate(next) {
    setDate(next);
    const today = todayInput();
    setMode(next < today ? "actual" : "schedule");
  }

  async function record(mission, legs, event, at = "") {
    const recordable = (legs || []).filter((leg) =>
      /^\d+$/.test(String(leg.id || ""))
    );
    if (!recordable.length) return;
    setBusy(
      `${mission.id}-${recordable.map((leg) => leg.id).join("-")}-${event}`
    );
    setActionError("");
    try {
      await Promise.all(
        recordable.map((leg) =>
          api.post(`/missions/${mission.id}/itineraries/${leg.id}/actual/`, {
            event,
            ...(at ? { at } : {}),
          })
        )
      );
      reload();
      fuelOutstanding.reload?.();
      return true;
    } catch (err) {
      setActionError(err.fieldErrors?.detail || err.message || "The time could not be recorded.");
      return false;
    } finally {
      setBusy("");
    }
  }

  function openRecord(mission, planes, legIndex, event) {
    const legs = elementLegs(planes, legIndex).filter((leg) =>
      /^\d+$/.test(String(leg.id || ""))
    );
    if (!legs.length) return;
    setRecordTarget({ mission, legs, event });
    setRecordAt(toInputDateTime(new Date().toISOString()));
  }

  async function saveRecord() {
    if (!recordTarget) return;
    const target = recordTarget;
    const ok = await record(target.mission, target.legs, target.event, recordAt);
    setRecordTarget(null);
    // A shutdown is when the crew knows what was burned, so the fuel figure is
    // asked for immediately; the same dialog stays reachable from the mission.
    if (ok && target.event === "sd") setFuelTarget({ mission: target.mission });
  }

  async function openOutstandingFuel(row) {
    setBusy(`fuel-${row.mission}`);
    setActionError("");
    try {
      const mission = await api.get(`/missions/${row.mission}/`);
      setFuelTarget({ mission, aircraftId: row.aircraft[0]?.aircraft });
    } catch (err) {
      setActionError(err.message || "The mission could not be opened.");
    } finally {
      setBusy("");
    }
  }

  function changeItinerary(mission) {
    navigate(`/missions/${mission.id}/edit`);
  }

  async function boardPost(mission, action, body = {}) {
    setBusy(`${action}-${mission.id}`);
    setActionError("");
    try {
      await api.post(`/missions/${mission.id}/${action}/`, body);
      reload();
    } catch (err) {
      setActionError(
        err.fieldErrors?.detail || err.message || "The mission could not be updated."
      );
    } finally {
      setBusy("");
    }
  }

  function toggleHold(mission) {
    const holding = !mission.is_held;
    const heading = holding ? "Hold" : "Release hold on";
    if (
      !window.confirm(
        `${heading} mission ${mission.mission_number}?${
          holding
            ? "\n\nThe board will refuse ATD until the hold is released."
            : ""
        }`
      )
    )
      return;
    boardPost(mission, "hold", {
      hold: holding,
      reason: holding
        ? "Held from the Daily Operations board."
        : "Released from hold from the Daily Operations board.",
    });
  }

  function cancelMission(mission) {
    setActionError("");
    setCancelTarget({ mission, rows: remainingItineraryLegs(mission) });
  }

  async function applyCancel() {
    if (!cancelTarget) return;
    const mission = cancelTarget.mission;
    setBusy(`cancel-${mission.id}`);
    setActionError("");
    try {
      await api.post(`/missions/${mission.id}/cancel/`, {
        reason: "Cancelled from the Daily Operations board.",
      });
      setCancelTarget(null);
      reload();
    } catch (err) {
      setActionError(
        err.fieldErrors?.detail || err.message || "The mission could not be cancelled."
      );
    } finally {
      setBusy("");
    }
  }

  async function deleteMission(mission) {
    if (
      !window.confirm(
        `Permanently delete mission ${mission.mission_number}?\n\nThis removes the mission and its flight records. It cannot be undone.`
      )
    )
      return;
    const reason = window.prompt(
      "Reason for deleting (recorded in the audit trail):",
      ""
    );
    if (reason === null) return;
    if (reason.trim().length < 3) {
      setActionError("Record a reason of at least 3 characters.");
      return;
    }
    setBusy(`delete-${mission.id}`);
    setActionError("");
    try {
      await api.del(`/missions/${mission.id}/`, { reason: reason.trim() });
      reload();
    } catch (err) {
      setActionError(err.message || "The mission could not be deleted.");
    } finally {
      setBusy("");
    }
  }

  let flightNumber = 0;

  return (
    <>
    <Card>
      <div className="flight-ops-head">
        <div className="flight-ops-title">
          <span className="flight-ops-kicker">Daily operations board</span>
          <h3>Flight Operations</h3>
          <p className="hint">
            {view === "map"
              ? "Simulated aircraft movement from the saved flight plan. This is not GPS tracking; actual ATD and ATA remain on the board."
              : actual
                ? "Record engine start (SE), takeoff (ATD), landing (ATA), shutdown (SD), and overflight TOT/TOOT. Planned ETD/ETA stay unchanged."
                : "Plan the day's departures, routes, and missions. Switch to Actual after flight."}
          </p>
        </div>
        <div className="flight-ops-toolbar">
          <div
            className="flight-view-switch"
            role="group"
            aria-label="Flight Operations view"
          >
            <button
              type="button"
              className={`flight-view-button ${view === "board" ? "is-active" : ""}`}
              aria-pressed={view === "board"}
              onClick={() => setView("board")}
            >
              Board
            </button>
            <button
              type="button"
              className={`flight-view-button ${view === "map" ? "is-active" : ""}`}
              aria-pressed={view === "map"}
              onClick={() => setView("map")}
            >
              Map View
            </button>
          </div>
          <label className="flight-date-control">
            <span>Operations date</span>
            <Input
              type="date"
              aria-label="Flight Operations date"
              value={date}
              onChange={(event) => changeDate(event.target.value)}
            />
          </label>
          {view === "board" && (
            <div
              className="flight-mode-switch"
              role="group"
              aria-label="Flight Operations mode"
            >
              <button
                type="button"
                className={`flight-mode-button ${
                  mode === "schedule" ? "is-active" : ""
                }`}
                aria-pressed={mode === "schedule"}
                onClick={() => setMode("schedule")}
              >
                Schedule
              </button>
              <button
                type="button"
                className={`flight-mode-button ${
                  mode === "actual" ? "is-active" : ""
                }`}
                aria-pressed={mode === "actual"}
                onClick={() => setMode("actual")}
              >
                Actual
              </button>
            </div>
          )}
          <a
            className="btn btn-info btn-sm flight-pdf-button"
            href={`${API_BASE}/missions/air-operations/${qs({ date, mode })}`}
            target="_blank"
            rel="noreferrer"
          >
            Download PDF
          </a>
        </div>
      </div>

      <ErrorBanner message={error} onRetry={reload} />
      {actionError && <ErrorBanner message={actionError} />}
      {notice && (
        <div className="alert alert-success" role="status">
          {notice}
        </div>
      )}
      {view === "board" && timingWarnings.length > 0 && (
        <div className="alert alert-warning" role="status">
          <strong>
            {timingWarnings.length} actual-time timing check
            {timingWarnings.length === 1 ? "" : "s"}.
          </strong>{" "}
          Review the highlighted flight warnings below; the recorded actual
          times remain in effect.
        </div>
      )}
      {view === "map" ? (
        <AirSimulationMap date={date} />
      ) : loading && !data ? (
        <Loading />
      ) : summary.elements === 0 ? (
        <EmptyState message="No flight operations are recorded for this date. Choose another date or create an air mission." />
      ) : (
        <>
          <div className="flight-ops-summary" aria-label="Flight operations summary">
            <div className="flight-summary-card">
              <span>Flights</span>
              <strong>{summary.elements}</strong>
            </div>
            <div className="flight-summary-card">
              <span>Aircraft</span>
              <strong>{summary.aircraft}</strong>
            </div>
            <div className="flight-summary-card">
              <span>Complete</span>
              <strong>{summary.green}</strong>
            </div>
            <div className="flight-summary-card">
              <span>In progress</span>
              <strong>{summary.yellow}</strong>
            </div>
            <div className="flight-summary-card">
              <span>Not started</span>
              <strong>{summary.gray}</strong>
            </div>
          </div>

          <div className="flight-ops-legend" aria-label="Status legend">
            <span className="flight-legend-title">Status</span>
            {["gray", "yellow", "green"].map((status) => (
              <span className="flight-legend-item" key={status}>
                <span className={`flight-status-dot status-${status}`} />
                {statusLabel(status)}
              </span>
            ))}
          </div>

          {canFuel && (fuelOutstanding.data?.results || []).length ? (
            <div className="flight-fuel-unrecorded">
              <div className="flight-fuel-unrecorded-head">
                <strong>Fuel not recorded</strong>
                <span className="hint">
                  Landed sorties still owed a POL-01 figure. Stock is not
                  deducted until the crew confirms it.
                </span>
              </div>
              <ul>
                {(fuelOutstanding.data.results || []).map((row) => (
                  <li key={row.mission}>
                    <span className="flight-fuel-unrecorded-mission">
                      <strong>{row.mission_number}</strong>{" "}
                      <span className="hint">{row.title}</span>
                    </span>
                    <span className="hint">
                      {row.aircraft.map((plane) => plane.label).join(", ")}
                    </span>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => openOutstandingFuel(row)}
                      disabled={Boolean(busy)}
                    >
                      Record fuel
                    </Button>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          <div className="table-wrap flight-table-wrap">
            <table className="data-table flight-table">
            <colgroup>
                <col style={{ width: 54 }} />
                <col style={{ width: 142 }} />
                <col style={{ width: 128 }} />
                <col style={{ width: 160 }} />
                <col style={{ width: actual ? 268 : 68 }} />
                <col style={{ width: 122 }} />
                <col />
                {showActions && <col style={{ width: 100 }} />}
              </colgroup>
              <thead>
                <tr>
                  <th scope="col">FLIGHT NR</th>
                  <th scope="col">AIRCRAFT</th>
                  <th scope="col">PILOTS</th>
                  <th scope="col">MISSION</th>
                  <th scope="col">
                    {actual ? "ACTUALS BY LEG" : "ETD (PST)"}
                  </th>
                  <th scope="col">FUEL</th>
                  <th scope="col">ROUTE</th>
                  {showActions && <th scope="col">ACTIONS</th>}
                </tr>
              </thead>
            {missions.map((mission) => {
              const elements = missionElements(mission);
              const remainingLegs = remainingItineraryLegs(mission);
              const airborne = Boolean(mission.is_airborne);
              const missionWarnings = mission.timing_warnings || [];
              const warningText = missionWarnings
                .map((warning) => warning.message)
                .join("\n");
              return elements.map((element, elementIndex) => {
                const planes = element.aircraft || [];
                if (!planes.length) return null;
                flightNumber += 1;
                const dayFlightNumber = flightNumber;
                const legs = elementItineraries(planes);
                const firstLeg = legs[0] || {};
                const status = routeStatus(planes);
                const closed =
                  mission.status === "completed" || mission.status === "cancelled";
                const canChange = canPlan && !closed && remainingLegs.length > 0;
                const canCancel = canPlan && !closed && !airborne;
                const canHold = canPlan && !closed && !airborne;
                return (
                  <tbody key={`${mission.id}-${element.id || element.sequence}`}>
                    <tr>
                      <td
                        className="flight-nr"
                        title={`Flight ${dayFlightNumber} of the day`}
                      >
                        {dayFlightNumber}
                      </td>
                      <td className="flight-aircraft">
                        {planes
                          .map(
                            (plane) =>
                              `${aircraftLabel(plane)}${
                                plane.is_lead ? " (Lead)" : ""
                              }`
                          )
                          .join("\n") || "—"}
                      </td>
                      <td className="flight-pilots">
                        {planes
                          .map((plane, aircraftIndex) => {
                            const crew = crewLabel(plane) || "—";
                            return planes.length > 1
                              ? `${aircraftShort(plane, aircraftIndex)}\n${crew}`
                              : crew;
                          })
                          .join("\n\n") || "—"}
                      </td>
                      <td className="flight-mission">
                        <div className="flight-mission-content">
                          {mission.mission_number ? (
                            <span className="flight-mission-ref">
                              {mission.mission_number}
                            </span>
                          ) : null}
                          <span className="flight-mission-text">
                            {firstLeg.display_mission ||
                              firstLeg.mission ||
                              element.mission ||
                              "—"}
                          </span>
                          <span
                            className={`flight-status-badge status-${status}`}
                          >
                            <span
                              className={`flight-status-dot status-${status}`}
                            />
                            {statusLabel(status)}
                          </span>
                          {mission.is_held && (
                            <span className="flight-status-badge status-yellow">
                              On hold
                            </span>
                          )}
                          {missionWarnings.length > 0 && (
                            <span
                              className="flight-status-badge status-yellow"
                              title={warningText}
                              aria-label={`Timing check: ${warningText}`}
                            >
                              Check actual times
                            </span>
                          )}
                          {mission.status === "cancelled" && (
                            <span className="flight-status-badge status-cancelled">
                              Cancelled
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="flight-time">
                        {actual ? (
                          <div className="actual-leg-times">
                            {legs.map((leg, legIndex) => {
                              const previous =
                                legIndex > 0 ? legs[legIndex - 1] : null;
                              const nextLeg = legs[legIndex + 1] || null;
                              const originIsOver = Boolean(previous?.overflight);
                              const legTone = legStatus(leg, planes, legIndex);
                              const hasId = /^\d+$/.test(String(leg.id || ""));
                              const previousHasId = /^\d+$/.test(
                                String(previous?.id || "")
                              );
                              const departureValue = originIsOver
                                ? timeLabel(previous?.toot, true)
                                : timeLabel(leg.atd, true);
                              const arrivalValue = leg.overflight
                                ? timeLabel(leg.tot, true)
                                : timeLabel(leg.ata, true);
                              const finalOverflight = leg.overflight && !nextLeg;
                              const dependencyHold = Boolean(
                                mission.dependency?.enabled &&
                                  !mission.dependency?.satisfied
                              );
                              const canAtd =
                                hasId &&
                                !originIsOver &&
                                !leg.atd &&
                                !leg.ata &&
                                !dependencyHold &&
                                !mission.is_held &&
                                mission.status !== "cancelled" &&
                                (legIndex === 0 || Boolean(previous?.ata));
                              const canAta =
                                hasId &&
                                !leg.overflight &&
                                !leg.ata &&
                                (originIsOver
                                  ? Boolean(previous?.toot)
                                  : Boolean(leg.atd));
                              const canTot =
                                hasId &&
                                leg.overflight &&
                                (originIsOver
                                  ? Boolean(previous?.toot)
                                  : Boolean(leg.atd)) &&
                                !leg.tot;
                              const canSe =
                                hasId &&
                                !originIsOver &&
                                !leg.se &&
                                !leg.atd &&
                                !leg.ata &&
                                mission.status !== "cancelled";
                              const canSd =
                                hasId &&
                                !leg.overflight &&
                                Boolean(leg.ata) &&
                                !leg.sd &&
                                mission.status !== "cancelled";
                              const canTootPrevious =
                                originIsOver &&
                                previousHasId &&
                                Boolean(previous?.tot) &&
                                !previous?.toot;
                              const canTootFinal =
                                finalOverflight &&
                                hasId &&
                                Boolean(leg.tot) &&
                                !leg.toot;
                              return (
                                <div
                                  className={`actual-leg-time status-${legTone}`}
                                  key={`${mission.id}-${element.id || element.sequence}-actual-${legIndex}`}
                                  title={`Leg ${legIndex + 1}: ${actualRouteLabel(leg, previous)}`}
                                >
                                  <span className="actual-leg-number">
                                    L{legIndex + 1}
                                  </span>
                                  <span className="actual-leg-values">
                                    {originIsOver ? null : (
                                      <span className="actual-leg-value">
                                        <button
                                          type="button"
                                          className="actual-time-action"
                                          disabled={Boolean(busy) || !canSe}
                                          onClick={() =>
                                            openRecord(
                                              mission,
                                              planes,
                                              legIndex,
                                              "se"
                                            )
                                          }
                                          title={
                                            leg.atd
                                              ? "The takeoff is already recorded for this leg."
                                              : "Record engine start before takeoff"
                                          }
                                          aria-label={`Record engine start for leg ${legIndex + 1}`}
                                        >
                                          SE
                                        </button>
                                        <strong>{timeLabel(leg.se, true)}</strong>
                                      </span>
                                    )}
                                    <span className="actual-leg-value">
                                      {originIsOver ? (
                                        <button
                                          type="button"
                                          className="actual-time-action"
                                          disabled={Boolean(busy) || !canTootPrevious}
                                          onClick={() =>
                                            openRecord(
                                              mission,
                                              planes,
                                              legIndex - 1,
                                              "toot"
                                            )
                                          }
                                          title="Record departure from the target; unlocks this ATA"
                                          aria-label={`Record TOOT at the origin of leg ${legIndex + 1}`}
                                        >
                                          TOOT
                                        </button>
                                      ) : (
                                        <button
                                          type="button"
                                          className="actual-time-action"
                                          disabled={Boolean(busy) || !canAtd}
                                          onClick={() =>
                                            openRecord(
                                              mission,
                                              planes,
                                              legIndex,
                                              "takeoff"
                                            )
                                          }
                                          title={
                                            mission.is_held
                                              ? "This mission is on hold. Release the hold before recording ATD."
                                              : dependencyHold
                                                ? mission.dependency.label
                                                : "Record actual takeoff for this leg"
                                          }
                                          aria-label={`Record ATD for leg ${legIndex + 1}`}
                                        >
                                          ATD
                                        </button>
                                      )}
                                      <strong>{departureValue}</strong>
                                    </span>
                                    <span className="actual-leg-value">
                                      {leg.overflight ? (
                                        <button
                                          type="button"
                                          className="actual-time-action"
                                          disabled={Boolean(busy) || !canTot}
                                          onClick={() =>
                                            openRecord(
                                              mission,
                                              planes,
                                              legIndex,
                                              "tot"
                                            )
                                          }
                                          title="Record arrival over target"
                                          aria-label={`Record TOT for leg ${legIndex + 1}`}
                                        >
                                          TOT
                                        </button>
                                      ) : (
                                        <button
                                          type="button"
                                          className="actual-time-action"
                                          disabled={Boolean(busy) || !canAta}
                                          onClick={() =>
                                            openRecord(
                                              mission,
                                              planes,
                                              legIndex,
                                              "landing"
                                            )
                                          }
                                          title="Record actual landing for this leg"
                                          aria-label={`Record ATA for leg ${legIndex + 1}`}
                                        >
                                          ATA
                                        </button>
                                      )}
                                      <strong>{arrivalValue}</strong>
                                    </span>
                                    {leg.overflight ? null : (
                                      <span className="actual-leg-value">
                                        <button
                                          type="button"
                                          className="actual-time-action"
                                          disabled={Boolean(busy) || !canSd}
                                          onClick={() =>
                                            openRecord(
                                              mission,
                                              planes,
                                              legIndex,
                                              "sd"
                                            )
                                          }
                                          title={
                                            leg.ata
                                              ? "Record engine shutdown after landing"
                                              : "Record the landing (ATA) before the shutdown"
                                          }
                                          aria-label={`Record engine shutdown for leg ${legIndex + 1}`}
                                        >
                                          SD
                                        </button>
                                        <strong>{timeLabel(leg.sd, true)}</strong>
                                      </span>
                                    )}
                                    {finalOverflight ? (
                                      <span className="actual-leg-value">
                                        <button
                                          type="button"
                                          className="actual-time-action"
                                          disabled={Boolean(busy) || !canTootFinal}
                                          onClick={() =>
                                            openRecord(
                                              mission,
                                              planes,
                                              legIndex,
                                              "toot"
                                            )
                                          }
                                          title="Record departure from target"
                                          aria-label={`Record TOOT for leg ${legIndex + 1}`}
                                        >
                                          TOOT
                                        </button>
                                        <strong>{timeLabel(leg.toot, true)}</strong>
                                      </span>
                                    ) : null}
                                  </span>
                                </div>
                              );
                            })}
                          </div>
                        ) : (
                          <span className="scheduled-time">
                            {timeLabel(firstLeg.etd, false)}
                          </span>
                        )}
                      </td>
                      <td className="flight-fuel">
                        {legs.map((leg, legIndex) => {
                          const rows = planes
                            .map((plane, aircraftIndex) => ({
                              plane,
                              aircraftIndex,
                              planeLeg: plane.itineraries?.[legIndex],
                            }))
                            .filter(({ plane, planeLeg }) => {
                              if (!planeLeg?.id) return false;
                              return Boolean(
                                fuelDrawFor(mission, plane.id, planeLeg.id) ||
                                  (canFuel && legShutdown(planeLeg))
                              );
                            });
                          return (
                            <div
                              className="flight-fuel-leg"
                              key={`${mission.id}-fuel-leg-${legIndex}`}
                            >
                              <span className="flight-fuel-leg-number">
                                L{legIndex + 1}
                              </span>
                              {rows.length ? (
                              <span className="flight-fuel-leg-values">
                                {rows.map(
                                  ({ plane, aircraftIndex, planeLeg }) => {
                                    const draw = fuelDrawFor(
                                      mission,
                                      plane.id,
                                      planeLeg.id
                                    );
                                    return (
                                      <span
                                        className="flight-fuel-row"
                                        key={
                                          planeLeg.id ||
                                          `${mission.id}-fuel-${aircraftIndex}-${legIndex}`
                                        }
                                      >
                                        {planes.length > 1 && (
                                          <span className="flight-fuel-tail">
                                            {aircraftShort(
                                              plane,
                                              aircraftIndex
                                            )}
                                          </span>
                                        )}
                                        {draw ? (
                                          canFuel ? (
                                            <button
                                              type="button"
                                              className="flight-fuel-value"
                                              disabled={Boolean(busy)}
                                              title="Correct this leg's fuel figure"
                                              onClick={() =>
                                                setFuelTarget({
                                                  mission,
                                                  aircraftId: plane.id,
                                                  legId: planeLeg.id,
                                                })
                                              }
                                            >
                                              {fuelLabel(draw)}
                                            </button>
                                          ) : (
                                            <span className="flight-fuel-value">
                                              {fuelLabel(draw)}
                                            </span>
                                          )
                                        ) : (
                                          <button
                                            type="button"
                                            className="actual-time-action"
                                            disabled={Boolean(busy)}
                                            title={`Record the POL-01 burned on leg ${legIndex + 1}`}
                                            aria-label={`Record fuel for leg ${legIndex + 1}`}
                                            onClick={() =>
                                              setFuelTarget({
                                                mission,
                                                aircraftId: plane.id,
                                                legId: planeLeg.id,
                                              })
                                            }
                                          >
                                            Record fuel
                                          </button>
                                        )}
                                      </span>
                                    );
                                  }
                                )}
                              </span>
                              ) : (
                                <span className="flight-fuel-leg-empty">
                                  ---
                                </span>
                              )}
                            </div>
                          );
                        })}
                      </td>
                      <td className="flight-route">
                        <div className="route-legs">
                          {legs.length
                            ? legs.map((leg, legIndex) => {
                                const previous =
                                  legIndex > 0 ? legs[legIndex - 1] : null;
                                const legTone = legStatus(leg, planes, legIndex);
                                return (
                                  <div
                                    className={`route-leg status-${legTone}`}
                                    key={`${mission.id}-${element.id || element.sequence}-route-${legIndex}`}
                                  >
                                    <span className="route-leg-number">
                                      L{legIndex + 1}
                                    </span>
                                    <span className="route-leg-text">
                                      [{legRouteLabel(leg, previous)}]
                                      {leg.overflight ? " (overflight)" : ""}
                                      {leg.return_to_origin ? " (return)" : ""}
                                    </span>
                                  </div>
                                );
                              })
                            : "—"}
                        </div>
                      </td>
                      {showActions && (
                        <td className="flight-actions-cell">
                          {elementIndex === 0 ? (
                            <div className="flight-actions">
                              {canPlan && !closed && (
                                <>
                                  <Button
                                    variant="ghost"
                                    size="sm"
                                    disabled={Boolean(busy) || !canChange}
                                    title={
                                      remainingLegs.length
                                        ? "Open the Air Mission page to edit unflown details"
                                        : "No remaining itinerary legs to change"
                                    }
                                    onClick={() => changeItinerary(mission)}
                                  >
                                    Change Itin
                                  </Button>
                                  <Button
                                    variant={mission.is_held ? "warning" : "ghost"}
                                    size="sm"
                                    disabled={Boolean(busy) || !canHold}
                                    title={
                                      airborne
                                        ? "Wait until every aircraft has landed at its destination."
                                        : mission.is_held
                                          ? "Release the hold and allow ATD again"
                                          : "Hold this mission and block ATD"
                                    }
                                    onClick={() => toggleHold(mission)}
                                  >
                                    {mission.is_held ? "Release Hold" : "Hold"}
                                  </Button>
                                  <Button
                                    variant="warning"
                                    size="sm"
                                    disabled={Boolean(busy) || !canCancel}
                                    title={
                                      airborne
                                        ? "Wait until every aircraft has landed at its destination."
                                        : "Cancel the remaining itinerary without deleting its history"
                                    }
                                    onClick={() => cancelMission(mission)}
                                  >
                                    Cancel
                                  </Button>
                                </>
                              )}
                              {isAdmin && (
                                <Button
                                  variant="danger"
                                  size="sm"
                                  disabled={Boolean(busy)}
                                  title="Administrator only: permanently delete this mission"
                                  onClick={() => deleteMission(mission)}
                                >
                                  Delete
                                </Button>
                              )}
                            </div>
                          ) : null}
                        </td>
                      )}
                    </tr>
                  </tbody>
                );
              });
            })}
            </table>
          </div>
        </>
      )}
    </Card>
    {fuelTarget && (
      <AirFuelDialog
        mission={fuelTarget.mission}
        aircraftId={fuelTarget.aircraftId}
        legId={fuelTarget.legId}
        onClose={() => setFuelTarget(null)}
        onSaved={() => {
          reload();
          fuelOutstanding.reload?.();
        }}
      />
    )}
    {recordTarget && (
      <Modal
        title={recordTitle(recordTarget.event)}
        onClose={() => setRecordTarget(null)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setRecordTarget(null)}>
              Cancel
            </Button>
            <Button onClick={saveRecord} disabled={Boolean(busy) || !recordAt}>
              {busy ? "Recording…" : "Save actual time"}
            </Button>
          </>
        }
      >
        <p className="hint">{recordHint(recordTarget.event)}</p>
        <Input
          type="datetime-local"
          value={recordAt}
          onChange={(event) => setRecordAt(event.target.value)}
          aria-label="Actual time"
        />
        {actionError && <p className="field-message">{actionError}</p>}
      </Modal>
    )}
    {cancelTarget && (
      <Modal
        title={`Cancel mission · ${cancelTarget.mission.mission_number}`}
        onClose={() => setCancelTarget(null)}
        footer={
          <>
            <Button
              variant="ghost"
              onClick={() => setCancelTarget(null)}
              disabled={Boolean(busy)}
            >
              Keep Mission
            </Button>
            <Button
              variant="danger"
              onClick={applyCancel}
              disabled={Boolean(busy)}
            >
              {busy ? "Cancelling…" : "Cancel Mission"}
            </Button>
          </>
        }
      >
        <p className="hint">
          Cancelling marks the mission as cancelled. The unflown legs below will
          be slashed; every leg already flown keeps its recorded actual times.
        </p>
        {cancelTarget.rows.length ? (
          <ul className="cancel-leg-list">
            {cancelTarget.rows.map(({ leg, elementIndex, legIndex }) => (
              <li key={leg.id}>
                <strong>
                  Element {elementIndex + 1} · Leg {legIndex + 1}
                </strong>
                <span>{legRouteLabel(leg)}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p>No unflown legs remain; only the mission status will change.</p>
        )}
        {actionError && <p className="field-message">{actionError}</p>}
      </Modal>
    )}
    </>
  );
}
