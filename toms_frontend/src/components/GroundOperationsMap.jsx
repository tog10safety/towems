import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { divIcon } from "leaflet";
import { MapContainer, Marker, Polyline, Popup, Tooltip, useMap } from "react-leaflet";
import { useAuth } from "../auth/AuthContext.jsx";
import { useApi } from "../hooks/useApi.js";
import { useTheme } from "../theme/ThemeContext.jsx";
import AbortMissionDialog from "./AbortMissionDialog.jsx";
import { Badge, Button, ErrorBanner, Loading } from "./ui.jsx";
import MapBasemapLayer from "./map/MapBasemapLayer.jsx";
import MapFrameControls from "./map/MapFrameControls.jsx";
import MapResize from "./map/MapResize.jsx";
import useMapFullScreen from "../hooks/useMapFullScreen.js";
import {
  BASEMAP_STORAGE_KEY,
  readStored,
  resolveBasemap,
  writeStored,
} from "../map/config.js";
import { abortPhrase } from "../utils/abort.js";
import { formatDateTime } from "../utils/format.js";
import { headingCone, headingFromTrack } from "../map/vehicleHeading.js";

/**
 * Live ground movement (Ground Operations).
 *
 * One picture of every ground mission that is actually running, drawn from the
 * drivers' own pings: the planned route, the trail already driven, and each
 * vehicle's last reported position. Nothing is inferred - a vehicle that has
 * stopped reporting is shown as such, never placed at its planned stop.
 */

// The driver's phone flushes a position every few seconds, so the picture is
// pulled on the same rhythm while somebody is watching it. A hidden tab does
// no work at all and refreshes the moment it comes back.
const REFRESH_MS = 4000;
const DEFAULT_CENTER = [12.8797, 121.774];

function vehicleIcon(run) {
  // Behind plan outranks everything else the dot could say: the Operations
  // Center has to spot a late convoy without opening a popup.
  const classes = ["ground-vehicle-pin"];
  if (run.is_live) classes.push(run.is_lead ? "is-lead" : "is-live");
  else classes.push("is-stale");
  if (run.delay?.status === "late") classes.push("is-late");
  // The monitors see which way the vehicle is going, from the breadcrumbs the
  // driver's phone has already reported.
  const heading = headingFromTrack(run.trail);
  return divIcon({
    className: classes.join(" "),
    html: `${headingCone(heading)}<span></span>`,
    iconSize: [26, 26],
    iconAnchor: [13, 13],
    popupAnchor: [0, -14],
  });
}

function missionIcon(mission) {
  const reporting = (mission.runs || []).filter(
    (run) => run.latitude != null && run.longitude != null
  ).length;
  const classes = ["ground-mission-pin"];
  if (!reporting) classes.push("is-idle");
  if (mission.abort) classes.push("is-aborted");
  return divIcon({
    className: classes.join(" "),
    html: `<span>${reporting || "–"}</span>`,
    iconSize: [28, 28],
    iconAnchor: [14, 14],
    popupAnchor: [0, -15],
  });
}

/** The point an abort order sends the vehicles to. */
function abortTargetIcon() {
  return divIcon({
    className: "ground-abort-pin",
    html: "<span></span>",
    iconSize: [22, 22],
    iconAnchor: [11, 11],
    popupAnchor: [0, -12],
  });
}

/** Fit the picture once per set of missions, not on every four-second poll. */
function GroundFit({ points, signature }) {
  const map = useMap();

  useEffect(() => {
    if (!points.length) return;
    map.invalidateSize();
    if (points.length === 1) map.setView(points[0], 13);
    else map.fitBounds(points, { padding: [40, 40], maxZoom: 12 });
  }, [signature]); // eslint-disable-line react-hooks/exhaustive-deps

  return null;
}

function minutesAgo(seconds) {
  if (seconds == null) return "no fix yet";
  if (seconds < 90) return "just now";
  const minutes = Math.round(seconds / 60);
  return `${minutes} min ago`;
}

export default function GroundOperationsMap() {
  const { permissions } = useAuth();
  // The abort order is the Operations Center's own; the register offers it on
  // the same capability, and the server checks it again.
  const canPlan = Boolean(permissions?.can_plan_missions);
  const [abortMission, setAbortMission] = useState(null);
  const { resolvedTheme } = useTheme();
  const config = useApi("/map/config/");
  const { data, loading, error, reload } = useApi("/gis/ground-live/");
  const [basemapId, setBasemapId] = useState(() => readStored(BASEMAP_STORAGE_KEY, ""));
  const [basemapFailure, setBasemapFailure] = useState("");
  const [updatedAt, setUpdatedAt] = useState(null);
  const [now, setNow] = useState(() => Date.now());
  // Full screen fills the app's content frame, not the browser window: the
  // sidebar and top bar keep their buttons. Shared with the other maps.
  const { fullScreen, frame, toggleFullScreen } = useMapFullScreen();

  const basemap = useMemo(
    () => resolveBasemap(config.data, basemapId, resolvedTheme),
    [config.data, basemapId, resolvedTheme]
  );

  // The picture is only worth watching if it moves by itself.
  useEffect(() => {
    function refresh() {
      if (document.visibilityState === "visible") reload();
    }
    const timer = window.setInterval(refresh, REFRESH_MS);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [reload]);

  useEffect(() => {
    if (data) setUpdatedAt(Date.now());
  }, [data]);

  // A moving "seconds ago" is how an operator tells a live feed from a frozen
  // one at a glance.
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const secondsAgo = updatedAt ? Math.max(0, Math.round((now - updatedAt) / 1000)) : null;

  const missions = data?.missions ?? [];
  const runs = missions.flatMap((mission) =>
    (mission.runs || []).map((run) => ({ ...run, mission }))
  );
  const reporting = runs.filter((run) => run.latitude != null && run.longitude != null);
  const liveCount = reporting.filter((run) => run.is_live).length;
  const quietCount = reporting.length - liveCount;
  const lateCount = runs.filter((run) => run.delay?.status === "late").length;
  const abortedCount = missions.filter((mission) => mission.abort).length;

  const points = [];
  missions.forEach((mission) => {
    if (mission.latitude != null && mission.longitude != null) {
      points.push([mission.latitude, mission.longitude]);
    }
    if (mission.abort?.target?.latitude != null && mission.abort.target.longitude != null) {
      points.push([mission.abort.target.latitude, mission.abort.target.longitude]);
    }
  });
  reporting.forEach((run) => points.push([run.latitude, run.longitude]));
  runs.forEach((run) => (run.route || []).forEach((pair) => points.push(pair)));

  const signature = `${missions.map((mission) => mission.id).join(",")}|${
    points.length ? "placed" : "empty"
  }`;

  function chooseBasemap(id) {
    setBasemapId(id);
    writeStored(BASEMAP_STORAGE_KEY, id);
    setBasemapFailure("");
  }

  return (
    <section
      className={`card map-card${fullScreen ? " is-fullscreen" : ""}`}
      style={fullScreen ? { left: frame.left, top: frame.top } : undefined}
    >
      <header className="card-head">
        <div>
          <h2>Live ground movement</h2>
          <p className="hint">
            {missions.length} mission{missions.length === 1 ? "" : "s"} in progress ·{" "}
                            {liveCount} vehicle{liveCount === 1 ? "" : "s"} reporting · positions come from the
            drivers&apos; phones.
          </p>
        </div>
        <div className="card-actions">
          <Badge tone={secondsAgo != null && secondsAgo <= 15 ? "success" : "muted"}>
            {secondsAgo == null ? "Waiting for the first fix" : `Updated ${secondsAgo}s ago`}
          </Badge>
          <Badge tone={liveCount ? "success" : "muted"}>{liveCount} live</Badge>
          {quietCount > 0 && <Badge tone="warning">{quietCount} not reporting</Badge>}
          {lateCount > 0 && (
            <Badge tone="danger">
              {lateCount} behind plan
            </Badge>
          )}
          {abortedCount > 0 && (
            <Badge tone="danger">
              {abortedCount} aborted
            </Badge>
          )}
        </div>
      </header>

      <div className="card-body">
        <ErrorBanner message={error} onRetry={reload} />
        {basemapFailure && (
          <div className="alert alert-warning">
            The selected basemap could not be reached. The movement picture is unaffected — choose
            another map style.
          </div>
        )}

        <div className="map-layout">
          <div className="map-canvas map-canvas-dashboard">
            <MapFrameControls
              config={config.data}
              value={basemap?.id}
              onChangeBasemap={chooseBasemap}
              fullScreen={fullScreen}
              onToggleFullScreen={toggleFullScreen}
            />
            {loading && !data ? (
              <Loading label="Loading ground movement…" />
            ) : (
              <MapContainer
                center={points[0] || DEFAULT_CENTER}
                zoom={points.length ? 9 : 6}
                scrollWheelZoom
              >
                <MapBasemapLayer
                  basemap={basemap}
                  onError={() => setBasemapFailure(basemap?.id || "")}
                />
                <MapResize expanded={fullScreen} />
                <GroundFit points={points} signature={signature} />

                {runs.map((run) =>
                  (run.route || []).length > 1 ? (
                    <Polyline
                      key={`route-${run.id}`}
                      positions={run.route}
                      pathOptions={{
                        color: "#4f9cf9",
                        weight: 3,
                        opacity: 0.75,
                        dashArray: "6 5",
                      }}
                    />
                  ) : null
                )}

                {runs.map((run) =>
                  (run.trail || []).length > 1 ? (
                    <Polyline
                      key={`trail-${run.id}`}
                      positions={run.trail}
                      pathOptions={{ color: "#3fb950", weight: 4, opacity: 0.9 }}
                    />
                  ) : null
                )}

                {missions.map((mission) =>
                  mission.latitude != null && mission.longitude != null ? (
                    <Marker
                      key={`mission-${mission.id}`}
                      position={[mission.latitude, mission.longitude]}
                      icon={missionIcon(mission)}
                      zIndexOffset={300000}
                    >
                      <Popup>
                        <strong>{mission.mission_number}</strong>
                        <br />
                        {mission.title}
                        <br />
                        {mission.unit_acronym} · {formatDateTime(mission.start_at)}
                        <br />
                        {mission.location_name || ""}
                        <br />
                        {mission.abort ? (
                          <>
                            <strong>
                              ABORTED — {abortPhrase(mission.abort.destination)}
                              {mission.abort.target?.name
                                ? `: ${mission.abort.target.name}`
                                : ""}
                            </strong>
                            <br />
                            {mission.abort.reason}
                            <br />
                          </>
                        ) : null}
                        {(mission.runs || []).length} vehicle
                        {(mission.runs || []).length === 1 ? "" : "s"}
                        <br />
                        <Link to={`/missions/${mission.id}/track`}>Open the convoy map</Link>
                        {canPlan && !mission.abort && (
                          <div style={{ marginTop: 6 }}>
                            <Button
                              variant="danger"
                              size="sm"
                              onClick={() => setAbortMission(mission)}
                            >
                              Abort mission
                            </Button>
                          </div>
                        )}
                      </Popup>
                    </Marker>
                  ) : null
                )}

                {missions.map((mission) =>
                  mission.abort?.target?.latitude != null &&
                  mission.abort.target.longitude != null ? (
                    <Marker
                      key={`abort-${mission.id}`}
                      position={[
                        mission.abort.target.latitude,
                        mission.abort.target.longitude,
                      ]}
                      icon={abortTargetIcon()}
                      zIndexOffset={400000}
                    >
                      <Tooltip direction="top" offset={[0, -12]} permanent>
                        {mission.mission_number} ends here
                      </Tooltip>
                      <Popup>
                        <strong>
                          {abortPhrase(mission.abort.destination)}:{" "}
                          {mission.abort.target.name}
                        </strong>
                        <br />
                        {mission.abort.reason}
                        <br />
                        Ordered {formatDateTime(mission.abort.at)}
                      </Popup>
                    </Marker>
                  ) : null
                )}

                {reporting.map((run) => (
                  <Marker
                    key={`run-${run.id}`}
                    position={[run.latitude, run.longitude]}
                    icon={vehicleIcon(run)}
                    zIndexOffset={run.is_lead ? 600000 : 500000}
                  >
                    <Tooltip direction="top" offset={[0, -12]} permanent>
                      {run.registration_number}
                      {run.delay?.status === "late" ? " · late" : ""}
                    </Tooltip>
                    <Popup>
                      <strong>
                        {run.registration_number}
                        {run.is_lead ? " · Lead" : ""}
                      </strong>
                      <br />
                      {run.driver_name || "No driver"} · {run.mission.mission_number}
                      <br />
                      {run.is_live ? "Live" : "Not reporting"} ({minutesAgo(run.age_seconds)})
                      <br />
                      {run.recorded_stops}/{run.stop_count} stops recorded
                      {run.next_stop ? (
                        <>
                          <br />
                          Next: {run.next_stop.sequence}. {run.next_stop.location_name}
                        </>
                      ) : null}
                      {run.delay?.status === "late" ? (
                        <>
                          <br />
                          <strong>{run.delay.minutes} min behind plan</strong>
                          <br />
                          {run.delay.reason}
                        </>
                      ) : null}
                      <br />
                      <Link to={`/missions/${run.mission.id}/track/driver`}>Driver view</Link>
                    </Popup>
                  </Marker>
                ))}
              </MapContainer>
            )}
          </div>
        </div>

      </div>

      {abortMission && (
        <AbortMissionDialog
          mission={abortMission}
          onClose={() => setAbortMission(null)}
          onDone={() => {
            setAbortMission(null);
            reload();
          }}
        />
      )}
    </section>
  );
}
