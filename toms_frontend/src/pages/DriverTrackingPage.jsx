import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { API_BASE, api } from "../api/client.js";
import { useAuth } from "../auth/AuthContext.jsx";
import { Button, ErrorBanner, Loading, StatusBadge } from "../components/ui.jsx";
import { useApi } from "../hooks/useApi.js";
import { formatDateTime } from "../utils/format.js";

/**
 * The driver's own view of a dispatched mission.
 *
 * Built for a phone in a moving vehicle: one thumb-sized action per step, and
 * a GPS stream that survives a dead spot. Positions are queued, written to
 * local storage after every fix so a locked screen or a reload cannot lose
 * them, and sent in batches once the network is back. An official arrival time
 * is always a deliberate tap, never inferred from a breadcrumb.
 */

const FLUSH_MS = 12000;
const BATCH = 60;
const MAX_QUEUE = 200;

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

export default function DriverTrackingPage() {
  const { id } = useParams();
  const { user } = useAuth();
  const { data, loading, error, reload, setData } = useApi(`/missions/${id}/tracking/`, {
    deps: [id],
  });

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
  const [screenAwake, setScreenAwake] = useState(false);

  const queue = useRef([]);
  const queueRun = useRef(null);
  const watchId = useRef(null);
  const flushTimer = useRef(null);
  const activeRunRef = useRef(null);
  const wakeLock = useRef(null);

  const runs = data?.runs ?? [];
  const delays = data?.delays ?? [];
  const myRuns = useMemo(
    () => runs.filter((run) => run.driver && String(run.driver) === String(user?.id)),
    [runs, user?.id]
  );
  const canRecordOthers = Boolean(data?.can_manage || data?.is_lead);
  const actionable = useMemo(
    () =>
      runs.filter(
        (run) =>
          run.status === "active" &&
          (canRecordOthers || String(run.driver) === String(user?.id))
      ),
    [runs, canRecordOthers, user?.id]
  );

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
        setLastFix({ latitude, longitude, accuracy });
        setGpsState("live");
        if (queue.current.length >= 12) flush();
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
    if (!streaming) {
      setScreenAwake(false);
      if (wakeLock.current) {
        wakeLock.current.release?.().catch(() => {});
        wakeLock.current = null;
      }
      return undefined;
    }
    let cancelled = false;

    async function acquire() {
      if (typeof navigator === "undefined" || !("wakeLock" in navigator)) return;
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
  }, [streaming]);

  async function record(run, action, stop) {
    setBusy(`${run.id}:${action}:${stop?.id ?? ""}`);
    setFailure("");
    setMessage("");
    try {
      const payload = { run: run.id, action };
      if (stop) payload.stop = stop.id;
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
      setFailure(err.message || "That movement could not be recorded.");
    } finally {
      setBusy("");
    }
  }

  if (loading && !data) return <Loading label="Loading your movement task…" />;
  if (error) return <ErrorBanner message={error} onRetry={reload} />;
  if (!data) return null;

  const mission = data.mission;
  const activeRun = runs.find((run) => run.id === activeRunId) || null;
  const activeDelay = delays.find((row) => row.run === activeRunId) || null;
  const stale = runs.every((run) => run.status !== "active");

  return (
    <>
      <div className="page-head driver-head">
        <div>
          <p className="hint">
            <Link to={`/missions/${mission.id}`}>← Mission record</Link>
          </p>
          <h1>{mission.mission_number}</h1>
          <p>
            {mission.title} <StatusBadge status={mission.status} />
          </p>
          <p className="hint">
            {mission.location_name || "Objective area"} ·{" "}
            {mission.start_at ? formatDateTime(mission.start_at) : "No start time"}
          </p>
        </div>
        <div className="btn-row">
          <Button variant="ghost" size="sm" onClick={reload}>
            Refresh
          </Button>
          <a
            className="btn btn-ghost btn-sm"
            href={`${API_BASE}/missions/${mission.id}/tracking/log/`}
            target="_blank"
            rel="noreferrer"
          >
            Movement log
          </a>
        </div>
      </div>

      <ErrorBanner message={failure} onRetry={reload} />
      {message && (
        <div className="alert alert-success" role="status">
          {message}
        </div>
      )}

      {activeDelay?.status === "late" && (
        <div className="alert alert-warning" role="status">
          <strong>
            {activeDelay.minutes} min behind plan
            {activeDelay.stop_sequence ? ` · stop ${activeDelay.stop_sequence}` : ""}
          </strong>
          <p className="hint" style={{ margin: "4px 0 0" }}>
            {activeDelay.reason} The Operations Center has been told.
          </p>
        </div>
      )}
      {activeDelay?.status === "awaiting_departure" && activeDelay.planned_at && (
        <p className="hint">
          Planned departure {formatDateTime(activeDelay.planned_at)} — confirm the
          actual departure when the vehicle rolls.
        </p>
      )}

      {!online && (
        <div className="alert alert-warning" role="status">
          No network. Positions are being kept on this phone and will be sent when the
          signal returns.
        </div>
      )}
      {queued > 0 && (
        <p className="hint">
          {queued} position{queued === 1 ? "" : "s"} waiting to send
          {online ? "" : " — saved on this phone"}.
        </p>
      )}

      {runs.length === 0 && (
        <div className="card">
          <div className="card-body">
            <p>
              The Operations Center has not opened movement tracking for this mission yet.
              Ask them to dispatch the mission, or to press “Start movement tracking”.
            </p>
          </div>
        </div>
      )}

      {myRuns.length === 0 && runs.length > 0 && !canRecordOthers && (
        <div className="card">
          <div className="card-body">
            <p>
              You are not the designated driver of a vehicle on this mission. The board is
              read-only for you.
            </p>
          </div>
        </div>
      )}

      {runs.length > 0 && (
        <div className="driver-grid">
          {runs.map((run) => {
            const mine = String(run.driver) === String(user?.id);
            const editable =
              run.status === "active" && (mine || data.can_manage || data.is_lead);
            const delay = delays.find((row) => row.run === run.id) || null;
            const stops = run.stops || [];
            return (
              <section className="card driver-run" key={run.id}>
                <header className="card-head">
                  <div>
                    <h2>
                      {run.registration_number}
                      {run.is_lead ? <span className="badge badge-info">Lead</span> : null}
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
                    {delay?.status === "late" && (
                      <div className="detail">
                        <dt>Behind plan</dt>
                        <dd>{delay.minutes} min</dd>
                      </div>
                    )}
                  </dl>

                  {editable && run.atd_source !== "driver" && (
                    <Button
                      onClick={() => record(run, "depart")}
                      disabled={busy === `${run.id}:depart:`}
                    >
                      {busy === `${run.id}:depart:`
                        ? "Recording…"
                        : "Confirm actual departure"}
                    </Button>
                  )}

                  <ol className="stop-timeline">
                    {stops.map((stop, index) => {
                      const isOrigin = index === 0;
                      const isFinal = index === stops.length - 1;
                      const previous = index > 0 ? stops[index - 1] : null;
                      // Stop 1 is the departure point: its wheel-roll is the
                      // run's departure confirmation, not an arrival. Each
                      // later arrival waits for the previous departure.
                      const canArrive =
                        editable &&
                        !isOrigin &&
                        stop.status === "pending" &&
                        previous?.status === "departed";
                      const canDepart =
                        editable && !isFinal && stop.status === "arrived";
                      const planned = isOrigin
                        ? stop.planned_departure || stop.planned_arrival
                        : stop.planned_arrival;
                      return (
                        <li key={stop.id} className={`stop-row stop-${stop.status}`}>
                          <div className="stop-copy">
                            <strong>
                              {stop.sequence}. {stop.location_name}
                            </strong>
                            <p className="hint">
                              Planned {isOrigin ? "departure" : "arrival"}{" "}
                              {formatDateTime(planned)}
                              {stop.arrived_at
                                ? ` · Arrived ${formatDateTime(stop.arrived_at)}`
                                : ""}
                              {stop.departed_at
                                ? ` · Departed ${formatDateTime(stop.departed_at)}`
                                : ""}
                            </p>
                            {isOrigin && stop.status === "pending" && (
                              <p className="hint">
                                Confirm the actual departure above before marking the
                                next stop.
                              </p>
                            )}
                          </div>
                          {canArrive && (
                            <Button
                              size="sm"
                              onClick={() => record(run, "arrive_stop", stop)}
                              disabled={Boolean(busy)}
                            >
                              Arrived
                            </Button>
                          )}
                          {canDepart && (
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => record(run, "depart_stop", stop)}
                              disabled={Boolean(busy)}
                            >
                              Depart
                            </Button>
                          )}
                        </li>
                      );
                    })}
                  </ol>

                  {editable &&
                    stops.length > 0 &&
                    stops.every((stop) => stop.status !== "pending") && (
                      <Button
                        variant="ghost"
                        onClick={() => record(run, "complete")}
                        disabled={Boolean(busy)}
                      >
                        Complete this vehicle's run
                      </Button>
                    )}

                  {run.last_ping_at && (
                    <p className="hint">
                      Last position {formatDateTime(run.last_ping_at)} ·{" "}
                      {run.is_live ? "live" : "stale"}
                    </p>
                  )}
                </div>
              </section>
            );
          })}
        </div>
      )}

      {activeRun && actionable.length > 0 && !stale && (
        <section className="card driver-gps">
          <header className="card-head">
            <h2>Live position</h2>
            <span className={`badge badge-${gpsState === "live" ? "success" : "muted"}`}>
              {gpsState === "live"
                ? "Streaming"
                : gpsState === "locating"
                  ? "Finding GPS…"
                  : gpsState === "offline"
                    ? "Waiting for network"
                    : gpsState === "error"
                      ? "Not sending"
                      : "Off"}
            </span>
          </header>
          <div className="card-body">
            <p className="hint">
              Your phone's GPS is sent to the Operations Center while this page is open.
              Positions are saved on the phone, so a dead spot or a locked screen does not
              lose them — keep the page open and the screen will stay awake.
            </p>
            {actionable.length > 1 && (
              <label className="field">
                <span>Vehicle</span>
                <select
                  className="input"
                  value={activeRunId ?? ""}
                  onChange={(event) => {
                    // Only discard breadcrumbs that belong to the run being left.
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
            {lastFix && (
              <p className="hint">
                {lastFix.latitude.toFixed(5)}, {lastFix.longitude.toFixed(5)}
                {lastFix.accuracy ? ` · ±${Math.round(lastFix.accuracy)} m` : ""}
              </p>
            )}
            {screenAwake && <p className="hint">Screen is being kept awake.</p>}
            {gpsError && <p className="field-message">{gpsError}</p>}
            <div className="btn-row">
              {streaming ? (
                <>
                  <Button variant="ghost" onClick={() => setStreaming(false)}>
                    Stop sharing position
                  </Button>
                  <Button variant="ghost" size="sm" onClick={flush} disabled={!online}>
                    Send now
                  </Button>
                </>
              ) : (
                <Button onClick={() => setStreaming(true)}>Start sharing position</Button>
              )}
            </div>
            <p className="hint">
              Browsers only allow location on HTTPS or localhost. On a phone, open TOWEMS from
              its HTTPS address, not a bare IP over HTTP.
            </p>
          </div>
        </section>
      )}

      <p className="hint">
        <Link to={`/missions/${mission.id}/track`}>Open the convoy map</Link>
      </p>
    </>
  );
}
