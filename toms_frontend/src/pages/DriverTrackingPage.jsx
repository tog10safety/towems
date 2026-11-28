import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useParams, useSearchParams } from "react-router-dom";
import { divIcon } from "leaflet";
import { MapContainer, Marker, Polyline, Popup, Tooltip, useMap } from "react-leaflet";
import { API_BASE, api } from "../api/client.js";
import { useAuth } from "../auth/AuthContext.jsx";
import { Button, Card, ErrorBanner, Loading, StatusBadge } from "../components/ui.jsx";
import PdfViewer from "../components/PdfViewer.jsx";
import BasemapControl from "../components/map/BasemapControl.jsx";
import MapBasemapLayer from "../components/map/MapBasemapLayer.jsx";
import Icon from "../components/Icon.jsx";
import { useApi } from "../hooks/useApi.js";
import { useTheme } from "../theme/ThemeContext.jsx";
import {
  BASEMAP_STORAGE_KEY,
  readStored,
  resolveBasemap,
  writeStored,
} from "../map/config.js";
import { headingCone, nextHeading } from "../map/vehicleHeading.js";
import { abortPhrase, abortShort } from "../utils/abort.js";
import { formatDateTime } from "../utils/format.js";

/**
 * The driver's own view of a dispatched mission.
 *
 * Built for a phone mounted in a moving vehicle: the map is the page, the road
 * route and the next stop are always visible, and one thumb-sized button
 * records the current movement. A GPS stream that survives a dead spot sits
 * underneath: positions are queued, written to local storage after every fix,
 * and sent in batches once the network is back. An official arrival time is
 * always a deliberate tap, never inferred from a breadcrumb.
 */

// Short enough that the Operations Center sees the vehicle move, long enough
// that a phone on a weak signal is not sending a request every second.
const FLUSH_MS = 4000;
const BATCH = 60;
const MAX_QUEUE = 200;
const DEFAULT_CENTER = [12.8797, 121.774];
const WAKE_STORAGE_KEY = "toms.driver.keep-screen-awake";
const POLL_MS = 30000;

const GPS_LABELS = {
  idle: "GPS off",
  locating: "Finding GPS…",
  live: "Live",
  sending: "Sending…",
  offline: "Waiting for network",
  error: "Not sending",
};

function toNumber(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function queueKey(missionId) {
  return `toms.track.queue.${missionId}`;
}

function readStoredQueue(missionId) {
  try {
    const raw = window.localStorage.getItem(queueKey(missionId));
    if (!raw) return { run: null, rows: [] };
    const parsed = JSON.parse(raw);
    if (!parsed || !Array.isArray(parsed.rows)) return { run: null, rows: [] };
    // A breadcrumb older than a mission day is not worth replaying.
    const cutoff = Date.now() - 24 * 60 * 60 * 1000;
    const rows = parsed.rows
      .filter((row) => row && typeof row.latitude === "number")
      .filter((row) => {
        const at = Date.parse(row.recorded_at || "");
        return Number.isFinite(at) ? at >= cutoff : true;
      })
      .slice(-MAX_QUEUE);
    return { run: parsed.run ?? null, rows };
  } catch {
    return { run: null, rows: [] };
  }
}

function writeStoredQueue(missionId, runId, rows) {
  try {
    if (!rows.length) {
      window.localStorage.removeItem(queueKey(missionId));
      return;
    }
    window.localStorage.setItem(
      queueKey(missionId),
      JSON.stringify({ run: runId, rows: rows.slice(-MAX_QUEUE) })
    );
  } catch {
    // Private mode or a full quota: the stream keeps working in memory.
  }
}

/** OSRM geometry is GeoJSON `[longitude, latitude]`; Leaflet wants the reverse. */
function roadPositions(geometry) {
  const coordinates = geometry?.coordinates ?? [];
  return coordinates
    .filter((pair) => Array.isArray(pair) && pair.length >= 2)
    .map(([longitude, latitude]) => [latitude, longitude]);
}

function haversineKm(first, second) {
  if (!Array.isArray(first) || !Array.isArray(second)) return null;
  const toRad = (value) => (value * Math.PI) / 180;
  const [lat1, lon1] = first;
  const [lat2, lon2] = second;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 6371 * 2 * Math.asin(Math.min(1, Math.sqrt(a)));
}

/**
 * The itinerary as it was recorded: origin, every stop, destination, in order.
 * Stop names are shown exactly as the plan stores them - the label must never
 * quietly edit the mission a driver is being held to.
 */
function itineraryLabel(stops) {
  return (stops || [])
    .map((stop) => String(stop.location_name || "").trim())
    .filter(Boolean)
    .join(" - ");
}

/** Where an abort order ends this convoy: the station or the plan's last stop. */
function abortPin() {
  return divIcon({
    className: "driver-abort-pin",
    html: "<span></span>",
    iconSize: [26, 26],
    iconAnchor: [13, 13],
    popupAnchor: [0, -14],
  });
}

function stopPin(stop, isNext, isFinal, isSuperseded = false) {
  const tone = isSuperseded
    ? "superseded"
    : stop.status === "departed" || (isFinal && stop.status === "arrived")
      ? "done"
      : stop.status === "arrived"
        ? "arrived"
        : isNext
          ? "next"
          : "pending";
  return divIcon({
    className: `driver-pin is-${tone}`,
    html: `<span>${Number(stop.sequence) || ""}</span>`,
    iconSize: [30, 30],
    iconAnchor: [15, 15],
    popupAnchor: [0, -16],
  });
}

/**
 * The driver's own marker: a dot, with a cone that DriverHeading turns to the
 * way the vehicle is travelling, so the map reads like a phone map.
 *
 * The icon is built once. Rebuilding it per bearing would replace the element
 * on every fix and restart the halo animation.
 */
const SELF_ICON = divIcon({
  className: "driver-self",
  html: `<span class="driver-self-halo"></span>${headingCone(null)}<span class="driver-self-dot"></span>`,
  iconSize: [46, 46],
  iconAnchor: [23, 23],
});

/** Turn the marker's cone in place, and hide it until a bearing is known. */
function DriverHeading({ heading }) {
  const map = useMap();

  useEffect(() => {
    const cone = map
      .getContainer()
      .querySelector(".driver-self .vehicle-heading-cone");
    if (!cone) return;
    // The shared cone shows itself on this class. Turning it in place keeps the
    // marker element - and its halo animation - alive between fixes.
    const known = Number.isFinite(heading);
    cone.classList.toggle("is-known", known);
    if (known) cone.style.transform = `rotate(${Math.round(heading) % 360}deg)`;
  }, [map, heading]);

  return null;
}

/**
 * Position the map on the plan once, then leave the driver's own view alone.
 *
 * The fit is keyed on the plan, never on the live position: sending a location
 * must not take the map back from a driver who has zoomed in. A pan or a zoom
 * by the driver also ends automatic fitting, the way a phone map behaves.
 * Leaflet is still told when its container changes size - the sheet opening,
 * the map being maximised, the phone turning - which it cannot notice itself.
 */
function DriverMapFit({ points, planKey, layoutKey, abortKey, vehicleKey }) {
  const map = useMap();
  const fittedPlan = useRef(null);
  const fittedOnce = useRef(false);
  const movedByDriver = useRef(false);
  const lastAbort = useRef("");
  const lastVehicle = useRef(null);
  const latestPoints = useRef(points);
  latestPoints.current = points;

  useEffect(() => {
    const node = map.getContainer();
    const claim = () => {
      movedByDriver.current = true;
    };
    // Only a gesture starts these; a programmatic fit does not.
    node.addEventListener("pointerdown", claim);
    node.addEventListener("wheel", claim, { passive: true });
    node.addEventListener("touchstart", claim, { passive: true });
    return () => {
      node.removeEventListener("pointerdown", claim);
      node.removeEventListener("wheel", claim);
      node.removeEventListener("touchstart", claim);
    };
  }, [map]);

  // A new container size keeps the view the driver is on.
  useEffect(() => {
    map.invalidateSize({ pan: false });
  }, [map, layoutKey]);

  useEffect(() => {
    if (typeof ResizeObserver === "undefined") return undefined;
    const node = map.getContainer();
    const observer = new ResizeObserver(() => map.invalidateSize({ pan: false }));
    observer.observe(node);
    return () => observer.disconnect();
  }, [map]);

  useEffect(() => {
    const plan = latestPoints.current;
    if (!plan.length) return;
    // Two things may still take the map after the driver has moved it: an
    // abort order, and switching to another vehicle. Both change what the map
    // is about - where the convoy is going, or which plan is being shown - so
    // the driver has to be shown the new one. A new position, a new status or
    // a refresh still may not.
    const switchedVehicle =
      lastVehicle.current !== null && lastVehicle.current !== vehicleKey;
    lastVehicle.current = vehicleKey;
    const forced =
      switchedVehicle || (Boolean(abortKey) && lastAbort.current !== abortKey);
    lastAbort.current = abortKey || "";
    if (forced) {
      // The pan belonged to the plan that was on screen a moment ago.
      movedByDriver.current = false;
    } else {
      if (fittedPlan.current === planKey) return;
      // Once the driver has moved the map, a new plan may not move it back.
      if (fittedOnce.current && movedByDriver.current) return;
    }
    fittedPlan.current = planKey;
    fittedOnce.current = true;
    if (plan.length === 1) map.setView(plan[0], 15);
    else map.fitBounds(plan, { padding: [44, 44], maxZoom: 15 });
  }, [map, planKey, abortKey, vehicleKey]);

  return null;
}

function DriverRecenter({ point, token }) {
  const map = useMap();

  useEffect(() => {
    if (!point) return;
    map.flyTo(point, Math.max(map.getZoom(), 15), { duration: 0.6 });
  }, [token]); // eslint-disable-line react-hooks/exhaustive-deps

  return null;
}

/**
 * Leaflet parks its attribution notice in a corner container at z-index 1000,
 * where it floats over whatever the page draws inside the map. Handing the
 * control to a slot in the page's own layout keeps the copyright notice
 * visible without it ever sitting on top of the trip details, at any width.
 */
function DriverAttribution({ hostRef }) {
  const map = useMap();

  useEffect(() => {
    const notice = map.attributionControl?.getContainer();
    const host = hostRef.current;
    if (!notice || !host) return undefined;
    host.appendChild(notice);
  }, [map, hostRef]);

  return null;
}

function ExpandGlyph({ expanded }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="20"
      height="20"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {expanded ? (
        <>
          <path d="M9 4v5H4" />
          <path d="M15 20v-5h5" />
          <path d="M20 9h-5V4" />
          <path d="M4 15h5v5" />
        </>
      ) : (
        <>
          <path d="M4 9V4h5" />
          <path d="M20 15v5h-5" />
          <path d="M15 4h5v5" />
          <path d="M9 20H4v-5" />
        </>
      )}
    </svg>
  );
}

function AlertGlyph() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="20"
      height="20"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M12 3 2.5 20h19L12 3Z" />
      <path d="M12 9v5" />
      <path d="M12 17.5v.01" />
    </svg>
  );
}

function LocateGlyph() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="20"
      height="20"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v3M12 19v3M2 12h3M19 12h3" />
    </svg>
  );
}

/** The itinerary as recorded times, never as inferred ones. */
function StopTimeline({ stops, supersededIds }) {
  if (!stops.length) {
    return <p className="hint">No stop has been planned for this vehicle.</p>;
  }
  return (
    <ol className="stop-timeline driver-timeline">
      {stops.map((stop, index) => {
        const isFinal = index === stops.length - 1;
        const skipped = Boolean(supersededIds?.has(stop.id));
        const tone = skipped
          ? "stop-superseded"
          : stop.status === "departed"
            ? "stop-departed"
            : stop.status === "arrived"
              ? isFinal
                ? "stop-arrived stop-completed"
                : "stop-arrived"
              : "";
        const when = skipped
          ? "Skipped by the abort order"
          : stop.status === "departed"
            ? `Departed ${formatDateTime(stop.departed_at)}`
            : stop.status === "arrived"
              ? `Arrived ${formatDateTime(stop.arrived_at)}`
              : `Planned ${formatDateTime(
                  stop.planned_arrival || stop.planned_departure
                )}`;
        // The position recorded with the tap, so a monitor reads where the
        // driver actually was rather than only when they said it happened.
        const position = skipped
          ? null
          : stop.status === "departed"
            ? stop.departed_position
            : stop.arrived_position;
        const state = skipped
          ? "Skipped"
          : stop.status === "departed"
            ? "Done"
            : stop.status === "arrived"
              ? isFinal
                ? "Complete"
                : "Here"
              : "Pending";
        return (
          <li key={stop.id} className={`stop-row ${tone}`}>
            <div className="stop-copy">
              <strong>
                {stop.sequence}. {stop.location_name}
              </strong>
              <p className="hint">
                {when}
                {position && (
                  <>
                    {" · tapped at "}
                    <span
                      className="stop-tap-position"
                      title={`${position.latitude.toFixed(5)}, ${position.longitude.toFixed(5)}`}
                    >
                      {position.mgrs ||
                        `${position.latitude.toFixed(5)}, ${position.longitude.toFixed(5)}`}
                    </span>
                  </>
                )}
              </p>
            </div>
            <span className={`driver-stop-state is-${skipped ? "skipped" : stop.status}`}>
              {state}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

/**
 * A closed mission has no driving controls left. Keep the driver's record
 * available, and nothing else: the movement log is the whole screen here.
 */
function CompletedDriverView({ mission }) {
  return (
    <div className="driver-app driver-complete">
      <header className="driver-bar">
        <div className="driver-bar-copy">
          <strong>{mission.mission_number}</strong>
          <span>
            {mission.title} · <StatusBadge status={mission.status} />
          </span>
        </div>
      </header>

      <Card title="Movement log" className="driver-log-card">
        <p className="hint">
          This mission is complete. The driver view is closed; the movement log
          remains available to you.
        </p>
        <PdfViewer
          path={`/missions/${mission.id}/tracking/log/`}
          title={`Movement log ${mission.mission_number}`}
        />
      </Card>
    </div>
  );
}

export default function DriverTrackingPage() {
  const { id } = useParams();
  const { user } = useAuth();
  const { resolvedTheme } = useTheme();
  const [searchParams] = useSearchParams();
  const location = useLocation();
  const wantsFullScreen = searchParams.get("fullscreen") === "1";
  const { data, loading, error, reload, setData } = useApi(`/missions/${id}/tracking/`, {
    deps: [id],
  });
  const config = useApi("/map/config/");

  const [message, setMessage] = useState("");
  const [failure, setFailure] = useState("");
  const [busy, setBusy] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [activeRunId, setActiveRunId] = useState(null);
  const [gpsState, setGpsState] = useState("idle");
  const [gpsError, setGpsError] = useState("");
  const [lastFix, setLastFix] = useState(null);
  const [queued, setQueued] = useState(0);
  const [online, setOnline] = useState(() =>
    typeof navigator === "undefined" ? true : navigator.onLine !== false
  );
  // Remembered on this phone: once a driver turns the keep-awake setting on,
  // the next shift does not have to find it again.
  const [keepAwake, setKeepAwake] = useState(() =>
    readStored(WAKE_STORAGE_KEY, true) !== false
  );
  const [screenAwake, setScreenAwake] = useState(false);
  const [expanded, setExpanded] = useState(wantsFullScreen);
  const [sheetOpen, setSheetOpen] = useState(true);
  const [focusToken, setFocusToken] = useState(0);
  const [basemapId, setBasemapId] = useState(() =>
    readStored(BASEMAP_STORAGE_KEY, "")
  );
  const [basemapFailure, setBasemapFailure] = useState("");
  const [sosOpen, setSosOpen] = useState(false);
  const [sosNote, setSosNote] = useState("");
  const [sosBusy, setSosBusy] = useState(false);
  const [sosMessage, setSosMessage] = useState("");
  const [sosError, setSosError] = useState("");

  const queue = useRef([]);
  const queueRun = useRef(null);
  const watchId = useRef(null);
  const flushTimer = useRef(null);
  const activeRunRef = useRef(null);
  const wakeLock = useRef(null);
  const attributeHost = useRef(null);

  const runs = data?.runs ?? [];
  const delays = data?.delays ?? [];
  const missionCompleted = data?.mission?.status === "completed";
  const canRecordOthers = Boolean(data?.can_manage || data?.is_lead);
  const myRuns = useMemo(
    () => runs.filter((run) => run.driver && String(run.driver) === String(user?.id)),
    [runs, user?.id]
  );
  const actionable = useMemo(
    () =>
      runs.filter(
        (run) =>
          run.status === "active" &&
          (canRecordOthers || String(run.driver) === String(user?.id))
      ),
    [runs, canRecordOthers, user?.id]
  );
  const activeRun = useMemo(
    () => runs.find((run) => run.id === activeRunId) || null,
    [runs, activeRunId]
  );

  const basemap = useMemo(
    () => resolveBasemap(config.data, basemapId, resolvedTheme),
    [config.data, basemapId, resolvedTheme]
  );

  const activeStops = activeRun?.stops ?? [];
  const nextIndex = activeStops.findIndex((stop) => stop.status === "pending");
  const nextStop = nextIndex >= 0 ? activeStops[nextIndex] : null;
  const previousStop = nextIndex > 0 ? activeStops[nextIndex - 1] : null;
  const recordedStops = activeStops.filter((stop) => stop.status !== "pending").length;
  const selfPoint = lastFix
    ? [lastFix.latitude, lastFix.longitude]
    : activeRun?.last_latitude != null && activeRun?.last_longitude != null
      ? [activeRun.last_latitude, activeRun.last_longitude]
      : null;
  const route = roadPositions(activeRun?.route?.geometry);
  const trail = (activeRun?.recent_pings ?? [])
    .filter((ping) => ping.latitude != null && ping.longitude != null)
    .map((ping) => [ping.latitude, ping.longitude]);
  const distanceToNext =
    selfPoint && nextStop && nextStop.latitude != null
      ? haversineKm(selfPoint, [nextStop.latitude, nextStop.longitude])
      : null;

  // What the map fits: the plan, never the live position. Including the
  // vehicle's own fix here made every location sent re-fit the map, which is
  // what pulled a driver's zoom back out.
  const planPoints = [...route];
  activeStops.forEach((stop) => {
    if (stop.latitude != null && stop.longitude != null) {
      planPoints.push([stop.latitude, stop.longitude]);
    }
  });
  if (
    data?.abort?.target?.latitude != null &&
    data.abort.target.longitude != null
  ) {
    planPoints.push([data.abort.target.latitude, data.abort.target.longitude]);
  }
  // A different plan - a stop added, an abort point pinned - is the only thing
  // that may reposition the map; a new status or a new fix may not.
  const planKey = [
    route.length > 1 ? "routed" : "direct",
    activeStops
      .map((stop) => `${stop.id}:${stop.latitude},${stop.longitude}`)
      .join("|"),
    data?.abort?.target
      ? `${data.abort.target.latitude},${data.abort.target.longitude}`
      : "",
  ].join("~");

  // Restore any breadcrumbs that were never sent before the page was closed.
  useEffect(() => {
    const stored = readStoredQueue(id);
    if (!stored.rows.length) return;
    queue.current = stored.rows;
    queueRun.current = stored.run;
    setQueued(stored.rows.length);
  }, [id]);

  useEffect(() => {
    if (activeRunId != null) return;
    const first = myRuns[0] || (canRecordOthers ? actionable[0] : null);
    if (first) setActiveRunId(first.id);
  }, [myRuns, actionable, canRecordOthers, activeRunId]);

  useEffect(() => {
    activeRunRef.current = activeRunId;
  }, [activeRunId]);

  // A lead notification opens this page with fullscreen=1. Re-apply it on a
  // fresh navigation to the same route, so a second lead alert still takes the
  // driver straight to the maximised map.
  useEffect(() => {
    if (wantsFullScreen) setExpanded(true);
  }, [wantsFullScreen, location.key]);

  // A closed mission has no GPS stream or emergency control left. Drop any
  // unsent breadcrumbs instead of retrying them against a closed run.
  useEffect(() => {
    if (!missionCompleted) return;
    setStreaming(false);
    setSosOpen(false);
    queue.current = [];
    queueRun.current = null;
    setQueued(0);
    writeStoredQueue(id, null, []);
  }, [missionCompleted, id]);

  // Another operator may open or close a run, or record a milestone, while this
  // phone has the page open, so the driver's picture is refreshed too.
  useEffect(() => {
    const timer = window.setInterval(() => reload(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [reload]);

  useEffect(() => {
    function goOnline() {
      setOnline(true);
    }
    function goOffline() {
      setOnline(false);
    }
    window.addEventListener("online", goOnline);
    window.addEventListener("offline", goOffline);
    return () => {
      window.removeEventListener("online", goOnline);
      window.removeEventListener("offline", goOffline);
    };
  }, []);

  useEffect(() => {
    writeStored(WAKE_STORAGE_KEY, keepAwake);
  }, [keepAwake]);

  function chooseBasemap(value) {
    setBasemapId(value);
    writeStored(BASEMAP_STORAGE_KEY, value);
    setBasemapFailure("");
  }

  function persist() {
    writeStoredQueue(id, queueRun.current, queue.current);
  }

  async function flush() {
    const runId = activeRunRef.current;
    if (!runId || queue.current.length === 0) return;
    // A queued breadcrumb may belong to a vehicle the driver has since switched
    // away from; only send the batch that matches the current run.
    if (queueRun.current != null && String(queueRun.current) !== String(runId)) {
      queue.current = [];
      queueRun.current = runId;
      persist();
      setQueued(0);
      return;
    }
    if (typeof navigator !== "undefined" && navigator.onLine === false) {
      setGpsState("offline");
      return;
    }
    const batch = queue.current.slice(0, BATCH);
    try {
      await api.post(`/missions/${id}/tracking/ping/`, { run: runId, pings: batch });
      queue.current = queue.current.slice(batch.length);
      persist();
      setQueued(queue.current.length);
      setGpsState("sending");
      setGpsError("");
    } catch (err) {
      // Keep the breadcrumbs; a tunnel or a dead spot is not data loss.
      setGpsError(err.message || "Position could not be sent.");
      setGpsState("error");
    }
  }

  // Recovered breadcrumbs are sent as soon as there is a network again.
  useEffect(() => {
    if (!online || !activeRunId || queue.current.length === 0) return;
    flush();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [online, activeRunId]);

  useEffect(() => {
    if (!streaming) return undefined;
    if (!navigator.geolocation) {
      setGpsError("This browser has no location support.");
      setStreaming(false);
      return undefined;
    }
    if (!window.isSecureContext) {
      setGpsError(
        "Location is only allowed on HTTPS or localhost. Open TOWEMS from its HTTPS address on this phone."
      );
      setStreaming(false);
      return undefined;
    }

    setGpsError("");
    setGpsState("locating");
    queueRun.current = activeRunRef.current ?? queueRun.current;

    watchId.current = navigator.geolocation.watchPosition(
      (position) => {
        const { latitude, longitude, accuracy, speed, heading } = position.coords;
        const row = {
          latitude,
          longitude,
          accuracy_m: accuracy ?? null,
          speed_mps: toNumber(speed),
          heading_deg: toNumber(heading),
          recorded_at: new Date(position.timestamp).toISOString(),
        };
        queue.current = [...queue.current, row].slice(-MAX_QUEUE);
        persist();
        setQueued(queue.current.length);
        setLastFix((previous) => ({
          latitude,
          longitude,
          accuracy,
          heading: nextHeading(previous, latitude, longitude, heading),
        }));
        setGpsState("live");
        if (queue.current.length >= 4) flush();
      },
      (err) => {
        setGpsError(
          err.code === err.PERMISSION_DENIED
            ? "Location permission was refused. Allow it in the browser settings and try again."
            : err.message || "The phone could not get a GPS fix."
        );
        setGpsState("error");
      },
      { enableHighAccuracy: true, maximumAge: 5000, timeout: 20000 }
    );
    flushTimer.current = setInterval(flush, FLUSH_MS);

    return () => {
      if (watchId.current != null) navigator.geolocation.clearWatch(watchId.current);
      if (flushTimer.current) clearInterval(flushTimer.current);
      watchId.current = null;
      flushTimer.current = null;
      flush();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streaming]);

  // Keep the phone's screen on while the position is being shared. Browsers
  // drop the lock whenever the tab is hidden, so it is re-acquired on return.
  useEffect(() => {
    const supported =
      typeof navigator !== "undefined" && "wakeLock" in navigator;
    if (!streaming || !keepAwake || !supported) {
      setScreenAwake(false);
      if (wakeLock.current) {
        wakeLock.current.release?.().catch(() => {});
        wakeLock.current = null;
      }
      return undefined;
    }
    let cancelled = false;

    async function acquire() {
      try {
        const lock = await navigator.wakeLock.request("screen");
        if (cancelled) {
          lock.release?.().catch(() => {});
          return;
        }
        wakeLock.current = lock;
        setScreenAwake(true);
      } catch {
        setScreenAwake(false);
      }
    }

    function onVisible() {
      if (document.visibilityState === "visible") acquire();
    }

    acquire();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", onVisible);
      if (wakeLock.current) {
        wakeLock.current.release?.().catch(() => {});
        wakeLock.current = null;
      }
      setScreenAwake(false);
    };
  }, [streaming, keepAwake]);

  async function record(run, action, stop, { force = false } = {}) {
    setBusy(`${run.id}:${action}:${stop?.id ?? ""}`);
    setFailure("");
    setMessage("");
    try {
      const payload = { run: run.id, action };
      if (stop) payload.stop = stop.id;
      if (force) payload.force = true;
      const next = await api.post(`/missions/${id}/tracking/event/`, payload);
      setData(next);
      setMessage(
        action === "depart"
          ? `${run.registration_number}: departure confirmed.`
          : action === "complete"
            ? `${run.registration_number}: run completed.`
            : `${stop.location_name}: ${action === "arrive_stop" ? "arrival" : "departure"} recorded.`
      );
    } catch (err) {
      // An arrival is checked against this phone's last fix. When the server
      // refuses it, the driver may record it anyway - the distance goes into the
      // movement log and the Operations Center is told.
      const override = err.payload?.errors?.override;
      if (action === "arrive_stop" && override && !force) {
        if (
          window.confirm(
            `${err.message}\n\nRecord the arrival anyway? It is logged as unverified and the Operations Center is told.`
          )
        ) {
          await record(run, action, stop, { force: true });
          return;
        }
      }
      setFailure(err.message || "That movement could not be recorded.");
    } finally {
      setBusy("");
    }
  }

  async function sendEmergency() {
    const run = activeRun || myRuns[0] || (canRecordOthers ? actionable[0] : null);
    if (!run) {
      setSosError("No open vehicle run is available to report the emergency against.");
      return;
    }
    setSosBusy(true);
    setSosError("");
    setSosMessage("");
    try {
      const result = await api.post(`/missions/${id}/tracking/emergency/`, {
        run: run.id,
        note: sosNote.trim(),
      });
      setSosNote("");
      setSosMessage(
        result?.notified
          ? "Emergency alert sent to the Operations Center."
          : "This emergency was already reported; the Operations Center has it."
      );
      reload();
    } catch (err) {
      setSosError(err.message || "The emergency could not be sent.");
    } finally {
      setSosBusy(false);
    }
  }

  if (loading && !data) return <Loading label="Loading your movement task…" />;
  if (error) return <ErrorBanner message={error} onRetry={reload} />;
  if (!data) return null;

  // This screen belongs to the person designated as a driver on the mission.
  // The Operations Center follows movement from the mission map and the
  // movement log, so it is not given a driver's controls.
  if (!data.is_driver) {
    return (
      <div className="card" style={{ padding: 16 }}>
        <h2>Driver view</h2>
        <p className="hint">
          This screen is for the person designated as the driver on this mission. Your account
          is not on the driver roster, so the movement controls are not available here.
        </p>
        <p className="hint">
          <Link to={`/missions/${id}/track`}>← Back to the mission map</Link>
        </p>
      </div>
    );
  }

  const mission = data.mission;
  if (mission.status === "completed") {
    return <CompletedDriverView mission={mission} />;
  }
  const abort = data.abort;
  // After an abort the pinned point is the only stop that still matters. This
  // run's own copy of it is matched by the name and coordinates the order set,
  // because each vehicle keeps its own itinerary.
  const abortStop = abort
    ? activeStops.find((stop) => {
        if (stop.location_name !== abort.target?.name) return false;
        if (abort.target?.latitude == null || stop.latitude == null) return true;
        return (
          Math.abs(stop.latitude - abort.target.latitude) < 1e-6 &&
          Math.abs(stop.longitude - abort.target.longitude) < 1e-6
        );
      }) || null
    : null;
  // The distance that matters is the one to wherever this screen now points.
  const distanceToAbort =
    selfPoint && abort?.target?.latitude != null
      ? haversineKm(selfPoint, [abort.target.latitude, abort.target.longitude])
      : null;
  const distanceToFocus = abort ? distanceToAbort : distanceToNext;
  // Everything the order made irrelevant: still pending, and not the point the
  // convoy is now heading for.
  const supersededIds = abort
    ? new Set(
        activeStops
          .filter((stop) => stop.status === "pending" && stop.id !== abortStop?.id)
          .map((stop) => stop.id)
      )
    : new Set();
  // The driver reads their own itinerary first; the destination address is the
  // Operations Center's view of the mission, not a heading for the wheel.
  const itineraryRun = activeRun || myRuns[0] || actionable[0] || runs[0] || null;
  const itinerary = itineraryLabel(itineraryRun?.stops);
  const activeDelay = delays.find((row) => row.run === activeRun?.id) || null;
  const mine = Boolean(activeRun && String(activeRun.driver) === String(user?.id));
  const editable = Boolean(
    activeRun && activeRun.status === "active" && (mine || data.can_manage || data.is_lead)
  );
  // The Operations Center may have written a dispatch ATD; only the driver's
  // own confirmation counts as the wheel-roll, so it is still asked for.
  const awaitingDeparture = Boolean(activeRun && activeRun.atd_source !== "driver");
  // Arriving and departing are two separate taps on the same stop, and each one
  // unlocks the next leg: the run is on its way to the next pending stop only
  // after the stop just reached has been departed. Reading the *pending* stop
  // for the departure would ask a stop that has not been reached to be arrived
  // first, so the departure button could never appear after an arrival.
  const canArriveNext = Boolean(
    editable && nextStop && previousStop?.status === "departed"
  );
  const canDepartNext = Boolean(
    editable && nextStop && previousStop?.status === "arrived"
  );
  const allStopsRecorded =
    activeStops.length > 0 && activeStops.every((stop) => stop.status !== "pending");
  const wakeSupported =
    typeof navigator !== "undefined" && "wakeLock" in navigator;

  let primaryAction = null;
  if (editable && activeRun) {
    if (abort) {
      // The abort replaces what was left of the plan: arrive at the pinned
      // point, then close the run. A return to station is closed by the return
      // itself, so it goes straight to completion.
      primaryAction = awaitingDeparture
        ? {
            label: "Confirm departure",
            onClick: () => record(activeRun, "depart"),
          }
        : abortStop && abortStop.status === "pending"
          ? {
              label: `Arrived at ${abortStop.location_name}`,
              onClick: () => record(activeRun, "arrive_stop", abortStop),
            }
          : {
              label: "Complete this run",
              onClick: () => record(activeRun, "complete"),
            };
    } else if (awaitingDeparture) {
      primaryAction = {
        label: "Confirm departure",
        onClick: () => record(activeRun, "depart"),
      };
    } else if (canArriveNext) {
      primaryAction = {
        label: `Arrived at stop ${nextStop.sequence}`,
        onClick: () => record(activeRun, "arrive_stop", nextStop),
      };
    } else if (canDepartNext) {
      primaryAction = {
        label: `Depart stop ${previousStop.sequence}`,
        onClick: () => record(activeRun, "depart_stop", previousStop),
      };
    } else if (allStopsRecorded) {
      primaryAction = {
        label: "Complete this run",
        onClick: () => record(activeRun, "complete"),
      };
    }
  }

  return (
    <div className={`driver-app${expanded ? " is-expanded" : ""}`}>
      <header className="driver-bar">
        <div className="driver-bar-copy">
          <strong>{mission.mission_number}</strong>
          <span>
            {itinerary || mission.title} ·{" "}
            <StatusBadge status={mission.status} />
          </span>
        </div>
        <div className="driver-bar-actions">
          <button
            type="button"
            className="driver-icon-button"
            onClick={reload}
            aria-label="Refresh"
            title="Refresh"
          >
            <Icon name="refresh" size={18} />
          </button>
          <a
            className="driver-icon-button"
            href={`${API_BASE}/missions/${mission.id}/tracking/log/`}
            target="_blank"
            rel="noreferrer"
            aria-label="Open the movement log"
            title="Movement log"
          >
            <Icon name="download" size={18} />
          </a>
        </div>
      </header>

      <ErrorBanner message={failure} onRetry={reload} />
      {message && (
        <div className="alert alert-success" role="status">
          {message}
        </div>
      )}
      {!online && (
        <div className="alert alert-warning" role="status">
          No network. Positions are being kept on this phone and will be sent when
          the signal returns.
        </div>
      )}

      {abort && (
        <div className="alert alert-danger" role="alert">
          <strong>
            MISSION ABORTED — {abortPhrase(abort.destination)}
            {abort.target?.name ? `: ${abort.target.name}` : ""}
          </strong>
          <div>{abort.reason}</div>
          <div className="hint">
            {abort.destination === "station"
              ? "Make your way back to the station, then complete the run when you arrive."
              : "Make your way to this point, record your arrival there, then complete the run. The stops the order skipped are no longer part of the trip."}
          </div>
        </div>
      )}

      {runs.length === 0 && (
        <div className="card">
          <div className="card-body">
            <p>
              The Operations Center has not opened movement tracking for this mission
              yet. Ask them to dispatch the mission, or to press “Start movement
              tracking”.
            </p>
          </div>
        </div>
      )}

      {myRuns.length === 0 && runs.length > 0 && !canRecordOthers && (
        <div className="card">
          <div className="card-body">
            <p>
              You are not the designated driver of a vehicle on this mission. The
              board is read-only for you.
            </p>
          </div>
        </div>
      )}

      {activeRun ? (
        <div className="driver-stage">
          <div className="driver-map">
            <MapContainer
              center={planPoints[0] || DEFAULT_CENTER}
              zoom={planPoints.length ? 13 : 6}
              dragging
              zoomControl={false}
              scrollWheelZoom
            >
              <DriverAttribution hostRef={attributeHost} />
              <MapBasemapLayer
                basemap={basemap}
                onError={() => setBasemapFailure(basemap?.id || "")}
              />
              <DriverMapFit
                points={planPoints}
                planKey={planKey}
                layoutKey={`${expanded}:${sheetOpen}`}
                vehicleKey={activeRunId ?? ""}
                abortKey={
                  data?.abort?.target
                    ? `${data.abort.target.latitude},${data.abort.target.longitude}`
                    : ""
                }
              />
              <DriverRecenter point={selfPoint} token={focusToken} />

              {route.length > 1 && (
                <>
                  <Polyline
                    positions={route}
                    pathOptions={{ color: "#ffffff", weight: 11, opacity: 0.85 }}
                  />
                  <Polyline
                    positions={route}
                    pathOptions={{ color: "#2563eb", weight: 6, opacity: 0.95 }}
                  />
                </>
              )}
              {trail.length > 1 && (
                <Polyline
                  positions={trail}
                  pathOptions={{ color: "#22c55e", weight: 5, opacity: 0.9 }}
                />
              )}

              {abort?.target?.latitude != null && abort?.target?.longitude != null && (
                <Marker
                  position={[abort.target.latitude, abort.target.longitude]}
                  icon={abortPin()}
                  zIndexOffset={400000}
                >
                  <Tooltip direction="top" offset={[0, -14]} permanent>
                    {abortShort(abort.destination)}
                  </Tooltip>
                  <Popup>
                    <strong>{abort.target.name}</strong>
                    <br />
                    {abort.reason}
                  </Popup>
                </Marker>
              )}

              {activeStops.map((stop, index) => {
                if (stop.latitude == null || stop.longitude == null) return null;
                // The abort pin stands for the point the convoy is heading for,
                // so its planned-stop pin would only sit underneath it.
                if (abortStop?.id === stop.id) return null;
                const isNext = abort
                  ? false
                  : nextStop?.id === stop.id;
                const skipped = supersededIds.has(stop.id);
                return (
                  <Marker
                    key={`stop-${stop.id}`}
                    position={[stop.latitude, stop.longitude]}
                    icon={stopPin(
                      stop,
                      isNext,
                      index === activeStops.length - 1,
                      skipped
                    )}
                    zIndexOffset={isNext ? 600000 : 400000}
                  >
                    <Tooltip direction="top" offset={[0, -14]}>
                      {stop.sequence}. {stop.location_name}
                    </Tooltip>
                    <Popup>
                      <strong>
                        {stop.sequence}. {stop.location_name}
                      </strong>
                      <br />
                      {supersededIds.has(stop.id)
                        ? "Skipped by the abort order"
                        : stop.status === "departed"
                          ? "Departed"
                          : stop.status === "arrived"
                            ? "Arrived"
                            : "Not reached yet"}
                      <br />
                      Planned {formatDateTime(stop.planned_arrival || stop.planned_departure)}
                    </Popup>
                  </Marker>
                );
              })}

              {selfPoint && (
                <Marker position={selfPoint} icon={SELF_ICON} zIndexOffset={700000}>
                  <Popup>
                    <strong>Your position</strong>
                    <br />
                    {selfPoint[0].toFixed(5)}, {selfPoint[1].toFixed(5)}
                    {lastFix?.accuracy ? ` · ±${Math.round(lastFix.accuracy)} m` : ""}
                  </Popup>
                </Marker>
              )}
              {selfPoint && (
                <DriverHeading heading={lastFix?.heading ?? null} />
              )}
            </MapContainer>

            <div className="driver-map-top">
              <div className="driver-attribution" ref={attributeHost} />
              <div className="driver-map-pills">
                <span
                  className={`driver-pill ${
                    streaming && gpsState === "live" ? "is-live" : "is-off"
                  }`}
                >
                  {GPS_LABELS[gpsState] || "GPS off"}
                </span>
                {screenAwake && <span className="driver-pill is-awake">Screen on</span>}
                {!online && <span className="driver-pill is-off">Offline</span>}
                {queued > 0 && (
                  <span className="driver-pill">
                    {queued} position{queued === 1 ? "" : "s"} queued
                  </span>
                )}
              </div>
            </div>

            <div className="driver-map-actions">
              <button
                type="button"
                className="driver-map-button is-sos"
                onClick={() => {
                  setSosOpen(true);
                  setSosError("");
                  setSosMessage("");
                }}
                aria-label="Send an emergency alert"
                title="Emergency alert"
              >
                <AlertGlyph />
              </button>
              <button
                type="button"
                className="driver-map-button"
                onClick={() => setFocusToken((value) => value + 1)}
                aria-label="Centre the map on my position"
                title="Centre on my position"
              >
                <LocateGlyph />
              </button>
              <button
                type="button"
                className="driver-map-button"
                onClick={() => setExpanded((value) => !value)}
                aria-label={expanded ? "Minimise the map" : "Maximise the map"}
                title={expanded ? "Minimise the map" : "Maximise the map"}
              >
                <ExpandGlyph expanded={expanded} />
              </button>
            </div>

            {/* Always reachable: the sheet can be collapsed, the alert cannot
                be hidden behind it. */}
            {sosOpen && (
              <div
                className="driver-sos-panel"
                role="dialog"
                aria-label="Emergency alert"
              >
                <div className="driver-sos-head">
                  <h3>Emergency</h3>
                  <span className="hint">
                    Alerts the Operations Center at once with your vehicle and last
                    reported position.
                  </span>
                </div>
                {sosMessage ? (
                  <>
                    <div className="alert alert-warning" role="status">
                      {sosMessage}
                    </div>
                    <div className="driver-actions-row">
                      <Button variant="ghost" onClick={() => setSosOpen(false)}>
                        Close
                      </Button>
                    </div>
                  </>
                ) : (
                  <>
                    <input
                      className="input"
                      value={sosNote}
                      maxLength={300}
                      onChange={(event) => setSosNote(event.target.value)}
                      placeholder="What happened (optional)"
                      aria-label="Emergency details"
                    />
                    <div className="driver-actions-row">
                      <Button
                        variant="danger"
                        onClick={sendEmergency}
                        disabled={sosBusy}
                      >
                        {sosBusy ? "Sending…" : "Send alert now"}
                      </Button>
                      <Button
                        variant="ghost"
                        onClick={() => setSosOpen(false)}
                        disabled={sosBusy}
                      >
                        Cancel
                      </Button>
                    </div>
                    {sosError && <p className="field-message">{sosError}</p>}
                  </>
                )}
              </div>
            )}

            <aside
              className={`driver-panel${sheetOpen ? " is-open" : ""}`}
              aria-label="Trip details"
            >
              <button
                type="button"
                className="driver-sheet-handle"
                aria-expanded={sheetOpen}
                onClick={() => setSheetOpen((open) => !open)}
              >
                <span className="driver-sheet-grip" aria-hidden="true" />
                <span className="driver-sheet-copy">
                  <strong>
                    {activeRun.registration_number}
                    {activeRun.is_lead ? " · Lead" : ""}
                  </strong>
                  <span>
                    {abort
                      ? `${recordedStops} recorded · ${supersededIds.size} skipped`
                      : `${recordedStops}/${activeStops.length} stops`}
                    {" · "}
                    {abort
                      ? `Abort: ${abort.target?.name || "pinned point"}`
                      : nextStop
                        ? `Next: ${nextStop.location_name}`
                        : "All stops recorded"}
                  </span>
                </span>
                <span className="driver-sheet-toggle">
                  {sheetOpen ? "Hide" : "Show"}
                </span>
              </button>

              {sheetOpen && (
                <div className="driver-sheet-body">
                  {activeDelay?.status === "late" && (
                    <div className="alert alert-warning" role="status">
                      <strong>
                        {activeDelay.minutes} min behind plan
                        {activeDelay.stop_sequence
                          ? ` · stop ${activeDelay.stop_sequence}`
                          : ""}
                      </strong>
                      <p className="hint" style={{ margin: "4px 0 0" }}>
                        {activeDelay.reason} The Operations Center has been told.
                      </p>
                    </div>
                  )}

                  {actionable.length > 1 && (
                    <label className="field">
                      <span>Vehicle</span>
                      <select
                        className="input"
                        value={activeRunId ?? ""}
                        onChange={(event) => {
                          // Only discard breadcrumbs that belong to the run being
                          // left.
                          flush();
                          queue.current = [];
                          queueRun.current = Number(event.target.value);
                          persist();
                          setQueued(0);
                          setActiveRunId(Number(event.target.value));
                        }}
                      >
                        {actionable.map((run) => (
                          <option key={run.id} value={run.id}>
                            {run.registration_number}
                          </option>
                        ))}
                      </select>
                    </label>
                  )}

                  <section className="driver-next">
                    <span className="driver-next-kicker">
                      {abort
                        ? "Abort end point"
                        : nextStop
                          ? nextIndex === 0
                            ? "Departure point"
                            : `Stop ${nextStop.sequence} of ${activeStops.length}`
                          : "Itinerary"}
                    </span>
                    <h3>
                      {abort
                        ? abort.target?.name || "Pinned point"
                        : nextStop
                          ? nextStop.location_name
                          : "Every stop is recorded"}
                    </h3>
                    <p className="driver-next-meta">
                      {abort ? (
                        <span>{abortPhrase(abort.destination)}</span>
                      ) : nextStop ? (
                        <span>
                          Planned{" "}
                          {formatDateTime(
                            nextStop.planned_arrival || nextStop.planned_departure
                          )}
                        </span>
                      ) : null}
                      {distanceToFocus != null && (
                        <span>{distanceToFocus.toFixed(1)} km away</span>
                      )}
                      {awaitingDeparture && activeRun.etd ? (
                        <span>ETD {formatDateTime(activeRun.etd)}</span>
                      ) : null}
                    </p>
                    {primaryAction ? (
                      <button
                        type="button"
                        className="btn btn-primary driver-primary"
                        onClick={primaryAction.onClick}
                        disabled={Boolean(busy)}
                      >
                        {busy ? "Recording…" : primaryAction.label}
                      </button>
                    ) : (
                      <p className="hint">
                        {editable
                          ? "Waiting for the previous movement to be recorded."
                          : "This run is read-only for you."}
                      </p>
                    )}
                  </section>

                  <section className="driver-gps-card">
                    <div className="driver-gps-head">
                      <h3>Live position</h3>
                      <span
                        className={`badge ${
                          streaming ? "badge-success" : "badge-muted"
                        }`}
                      >
                        {streaming ? "Sharing" : "Off"}
                      </span>
                    </div>
                    <p className="hint">
                      {lastFix
                        ? `${lastFix.latitude.toFixed(5)}, ${lastFix.longitude.toFixed(
                            5
                          )}${lastFix.accuracy ? ` · ±${Math.round(lastFix.accuracy)} m` : ""}`
                        : "Waiting for the first GPS fix."}
                    </p>

                    <button
                      type="button"
                      className="driver-switch"
                      role="switch"
                      aria-checked={keepAwake}
                      onClick={() => setKeepAwake((value) => !value)}
                    >
                      <span className="driver-switch-copy">
                        <strong>Keep screen awake</strong>
                        <span className="hint">
                          Stops the phone sleeping while the position is shared.
                        </span>
                      </span>
                      <span className="driver-switch-track" aria-hidden="true">
                        <span />
                      </span>
                    </button>
                    {screenAwake && (
                      <p className="hint">The screen is being kept awake.</p>
                    )}
                    {!wakeSupported && streaming && keepAwake && (
                      <p className="field-message">
                        This browser cannot hold the screen awake. Set the phone's
                        auto-lock to Never while driving.
                      </p>
                    )}

                    {gpsError && <p className="field-message">{gpsError}</p>}

                    <div className="driver-actions-row">
                      {streaming ? (
                        <>
                          <Button variant="ghost" onClick={() => setStreaming(false)}>
                            Stop sharing
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={flush}
                            disabled={!online}
                          >
                            Send now
                          </Button>
                        </>
                      ) : (
                        <Button onClick={() => setStreaming(true)}>
                          Start sharing position
                        </Button>
                      )}
                    </div>
                    <p className="hint">
                      Browsers only allow location on HTTPS or localhost. On a
                      phone, open TOWEMS from its HTTPS address, not a bare IP over
                      HTTP.
                    </p>
                  </section>

                  <section>
                    <h3 className="driver-section-title">Stops</h3>
                    <StopTimeline stops={activeStops} supersededIds={supersededIds} />
                  </section>

                  <div className="driver-map-style">
                    <span className="hint">Map style</span>
                    <BasemapControl
                      config={config.data}
                      value={basemap?.id}
                      onChange={chooseBasemap}
                      compact
                    />
                  </div>
                  {basemapFailure && (
                    <p className="field-message">
                      The selected basemap could not be reached. Choose another map
                      style; the route is unaffected.
                    </p>
                  )}
                </div>
              )}
            </aside>
          </div>
        </div>
      ) : (
        runs.length > 0 && (
          <div className="driver-grid">
            {runs.map((run) => (
              <section className="card driver-run" key={run.id}>
                <header className="card-head">
                  <div>
                    <h2>
                      {run.registration_number}
                      {run.is_lead ? (
                        <span className="badge badge-info">Lead</span>
                      ) : null}
                    </h2>
                    <p className="hint">
                      {run.vehicle_type} · {run.driver_name || "No driver"} ·{" "}
                      {run.trip_number}
                    </p>
                  </div>
                  <StatusBadge status={run.status} />
                </header>
                <div className="card-body">
                  <dl className="details">
                    <div className="detail">
                      <dt>ETD (planned)</dt>
                      <dd>{formatDateTime(run.etd)}</dd>
                    </div>
                    <div className="detail">
                      <dt>ATD (actual)</dt>
                      <dd>
                        {formatDateTime(run.atd)}
                        {run.atd_source_label ? (
                          <span className="hint"> · {run.atd_source_label}</span>
                        ) : null}
                      </dd>
                    </div>
                  </dl>
                  <StopTimeline stops={run.stops || []} />
                  {run.last_ping_at && (
                    <p className="hint">
                      Last position {formatDateTime(run.last_ping_at)} ·{" "}
                      {run.is_live ? "live" : "stale"}
                    </p>
                  )}
                </div>
              </section>
            ))}
          </div>
        )
      )}

      <div className="driver-below">
        {activeRun && (
          <dl className="details driver-details">
            <div className="detail">
              <dt>Vehicle</dt>
              <dd>
                {activeRun.registration_number} · {activeRun.driver_name || "No driver"}
                {activeRun.atd_source_label ? ` · ${activeRun.atd_source_label}` : ""}
              </dd>
            </div>
            <div className="detail">
              <dt>ETD (planned)</dt>
              <dd>{formatDateTime(activeRun.etd)}</dd>
            </div>
            <div className="detail">
              <dt>ATD (actual)</dt>
              <dd>{formatDateTime(activeRun.atd)}</dd>
            </div>
            {activeRun.last_ping_at && (
              <div className="detail">
                <dt>Last position</dt>
                <dd>
                  {formatDateTime(activeRun.last_ping_at)} ·{" "}
                  {activeRun.is_live ? "live" : "stale"}
                </dd>
              </div>
            )}
          </dl>
        )}
      </div>
    </div>
  );
}
