import { useEffect, useMemo, useState } from "react";
import L from "leaflet";
import {
  MapContainer,
  Marker,
  Polygon,
  Popup,
  ZoomControl,
  useMap,
  useMapEvents,
} from "react-leaflet";
import { Button, Loading, StatusBadge } from "../components/ui.jsx";
import BasemapControl from "../components/map/BasemapControl.jsx";
import MapBasemapLayer from "../components/map/MapBasemapLayer.jsx";
import UnitMarkerLayer from "../components/map/UnitMarkerLayer.jsx";
import { useApi } from "../hooks/useApi.js";
import {
  AIR_ASSET_TYPE_GROUPS,
  AirAssetGlyph,
  aircraftTypeGroup,
  aircraftTypeMarker,
} from "../map/airAssets.jsx";
import {
  BASEMAP_STORAGE_KEY,
  readStored,
  resolveBasemap,
  writeStored,
} from "../map/config.js";
import { useTheme } from "../theme/ThemeContext.jsx";
import { formatDateTime } from "../utils/format.js";
import {
  FuelGlyph,
  FuelPopup,
  fuelMarker,
  groupFuelPlaces,
} from "../map/fuel.jsx";

const DEFAULT_CENTER = [12.8797, 121.774];
//: Two pins closer than this many screen pixels are one mark and cluster. The
//: tolerance is therefore recomputed from the zoom: far apart on the ground the
//: zoomed-out view cannot separate them, while zooming into the camp spreads
//: them into their own seals.
const PLACE_TOLERANCE_PIXELS = 14;
const AIR_UNIT_OFFSETS = [
  [0, -34],
  [34, 0],
  [-34, 0],
  [0, 34],
  [24, -24],
  [-24, -24],
  [24, 24],
  [-24, 24],
];

/**
 * Read-only AOR / GIS viewer for command, operations and safety posts.
 *
 * Everything outside the map frame is deliberately absent. The map carries its
 * own legend and style picker, and the three checkboxes only show or hide data
 * already scoped by the server to the caller's unit and subordinates.
 */
export default function AORViewer() {
  const { resolvedTheme } = useTheme();
  const picture = useApi(
    "/gis/operational-picture/?layers=aor,units,vehicles,fuel"
  );
  const config = useApi("/map/config/");
  const airAssets = useApi("/inventory/air-assets/?page_size=200");

  const [visible, setVisible] = useState({
    units: true,
    vehicles: false,
    air: false,
    fuel: false,
  });
  const [openTool, setOpenTool] = useState("");
  const [expandedGroups, setExpandedGroups] = useState(() => new Set());
  const [zoom, setZoom] = useState(6);
  const [basemapId, setBasemapId] = useState(() =>
    readStored(BASEMAP_STORAGE_KEY, "")
  );
  const [basemapFailure, setBasemapFailure] = useState("");

  const basemap = useMemo(
    () => resolveBasemap(config.data, basemapId, resolvedTheme),
    [config.data, basemapId, resolvedTheme]
  );

  const aors = picture.data?.aors ?? [];
  const units = picture.data?.units ?? [];
  const scope = picture.data?.scope;
  const scopeIds = useMemo(
    () => new Set(picture.data?.scope?.unit_ids ?? []),
    [picture.data]
  );
  // The operational-picture endpoint is already scoped; this keeps unassigned
  // vehicles out as well, so the layer is strictly the current unit and below.
  const vehicles = useMemo(
    () =>
      (picture.data?.vehicle_positions ?? []).filter((position) =>
        scopeIds.has(position.unit_id)
      ),
    [picture.data, scopeIds]
  );
  const locatedAirAssets = useMemo(
    () =>
      (airAssets.data?.results ?? []).filter(
        (asset) =>
          asset.map_location?.latitude != null &&
          asset.map_location?.longitude != null
      ),
    [airAssets.data]
  );
  // The operational-picture endpoint is already scoped to the caller's unit
  // and subordinates; this keeps a store outside the scope out of the layer.
  const fuelPositions = useMemo(
    () =>
      (picture.data?.fuel_positions ?? []).filter((position) =>
        scopeIds.has(position.unit_id)
      ),
    [picture.data, scopeIds]
  );
  // One pin per place, each carrying its own batch table.
  const fuelPlaces = useMemo(
    () => groupFuelPlaces(fuelPositions),
    [fuelPositions]
  );
  const airTypeRows = useMemo(
    () =>
      AIR_ASSET_TYPE_GROUPS.map((group) => ({
        ...group,
        count: locatedAirAssets.filter(
          (asset) => aircraftTypeGroup(asset).value === group.value
        ).length,
      })).filter((group) => group.count > 0),
    [locatedAirAssets]
  );
  const pictureUpdatedAt = picture.data?.generated_at || "";
  const airUpdatedAt = useMemo(() => {
    const times = locatedAirAssets
      .map((asset) => asset.updated_at)
      .filter(Boolean)
      .sort();
    return times[times.length - 1] || "";
  }, [locatedAirAssets]);
  // Any pins sharing a point cluster under one numbered pin: two aircraft of
  // the same type parked together, a unit seal under an aircraft, or both. A
  // numbered cluster pin stands in until selected, then each pin is spread on a
  // ring so every one can be opened without losing the stored coordinate.
  const overlapGroups = useMemo(() => {
    const groups = [];
    // One pixel at this zoom and latitude, in degrees: Leaflet's Web Mercator
    // resolution at equator is 156543.03392 m/px, shrinking by 2^zoom, and a
    // degree of latitude is ~111.32 km. The tolerance is a few marker widths.
    const latitude = Number(
      picture.data?.center?.latitude ?? units[0]?.latitude ?? 12.88
    );
    const metresPerPixel =
      (156543.03392 * Math.cos((latitude * Math.PI) / 180)) / 2 ** zoom;
    const tolerance =
      (PLACE_TOLERANCE_PIXELS * metresPerPixel) / 111320;
    // Pins closer than that are the same mark on screen; grouping by exact
    // coordinate left the second seal underneath the first, so its name could
    // never be opened.
    const near = (group, unitLatitude, unitLongitude) =>
      Math.abs(group.latitude - unitLatitude) <= tolerance &&
      Math.abs(group.longitude - unitLongitude) <= tolerance;
    const placeFor = (latitude, longitude) => {
      const lat = Number(latitude);
      const lon = Number(longitude);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
      const existing = groups.find((group) => near(group, lat, lon));
      if (existing) return existing;
      const group = {
        key: `${lat.toFixed(5)},${lon.toFixed(5)}`,
        latitude: lat,
        longitude: lon,
        units: [],
        air: [],
      };
      groups.push(group);
      return group;
    };

    // Only the layers actually drawn can overlap: hiding the unit seals must
    // not leave two aircraft stacked because a hidden seal shares their point.
    if (visible.units) {
      for (const unit of units) {
        placeFor(unit.latitude, unit.longitude)?.units.push(unit);
      }
    }
    if (visible.air) {
      for (const asset of locatedAirAssets) {
        placeFor(
          asset.map_location?.latitude,
          asset.map_location?.longitude
        )?.air.push(asset);
      }
    }

    // Sit the cluster among its members rather than on whichever pin happened
    // to be read first.
    for (const group of groups) {
      const points = [
        ...group.units.map((unit) => [unit.latitude, unit.longitude]),
        ...group.air.map((asset) => [
          asset.map_location?.latitude,
          asset.map_location?.longitude,
        ]),
      ]
        .map(([lat, lon]) => [Number(lat), Number(lon)])
        .filter(([lat, lon]) => Number.isFinite(lat) && Number.isFinite(lon));
      if (points.length) {
        group.latitude =
          points.reduce((sum, [lat]) => sum + lat, 0) / points.length;
        group.longitude =
          points.reduce((sum, [, lon]) => sum + lon, 0) / points.length;
      }
    }

    return groups.filter((group) => group.units.length + group.air.length > 1);
  }, [units, locatedAirAssets, visible.units, visible.air, zoom, picture.data]);
  const overlapUnitIds = useMemo(
    () =>
      new Set(
        overlapGroups.flatMap((group) => group.units.map((unit) => unit.unit_id))
      ),
    [overlapGroups]
  );
  const overlapAirIds = useMemo(
    () => new Set(overlapGroups.flatMap((group) => group.air.map((asset) => asset.id))),
    [overlapGroups]
  );

  function toggleLayer(layer) {
    setVisible((current) => ({ ...current, [layer]: !current[layer] }));
  }

  function toggleTool(tool) {
    setOpenTool((current) => (current === tool ? "" : tool));
  }

  function toggleGroup(key) {
    setExpandedGroups((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function chooseBasemap(id) {
    setBasemapId(id);
    writeStored(BASEMAP_STORAGE_KEY, id);
    setBasemapFailure("");
  }

  function retry() {
    picture.reload();
    config.reload();
    airAssets.reload();
  }

  const problem = picture.error || config.error;
  const scopeLabel =
    scope?.unit_acronym || scope?.unit_name || "Your unit and subordinates";
  // Count from the authorised scope, not from units that happen to have a map
  // pin; a subordinate without a pin is still part of the scope.
  const subordinateCount = Math.max(0, (scope?.unit_ids?.length ?? 0) - 1);
  const airProblem = airAssets.error;
  const aorState = layerState({
    loading: picture.loading,
    error: picture.error,
    hasData: Boolean(picture.data),
    count: aors.length,
    emptyLabel: "No coverage",
    timestamp: pictureUpdatedAt,
  });
  const unitState = layerState({
    loading: picture.loading,
    error: picture.error,
    hasData: Boolean(picture.data),
    count: units.length,
    emptyLabel: "No unit pins",
    timestamp: pictureUpdatedAt,
  });
  const vehicleState = layerState({
    loading: picture.loading,
    error: picture.error,
    hasData: Boolean(picture.data),
    count: vehicles.length,
    emptyLabel: "No positions",
    timestamp: pictureUpdatedAt,
  });
  const airState = layerState({
    loading: airAssets.loading,
    error: airAssets.error,
    hasData: Boolean(airAssets.data),
    count: locatedAirAssets.length,
    emptyLabel: "No mapped aircraft",
    timestamp: airUpdatedAt,
  });
  const fuelState = layerState({
    loading: picture.loading,
    error: picture.error,
    hasData: Boolean(picture.data),
    count: fuelPlaces.length,
    emptyLabel: "No mapped fuel",
    timestamp: pictureUpdatedAt,
  });
  const printLayerNames =
    [
      visible.units && `Unit Locations (${units.length})`,
      visible.vehicles && `Mobility Assets (${vehicles.length})`,
      visible.air && `Air Assets (${locatedAirAssets.length})`,
      visible.fuel && `Fuel Locations (${fuelPlaces.length})`,
      aors.length > 0 && `AOR Boundaries (${aors.length})`,
    ]
      .filter(Boolean)
      .join(" · ") || "No layers selected";

  return (
    <div className="aor-viewer">
      <div className="aor-print-head">
        <strong>AOR / GIS — {scopeLabel}</strong>
        <span>
          {subordinateCount > 0
            ? `${subordinateCount} subordinate unit${
                subordinateCount === 1 ? "" : "s"
              }`
            : "Current unit"}{" "}
          · Data updated{" "}
          {pictureUpdatedAt ? formatDateTime(pictureUpdatedAt) : "—"}
        </span>
        <span>Visible layers: {printLayerNames}</span>
      </div>

      <div className="map-canvas aor-viewer-map">
        {!picture.data && picture.loading && (
          <div className="aor-viewer-loading">
            <Loading label="Loading the AOR picture…" />
          </div>
        )}

        <MapContainer
          center={DEFAULT_CENTER}
          zoom={6}
          scrollWheelZoom
          zoomControl={false}
        >
          <MapBasemapLayer
            basemap={basemap}
            onError={(id) => setBasemapFailure(id)}
          />
          <ZoomControl position="bottomright" />
          <TrackZoom onZoom={setZoom} />
          <FitToPicture
            picture={picture.data}
            vehicles={vehicles}
            airAssets={locatedAirAssets}
            fuelPositions={fuelPositions}
          />
          <PrintMap />

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
                  fillOpacity: 0.13,
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

          {visible.units && (
            <UnitMarkerLayer
              units={units.filter((unit) => !overlapUnitIds.has(unit.unit_id))}
            />
          )}

          {overlapGroups.map((group) => (
              <OverlapGroup
                key={group.key}
                group={group}
                expanded={expandedGroups.has(group.key)}
                onToggle={() => toggleGroup(group.key)}
              />
            ))}

          {visible.vehicles &&
            vehicles.map((position) => (
              <Marker
                key={`vehicle-${position.vehicle_id}`}
                position={[Number(position.latitude), Number(position.longitude)]}
                icon={vehicleMarker(position)}
                zIndexOffset={400000}
              >
                <Popup>
                  <strong>
                    {position.display_name || position.registration_number}
                  </strong>
                  <br />
                  {position.unit_acronym || "Unassigned"} ·{" "}
                  {position.vehicle_type || "Vehicle"}
                  <br />
                  <StatusBadge status={position.status} />
                  {position.is_stale ? " Last known position" : " Last reported"}
                  <br />
                  {position.mgrs ? `MGRS ${position.mgrs}` : ""}
                  {position.mgrs && position.reported_at ? " · " : ""}
                  {position.reported_at
                    ? formatDateTime(position.reported_at)
                    : ""}
                </Popup>
              </Marker>
            ))}

          {visible.fuel &&
            fuelPlaces.map((place) => (
              <Marker
                key={`fuel-${place.key}`}
                position={[Number(place.latitude), Number(place.longitude)]}
                icon={fuelMarker(place)}
                zIndexOffset={300000}
              >
                <Popup className="pol-popup" maxWidth={360} minWidth={240}>
                  <FuelPopup place={place} />
                </Popup>
              </Marker>
            ))}

          {visible.air &&
            locatedAirAssets
              .filter((asset) => !overlapAirIds.has(asset.id))
              .map((asset) => {
                return (
                <Marker
                  key={`air-asset-${asset.id}`}
                  position={[
                    Number(asset.map_location.latitude),
                    Number(asset.map_location.longitude),
                  ]}
                  icon={aircraftTypeMarker(asset)}
                  zIndexOffset={500000}
                >
                  <Popup>
                    <AirAssetPopup asset={asset} />
                  </Popup>
                </Marker>
              );
            })}
        </MapContainer>

        <aside
          className={`aor-viewer-legend ${
            openTool === "legend" ? "" : "is-collapsed"
          }`}
          aria-label="Map legend"
        >
          <div className="aor-viewer-legend-head">
            <div className="aor-viewer-legend-title">
              <strong>AOR / GIS</strong>
              <span className="hint">
                View only · {scopeLabel}
                {subordinateCount > 0
                  ? ` + ${subordinateCount} subordinate unit${
                      subordinateCount === 1 ? "" : "s"
                    }`
                  : ""}
              </span>
            </div>
          </div>

          <ul className="map-legend">
            <li className={`aor-layer-row ${visible.units ? "" : "is-hidden"}`}>
              <label className="legend-toggle">
                <input
                  type="checkbox"
                  checked={visible.units}
                  onChange={() => toggleLayer("units")}
                />
                <span className="legend-icon" style={{ color: "#2b6cb0" }}>
                  <UnitGlyph />
                </span>
                <span>Unit Locations</span>
                <span className="hint">({units.length})</span>
              </label>
              <LayerMeta state={unitState} />
            </li>
            <li className={`aor-layer-row ${visible.vehicles ? "" : "is-hidden"}`}>
              <label className="legend-toggle">
                <input
                  type="checkbox"
                  checked={visible.vehicles}
                  onChange={() => toggleLayer("vehicles")}
                />
                <span className="legend-icon" style={{ color: "#a371f7" }}>
                  <VehicleGlyph />
                </span>
                <span>Mobility Assets Locations</span>
                <span className="hint">({vehicles.length})</span>
              </label>
              <LayerMeta state={vehicleState} />
            </li>
            <li className={`aor-layer-row ${visible.air ? "" : "is-hidden"}`}>
              <label className="legend-toggle">
                <input
                  type="checkbox"
                  checked={visible.air}
                  onChange={() => toggleLayer("air")}
                />
                <span className="legend-icon" style={{ color: "#4f9cf9" }}>
                  <AirAssetGlyph icon="fixed" size={13} />
                </span>
                <span>Air Assets Locations</span>
                <span className="hint">({locatedAirAssets.length})</span>
              </label>
              <LayerMeta state={airState} />
            </li>
            <li className={`aor-layer-row ${visible.fuel ? "" : "is-hidden"}`}>
              <label className="legend-toggle">
                <input
                  type="checkbox"
                  checked={visible.fuel}
                  onChange={() => toggleLayer("fuel")}
                />
                <span className="legend-icon" style={{ color: "#d97706" }}>
                  <FuelGlyph />
                </span>
                <span>Fuel Locations</span>
                <span className="hint">({fuelPlaces.length})</span>
              </label>
              <LayerMeta state={fuelState} />
            </li>
            <li className="aor-viewer-boundary-row aor-layer-row">
              <span className="aor-layer-label">
                <span className="legend-line" /> Unit AOR boundary
              </span>
              <LayerMeta state={aorState} />
            </li>
          </ul>

          {picture.data?.boundary_scope === "all_units" && (
            <p className="aor-viewer-note">
              Boundaries and unit seals cover every unit — the Operations Center
              watch. Mobility, air assets and fuel stay within your own scope.
            </p>
          )}

          {visible.air && airTypeRows.length > 0 && (
            <div className="aor-viewer-airtypes">
              {airTypeRows.map((group) => (
                <div key={group.value} className="aor-viewer-airtype">
                  <span
                    className="air-asset-legend-glyph"
                    style={{ color: group.color }}
                  >
                    <AirAssetGlyph icon={group.icon} size={13} />
                  </span>
                  <span>{group.label}</span>
                  <span className="hint">({group.count})</span>
                </div>
              ))}
            </div>
          )}

          {!airAssets.loading && !airProblem && locatedAirAssets.length === 0 && (
            <p className="aor-viewer-note">
              No air asset has a mapped current location.
            </p>
          )}
          {airProblem && (
            <p className="aor-viewer-note">
              Air assets could not be loaded.
            </p>
          )}
          {visible.fuel && picture.data?.fuel_without_position > 0 && (
            <p className="aor-viewer-note">
              {picture.data.fuel_without_position} fuel store
              {picture.data.fuel_without_position === 1 ? "" : "s"} hold no
              mapped position (no stored point, airfield or unit marker) and are
              not drawn.
            </p>
          )}
          {visible.vehicles &&
            picture.data?.vehicles_without_position > 0 && (
              <p className="aor-viewer-note">
                {picture.data.vehicles_without_position} mobility asset
                {picture.data.vehicles_without_position === 1 ? "" : "s"} without
                a position report are not drawn. Positions are last known, not
                live tracking.
              </p>
            )}
        </aside>

        <div className="map-tools aor-map-tools" aria-label="Map controls">
          <div className="map-tool-bar">
            <AorToolButton
              label="Map style"
              active={openTool === "style"}
              onClick={() => toggleTool("style")}
            >
              <StyleGlyph />
            </AorToolButton>
            <AorToolButton label="Print / PDF" onClick={() => window.print()}>
              <PrintGlyph />
            </AorToolButton>
            <AorToolButton
              label="Legend"
              active={openTool === "legend"}
              onClick={() => toggleTool("legend")}
            >
              <LegendGlyph />
            </AorToolButton>
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
        </div>

        {(problem || basemapFailure) && (
          <div className="aor-viewer-alert" role="alert">
            <span>
              {problem
                ? "The AOR picture could not be loaded."
                : `The “${basemapFailure}” basemap could not be reached. Pick another map style.`}
            </span>
            {problem && (
              <Button size="sm" variant="ghost" onClick={retry}>
                Retry
              </Button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function AirAssetPopup({ asset }) {
  const group = aircraftTypeGroup(asset);
  return (
    <>
      <strong>
        {asset.model} · {asset.tail_number}
      </strong>
      {asset.callsign && (
        <>
          <br />
          {asset.callsign}
        </>
      )}
      <br />
      <StatusBadge status={asset.operational_status} />{" "}
      <span className="hint">{group.label}</span>
      <br />
      {asset.assigned_unit_label || asset.assigned_unit || "Unassigned"}
      {asset.opcon_unit_acronym
        ? ` · OPCON ${asset.opcon_unit_acronym}`
        : ""}
      {asset.category_labels?.length > 0 && (
        <>
          <br />
          <span className="hint">{asset.category_labels.join(", ")}</span>
        </>
      )}
      {asset.current_location && (
        <>
          <br />
          {asset.current_location}
        </>
      )}
      {asset.map_location?.mgrs && (
        <>
          <br />
          MGRS {asset.map_location.mgrs}
        </>
      )}
    </>
  );
}

/**
 * One numbered pin for any two or more pins sharing a coordinate - two parked
 * aircraft, a unit seal under an aircraft, or a whole mix. Selecting it opens
 * the real symbols in a ring around the point; selecting the numbered pin
 * again collapses them.
 */
function OverlapGroup({ group, expanded, onToggle }) {
  const unitOffsets = {};
  const airOffsets = new Map();
  const members = [
    ...group.units.map((unit) => ({ type: "unit", unit })),
    ...group.air.map((asset) => ({ type: "air", asset })),
  ];

  members.forEach((member, index) => {
    const offset = AIR_UNIT_OFFSETS[index % AIR_UNIT_OFFSETS.length];
    if (member.type === "unit") unitOffsets[member.unit.unit_id] = offset;
    else airOffsets.set(member.asset.id, offset);
  });

  return (
    <>
      <Marker
        position={[group.latitude, group.longitude]}
        icon={overlapGroupIcon(group, expanded)}
        zIndexOffset={2000000}
        eventHandlers={{ click: onToggle }}
      />

      {expanded && (
        <>
          {group.units.length > 0 && (
            <UnitMarkerLayer units={group.units} offsets={unitOffsets} />
          )}
          {group.air.map((asset) => (
            <Marker
              key={`overlap-air-${asset.id}`}
              position={[
                Number(asset.map_location.latitude),
                Number(asset.map_location.longitude),
              ]}
              icon={aircraftTypeMarker(asset, airOffsets.get(asset.id))}
              zIndexOffset={500000}
            >
              <Popup>
                <AirAssetPopup asset={asset} />
              </Popup>
            </Marker>
          ))}
        </>
      )}
    </>
  );
}

/**
 * The unit that fronts a cluster: the mother unit, not the first pin listed.
 * A member whose parent is also in the group is subordinate to that parent, so
 * the one with no parent present is the highest unit at the place.
 */
function clusterLead(units) {
  if (!units.length) return null;
  const present = new Set(units.map((unit) => unit.unit_id));
  return (
    units.find(
      (unit) => unit.unit_parent_id == null || !present.has(unit.unit_parent_id)
    ) || units[0]
  );
}

function overlapGroupIcon(group, expanded) {
  const count = group.units.length + group.air.length;
  const label = expanded
    ? "Collapse the overlapping pins"
    : `Show ${count} overlapping pins`;
  const lead = clusterLead(group.units);
  const size = 36;
  const attributes = `role="button" aria-label="${label}" title="${label}"`;
  const badge = `<span class="map-overlap-count">${count}</span>`;
  let inner;
  if (expanded) {
    inner = `<span class="map-overlap-marker is-expanded" ${attributes}>−</span>`;
  } else if (lead?.logo_url) {
    // The unit's own seal identifies the place; the badge keeps the count.
    inner =
      `<span class="map-overlap-marker is-seal" ${attributes}>` +
      `<span class="unit-pin"><img src="${escapeHtml(lead.logo_url)}" alt="" /></span>` +
      `${badge}</span>`;
  } else {
    const fallback = lead
      ? escapeHtml((lead.unit_acronym || lead.unit_name || "?").slice(0, 5))
      : String(count);
    inner = `<span class="map-overlap-marker" ${attributes}>${fallback}${badge}</span>`;
  }
  return L.divIcon({
    className: "map-overlap-icon",
    html: inner,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  });
}

function escapeHtml(text) {
  return String(text ?? "").replace(
    /[&<>"']/g,
    (character) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[
        character
      ])
  );
}

function AorToolButton({ label, active = false, onClick, children }) {
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

function PrintGlyph() {
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
      <path d="M7 8V3h10v5" />
      <path d="M7 17H4a1 1 0 0 1-1-1v-5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v5a1 1 0 0 1-1 1h-3" />
      <rect x="7" y="14" width="10" height="7" rx="1" />
      <path d="M18 12h.01" />
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

function layerState({ loading, error, hasData, count, emptyLabel, timestamp }) {
  if (loading && !hasData) {
    return { status: "Loading", tone: "loading", timestamp: "" };
  }
  if (error) {
    return { status: "Unavailable", tone: "error", timestamp: "" };
  }
  if (!hasData) {
    return { status: "Waiting", tone: "loading", timestamp: "" };
  }
  return {
    status: count > 0 ? "Ready" : emptyLabel,
    tone: count > 0 ? "ready" : "empty",
    timestamp,
  };
}

function LayerMeta({ state }) {
  return (
    <span className="aor-layer-meta">
      <span className={`aor-layer-status ${state.tone}`}>{state.status}</span>
      <span>
        {state.timestamp
          ? `Updated ${formatDateTime(state.timestamp)}`
          : "Not updated yet"}
      </span>
    </span>
  );
}

/**
 * Leaflet keeps its own size while the print stylesheet changes the frame.
 * Invalidating on both sides of the print dialog keeps the visible map and
 * tiles aligned with the page that is produced.
 */
/** Feeds the map's zoom into the overlap tolerance, so pins separate on zoom. */
function TrackZoom({ onZoom }) {
  useMapEvents({
    zoomend(event) {
      onZoom(event.target.getZoom());
    },
  });
  return null;
}

function PrintMap() {
  const map = useMap();

  useEffect(() => {
    const resize = () => map.invalidateSize({ animate: false });
    window.addEventListener("beforeprint", resize);
    window.addEventListener("afterprint", resize);
    return () => {
      window.removeEventListener("beforeprint", resize);
      window.removeEventListener("afterprint", resize);
    };
  }, [map]);

  return null;
}

function UnitGlyph() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="13"
      height="13"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M12 3 5 6v5c0 4.4 2.9 8.4 7 9.5 4.1-1.1 7-5.1 7-9.5V6l-7-3Z" />
      <path d="m9 12 2 2 4-4" />
    </svg>
  );
}

function VehicleGlyph() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="13"
      height="13"
      fill="currentColor"
      stroke="none"
      aria-hidden="true"
    >
      <path d="M8 3h8l2 4v13l-2 2H8l-2-2V7l2-4Zm1 3v3h6V6H9Zm0 6h6v5H9v-5Z" />
    </svg>
  );
}

/** A top-down vehicle, matching the mobility colour used on the dashboard. */
function vehicleMarker(position) {
  const colour = position.is_stale ? "#8b949e" : "#a371f7";
  return L.divIcon({
    className: "vehicle-map-icon",
    html:
      '<span class="vehicle-map-marker">' +
      `<svg viewBox="0 0 48 48" width="30" height="30" fill="${colour}" ` +
      'stroke="#ffffff" stroke-width="1.35" stroke-linejoin="round" aria-hidden="true">' +
      '<rect x="11" y="10" width="5" height="9" rx="2" fill="#20262e"/>' +
      '<rect x="32" y="10" width="5" height="9" rx="2" fill="#20262e"/>' +
      '<rect x="11" y="30" width="5" height="9" rx="2" fill="#20262e"/>' +
      '<rect x="32" y="30" width="5" height="9" rx="2" fill="#20262e"/>' +
      '<path d="M18 5h12l3 5v28l-3 5H18l-3-5V10l3-5Z"/>' +
      '<path d="M18 12h12v6H18z" fill="#ffffff" opacity="0.82" stroke="none"/>' +
      '<path d="M18 31h12M18 36h12" fill="none" opacity="0.55"/>' +
      '<path d="M21 21h6v6h-6z" fill="#ffffff" opacity="0.35" stroke="none"/>' +
      "</svg></span>",
    iconSize: [30, 30],
    iconAnchor: [15, 15],
    popupAnchor: [0, -14],
  });
}

function FitToPicture({ picture, vehicles, airAssets, fuelPositions }) {
  const map = useMap();

  useEffect(() => {
    if (!picture) return;
    const bounds = L.latLngBounds([]);
    const extend = (latitude, longitude) => {
      if (latitude == null || longitude == null) return;
      bounds.extend([Number(latitude), Number(longitude)]);
    };

    for (const aor of picture.aors ?? []) {
      for (const ring of toLeafletPositions(aor.geometry)) {
        for (const [latitude, longitude] of ring) extend(latitude, longitude);
      }
    }
    for (const unit of picture.units ?? []) {
      extend(unit.latitude, unit.longitude);
    }
    // Only the scoped vehicles the viewer actually draws; an unassigned or
    // filtered position must not pull the view away from the unit's picture.
    for (const position of vehicles ?? []) {
      extend(position.latitude, position.longitude);
    }
    for (const asset of airAssets ?? []) {
      extend(asset.map_location?.latitude, asset.map_location?.longitude);
    }
    for (const store of fuelPositions ?? []) {
      extend(store.latitude, store.longitude);
    }

    if (bounds.isValid()) {
      map.fitBounds(bounds.pad(0.12), { maxZoom: 11, animate: false });
    }
  }, [map, picture, vehicles, airAssets, fuelPositions]);

  return null;
}

/** GeoJSON [lon, lat] -> Leaflet [lat, lon] for Polygon / MultiPolygon. */
function toLeafletPositions(geometry) {
  if (!geometry) return [];
  if (geometry.type === "Polygon") {
    return geometry.coordinates.map((ring) =>
      ring.map(([lon, lat]) => [lat, lon])
    );
  }
  if (geometry.type === "MultiPolygon") {
    return geometry.coordinates.flatMap((polygon) =>
      polygon.map((ring) => ring.map(([lon, lat]) => [lat, lon]))
    );
  }
  return [];
}
