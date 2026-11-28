import { Fragment, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
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
import { API_BASE, api } from "../api/client.js";
import { useAuth } from "../auth/AuthContext.jsx";
import {
  Badge,
  Button,
  ErrorBanner,
  Loading,
  Modal,
  StatusBadge,
} from "../components/ui.jsx";
import PdfViewer from "../components/PdfViewer.jsx";
import Icon from "../components/Icon.jsx";
import MapFrameControls from "../components/map/MapFrameControls.jsx";
import MapResize from "../components/map/MapResize.jsx";
import useMapFullScreen from "../hooks/useMapFullScreen.js";
import MapBasemapLayer from "../components/map/MapBasemapLayer.jsx";
import { useApi } from "../hooks/useApi.js";
import { useTheme } from "../theme/ThemeContext.jsx";
import {
  BASEMAP_STORAGE_KEY,
  availableBasemaps,
  readStored,
  resolveBasemap,
  writeStored,
} from "../map/config.js";
import { formatDateTime } from "../utils/format.js";
import { headingCone, headingForPings } from "../map/vehicleHeading.js";

const DEFAULT_CENTER = [12.8797, 121.774];

/**
 * A vehicle on the mission map, with the cone its pings point in.
 *
 * The colour keeps the meaning the circle had: gold for the convoy lead, red
 * for the others. A vehicle whose phone has never carried a bearing shows the
 * plain pin.
 */
function vehiclePin(run) {
  return divIcon({
    className: `mission-vehicle-pin${run.is_lead ? " is-lead" : ""}`,
    html: `${headingCone(headingForPings(run.recent_pings))}<span></span>`,
    iconSize: [24, 24],
    iconAnchor: [12, 12],
    popupAnchor: [0, -14],
  });
}

/** Fit the view to everything this mission actually has on the map. */
function FitTrack({ points, panelOpen }) {
  const map = useMap();
  const key = points.map((point) => point.join(",")).join("|");

  useEffect(() => {
    // Showing or hiding the side panel changes the container width without a
    // window resize, which Leaflet does not notice on its own.
    map.invalidateSize();
    if (points.length === 1) map.setView(points[0], 12);
    else if (points.length > 1) map.fitBounds(points, { padding: [48, 48] });
  }, [key, panelOpen]); // eslint-disable-line react-hooks/exhaustive-deps

  return null;
}

/** OSRM geometry is GeoJSON `[longitude, latitude]`; Leaflet wants the reverse. */
function roadPositions(geometry) {
  const coordinates = geometry?.coordinates ?? [];
  return coordinates
    .filter((pair) => Array.isArray(pair) && pair.length >= 2)
    .map(([longitude, latitude]) => [latitude, longitude]);
}

function stopPositions(itinerary) {
  return (itinerary || [])
    .filter((stop) => stop.latitude != null && stop.longitude != null)
    .map((stop) => [stop.latitude, stop.longitude]);
}

function formatKilometres(metres) {
  if (metres == null) return "—";
  return `${(metres / 1000).toFixed(1)} km`;
}

function formatSpan(seconds) {
  if (seconds == null) return "—";
  const totalMinutes = Math.round(seconds / 60);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return hours > 0 ? `${hours} h ${minutes} min` : `${minutes} min`;
}

/**
 * A mission's own map, opened in its own tab: the planned road route for every
 * vehicle, the ordered stops, and the mission objective pin.
 *
 * The line drawn is the routing engine's road geometry. When a trip has no
 * calculated route the map says so and draws only its stops - it never joins
 * them with a straight line that would read as an "as the crow flies" track.
 */
export default function MissionTrackPage() {
  const { id } = useParams();
  const { permissions, user } = useAuth();
  const { resolvedTheme } = useTheme();
  const { data, loading, error, reload } = useApi(`/missions/${id}/`, { deps: [id] });
  const config = useApi("/map/config/");
  // The execution log sits beside the plan. A user who may not monitor it
  // simply gets the planned map they already had.
  const tracking = useApi(`/missions/${id}/tracking/`, { deps: [id] });
  const [trackFailure, setTrackFailure] = useState("");
  const [basemapId, setBasemapId] = useState(() => readStored(BASEMAP_STORAGE_KEY, ""));
  const [basemapFailure, setBasemapFailure] = useState("");
  const [calculating, setCalculating] = useState(false);
  const [routeError, setRouteError] = useState("");
  // Both panels live over the map and open from icons on the map itself, so the
  // page header stays a header instead of a row of toggles.
  const [panelOpen, setPanelOpen] = useState(false);
  // The map's own full-screen mode, shared with the other maps.
  const { fullScreen, frame, toggleFullScreen } = useMapFullScreen();
  const [infoOpen, setInfoOpen] = useState(false);
  const [logOpen, setLogOpen] = useState(false);

  const basemap = useMemo(
    () => resolveBasemap(config.data, basemapId, resolvedTheme),
    [config.data, basemapId, resolvedTheme]
  );

  function chooseBasemap(value) {
    setBasemapId(value);
    writeStored(BASEMAP_STORAGE_KEY, value);
    setBasemapFailure("");
  }

  // One trip can carry several vehicles; its route is drawn once.
  const trips = useMemo(() => {
    const byTrip = new Map();
    for (const row of data?.vehicles ?? []) {
      const existing = byTrip.get(row.trip_id);
      if (existing) existing.vehicles.push(row);
      else byTrip.set(row.trip_id, { ...row, vehicles: [row] });
    }
    return [...byTrip.values()];
  }, [data?.vehicles]);

  const missionPoint =
    data?.latitude != null && data?.longitude != null
      ? [data.latitude, data.longitude]
      : null;

  const runs = tracking.data?.runs ?? [];
  const delays = tracking.data?.delays ?? [];
  const graceMinutes = tracking.data?.delay_grace_minutes ?? 15;
  const liveRuns = runs.filter(
    (run) => run.last_latitude != null && run.last_longitude != null
  );
  const lateRuns = delays.filter((row) => row.status === "late");

  const boundsPoints = useMemo(() => {
    const points = [];
    if (missionPoint) points.push(missionPoint);
    for (const trip of trips) {
      points.push(...roadPositions(trip.route_geometry));
      points.push(...stopPositions(trip.itinerary));
    }
    for (const run of runs) {
      if (run.last_latitude != null && run.last_longitude != null) {
        points.push([run.last_latitude, run.last_longitude]);
      }
    }
    return points;
  }, [missionPoint, trips, runs]);

  async function startTracking() {
    setTrackFailure("");
    try {
      const next = await api.post(`/missions/${id}/tracking/start/`, {});
      tracking.setData(next);
    } catch (err) {
      setTrackFailure(err.message || "Movement tracking could not be started.");
    }
  }

  async function setLead(runId) {
    setTrackFailure("");
    try {
      const next = await api.post(`/missions/${id}/tracking/lead/`, { run: runId });
      tracking.setData(next);
    } catch (err) {
      setTrackFailure(err.message || "The convoy lead could not be changed.");
    }
  }

  async function completeMission(force = false) {
    const prompt = force
      ? "Close the mission and cancel any vehicle run still open?"
      : "Close the mission? Every vehicle run must already be completed.";
    if (!window.confirm(prompt)) return;
    setTrackFailure("");
    try {
      await api.post(`/missions/${id}/tracking/complete/`, { force });
      tracking.reload();
      reload();
    } catch (err) {
      setTrackFailure(err.message || "The mission could not be closed.");
    }
  }

  async function calculateRoutes() {
    setCalculating(true);
    setRouteError("");
    try {
      const failures = [];
      for (const trip of trips) {
        try {
          await api.post(`/trips/${trip.trip_id}/route/`, {});
        } catch (err) {
          failures.push(
            `${trip.trip_number || `Trip ${trip.trip_id}`}: ${err.message || "route failed"}`
          );
        }
      }
      reload();
      if (failures.length) setRouteError(failures.join(" "));
    } finally {
      setCalculating(false);
    }
  }

  if (loading && !data) return <Loading label="Loading mission map…" />;
  if (error) return <ErrorBanner message={error} onRetry={reload} />;
  if (!data) return null;

  const canCalculate = Boolean(permissions?.can_plan_missions);
  const canManageTracking = Boolean(tracking.data?.can_manage);
  const myRuns = runs.filter((run) => run.driver && String(run.driver) === String(user?.id));
  const uncalculated = trips.filter((trip) => trip.route_status !== "calculated");

  // Everything the mission-information panel shows, resolved once.
  const risk = data.risk_assessment;
  const involved = (data.personnel || []).filter((row) => row.is_active !== false);
  const vehicles = data.vehicles || [];
  const air = data.air_detail;

  return (
    <>
      <div className="page-head">
        <div>
          <p className="hint">
            <Link to={`/missions/${data.id}`}>← Mission record</Link>
          </p>
          <h1>
            {data.mission_number} · {data.title}
          </h1>
          <p>
            <Badge tone={data.category === "air" ? "info" : "muted"}>
              {data.category === "air" ? "Air Operations" : "Ground Operations"}
            </Badge>{" "}
            <StatusBadge status={data.status} />{" "}
            {data.unit_acronym && <span className="hint">{data.unit_acronym}</span>}
          </p>
        </div>
        <div className="btn-row">
          {uncalculated.length > 0 && canCalculate && (
            <Button onClick={calculateRoutes} disabled={calculating}>
              {calculating ? "Calculating road route…" : "Calculate road route"}
            </Button>
          )}
          {runs.length > 0 && (
            <Button variant="ghost" onClick={() => setLogOpen(true)}>
              Movement log
            </Button>
          )}
          {/* Only the person designated as a driver on this mission gets the
              driver screen. The Operations Center monitors from the map and the
              movement log, so it is never offered a driver's controls. Once the
              mission is complete the driver keeps only the movement log. */}
          {myRuns.length > 0 && data.status !== "completed" && (
            <Link className="btn btn-ghost btn-md" to={`/missions/${data.id}/track/driver`}>
              Driver view
            </Link>
          )}
        </div>
      </div>

      <ErrorBanner message={routeError} />
      {lateRuns.length > 0 && (
        <div className="alert alert-warning" role="status">
          <strong>
            {lateRuns.length === 1
              ? `${lateRuns[0].vehicle} is ${lateRuns[0].minutes} min behind plan`
              : `${lateRuns.length} vehicles are behind plan`}
          </strong>
          <ul className="hint" style={{ margin: "4px 0 0 16px" }}>
            {lateRuns.map((row) => (
              <li key={row.run}>
                <strong>{row.vehicle}</strong> · {row.minutes} min · {row.reason}
              </li>
            ))}
          </ul>
          <p className="hint" style={{ margin: "4px 0 0" }}>
            A vehicle counts as late past {graceMinutes} minutes. The monitoring roles have
            been notified.
          </p>
        </div>
      )}
      {basemapFailure && (
        <div className="alert alert-warning">
          <span>
            The selected basemap could not be reached. The planned route is unaffected — choose
            another map style.
          </span>
          <button
            type="button"
            className="link-button"
            onClick={() => {
              const fallback = availableBasemaps(config.data).find(
                (item) => item.id !== basemapFailure
              );
              if (fallback) chooseBasemap(fallback.id);
            }}
          >
            Use another basemap
          </button>
        </div>
      )}

      <div
        className={`map-layout map-layout-track${fullScreen ? " is-fullscreen" : ""}`}
        style={fullScreen ? { left: frame.left, top: frame.top } : undefined}
      >
        <div className="map-canvas map-canvas-track">
          <MapFrameControls
            config={config.data}
            value={basemap?.id}
            onChangeBasemap={chooseBasemap}
            fullScreen={fullScreen}
            onToggleFullScreen={toggleFullScreen}
          />
          <div className="map-fabs" role="group" aria-label="Map panels">
            <button
              type="button"
              className={`map-fab${panelOpen ? " is-active" : ""}`}
              onClick={() => setPanelOpen((open) => !open)}
              aria-pressed={panelOpen}
              aria-label="Route panel"
              title="Route panel"
            >
              <Icon name="route" size={19} />
            </button>
            <button
              type="button"
              className={`map-fab${infoOpen ? " is-active" : ""}`}
              onClick={() => setInfoOpen((open) => !open)}
              aria-pressed={infoOpen}
              aria-label="Mission details"
              title="Mission details"
            >
              <Icon name="info" size={19} />
            </button>
          </div>
          <MapContainer
            center={boundsPoints[0] || DEFAULT_CENTER}
            zoom={boundsPoints.length ? 11 : 6}
            scrollWheelZoom
          >
            <MapBasemapLayer
              basemap={basemap}
              onError={(value) => setBasemapFailure(value)}
            />
            <MapResize expanded={fullScreen} />
            <FitTrack points={boundsPoints} panelOpen={panelOpen} />

            {missionPoint && (
              <CircleMarker
                center={missionPoint}
                radius={9}
                pathOptions={{
                  color: "#f0b429",
                  fillColor: "#f0b429",
                  fillOpacity: 0.75,
                }}
              >
                <Popup>
                  <strong>{data.mission_number}</strong>
                  <br />
                  {data.title}
                  <br />
                  {data.location_name || "Objective location"}
                  <br />
                  {data.start_at ? formatDateTime(data.start_at) : ""}
                  {data.end_at ? ` – ${formatDateTime(data.end_at)}` : ""}
                </Popup>
              </CircleMarker>
            )}

            {liveRuns.map((run) => (
              <Marker
                key={`run-${run.id}`}
                position={[run.last_latitude, run.last_longitude]}
                icon={vehiclePin(run)}
                zIndexOffset={run.is_lead ? 600000 : 500000}
              >
                <Tooltip permanent direction="top" offset={[0, -14]}>
                  {run.registration_number}
                  {run.is_lead ? " · Lead" : ""}
                </Tooltip>
                <Popup>
                  <strong>{run.registration_number}</strong>
                  <br />
                  {run.driver_name || "No driver"}
                  <br />
                  {run.is_live ? "Live position" : "Last reported position"}
                  <br />
                  {formatDateTime(run.last_ping_at)}
                  {run.last_accuracy_m != null ? ` · ±${Math.round(run.last_accuracy_m)} m` : ""}
                </Popup>
              </Marker>
            ))}

            {trips.map((trip) => {
              const road = roadPositions(trip.route_geometry);
              return (
                <Fragment key={trip.trip_id}>
                  {road.length > 1 && (
                    <Polyline
                      positions={road}
                      pathOptions={{ color: "#39c5cf", weight: 5, opacity: 0.9 }}
                    >
                      <Popup>
                        <strong>{trip.trip_number || `Trip ${trip.trip_id}`}</strong>
                        <br />
                        {trip.vehicles
                          .map((vehicle) => vehicle.registration_number)
                          .filter(Boolean)
                          .join(", ") || "No vehicle recorded"}
                        <br />
                        Road route · {formatKilometres(trip.route_distance_m)} ·{" "}
                        {formatSpan(trip.route_duration_s)}
                        <br />
                        {trip.route_provider || "Routing engine"}
                      </Popup>
                    </Polyline>
                  )}

                  {(trip.itinerary || []).map((stop, index) => {
                    if (stop.latitude == null || stop.longitude == null) return null;
                    const last = index === (trip.itinerary || []).length - 1;
                    return (
                      <CircleMarker
                        key={`${trip.trip_id}-${stop.id}`}
                        center={[stop.latitude, stop.longitude]}
                        radius={6}
                        pathOptions={{
                          color: last ? "#3fb950" : "#4f9cf9",
                          fillColor: last ? "#3fb950" : "#4f9cf9",
                          fillOpacity: 0.9,
                        }}
                      >
                        <Tooltip direction="top" offset={[0, -6]}>
                          {stop.sequence}. {stop.location_name}
                        </Tooltip>
                        <Popup>
                          <strong>
                            {stop.sequence}. {stop.location_name}
                          </strong>
                          <br />
                          {stop.planned_departure
                            ? `Depart ${formatDateTime(stop.planned_departure)}`
                            : "No departure time"}
                          <br />
                          {stop.planned_arrival
                            ? `Arrive ${formatDateTime(stop.planned_arrival)}`
                            : "No arrival time"}
                          {stop.purpose ? (
                            <>
                              <br />
                              {stop.purpose}
                            </>
                          ) : null}
                        </Popup>
                      </CircleMarker>
                    );
                  })}
                </Fragment>
              );
            })}
          </MapContainer>
        </div>

        {infoOpen && (
          <aside className="card map-overlay map-track-info" aria-label="Mission details">
            <button
              type="button"
              className="map-overlay-close"
              onClick={() => setInfoOpen(false)}
              aria-label="Hide mission details"
              title="Hide mission details"
            >
              <Icon name="close" size={16} />
            </button>
            <h2>Mission details</h2>
            <p className="hint">
              The plan as it was recorded. Nothing here is a live position — the map shows the
              movement.
            </p>

            <dl className="details">
              <div className="detail">
                <dt>Mission</dt>
                <dd>
                  <strong>{data.mission_number}</strong> · {data.title}
                  <br />
                  <StatusBadge status={data.status} />{" "}
                  <Badge tone={data.category === "air" ? "info" : "muted"}>
                    {data.category === "air" ? "Air Operations" : "Ground Operations"}
                  </Badge>
                  {data.unit_acronym ? ` · ${data.unit_acronym}` : ""}
                </dd>
              </div>
              <div className="detail">
                <dt>Objective</dt>
                <dd>
                  {data.purpose || "No objective recorded."}
                  {data.location_name ? (
                    <>
                      <br />
                      <span className="hint">{data.location_name}</span>
                    </>
                  ) : null}
                  <br />
                  <span className="hint">
                    {formatDateTime(data.start_at)}
                    {data.end_at ? ` – ${formatDateTime(data.end_at)}` : ""}
                  </span>
                </dd>
              </div>
              <div className="detail">
                <dt>Risk rating</dt>
                <dd>
                  {risk ? (
                    <>
                      <strong>{risk.risk_category || "Not rated"}</strong>
                      {risk.total_percent != null ? ` · ${risk.total_percent}%` : ""}
                      {risk.authority_level ? (
                        <>
                          <br />
                          <span className="hint">Approval level: {risk.authority_level}</span>
                        </>
                      ) : null}
                    </>
                  ) : (
                    <span className="hint">{data.mrm?.label || "No risk assessment yet."}</span>
                  )}
                </dd>
              </div>
              <div className="detail">
                <dt>Itineraries</dt>
                <dd>
                  {trips.length === 0 && !air ? (
                    <span className="hint">No itinerary planned.</span>
                  ) : null}
                  {trips.map((trip) => (
                    <div key={trip.trip_id} style={{ marginBottom: 6 }}>
                      <strong>{trip.trip_number || `Trip ${trip.trip_id}`}</strong>
                      {(trip.itinerary || []).length === 0 ? (
                        <span className="hint"> · no stops</span>
                      ) : (
                        <ol className="hint" style={{ margin: "4px 0 0 16px" }}>
                          {(trip.itinerary || []).map((stop) => (
                            <li key={stop.id}>
                              {stop.location_name}
                              {stop.planned_departure
                                ? ` · ${formatDateTime(stop.planned_departure)}`
                                : ""}
                            </li>
                          ))}
                        </ol>
                      )}
                    </div>
                  ))}
                  {air ? (
                    <div>
                      <strong>
                        {air.departure_location || "Departure"} →{" "}
                        {air.destination_location || "Destination"}
                      </strong>
                      {air.route ? (
                        <>
                          <br />
                          <span className="hint">{air.route}</span>
                        </>
                      ) : null}
                      <br />
                      <span className="hint">
                        {formatDateTime(air.scheduled_departure)}
                        {air.scheduled_arrival
                          ? ` – ${formatDateTime(air.scheduled_arrival)}`
                          : ""}
                      </span>
                    </div>
                  ) : null}
                </dd>
              </div>
              <div className="detail">
                <dt>Vehicle details</dt>
                <dd>
                  {vehicles.length === 0 ? (
                    <span className="hint">No vehicle assigned.</span>
                  ) : (
                    <ul className="hint" style={{ margin: 0, paddingLeft: 16 }}>
                      {vehicles.map((vehicle) => (
                        <li key={`${vehicle.trip_id}-${vehicle.vehicle_id}`}>
                          <strong>{vehicle.registration_number}</strong>
                          {vehicle.make_model ? ` · ${vehicle.make_model}` : ""}
                          {vehicle.vehicle_type ? ` · ${vehicle.vehicle_type}` : ""}
                          {vehicle.capacity ? ` · ${vehicle.capacity} seats` : ""}
                          <br />
                          Driver: {vehicle.driver_name || "not designated"}
                        </li>
                      ))}
                    </ul>
                  )}
                </dd>
              </div>
              <div className="detail">
                <dt>Personnel involved</dt>
                <dd>
                  {involved.length === 0 ? (
                    <span className="hint">No personnel listed.</span>
                  ) : (
                    <ul className="hint" style={{ margin: 0, paddingLeft: 16 }}>
                      {involved.map((row) => (
                        <li key={row.id}>
                          {row.rank ? `${row.rank} ` : ""}
                          {row.user_name}
                          {row.display_role ? ` · ${row.display_role}` : ""}
                        </li>
                      ))}
                    </ul>
                  )}
                </dd>
              </div>
              <div className="detail">
                <dt>Authority</dt>
                <dd>
                  {risk?.decision_authority_label ? (
                    <>
                      {risk.decision_authority_label}
                      {risk.decision_authority_name ? (
                        <span className="hint"> · {risk.decision_authority_name}</span>
                      ) : null}
                    </>
                  ) : (
                    <span className="hint">Not yet designated.</span>
                  )}
                  <br />
                  <span className="hint">
                    Mission approval: {data.approval?.stage_label || data.status}
                  </span>
                </dd>
              </div>
            </dl>
          </aside>
        )}

        {panelOpen && (
        <aside className="card map-track-side map-overlay" aria-label="Route panel">
          <button
            type="button"
            className="map-overlay-close"
            onClick={() => setPanelOpen(false)}
            aria-label="Hide route panel"
            title="Hide route panel"
          >
            <Icon name="close" size={16} />
          </button>
          <h2>Planned movement</h2>
          <p className="hint">
            Routes follow the road network calculated by the routing engine; stops keep their
            planned order. Nothing here is a live GPS position.
          </p>

          <h3>Vehicles & routes</h3>
          {trips.length === 0 ? (
            <p className="hint">No vehicle is assigned to this mission.</p>
          ) : (
            <ul className="track-trip-list">
              {trips.map((trip) => {
                const road = roadPositions(trip.route_geometry);
                return (
                  <li key={trip.trip_id}>
                    <strong>{trip.trip_number || `Trip ${trip.trip_id}`}</strong>
                    <p className="hint">
                      {trip.vehicles
                        .map((vehicle) => vehicle.registration_number)
                        .filter(Boolean)
                        .join(", ") || "No vehicle recorded"}
                    </p>
                    {trip.route_status === "calculated" && road.length > 1 ? (
                      <p className="hint">
                        Road route · {formatKilometres(trip.route_distance_m)} ·{" "}
                        {formatSpan(trip.route_duration_s)}
                      </p>
                    ) : (
                      <p className="field-message">
                        {trip.route_error ||
                          "No road route has been calculated for this trip yet."}
                      </p>
                    )}
                  </li>
                );
              })}
            </ul>
          )}

          <h3>Live movement</h3>
          {tracking.error && !tracking.data ? (
            <p className="hint">
              You are not assigned to monitor this mission's movement.
            </p>
          ) : runs.length === 0 ? (
            <>
              <p className="hint">
                No vehicle run has been opened. Dispatching the mission starts tracking
                automatically.
              </p>
              {canManageTracking && data.status === "active" && (
                <Button size="sm" onClick={startTracking}>
                  Start movement tracking
                </Button>
              )}
            </>
          ) : (
            <ul className="track-trip-list">
              {runs.map((run) => (
                <li key={run.id}>
                  <strong>
                    {run.registration_number}
                    {run.is_lead ? " · Lead" : ""}
                  </strong>
                  <p className="hint">
                    {run.driver_name || "No driver"} · 
                    {run.is_live ? " live" : " no live fix"}
                  </p>
                  <p className="hint">
                    ETD {formatDateTime(run.etd)}
                    <br />
                    ATD {formatDateTime(run.atd)}
                    {run.atd_source_label ? ` (${run.atd_source_label})` : ""}
                    <br />
                    {run.stops?.filter((stop) => stop.status !== "pending").length || 0}
                    /{run.stops?.length || 0} stops logged · {run.status_label}
                  </p>
                  {(() => {
                    const delay = delays.find((row) => row.run === run.id);
                    if (!delay || delay.status === "closed") return null;
                    if (delay.status === "late") {
                      return (
                        <p className="field-message">
                          {delay.minutes} min behind plan
                          {delay.stop_sequence ? ` at stop ${delay.stop_sequence}` : ""}
                        </p>
                      );
                    }
                    if (delay.status === "awaiting_departure") {
                      return <p className="hint">Awaiting confirmed departure</p>;
                    }
                    return null;
                  })()}
                  {canManageTracking && run.status === "active" && (
                    <Button
                      size="sm"
                      variant={run.is_lead ? "ghost" : "primary"}
                      onClick={() => setLead(run.id)}
                      disabled={run.is_lead}
                    >
                      {run.is_lead ? "Convoy lead" : "Make convoy lead"}
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
          {trackFailure && <p className="field-message">{trackFailure}</p>}
          {canManageTracking && data.status === "active" && runs.length > 0 && (
            <div className="btn-row" style={{ marginTop: 10 }}>
              <Button size="sm" variant="ghost" onClick={() => completeMission(false)}>
                Complete mission
              </Button>
            </div>
          )}

          {data.start_at && (
            <dl className="details" style={{ marginTop: 16 }}>
              <div className="detail">
                <dt>Start</dt>
                <dd>{formatDateTime(data.start_at)}</dd>
              </div>
              <div className="detail">
                <dt>End</dt>
                <dd>{formatDateTime(data.end_at)}</dd>
              </div>
            </dl>
          )}

          {runs.length > 0 && (
            <>
              <h3>Movement log</h3>
              <p className="hint">
                Planned times beside the times a person recorded, with who recorded them.
              </p>
              <Button size="sm" variant="ghost" onClick={() => setLogOpen(true)}>
                Open movement log
              </Button>
            </>
          )}
        </aside>
        )}
      </div>

      {logOpen && (
        <Modal
          title={`Movement log · ${data.mission_number}`}
          onClose={() => setLogOpen(false)}
          wide
          footer={
            <div className="btn-row">
              <a
                className="btn btn-ghost btn-sm"
                href={`${API_BASE}/missions/${data.id}/tracking/log/?download=1`}
              >
                Download
              </a>
              <Button variant="ghost" size="sm" onClick={() => setLogOpen(false)}>
                Close
              </Button>
            </div>
          }
        >
          <PdfViewer
            path={`/missions/${data.id}/tracking/log/`}
            title={`Movement log ${data.mission_number}`}
          />
        </Modal>
      )}
    </>
  );
}
