import { useEffect, useMemo, useRef, useState } from "react";
import {
  CircleMarker,
  MapContainer,
  Polygon,
  Popup,
  useMap,
  useMapEvents,
} from "react-leaflet";
import { api } from "../api/client.js";
import { useApi } from "../hooks/useApi.js";
import { useTheme } from "../theme/ThemeContext.jsx";
import {
  BASEMAP_STORAGE_KEY,
  readStored,
  resolveBasemap,
  writeStored,
} from "../map/config.js";
import BasemapControl from "./map/BasemapControl.jsx";
import MapBasemapLayer from "./map/MapBasemapLayer.jsx";
import { Badge, Button, Field, Input, Modal, Select } from "./ui.jsx";

const DEFAULT_CENTER = [12.8797, 121.774];
const DEFAULT_ZOOM = 6;
const MGRS_IN_TEXT = /\b\d{1,2}[C-HJ-NP-X][A-HJ-NP-Z][A-HJ-NP-V]\d{2,10}\b/i;

/** Use the most specific part of a typed location as the opening search. */
function initialSearchTerm(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  const grid = text.match(MGRS_IN_TEXT)?.[0];
  if (grid) return grid;
  return text.split(",")[0].split("(")[0].trim() || text;
}

const FEATURE_TYPES = [
  { value: "friendly_troop", label: "Friendly troops", color: "#3fb950" },
  { value: "frequent_location", label: "Frequently searched location", color: "#f0b429" },
  { value: "operational_area", label: "Operational area", color: "#4f9cf9" },
];
const UNIT_SOURCE = "unit_location";
const AIRPORT_SOURCE = "airport";
const AIRSTRIP_SOURCE = "airstrip";
const AIRFIELD_SOURCE = "airfield";
const MANUAL_SOURCE = "manual";
const GEOCODE_SOURCE = "geocode";
const TYPE_COLORS = {
  ...Object.fromEntries(FEATURE_TYPES.map((row) => [row.value, row.color])),
  [UNIT_SOURCE]: "#2f6fed",
  [AIRPORT_SOURCE]: "#e5484d",
  [AIRSTRIP_SOURCE]: "#8b5cf6",
  [AIRFIELD_SOURCE]: "#e5484d",
  [MANUAL_SOURCE]: "#d97706",
  [GEOCODE_SOURCE]: "#0f9d8a",
};

function featureCentre(location) {
  if (!location) return null;
  if (location.latitude != null && location.longitude != null) {
    return [Number(location.latitude), Number(location.longitude)];
  }
  const geometry = location.geometry || {};
  if (geometry.type === "Point" && (geometry.coordinates || []).length >= 2) {
    return [Number(geometry.coordinates[1]), Number(geometry.coordinates[0])];
  }
  return null;
}

function polygonLatLngs(geometry) {
  if (!geometry) return [];
  if (geometry.type === "MultiPolygon") {
    return (geometry.coordinates || []).flatMap((polygon) =>
      (polygon[0] || []).map(([lng, lat]) => [lat, lng])
    );
  }
  return (geometry.coordinates?.[0] || []).map(([lng, lat]) => [lat, lng]);
}

function compact(parts) {
  return parts.filter(Boolean).join(" · ");
}

function MapFocus({ target }) {
  const map = useMap();
  useEffect(() => {
    if (!target) return;
    if (target.bbox) {
      map.fitBounds(target.bbox, { animate: true, padding: [24, 24] });
      return;
    }
    map.setView(target.center, target.zoom ?? 13, { animate: true });
  }, [target, map]);
  return null;
}

function ClickToPick({ onPick }) {
  useMapEvents({
    click(event) {
      onPick(event.latlng.lat, event.latlng.lng);
    },
  });
  return null;
}

/**
 * Browse the Operations Center's operational-mapping picture and use one entry
 * as a location.
 *
 * Saved features, unit pins and airfields can be included. Each result carries
 * the label, place and MGRS grid needed by registers that record a location as
 * one readable line, such as an aircraft's current location.
 */
export default function OperationalLocationPickerModal({
  onApply,
  onClose,
  includeUnits = false,
  includeAirfields = false,
  featureTypes = null,
  excludeFeatureTypes = [],
  allowManual = false,
  title = "Select from Operational Mapping",
  initialQuery = "",
  searchExternal = false,
}) {
  const { resolvedTheme } = useTheme();
  const featuresApi = useApi("/operational-mapping/?page_size=500");
  const unitsApi = useApi(includeUnits ? "/units/markers/" : "", {
    enabled: includeUnits,
  });
  const airfieldsApi = useApi(
    includeAirfields ? "/airfields/?page_size=500" : "",
    { enabled: includeAirfields }
  );
  const config = useApi("/map/config/");
  const [query, setQuery] = useState(() => initialSearchTerm(initialQuery));
  const [type, setType] = useState("");
  const [selectedKey, setSelectedKey] = useState("");
  const [focus, setFocus] = useState(null);
  const [geocode, setGeocode] = useState(null);
  const [geocoding, setGeocoding] = useState(false);
  const [geocodeNote, setGeocodeNote] = useState("");
  const [externalLocations, setExternalLocations] = useState([]);
  const [searchingExternal, setSearchingExternal] = useState(false);
  const [externalNote, setExternalNote] = useState("");
  const initialFocusDone = useRef(false);
  const [manual, setManual] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const pickSequence = useRef(0);
  const [basemapId, setBasemapId] = useState(() =>
    readStored(BASEMAP_STORAGE_KEY, "")
  );
  const basemap = useMemo(
    () => resolveBasemap(config.data, basemapId, resolvedTheme),
    [config.data, basemapId, resolvedTheme]
  );

  const features = featuresApi.data?.results ?? [];
  const units = unitsApi.data?.results ?? [];
  const airfields = airfieldsApi.data?.results ?? [];
  const allowedFeatureTypes = featureTypes?.length ? featureTypes : null;
  const excludedFeatureKey = excludeFeatureTypes.join("|");
  const excludedFeatureTypes = useMemo(
    () => new Set(excludeFeatureTypes),
    [excludedFeatureKey]
  );

  const locations = useMemo(() => {
    const featureLocations = features
      .filter(
        (feature) =>
          (!allowedFeatureTypes ||
            allowedFeatureTypes.includes(feature.feature_type)) &&
          !excludedFeatureTypes.has(feature.feature_type)
      )
      .map((feature) => ({
        key: `feature-${feature.id}`,
        sourceType: "feature",
        sourceLabel: feature.feature_type_label || "Operational mapping",
        feature_type: feature.feature_type,
        geometry: feature.geometry,
        color: feature.color,
        label: feature.acronym || feature.name,
        name: feature.name,
        address: feature.address || "",
        description: feature.description || "",
        mgrs: feature.mgrs || "",
        latitude: feature.latitude,
        longitude: feature.longitude,
        unit_name: feature.unit_name || "",
        unit_acronym: feature.unit_acronym || "",
      }));
    const unitLocations = units.map((unit) => ({
      key: `unit-${unit.unit_id}`,
      sourceType: UNIT_SOURCE,
      sourceLabel: "Unit location",
      label: unit.unit_acronym || unit.unit_name,
      name: unit.unit_name,
      address: unit.place_name || "",
      description: "",
      mgrs: unit.mgrs || "",
      latitude: unit.latitude,
      longitude: unit.longitude,
      unit_name: unit.unit_name || "",
      unit_acronym: unit.unit_acronym || "",
      color: TYPE_COLORS[UNIT_SOURCE],
    }));
    const airfieldLocations = airfields.map((field) => ({
      key: `airfield-${field.id}`,
      sourceType:
        field.kind === AIRSTRIP_SOURCE ? AIRSTRIP_SOURCE : AIRPORT_SOURCE,
      sourceLabel: field.kind_label || "Airfield",
      label: field.code || field.name,
      name: field.name,
      address: field.location || "",
      description: "",
      mgrs: field.grid_coordinate || "",
      latitude: field.latitude,
      longitude: field.longitude,
      unit_name: "",
      unit_acronym: "",
      color: TYPE_COLORS[field.kind] || TYPE_COLORS[AIRPORT_SOURCE],
    }));
    const manualLocation = manual
      ? {
          key: "manual-pick",
          sourceType: MANUAL_SOURCE,
          sourceLabel: "Manually plotted point",
          label: "Map point",
          name: "Manually plotted point",
          address: "",
          description: "",
          mgrs: manual.mgrs || "",
          latitude: manual.latitude,
          longitude: manual.longitude,
          unit_name: "",
          unit_acronym: "",
          color: TYPE_COLORS[MANUAL_SOURCE],
        }
      : null;
    const geocodeLocation = geocode
      ? {
          key: "geocode-pick",
          sourceType: GEOCODE_SOURCE,
          sourceLabel: "Geocoded place",
          label: geocode.label,
          name: geocode.label,
          address: geocode.detail || "",
          description: "",
          mgrs: "",
          latitude: geocode.latitude,
          longitude: geocode.longitude,
          unit_name: "",
          unit_acronym: "",
          color: TYPE_COLORS[GEOCODE_SOURCE],
        }
      : null;
    return [
      ...(manualLocation ? [manualLocation] : []),
      ...(geocodeLocation ? [geocodeLocation] : []),
      ...externalLocations,
      ...featureLocations,
      ...unitLocations,
      ...airfieldLocations,
    ];
  }, [
    features,
    units,
    airfields,
    allowedFeatureTypes,
    excludedFeatureTypes,
    externalLocations,
    manual,
    geocode,
  ]);

  const featureTypeOptions = FEATURE_TYPES.filter(
    ({ value }) =>
      (!allowedFeatureTypes || allowedFeatureTypes.includes(value)) &&
      !excludedFeatureTypes.has(value)
  );
  const typeOptions = [
    ...(includeUnits
      ? [{ value: UNIT_SOURCE, label: "Unit locations" }]
      : []),
    ...(includeAirfields
      ? [
          { value: AIRPORT_SOURCE, label: "Airports" },
          { value: AIRSTRIP_SOURCE, label: "Air strips" },
        ]
      : []),
    ...featureTypeOptions.map(({ value, label }) => ({ value, label })),
  ];

  const matches = useMemo(() => {
    const term = query.trim().toLowerCase();
    return locations.filter((location) => {
      if (type) {
        if (location.sourceType === "feature") {
          if (location.feature_type !== type) return false;
        } else if (
          location.sourceType !== type &&
          location.sourceType !== MANUAL_SOURCE
        ) {
          return false;
        }
      }
      if (location.sourceType === GEOCODE_SOURCE) return true;
      if (!term) return true;
      return [
        location.label,
        location.name,
        location.address,
        location.mgrs,
        location.description,
        location.sourceLabel,
        location.unit_name,
        location.unit_acronym,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase()
        .includes(term);
    });
  }, [locations, query, type]);

  const selected = locations.find((location) => location.key === selectedKey) || null;
  const loading =
    featuresApi.loading || unitsApi.loading || airfieldsApi.loading;
  const loadError = featuresApi.error || unitsApi.error || airfieldsApi.error;

  function choose(location) {
    const centre = featureCentre(location);
    setSelectedKey(location.key);
    setError("");
    setGeocodeNote("");
    setExternalNote("");
    if (centre) {
      setFocus({
        center: centre,
        bbox: location.bbox || null,
        zoom: location.bbox ? 11 : 14,
        key: Date.now(),
      });
    }
  }

  async function pickManual(latitude, longitude) {
    if (!allowManual) return;
    const sequence = pickSequence.current + 1;
    pickSequence.current = sequence;
    setSelectedKey("manual-pick");
    setManual({ latitude, longitude, mgrs: "" });
    setError("");
    setGeocodeNote("");
    try {
      const grid = await api.post("/gis/mgrs/", { latitude, longitude });
      if (pickSequence.current !== sequence) return;
      setManual({ latitude, longitude, mgrs: grid.mgrs || "" });
    } catch (err) {
      if (pickSequence.current !== sequence) return;
      setError(
        err.message ||
          "The point was selected, but its MGRS grid could not be resolved."
      );
    }
  }

  // Open on the location already in the leg field. A remembered MGRS is
  // authoritative; otherwise use an exact or unique operational-mapping match.
  useEffect(() => {
    if (!String(initialQuery || "").trim()) return undefined;
    if (loading || initialFocusDone.current) return undefined;

    const grid = String(initialQuery).match(MGRS_IN_TEXT)?.[0];
    if (grid) {
      let active = true;
      api
        .post("/gis/mgrs/", { mgrs: grid })
        .then((point) => {
          if (!active) return;
          initialFocusDone.current = true;
          setFocus({
            center: [Number(point.latitude), Number(point.longitude)],
            zoom: 15,
          });
        })
        .catch(() => {});
      return () => {
        active = false;
      };
    }

    const typed = String(initialQuery).trim().toLowerCase();
    const name = typed.split(",")[0].split("(")[0].trim();
    const exact = matches.filter((location) =>
      [location.label, location.name, location.mgrs]
        .filter(Boolean)
        .some((value) => String(value).trim().toLowerCase() === name)
    );
    const target = exact.length === 1 ? exact[0] : matches.length === 1 ? matches[0] : null;
    if (target) {
      initialFocusDone.current = true;
      choose(target);
      return undefined;
    }
    // Several saved entries match, so leave the choice to the operator rather
    // than pulling the map to an unrelated geocoder guess.
    if (matches.length > 0) {
      initialFocusDone.current = true;
      return undefined;
    }

    // The leg picker's external search already covers places, areas and MGRS.
    if (searchExternal) return undefined;

    // Nothing in the saved operational picture matched. Ask the configured
    // place index, then the TOWEMS records and boundary centroids, so a typed
    // place name still opens on its best known point.
    let active = true;
    setGeocoding(true);
    setGeocodeNote("");
    const term = initialSearchTerm(initialQuery);
    Promise.all([
      api
        .get(`/gis/places/?q=${encodeURIComponent(term)}`)
        .catch((error) => ({ results: [], error })),
      api
        .get(`/gis/search/?q=${encodeURIComponent(term)}`)
        .catch((error) => ({ results: [], error })),
      api
        .get(`/admin-areas/?q=${encodeURIComponent(term)}&limit=5`)
        .catch((error) => ({ results: [], error })),
    ])
      .then(([places, records, areas]) => {
        if (!active) return;
        initialFocusDone.current = true;
        const place = (places.results ?? []).find(
          (row) => row.latitude != null && row.longitude != null
        );
        const record = (records.results ?? []).find(
          (row) => row.latitude != null && row.longitude != null
        );
        const area = (areas.results ?? []).find(
          (row) => row.centroid_lat != null && row.centroid_lon != null
        );
        const hit = place || record || area;
        if (!hit) {
          setGeocodeNote(
            places.error
              ? "The place search service is unavailable and no saved or boundary location matched. Check the map or try again."
              : `No geocoded result found for "${term}". Zoom the map and pick the point directly.`
          );
          return;
        }
        const latitude = Number(
          hit.latitude != null ? hit.latitude : hit.centroid_lat
        );
        const longitude = Number(
          hit.longitude != null ? hit.longitude : hit.centroid_lon
        );
        if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return;
        setGeocode({
          label: hit.label || hit.name || term,
          detail:
            hit.detail ||
            (hit.level_label || hit.parent_name
              ? [hit.level_label, hit.parent_name].filter(Boolean).join(" · ")
              : ""),
          latitude,
          longitude,
        });
        setSelectedKey("geocode-pick");
        setFocus({
          center: [latitude, longitude],
          bbox: hit.bbox || null,
          zoom: hit.bbox ? 11 : 15,
          key: Date.now(),
        });
      })
      .catch(() => {})
      .finally(() => {
        if (active) setGeocoding(false);
      });
    return () => {
      active = false;
    };
  }, [initialQuery, loading, matches, searchExternal]);

  // Search beyond the saved operational picture: administrative areas,
  // external places, TOWEMS records, and a directly typed MGRS grid.
  useEffect(() => {
    if (!searchExternal) return undefined;
    const term = query.trim();
    if (term.length < 2 || loading) {
      setExternalLocations([]);
      setExternalNote("");
      setSearchingExternal(false);
      return undefined;
    }

    let active = true;
    setSearchingExternal(true);
    setExternalNote("");
    const timer = setTimeout(async () => {
      const grid = term.match(MGRS_IN_TEXT)?.[0] || "";
      const [mgrsPoint, places, areas, records] = await Promise.all([
        grid
          ? api.post("/gis/mgrs/", { mgrs: grid }).catch(() => null)
          : Promise.resolve(null),
        api
          .get(`/gis/places/?q=${encodeURIComponent(term)}`)
          .catch(() => ({ results: [] })),
        api
          .get(`/admin-areas/?q=${encodeURIComponent(term)}&limit=8`)
          .catch(() => ({ results: [] })),
        api
          .get(`/gis/search/?q=${encodeURIComponent(term)}`)
          .catch(() => ({ results: [] })),
      ]);
      if (!active) return;

      const rows = [];
      const seen = new Set();
      const add = (location) => {
        if (!location?.key || seen.has(location.key)) return;
        seen.add(location.key);
        rows.push(location);
      };

      if (mgrsPoint) {
        add({
          key: "mgrs-search",
          sourceType: "mgrs",
          sourceLabel: "MGRS location",
          label: `MGRS ${mgrsPoint.mgrs}`,
          name: `MGRS ${mgrsPoint.mgrs}`,
          address: "",
          description: "",
          mgrs: mgrsPoint.mgrs || grid,
          latitude: mgrsPoint.latitude,
          longitude: mgrsPoint.longitude,
          unit_name: "",
          unit_acronym: "",
          color: TYPE_COLORS[GEOCODE_SOURCE],
        });
      }

      for (const [index, row] of (places.results ?? []).entries()) {
        if (row.latitude == null || row.longitude == null) continue;
        add({
          key: `place-${row.provider_id || index}`,
          sourceType: "place",
          sourceLabel: row.source || "Place search",
          label: row.label || row.name || term,
          name: row.label || row.name || term,
          address: row.detail || "",
          description: "",
          mgrs: "",
          latitude: row.latitude,
          longitude: row.longitude,
          unit_name: "",
          unit_acronym: "",
          color: TYPE_COLORS[GEOCODE_SOURCE],
        });
      }

      for (const [index, row] of (areas.results ?? []).entries()) {
        if (row.centroid_lat == null || row.centroid_lon == null) continue;
        add({
          key: `area-${row.psgc || index}`,
          sourceType: "boundary",
          sourceLabel: "Administrative area",
          label: row.name || term,
          name: row.name || term,
          address: [row.level_label, row.parent_name, row.region_name]
            .filter(Boolean)
            .join(" · "),
          description: "",
          mgrs: "",
          latitude: row.centroid_lat,
          longitude: row.centroid_lon,
          bbox: row.bbox || null,
          unit_name: "",
          unit_acronym: "",
          color: "#f0b429",
        });
      }

      for (const [index, row] of (records.results ?? []).entries()) {
        if (row.latitude == null || row.longitude == null) continue;
        add({
          key: `record-${index}-${row.label}`,
          sourceType: "record",
          sourceLabel: "TOWEMS record",
          label: row.label || term,
          name: row.label || term,
          address: "",
          description: "",
          mgrs: "",
          latitude: row.latitude,
          longitude: row.longitude,
          unit_name: "",
          unit_acronym: "",
          color: TYPE_COLORS[GEOCODE_SOURCE],
        });
      }

      setExternalLocations(rows);
      setSearchingExternal(false);
      if (!rows.length) {
        setExternalNote(
          `No area, place or MGRS result found for "${term}". You can still click the map to plot it.`
        );
      }

      if (!initialFocusDone.current && !matches.length && rows.length) {
        const first = rows[0];
        initialFocusDone.current = true;
        setSelectedKey(first.key);
        setFocus({
          center: [Number(first.latitude), Number(first.longitude)],
          bbox: first.bbox || null,
          zoom: first.bbox ? 11 : 15,
          key: Date.now(),
        });
      }
    }, 300);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [query, searchExternal, loading]);

  async function useSelected() {
    if (!selected) return;
    const centre = featureCentre(selected);
    if (!centre) {
      setError("This location has no point that can be used.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const grid = await api.post("/gis/mgrs/", {
        latitude: centre[0],
        longitude: centre[1],
      });
      const mgrs = grid.mgrs || selected.mgrs || "";
      onApply({
        location_name: selected.name || selected.label,
        latitude: centre[0].toFixed(6),
        longitude: centre[1].toFixed(6),
        mgrs,
        detail: {
          label: selected.label,
          name: selected.name,
          address: selected.address,
          mgrs,
          source_type: selected.sourceType,
          unit_name: selected.unit_name,
          unit_acronym: selected.unit_acronym,
        },
      });
    } catch (err) {
      setError(err.message || "Could not resolve that location.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title={title}
      onClose={onClose}
      wide
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={useSelected} disabled={!selected || busy}>
            {busy ? "Using location…" : "Use this location"}
          </Button>
        </>
      }
    >
      <div className="form-grid">
        <Field
          label={
            searchExternal
              ? "Search locations or areas"
              : "Search operational locations"
          }
          hint={
            searchExternal
              ? "Search saved locations, administrative areas, places, or a MGRS grid."
              : "Search by name, acronym, address, MGRS grid or unit."
          }
        >
          <Input
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setGeocodeNote("");
            }}
            placeholder="e.g. HTOG10, TOW, Bancasi, 51PYK"
            autoFocus
          />
        </Field>
        <Field label="Location type">
          <Select value={type} onChange={(event) => setType(event.target.value)}>
            <option value="">All location types</option>
            {typeOptions.map((row) => (
              <option key={row.value} value={row.value}>
                {row.label}
              </option>
            ))}
          </Select>
        </Field>
      </div>

      {loading && !locations.length ? (
        <p className="hint">Loading operational mapping…</p>
      ) : geocoding && !locations.length ? (
        <p className="hint">Finding the typed location…</p>
      ) : (
        <>
          {loadError && <p className="field-message">{loadError}</p>}
          {geocodeNote && <p className="field-message">{geocodeNote}</p>}
          {searchingExternal && (
            <p className="hint">Searching areas, places and MGRS…</p>
          )}
          {externalNote && !matches.length && (
            <p className="hint">{externalNote}</p>
          )}
          <p className="hint" style={{ marginBottom: 6 }}>
            {matches.length} location result{matches.length === 1 ? "" : "s"} found.
          </p>
          {matches.length === 0 ? (
            <p className="hint">
              No matching location. Clear the search or choose another location
              type.
            </p>
          ) : (
            <div className="selection-results" role="list">
              {matches.map((location) => {
                const centre = featureCentre(location);
                return (
                  <button
                    type="button"
                    key={location.key}
                    role="listitem"
                    className={`selection-row ${
                      location.key === selectedKey ? "selected" : ""
                    }`}
                    onClick={() => choose(location)}
                    disabled={!centre}
                  >
                    <span>
                      <strong>{location.label}</strong>
                      <span className="hint">
                        {compact([
                          location.name !== location.label ? location.name : "",
                          location.address,
                          location.mgrs,
                        ])}
                      </span>
                    </span>
                    <span className="hint">{location.sourceLabel}</span>
                  </button>
                );
              })}
            </div>
          )}
        </>
      )}

      <div className="btn-row" style={{ justifyContent: "flex-end", margin: "8px 0 6px" }}>
        <BasemapControl
          config={config.data}
          value={basemapId}
          onChange={(id) => {
            setBasemapId(id);
            writeStored(BASEMAP_STORAGE_KEY, id);
          }}
          compact
        />
      </div>

      {allowManual && (
        <p className="hint" style={{ marginBottom: 6 }}>
          Click anywhere on the map to plot a destination that is not already mapped.
        </p>
      )}

      <div className="map-canvas" style={{ height: 330 }}>
        <MapContainer center={DEFAULT_CENTER} zoom={DEFAULT_ZOOM} scrollWheelZoom>
          <MapBasemapLayer basemap={basemap} />
          <MapFocus target={focus} />
          {allowManual && <ClickToPick onPick={pickManual} />}
          {locations.map((location) => {
            const active = location.key === selectedKey;
            const colour =
              location.color ||
              TYPE_COLORS[location.feature_type] ||
              TYPE_COLORS[location.sourceType] ||
              "#4f9cf9";
            if (
              location.sourceType === "feature" &&
              location.feature_type === "operational_area"
            ) {
              const positions = polygonLatLngs(location.geometry);
              if (positions.length < 3) return null;
              return (
                <Polygon
                  key={location.key}
                  positions={positions}
                  eventHandlers={{ click: () => choose(location) }}
                  bubblingMouseEvents={false}
                  pathOptions={{
                    color: colour,
                    weight: active ? 4 : 2,
                    fillOpacity: active ? 0.3 : 0.14,
                  }}
                >
                  <Popup>{location.label}</Popup>
                </Polygon>
              );
            }
            const centre = featureCentre(location);
            if (!centre) return null;
            return (
              <CircleMarker
                key={location.key}
                center={centre}
                radius={active ? 11 : 7}
                eventHandlers={{ click: () => choose(location) }}
                bubblingMouseEvents={false}
                pathOptions={{
                  color: active ? "#ffffff" : colour,
                  weight: active ? 3 : 1,
                  fillColor: colour,
                  fillOpacity: 0.8,
                }}
              >
                <Popup>{location.label}</Popup>
              </CircleMarker>
            );
          })}
        </MapContainer>
      </div>

      {selected && (
        <p className="hint" style={{ marginTop: 8 }}>
          Selected: <strong>{selected.label}</strong>{" "}
          <Badge tone="info">{selected.sourceLabel}</Badge>
          {selected.address ? ` · ${selected.address}` : ""}
        </p>
      )}
      {error && <p className="field-message">{error}</p>}
    </Modal>
  );
}
