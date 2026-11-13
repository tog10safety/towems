import { useEffect, useMemo, useState } from "react";
import {
  Circle,
  CircleMarker,
  MapContainer,
  Polyline,
  Polygon,
  Popup,
  useMap,
} from "react-leaflet";
import { qs } from "../api/client.js";
import { useAuth } from "../auth/AuthContext.jsx";
import { formatDateTime } from "../utils/format.js";
import { Badge, ErrorBanner, Loading, Select } from "./ui.jsx";
import BasemapControl from "./map/BasemapControl.jsx";
import MapBasemapLayer from "./map/MapBasemapLayer.jsx";
import { AdminBoundariesLayer } from "./map/Boundaries.jsx";
import UnitMarkerLayer from "./map/UnitMarkerLayer.jsx";
import { useApi } from "../hooks/useApi.js";
import { useTheme } from "../theme/ThemeContext.jsx";
import {
  BASEMAP_STORAGE_KEY,
  availableBasemaps,
  readStored,
  resolveBasemap,
  writeStored,
} from "../map/config.js";

const DEFAULT_CENTER = [12.8797, 121.774];
const DEFAULT_ZOOM = 6;

const LAYERS = [
  { key: "aor", label: "AOR boundaries", color: "#4f9cf9" },
  { key: "units", label: "Unit locations", color: "#2b6cb0" },
  { key: "ground", label: "Ground operations", color: "#3fb950" },
  { key: "air", label: "Air operations", color: "#f0b429" },
  { key: "vehicles", label: "Vehicle last known", color: "#a371f7" },
  { key: "trips", label: "Vehicle routes", color: "#39c5cf" },
  { key: "hazards", label: "Georeferenced hazards", color: "#f85149" },
];

/**
 * Dashboard operational picture (spec §6).
 *
 * Everything drawn comes from stored coordinates in the user's own unit scope.
 * The endpoint ignores any attempt to widen that scope, and positions are
 * labelled with their report time - there is no live tracking in TOWEMS.
 */
export default function DashboardMap() {
  const { resolvedTheme } = useTheme();
  const { permissions } = useAuth();
  // AOR coverage is the Administrator's GIS record; other roles keep the
  // operational layers but never request or draw the boundaries.
  const canSeeAor = Boolean(permissions.is_admin);
  const layers = canSeeAor ? LAYERS : LAYERS.filter((layer) => layer.key !== "aor");
  const config = useApi("/map/config/");
  const [enabled, setEnabled] = useState(() => new Set(LAYERS.map((layer) => layer.key)));
  const [showBoundaries, setShowBoundaries] = useState(false);
  const [basemapId, setBasemapId] = useState(() => readStored(BASEMAP_STORAGE_KEY, ""));
  const [basemapFailure, setBasemapFailure] = useState("");
  const [openTool, setOpenTool] = useState("");
  const basemap = useMemo(
    () => resolveBasemap(config.data, basemapId, resolvedTheme),
    [config.data, basemapId, resolvedTheme]
  );

  function chooseBasemap(id) {
    setBasemapId(id);
    writeStored(BASEMAP_STORAGE_KEY, id);
    setBasemapFailure("");
  }
  const [unit, setUnit] = useState("");
  const [category, setCategory] = useState("");
  const [status, setStatus] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");

  const layerParam = layers
    .filter((layer) => enabled.has(layer.key))
    .map((layer) => layer.key)
    .join(",");

  const path = `/gis/operational-picture/${qs({
    layers: layerParam,
    unit,
    category,
    status,
    from,
    to,
  })}`;
  const { data, loading, error, reload } = useApi(path, {
    deps: [layerParam, unit, category, status, from, to],
  });

  const aors = canSeeAor ? data?.aors ?? [] : [];
  const unitPins = data?.units ?? [];
  const missions = data?.missions ?? [];
  // A completed mission is history: its pin leaves the dashboard with its
  // trace, and the record is read on the Missions page instead. The endpoint
  // already omits them; this also drains a payload fetched before completion.
  const liveMissions = missions.filter((mission) => mission.status !== "completed");
  const positions = data?.vehicle_positions ?? [];
  const routes = data?.trip_routes ?? [];
  // A finished mission leaves no trace on the operational map: its route is
  // history, not something to watch. The endpoint already drops those trips;
  // this keeps a payload fetched before the completion from drawing one.
  const liveRoutes = routes.filter(
    (route) => !["completed", "cancelled"].includes(route.mission_status)
  );
  const hazards = data?.hazards ?? [];
  const totalFeatures =
    aors.length +
    unitPins.length +
    liveMissions.length +
    positions.length +
    routes.length +
    hazards.length;

  const center = useMemo(() => {
    if (!data?.center) return DEFAULT_CENTER;
    return [data.center.latitude, data.center.longitude];
  }, [data]);

  function toggleLayer(key) {
    setEnabled((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function toggleTool(tool) {
    setOpenTool((current) => (current === tool ? "" : tool));
  }

  return (
    <section className="card map-card">
      <header className="card-head">
        <div>
          <h2>Operational map</h2>
          {data && (
            <p className="hint">
              Scope: {data.scope.unit_acronym || data.scope.unit_name || "no unit"} ·{" "}
              {data.scope.unit_count} unit(s), {data.scope.subordinate_count} subordinate
            </p>
          )}
        </div>
        <div className="card-actions">
          <Badge tone="info">{data?.layers?.length ?? 0} layers</Badge>
        </div>
      </header>

      <div className="card-body">
        <ErrorBanner message={error} onRetry={reload} />
        {basemapFailure && (
          <div className="alert alert-warning">
            <span>
              The selected basemap could not be reached. Operational data is unaffected — choose
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

        <div className="map-filters">
          <div className="map-filter-row">
            <label className="map-filter-field">
              <span>Unit and Subordinate</span>
              <Select
                value={unit}
                onChange={(event) => setUnit(event.target.value)}
                aria-label="Unit and subordinate filter"
              >
                <option value="">Unit and Subordinate</option>
                {(data?.scope?.units ?? []).map((row) => (
                  <option key={row.id} value={row.id}>
                    {row.acronym} — {row.name}
                  </option>
                ))}
              </Select>
            </label>
            <label className="map-filter-field">
              <span>Mission Categories</span>
              <Select
                value={category}
                onChange={(event) => setCategory(event.target.value)}
                aria-label="Mission categories"
              >
                <option value="">All mission categories</option>
                <option value="ground">Ground</option>
                <option value="air">Air</option>
              </Select>
            </label>
            <label className="map-filter-field">
              <span>Status</span>
              <Select
                value={status}
                onChange={(event) => setStatus(event.target.value)}
                aria-label="Mission status"
              >
                <option value="">Any status</option>
                <option value="draft">Draft</option>
                <option value="planned">Planned</option>
                <option value="approved">Approved</option>
                <option value="active">Active</option>
              </Select>
            </label>
          </div>
          <div className="map-filter-row map-filter-row-dates">
            <label className="map-date">
              <span>From</span>
              <input type="date" value={from} onChange={(event) => setFrom(event.target.value)} />
            </label>
            <label className="map-date">
              <span>To</span>
              <input type="date" value={to} onChange={(event) => setTo(event.target.value)} />
            </label>
          </div>
        </div>

        <div className="map-layout">
          <div className="map-canvas map-canvas-dashboard">
            {loading && !data ? (
              <Loading label="Loading operational picture…" />
            ) : (
              <MapContainer center={center} zoom={data?.zoom ?? DEFAULT_ZOOM} scrollWheelZoom>
                <MapBasemapLayer basemap={basemap} onError={(id) => setBasemapFailure(id)} />
                <Recenter center={center} zoom={data?.zoom} />
                <AdminBoundariesLayer enabled={showBoundaries} level="province" />

                {/* Units are pinned with their own seal: the image comes from
                    Unit Management, and the fallback shows the acronym. */}
                {enabled.has("units") && <UnitMarkerLayer units={unitPins} />}

                {aors.map((aor) => {
                  const rings = toLeafletPositions(aor.geometry);
                  if (!rings.length) return null;
                  return (
                    <Polygon
                      key={`aor-${aor.id}`}
                      positions={rings}
                      pathOptions={{
                        color: aor.color || "#4f9cf9",
                        weight: 2,
                        fillOpacity: 0.12,
                      }}
                    >
                      <Popup>
                        <strong>{aor.code}</strong> — {aor.name}
                        <br />
                        {aor.unit_acronym || "Unassigned"}
                      </Popup>
                    </Polygon>
                  );
                })}

                {liveMissions.map((mission) => (
                  <CircleMarker
                    key={`mission-${mission.id}`}
                    center={[mission.latitude, mission.longitude]}
                    radius={7}
                    pathOptions={{
                      color: mission.category === "air" ? "#f0b429" : "#3fb950",
                      fillColor: mission.category === "air" ? "#f0b429" : "#3fb950",
                      fillOpacity: 0.7,
                    }}
                  >
                    <Popup>
                      <strong>{mission.mission_number}</strong>
                      <br />
                      {mission.title}
                      <br />
                      {mission.category} · {mission.status}
                      <br />
                      {mission.unit_acronym}
                      <br />
                      {mission.location_name || ""}
                      {mission.mgrs ? ` · ${mission.mgrs}` : ""}
                      <br />
                      {mission.start_at ? formatWhen(mission.start_at) : ""}
                    </Popup>
                  </CircleMarker>
                ))}

                {positions.map((position) => (
                  <CircleMarker
                    key={`vehicle-${position.vehicle_id}`}
                    center={[position.latitude, position.longitude]}
                    radius={6}
                    pathOptions={{
                      color: position.is_stale ? "#8b949e" : "#a371f7",
                      fillColor: "#a371f7",
                      fillOpacity: position.is_stale ? 0.35 : 0.7,
                    }}
                  >
                    <Popup>
                      <strong>{position.display_name || position.registration_number}</strong>
                      <br />
                      {position.vehicle_type} · {position.status}
                      <br />
                      Last known position ({position.source})
                      <br />
                      Reported {formatWhen(position.reported_at)}
                      <br />
                      {position.is_stale ? "Stale report — not live tracking" : "Not live tracking"}
                    </Popup>
                  </CircleMarker>
                ))}

                {liveRoutes.map((route) => (
                  <Polyline
                    key={`route-${route.trip_id}`}
                    positions={route.points.map((point) => [point.latitude, point.longitude])}
                    pathOptions={{ color: "#39c5cf", weight: 3, dashArray: "6 4" }}
                  >
                    <Popup>
                      <strong>{route.trip_number}</strong>
                      <br />
                      {route.title}
                      <br />
                      {route.points.length} itinerary points · {route.status}
                    </Popup>
                  </Polyline>
                ))}

                {hazards.map((hazard) => (
                  <Circle
                    key={`hazard-${hazard.hazard_id}-${hazard.latitude}-${hazard.longitude}`}
                    center={[hazard.latitude, hazard.longitude]}
                    radius={Math.max(hazard.radius_metres || 0, 30)}
                    pathOptions={{
                      color: "#f85149",
                      fillColor: "#f85149",
                      fillOpacity: 0.15,
                      weight: 1,
                    }}
                  >
                    <Popup>
                      <strong>Hazard</strong>
                      <br />
                      {hazard.description}
                      <br />
                      {hazard.before_risk_rating || "—"} → {hazard.after_risk_rating || "—"}
                      <br />
                      {hazard.unit_acronym}
                    </Popup>
                  </Circle>
                ))}
              </MapContainer>
            )}

            <div className="map-tools" aria-label="Map controls">
              <div className="map-tool-bar">
                <MapToolButton
                  label="Map style"
                  active={openTool === "style"}
                  onClick={() => toggleTool("style")}
                >
                  <StyleGlyph />
                </MapToolButton>
                <MapToolButton
                  label="Layers"
                  active={openTool === "layers"}
                  onClick={() => toggleTool("layers")}
                >
                  <LayersGlyph />
                </MapToolButton>
                <MapToolButton
                  label="Legend"
                  active={openTool === "legend"}
                  onClick={() => toggleTool("legend")}
                >
                  <LegendGlyph />
                </MapToolButton>
              </div>

              {openTool === "style" && (
                <div className="map-tool-panel">
                  <div className="map-tool-head">
                    <strong>Map style</strong>
                    <button
                      type="button"
                      className="map-tool-close"
                      aria-label="Close map style"
                      onClick={() => setOpenTool("")}
                    >
                      ×
                    </button>
                  </div>
                  <BasemapControl
                    config={config.data}
                    value={basemap?.id}
                    onChange={chooseBasemap}
                    compact
                    showLabel={false}
                  />
                </div>
              )}

              {openTool === "layers" && (
                <div className="map-tool-panel">
                  <div className="map-tool-head">
                    <strong>Layers</strong>
                    <button
                      type="button"
                      className="map-tool-close"
                      aria-label="Close layers"
                      onClick={() => setOpenTool("")}
                    >
                      ×
                    </button>
                  </div>
                  <div className="map-tool-list">
                    {layers.map((layer) => (
                      <label key={layer.key} className="check">
                        <input
                          type="checkbox"
                          checked={enabled.has(layer.key)}
                          onChange={() => toggleLayer(layer.key)}
                        />
                        <span className="swatch" style={{ background: layer.color }} />{" "}
                        {layer.label}
                      </label>
                    ))}
                    <label className="check">
                      <input
                        type="checkbox"
                        checked={showBoundaries}
                        onChange={(event) => setShowBoundaries(event.target.checked)}
                      />
                      <span className="swatch outline" /> Province / city boundaries
                    </label>
                  </div>
                </div>
              )}

              {openTool === "legend" && (
                <div className="map-tool-panel">
                  <div className="map-tool-head">
                    <strong>Legend</strong>
                    <button
                      type="button"
                      className="map-tool-close"
                      aria-label="Close legend"
                      onClick={() => setOpenTool("")}
                    >
                      ×
                    </button>
                  </div>
                  <ul className="legend">
                    <li>
                      <span className="swatch" style={{ background: "#3fb950" }} /> Ground mission
                    </li>
                    <li>
                      <span className="swatch" style={{ background: "#f0b429" }} /> Air mission
                    </li>
                    <li>
                      <span className="swatch" style={{ background: "#a371f7" }} /> Last known
                      vehicle position
                    </li>
                    <li>
                      <span className="swatch" style={{ background: "#f85149" }} /> Hazard radius
                    </li>
                    {canSeeAor && (
                      <li>
                        <span className="swatch outline" /> AOR boundary
                      </li>
                    )}
                    <li>
                      <span className="swatch" style={{ background: "#2b6cb0" }} /> Unit location
                      (unit seal)
                    </li>
                  </ul>
                </div>
              )}
            </div>
          </div>
        </div>

        {!loading && data && totalFeatures === 0 && (
          <p className="hint" style={{ marginTop: 10 }}>
            No mapped records in this scope for the selected layers and filters. TOWEMS only plots
            stored coordinates, so nothing is shown rather than an approximate location.
          </p>
        )}
      </div>
    </section>
  );
}

function MapToolButton({ label, active, onClick, children }) {
  return (
    <button
      type="button"
      className={`map-tool-btn ${active ? "active" : ""}`}
      aria-label={label}
      aria-expanded={active}
      title={label}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

function StyleGlyph() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="19"
      height="19"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M12 3a9 9 0 1 0 0 18h1.4a2.1 2.1 0 0 0 0-4.2h-1.2a1.8 1.8 0 0 1 0-3.6H16a5 5 0 0 0 5-5A5.3 5.3 0 0 0 16 3h-4Z" />
      <circle cx="7.5" cy="9" r="1" fill="currentColor" stroke="none" />
      <circle cx="10" cy="6.5" r="1" fill="currentColor" stroke="none" />
      <circle cx="14" cy="6.5" r="1" fill="currentColor" stroke="none" />
    </svg>
  );
}

function LayersGlyph() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="19"
      height="19"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="m12 3 9 5-9 5-9-5 9-5Z" />
      <path d="m3 12 9 5 9-5" />
      <path d="m3 16 9 5 9-5" />
    </svg>
  );
}

function LegendGlyph() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="19"
      height="19"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M4 6h4M11 6h9M4 12h4M11 12h9M4 18h4M11 18h9" />
      <circle cx="6" cy="6" r="1" fill="currentColor" stroke="none" />
      <circle cx="6" cy="12" r="1" fill="currentColor" stroke="none" />
      <circle cx="6" cy="18" r="1" fill="currentColor" stroke="none" />
    </svg>
  );
}

function Recenter({ center, zoom }) {
  const map = useMap();
  useEffect(() => {
    if (!center) return;
    map.setView(center, zoom ?? map.getZoom());
  }, [center, zoom, map]);
  return null;
}

function toLeafletPositions(geometry) {
  if (!geometry) return [];
  const convert = (ring) => ring.map(([lon, lat]) => [lat, lon]);
  if (geometry.type === "Polygon") return geometry.coordinates.map(convert);
  if (geometry.type === "MultiPolygon") return geometry.coordinates.flat().map(convert);
  return [];
}

// Philippine time, 24-hour military format (see utils/format.js).
const formatWhen = formatDateTime;
