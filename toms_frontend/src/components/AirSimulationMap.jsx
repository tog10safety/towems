import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { divIcon } from "leaflet";
import {
  CircleMarker,
  MapContainer,
  Marker,
  Polyline,
  Popup,
  Tooltip,
  useMap,
} from "react-leaflet";
import { qs } from "../api/client.js";
import { useApi } from "../hooks/useApi.js";
import { useTheme } from "../theme/ThemeContext.jsx";
import {
  BASEMAP_STORAGE_KEY,
  readStored,
  resolveBasemap,
  writeStored,
} from "../map/config.js";
import { AIR_ASSET_GLYPHS, aircraftTypeGroup } from "../map/airAssets.jsx";
import { Button, EmptyState, ErrorBanner, Loading } from "./ui.jsx";
import MapFrameControls from "./map/MapFrameControls.jsx";
import MapResize from "./map/MapResize.jsx";
import useMapFullScreen from "../hooks/useMapFullScreen.js";
import MapBasemapLayer from "./map/MapBasemapLayer.jsx";
import Icon from "./Icon.jsx";
import { formatDateTime, formatTime } from "../utils/format.js";
import { legRouteLabel } from "../utils/airRoute.js";

const DEFAULT_CENTER = [12.8797, 121.774];
const POLL_MS = 10000;
const PLANNED_COLOR = "#94a3b8";
const TRAVELED_COLOR = "#22c55e";

const STATUS_ORDER = [
  "en_route",
  "planned",
  "pending_dependency",
  "on_hold",
  "unavailable",
  "timing_missing",
  "arrived",
  "landed",
  "cancelled",
];

const STATUS_COLORS = {
  planned: "#94a3b8",
  en_route: "#22c55e",
  arrived: "#3fb950",
  landed: "#94a3b8",
  on_hold: "#eab308",
  cancelled: "#8b949e",
  delayed: "#f97316",
  pending_dependency: "#a371f7",
  unavailable: "#e5484d",
  timing_missing: "#d29922",
};

function minutesLabel(value) {
  if (value === null || value === undefined) return "—";
  const rounded = Math.max(0, Math.round(Number(value)));
  if (rounded < 60) return `${rounded} min`;
  return `${Math.floor(rounded / 60)} h ${rounded % 60} min`;
}

function coordinateLabel(position) {
  if (!Array.isArray(position) || position.length < 2) return "—";
  return `${Number(position[0]).toFixed(5)}, ${Number(position[1]).toFixed(5)}`;
}

function markerOffset(row, index) {
  const formation = Math.max(1, Number(row.formation_size) || 1);
  const formationIndex = Math.max(0, Number(row.formation_index) || 0);
  const dx = (formationIndex - (formation - 1) / 2) * 7;
  const dy = (formationIndex + (index % 2)) * 2;
  return [dx, dy];
}

function simulationIcon(row, selected, index) {
  const group = aircraftTypeGroup(row);
  const colour = group.color || "#8b949e";
  const statusColour = STATUS_COLORS[row.status] || "#8b949e";
  const heading = Number(row.heading_degrees) || 0;
  const [dx, dy] = markerOffset(row, index);
  return divIcon({
    className: `air-sim-icon${selected ? " is-selected" : ""}`,
    html:
      `<span class="air-sim-marker" style="--aircraft-colour:${colour};--status-colour:${statusColour}">` +
      `<span class="air-sim-rotate" style="transform:rotate(${heading}deg)">` +
      `<svg viewBox="0 0 48 48" width="34" height="34" fill="currentColor" color="${colour}" stroke="#ffffff" stroke-width="1.2" stroke-linejoin="round" aria-hidden="true">` +
      `${AIR_ASSET_GLYPHS[group.icon] || AIR_ASSET_GLYPHS.other}</svg>` +
      `</span><span class="air-sim-status-dot"></span></span>`,
    iconSize: [40, 40],
    iconAnchor: [20 - dx, 20 - dy],
    popupAnchor: [dx, dy - 20],
  });
}

function MapController({ points, fitToken, focusPoint, focusToken }) {
  const map = useMap();

  useEffect(() => {
    map.invalidateSize();
    if (points.length === 1) map.setView(points[0], 11);
    else if (points.length > 1) map.fitBounds(points, { padding: [42, 42] });
    // fitToken is the deliberate "fit all" trigger; polling positions must not
    // keep yanking the operator's view back to the whole country.
  }, [fitToken]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!focusPoint) return;
    map.flyTo(focusPoint, Math.max(map.getZoom(), 10), { duration: 0.55 });
  }, [focusToken]); // eslint-disable-line react-hooks/exhaustive-deps

  return null;
}

function PopupRow({ label, children }) {
  return (
    <div className="air-sim-popup-row">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function LegPopup({ row, leg, previous }) {
  const landed = Boolean(leg.overflight ? leg.toot : leg.ata);
  const airborne = Boolean(!landed && (leg.atd || leg.tot));
  const cancelled = leg.status === "cancelled";
  const tone = cancelled
    ? "cancelled"
    : landed
      ? "arrived"
      : airborne
        ? "en_route"
        : "planned";
  const toneLabel = cancelled
    ? "Cancelled"
    : landed
      ? "Landed"
      : airborne
        ? "Airborne"
        : "Planned";
  return (
    <div className="air-sim-popup">
      <div className="air-sim-popup-head">
        <strong>
          Leg {leg.sequence} · {row.tail_number || row.callsign || "Aircraft"}
        </strong>
        <span className={`air-sim-status status-${tone}`}>{toneLabel}</span>
      </div>
      <p className="air-sim-popup-sim">
        {row.mission_number || "No mission"} · itinerary leg details
      </p>
      <dl>
        <PopupRow label="Route">{legRouteLabel(leg, previous)}</PopupRow>
        <PopupRow label="Origin">{leg.origin || "—"}</PopupRow>
        <PopupRow label="Destination">{leg.destination || "—"}</PopupRow>
        <PopupRow label="Leg task">{leg.mission || "—"}</PopupRow>
        <PopupRow label="ETD">{formatDateTime(leg.etd)}</PopupRow>
        <PopupRow label="ETA">{formatDateTime(leg.eta)}</PopupRow>
        <PopupRow label={leg.overflight ? "TOT" : "Actual takeoff"}>
          {formatDateTime(leg.overflight ? leg.tot : leg.atd)}
        </PopupRow>
        <PopupRow label={leg.overflight ? "TOOT" : "Actual landing"}>
          {formatDateTime(leg.overflight ? leg.toot : leg.ata)}
        </PopupRow>
        {leg.overflight ? (
          <PopupRow label="Leg type">Overflight</PopupRow>
        ) : null}
        {leg.return_to_origin ? (
          <PopupRow label="Leg type">Return to origin</PopupRow>
        ) : null}
      </dl>
    </div>
  );
}

function AircraftPopup({ row }) {
  return (
    <div className="air-sim-popup">
      <div className="air-sim-popup-head">
        <strong>{row.tail_number || row.callsign || "Aircraft"}</strong>
        <span className={`air-sim-status status-${row.status}`}>
          {row.status_label}
        </span>
      </div>
      <p className="air-sim-popup-sim">Simulated position — not live GPS</p>
      <dl>
        <PopupRow label="Aircraft type">
          {row.aircraft_type_label || row.aircraft_type || "—"}
        </PopupRow>
        <PopupRow label="Model">{row.aircraft_model || "—"}</PopupRow>
        <PopupRow label="Tail number">{row.tail_number || "—"}</PopupRow>
        <PopupRow label="Call sign">{row.callsign || "—"}</PopupRow>
        <PopupRow label="OPCON unit">{row.opcon_unit_label || "—"}</PopupRow>
        <PopupRow label="Mission">{row.mission_number || "—"}</PopupRow>
        <PopupRow label="Mission type">{row.mission_type || "—"}</PopupRow>
        <PopupRow label="Origin">{row.origin || "—"}</PopupRow>
        <PopupRow label="Destination">{row.destination || "—"}</PopupRow>
        <PopupRow label="ETD">{formatDateTime(row.etd)}</PopupRow>
        <PopupRow label="Actual takeoff">
          {row.actual_departure_at
            ? formatDateTime(row.actual_departure_at)
            : "—"}
        </PopupRow>
        <PopupRow label="Estimated enroute">
          {minutesLabel(row.estimated_enroute_minutes)}
        </PopupRow>
        <PopupRow label="Arrival buffer">
          {minutesLabel(row.buffer_minutes)} (configured)
        </PopupRow>
        <PopupRow label="Simulated arrival">
          {formatDateTime(row.simulated_arrival_at)}
        </PopupRow>
        <PopupRow label="Current position">
          {coordinateLabel(row.position)}
        </PopupRow>
        <PopupRow label="Flight progress">
          {Number(row.progress_percent || 0).toFixed(0)}%
        </PopupRow>
        <PopupRow label="Remaining enroute">
          {minutesLabel(row.remaining_enroute_minutes)}
        </PopupRow>
        <PopupRow label="Mission status">
          {row.status_label}
        </PopupRow>
        <PopupRow label="Record status">
          {row.mission_status_label || row.mission_status || "—"}
        </PopupRow>
      </dl>
      {row.status_reason ? (
        <p className="air-sim-popup-reason">{row.status_reason}</p>
      ) : null}
    </div>
  );
}

/**
 * Operational, time-based aircraft map for the Air Missions page.
 *
 * The backend supplies every position and timing. This component only polls the
 * snapshot and draws it, so a refresh, a new tab or a second operator cannot
 * produce a different aircraft position for the same moment.
 */
export default function AirSimulationMap({ date }) {
  const { resolvedTheme } = useTheme();
  const config = useApi("/map/config/");
  const path = `/missions/air-simulation/${qs({ date })}`;
  const { data, loading, error, reload } = useApi(path, { deps: [date] });
  const [basemapId, setBasemapId] = useState(() =>
    readStored(BASEMAP_STORAGE_KEY, "")
  );
  const [basemapFailure, setBasemapFailure] = useState("");
  // The map's own full-screen mode, shared with the other maps.
  const { fullScreen, frame, toggleFullScreen } = useMapFullScreen();
  const [selectedId, setSelectedId] = useState("");
  const [hiddenTracks, setHiddenTracks] = useState(() => new Set());
  const [focusToken, setFocusToken] = useState(0);
  const [fitToken, setFitToken] = useState(0);
  const lastAircraftKey = useRef("");

  const basemap = useMemo(
    () => resolveBasemap(config.data, basemapId, resolvedTheme),
    [config.data, basemapId, resolvedTheme]
  );
  const rows = data?.aircraft ?? [];
  const selected = rows.find((row) => row.id === selectedId) || null;

  useEffect(() => {
    lastAircraftKey.current = "";
    setHiddenTracks(new Set());
  }, [date]);

  useEffect(() => {
    let timer = null;

    function startPolling() {
      if (timer !== null) return;
      timer = window.setInterval(() => reload(), POLL_MS);
    }

    function stopPolling() {
      if (timer === null) return;
      window.clearInterval(timer);
      timer = null;
    }

    function onVisibilityChange() {
      if (document.visibilityState === "visible") {
        // Return to a fresh picture immediately, then resume the normal cadence.
        reload();
        startPolling();
      } else {
        stopPolling();
      }
    }

    if (document.visibilityState === "visible") startPolling();
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      stopPolling();
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [reload]);

  useEffect(() => {
    if (!data) return;
    const key = rows.map((row) => row.id).join("|");
    if (key && key !== lastAircraftKey.current) {
      lastAircraftKey.current = key;
      setFitToken((value) => value + 1);
    }
  }, [data, rows]);

  const mapPoints = useMemo(() => {
    const points = [];
    for (const row of rows) {
      for (const point of row.planned_route || []) {
        if (Array.isArray(point) && point.length >= 2) points.push(point);
      }
      if (Array.isArray(row.position) && row.position.length >= 2) {
        points.push(row.position);
      }
    }
    return points;
  }, [rows]);

  function chooseBasemap(value) {
    setBasemapId(value);
    writeStored(BASEMAP_STORAGE_KEY, value);
    setBasemapFailure("");
  }

  function focusAircraft(row) {
    setSelectedId(row.id);
    setFocusToken((value) => value + 1);
  }

  function toggleTrack(row) {
    // A formation shares one mission track; toggling one aircraft's eye hides
    // the route for the whole mission rather than leaving duplicate lines from
    // the other aircraft visible.
    const key = row.mission_id ?? row.id;
    setHiddenTracks((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  const sortedRows = useMemo(
    () =>
      [...rows].sort((first, second) => {
        const firstIndex = STATUS_ORDER.indexOf(first.status);
        const secondIndex = STATUS_ORDER.indexOf(second.status);
        return (
          (firstIndex < 0 ? 99 : firstIndex) -
            (secondIndex < 0 ? 99 : secondIndex) ||
          String(first.mission_number || "").localeCompare(
            String(second.mission_number || "")
          )
        );
      }),
    [rows]
  );

  return (
    <section className="air-sim" aria-label="Air mission simulation map">
      <div className="air-sim-disclaimer" role="note">
        <strong>Simulated tracking only.</strong>{" "}
        {data?.disclaimer ||
          "Positions are calculated from scheduled flight plans or recorded takeoffs, not GPS or ADS-B."}
      </div>

      <div className="air-sim-head">
        <div>
          <h3>Operational aircraft picture</h3>
          <p className="hint">
            Planned routes remain gray. The green segment is the simulated path
            already flown. Click a leg on the map to see that itinerary leg's
            details. The map refreshes automatically every 10 seconds.
          </p>
        </div>
        <div className="air-sim-head-actions">
          <span className="air-sim-updated">
            Snapshot {formatTime(data?.server_time)}
          </span>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setFitToken((value) => value + 1)}
          >
            Fit all aircraft
          </Button>
        </div>
      </div>

      <ErrorBanner message={error} onRetry={reload} />
      {basemapFailure && (
        <div className="alert alert-warning">
          The selected basemap could not be reached. The aircraft picture is
          unaffected — choose another map style.
        </div>
      )}

      {loading && !data ? (
        <Loading label="Loading simulated aircraft picture…" />
      ) : rows.length === 0 ? (
        <EmptyState message="No planned or active aircraft are recorded for this operations date." />
      ) : (
        <div
          className={`air-sim-layout${fullScreen ? " is-fullscreen" : ""}`}
          style={fullScreen ? { left: frame.left, top: frame.top } : undefined}
        >
          <div className="air-sim-canvas">
            <MapFrameControls
              config={config.data}
              value={basemap?.id}
              onChangeBasemap={chooseBasemap}
              fullScreen={fullScreen}
              onToggleFullScreen={toggleFullScreen}
            />
            <MapContainer
              center={mapPoints[0] || DEFAULT_CENTER}
              zoom={mapPoints.length ? 9 : 6}
              scrollWheelZoom
            >
              <MapBasemapLayer
                basemap={basemap}
                onError={() => setBasemapFailure(basemap?.id || "")}
              />
              <MapResize expanded={fullScreen} />
              <MapController
                points={mapPoints}
                fitToken={fitToken}
                focusPoint={selected?.position}
                focusToken={focusToken}
              />

              {rows.map((row, index) => {
                const route = row.planned_route || [];
                const traveled = row.traveled_route || [];
                const position = row.position;
                const trackHidden = hiddenTracks.has(row.mission_id);
                if (!Array.isArray(position) || position.length < 2) return null;
                return (
                  <Fragment key={row.id}>
                    {!trackHidden && route.length > 1 && (
                      <Polyline
                        positions={route}
                        pathOptions={{
                          color: PLANNED_COLOR,
                          weight: 3,
                          opacity: 0.7,
                        }}
                      />
                    )}
                    {!trackHidden && traveled.length > 1 && (
                      <Polyline
                        positions={traveled}
                        pathOptions={{
                          color: TRAVELED_COLOR,
                          weight: 5,
                          opacity: 0.95,
                        }}
                      />
                    )}
                    {!trackHidden &&
                      (row.legs || []).map((leg, legListIndex, allLegs) => {
                        if (!Array.isArray(leg.points) || leg.points.length < 2) {
                          return null;
                        }
                        const previous =
                          legListIndex > 0 ? allLegs[legListIndex - 1] : null;
                        return (
                          <Polyline
                            key={`${row.id}-leg-${leg.id ?? legListIndex}`}
                            positions={leg.points}
                            pathOptions={{
                              // A wide, effectively invisible line gives each
                              // leg a comfortable click target without changing
                              // how the route itself is drawn.
                              color: PLANNED_COLOR,
                              weight: 16,
                              opacity: 0.01,
                            }}
                            eventHandlers={{
                              click: () => setSelectedId(row.id),
                            }}
                          >
                            <Popup maxWidth={340}>
                              <LegPopup row={row} leg={leg} previous={previous} />
                            </Popup>
                          </Polyline>
                        );
                      })}
                    {!trackHidden && route[0] && (
                      <CircleMarker
                        center={route[0]}
                        radius={4}
                        pathOptions={{
                          color: PLANNED_COLOR,
                          fillColor: PLANNED_COLOR,
                          fillOpacity: 0.8,
                        }}
                      >
                        <Tooltip direction="top">Origin · {row.origin}</Tooltip>
                      </CircleMarker>
                    )}
                    {!trackHidden && route.length > 1 && (
                      <CircleMarker
                        center={route[route.length - 1]}
                        radius={5}
                        pathOptions={{
                          color: TRAVELED_COLOR,
                          fillColor: "#ffffff",
                          fillOpacity: 1,
                        }}
                      >
                        <Tooltip direction="top">
                          Destination · {row.destination}
                        </Tooltip>
                      </CircleMarker>
                    )}
                    <Marker
                      position={position}
                      icon={simulationIcon(row, selectedId === row.id, index)}
                      zIndexOffset={selectedId === row.id ? 900000 : 500000}
                      eventHandlers={{ click: () => setSelectedId(row.id) }}
                    >
                      <Popup maxWidth={360}>
                        <AircraftPopup row={row} />
                      </Popup>
                    </Marker>
                  </Fragment>
                );
              })}
            </MapContainer>
            <div className="air-sim-map-legend">
              <span><i className="air-sim-line planned" /> Planned route</span>
              <span><i className="air-sim-line traveled" /> Traveled</span>
              <span><i className="air-sim-plane-dot" /> Simulated aircraft</span>
            </div>
          </div>

          <aside className="air-sim-side">
            <div className="air-sim-side-head">
              <h3>Aircraft</h3>
              <span>{rows.length}</span>
            </div>
            <p className="hint">
              Select an aircraft to focus the map and open its latest simulated
              information. Use the eye to hide or show that aircraft's track.
              Formation aircraft remain grouped by mission.
            </p>
            <div className="air-sim-aircraft-list">
              {sortedRows.map((row) => {
                const trackHidden = hiddenTracks.has(row.mission_id);
                return (
                  <div
                    key={row.id}
                    className={`air-sim-aircraft ${
                      selectedId === row.id ? "is-selected" : ""
                    } ${trackHidden ? "is-track-hidden" : ""}`}
                  >
                    <button
                      type="button"
                      className="air-sim-aircraft-main"
                      onClick={() => focusAircraft(row)}
                    >
                      <span className="air-sim-aircraft-top">
                        <strong>
                          {row.tail_number || row.callsign || "Aircraft"}
                        </strong>
                        <span className={`air-sim-status status-${row.status}`}>
                          {row.status_label}
                        </span>
                      </span>
                      <span className="air-sim-aircraft-sub">
                        {row.mission_number || "No mission"}
                        {row.callsign ? ` · ${row.callsign}` : ""}
                        {row.is_lead ? " · Lead" : ""}
                      </span>
                      <span className="air-sim-aircraft-route">
                        {row.current_leg
                          ? `Leg ${row.current_leg.sequence} · ${
                              row.current_leg.origin || "—"
                            } → ${row.current_leg.destination || "—"}`
                          : `${row.origin || "—"} → ${row.destination || "—"}`}
                      </span>
                      <span className="air-sim-progress">
                        <span style={{ width: `${row.progress_percent || 0}%` }} />
                      </span>
                      <span className="air-sim-aircraft-times">
                        <span>
                          {Number(row.progress_percent || 0).toFixed(0)}%
                        </span>
                        <span>ETA {formatTime(row.simulated_arrival_at)}</span>
                      </span>
                    </button>
                    <button
                      type="button"
                      className="air-sim-track-toggle"
                      aria-pressed={trackHidden}
                      aria-label={
                        trackHidden
                          ? `Show track for mission ${row.mission_number || "this mission"}`
                          : `Hide track for mission ${row.mission_number || "this mission"}`
                      }
                      title={
                        trackHidden
                          ? "Show this flight mission's track on the map"
                          : "Hide this flight mission's track from the map"
                      }
                      onClick={() => toggleTrack(row)}
                    >
                      <Icon name={trackHidden ? "eyeOff" : "eye"} size={16} />
                    </button>
                  </div>
                );
              })}
            </div>

            <div className="air-sim-status-legend">
              <h4>Status legend</h4>
              {[
                ["planned", "Planned"],
                ["en_route", "En Route"],
                ["arrived", "Arrived"],
                ["landed", "Landed at stop"],
                ["delayed", "Delayed"],
                ["on_hold", "On Hold"],
                ["cancelled", "Cancelled"],
                ["pending_dependency", "Pending dependency"],
              ].map(([status, label]) => (
                <span key={status}>
                  <i className={`air-sim-dot status-${status}`} /> {label}
                </span>
              ))}
            </div>

          </aside>
        </div>
      )}
    </section>
  );
}