import { useEffect, useMemo, useRef, useState } from "react";
import { CircleMarker, MapContainer, Tooltip, useMap, useMapEvents } from "react-leaflet";
import { api } from "../api/client.js";
import { useAuth } from "../auth/AuthContext.jsx";
import { useApi } from "../hooks/useApi.js";
import {
  BASEMAP_STORAGE_KEY,
  readStored,
  resolveBasemap,
  writeStored,
} from "../map/config.js";
import { useTheme } from "../theme/ThemeContext.jsx";
import BasemapControl from "./map/BasemapControl.jsx";
import MapBasemapLayer from "./map/MapBasemapLayer.jsx";
import { Button, Field, Input, Modal } from "./ui.jsx";

function joinPlaceName(parts) {
  const kept = [];
  for (const part of parts) {
    const value = (part || "").trim();
    if (value && !kept.some((item) => item.toLowerCase() === value.toLowerCase())) {
      kept.push(value);
    }
  }
  return kept.join(", ");
}

const DEFAULT_CENTER = [12.8797, 121.774];
const DEFAULT_ZOOM = 6;
// Close enough to see the unit's own locality and its approaches.
const UNIT_ZOOM = 12;

function ClickToPick({ onPick }) {
  useMapEvents({
    click(event) {
      onPick(event.latlng.lat, event.latlng.lng);
    },
  });
  return null;
}

function Recenter({ position }) {
  const map = useMap();
  useEffect(() => {
    if (position) map.setView(position, Math.max(map.getZoom(), 12), { animate: true });
  }, [position, map]);
  return null;
}

/** Move the map to a searched place: its full boundary when known, else its centre. */
function MapFocus({ target }) {
  const map = useMap();
  useEffect(() => {
    if (!target) return;
    const bounds = target.bbox;
    if (Array.isArray(bounds) && bounds.length === 2) {
      map.fitBounds(bounds, { animate: true, padding: [20, 20] });
      return;
    }
    if (target.center) map.setView(target.center, target.zoom ?? 12, { animate: true });
  }, [target, map]);
  return null;
}

/**
 * Pick an operational area on the map (spec §5).
 *
 * The operator clicks a point; the modal asks the backend for the MGRS grid and
 * for the official PSGC area containing that point, so the mission record gets
 * the grid reference and the location name from stored reference data rather
 * than from anything typed by hand.
 */
export default function LocationPickerModal({
  initial,
  onClose,
  onApply,
  title = "Select operational area",
  unitId,
}) {
  const { resolvedTheme } = useTheme();
  const config = useApi("/map/config/");
  // The same persisted style the other maps use: a style chosen here applies
  // everywhere, and a style chosen elsewhere is honoured here.
  const [basemapId, setBasemapId] = useState(() => readStored(BASEMAP_STORAGE_KEY, ""));
  const basemap = useMemo(
    () => resolveBasemap(config.data, basemapId, resolvedTheme),
    [config.data, basemapId, resolvedTheme]
  );

  function chooseBasemap(id) {
    setBasemapId(id);
    writeStored(BASEMAP_STORAGE_KEY, id);
  }

  const start =
    initial?.latitude && initial?.longitude
      ? [Number(initial.latitude), Number(initial.longitude)]
      : null;

  // With nothing pinned yet the map opens on the unit's own post rather than on
  // the whole country, so the first click is usually already in the right area.
  // The unit being planned for wins; otherwise the operator's own unit.
  const { user } = useAuth();
  const focusUnitId = unitId || user?.unit || "";
  const unitMarker = useApi(focusUnitId ? `/units/${focusUnitId}/marker/` : "", {
    enabled: Boolean(focusUnitId) && !start,
  });
  const unitPoint =
    unitMarker.data?.latitude != null && unitMarker.data?.longitude != null
      ? [Number(unitMarker.data.latitude), Number(unitMarker.data.longitude)]
      : null;
  const unitName = unitMarker.data?.unit_acronym || unitMarker.data?.unit_name || "Unit";

  const [position, setPosition] = useState(start);
  const [area, setArea] = useState("");
  const [locationName, setLocationName] = useState(initial?.location_name || "");
  const [mgrs, setMgrs] = useState(initial?.mgrs || "");
  const [resolving, setResolving] = useState(false);
  const [error, setError] = useState("");
  const [pinNote, setPinNote] = useState("");

  const [query, setQuery] = useState("");
  const [hits, setHits] = useState([]);
  const [searching, setSearching] = useState(false);
  const [searchNote, setSearchNote] = useState("");
  const [focus, setFocus] = useState(null);

  // The unit's coordinates arrive a moment after the modal opens, so move the
  // map once they land - but never after the operator has started clicking.
  const openedAtUnit = useRef(false);
  // Each pick increments this, so a slow reverse lookup for an earlier point
  // can never overwrite the labels of a later one.
  const pickSequence = useRef(0);
  useEffect(() => {
    // A search made while the marker was still loading takes precedence.
    if (openedAtUnit.current || start || !unitPoint || focus) return;
    openedAtUnit.current = true;
    setFocus({ center: unitPoint, zoom: UNIT_ZOOM, bbox: null, key: Date.now() });
  }, [unitPoint, start, focus]);

  // Search is debounced and combines three sources:
  //  * a place index (Google Places when configured, else OpenStreetMap) for
  //    businesses, buildings and facilities - "SM City Butuan";
  //  * the stored PSGC boundaries for provinces and cities;
  //  * locations already recorded in TOWEMS.
  // The place index is the only external call, and it degrades to empty if the
  // service is unreachable, leaving the offline sources working.
  useEffect(() => {
    const term = query.trim();
    if (term.length < 2) {
      setHits([]);
      setSearchNote("");
      return undefined;
    }
    let active = true;
    setSearching(true);
    const timer = setTimeout(async () => {
      try {
        const [geo, records, found] = await Promise.all([
          api.get(`/admin-areas/?q=${encodeURIComponent(term)}&limit=12`),
          api.get(`/gis/search/?q=${encodeURIComponent(term)}`).catch(() => ({ results: [] })),
          api.get(`/gis/places/?q=${encodeURIComponent(term)}`).catch(() => ({ results: [] })),
        ]);
        if (!active) return;
        const geoHits = (geo.results ?? []).map((row) => ({
          key: `psgc-${row.psgc}`,
          label: row.name,
          detail: [row.level_label, row.parent_name, row.region_name]
            .filter(Boolean)
            .join(" · "),
          latitude: row.centroid_lat,
          longitude: row.centroid_lon,
          bbox: row.bbox,
          source: "Boundary",
          pins: false,
        }));
        const placeHits = (found.results ?? [])
          .filter((row) => row.latitude != null && row.longitude != null)
          .map((row, index) => ({
            key: `place-${row.provider_id || index}`,
            label: row.label,
            detail: row.detail,
            latitude: row.latitude,
            longitude: row.longitude,
            bbox: null,
            source: row.source || "Place",
            // A searched facility has an exact point: pin it straight away.
            pins: true,
          }));
        const known = (records.results ?? [])
          .filter((row) => row.latitude != null && row.longitude != null)
          .map((row, index) => ({
            key: `rec-${index}-${row.label}`,
            label: row.label,
            detail: "Recorded location in TOWEMS",
            latitude: row.latitude,
            longitude: row.longitude,
            bbox: null,
            source: "TOWEMS record",
            pins: false,
          }));
        const combined = [...placeHits, ...geoHits, ...known];
        setHits(combined);
        const note = (found.note || "").trim();
        setSearchNote(
          combined.length
            ? note
            : note || "No place, province, city or recorded location matches that name."
        );
      } catch (err) {
        if (active) setSearchNote(err.message || "Search failed.");
      } finally {
        if (active) setSearching(false);
      }
    }, 300);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [query]);

  function choosePlace(hit) {
    if (hit.pins) {
      // An exact place: move there and pin, so one click finishes the job.
      setFocus({
        center: [Number(hit.latitude), Number(hit.longitude)],
        bbox: null,
        zoom: 16,
        key: Date.now(),
      });
      setLocationName(hit.label);
      pick(Number(hit.latitude), Number(hit.longitude), hit.label);
      setSearchNote(`${hit.label} pinned. Adjust the point by clicking the map if needed.`);
      return;
    }
    // A boundary: move to it and let the operator pick the exact spot.
    setFocus({
      center: [Number(hit.latitude), Number(hit.longitude)],
      bbox: hit.bbox || null,
      zoom: 11,
      key: Date.now(),
    });
    if (hit.bbox) {
      setSearchNote(
        `${hit.label} outlined. Click the exact spot inside it to pin the operational area.`
      );
      return;
    }
    setSearchNote(`Moved to ${hit.label}. Click the map to pin the operational area.`);
  }

  async function pick(latitude, longitude, nameOverride = "") {
    const sequence = (pickSequence.current += 1);
    setResolving(true);
    setError("");
    setPosition([latitude, longitude]);
    try {
      const grid = await api.post("/gis/mgrs/", { latitude, longitude });
      if (pickSequence.current !== sequence) return;
      setMgrs(grid.mgrs);
      try {
        const pin = await api.get(
          `/gis/pin/?lat=${latitude.toFixed(6)}&lon=${longitude.toFixed(6)}`
        );
        if (pickSequence.current !== sequence) return;
        setArea(pin.administrative_area?.label || "");
        const detail = pin.location || {};
        if (nameOverride.trim()) {
          // A searched facility: name the pin by the establishment, then the
          // city and province it sits in.
          setLocationName(
            joinPlaceName([nameOverride.trim(), detail.city, detail.province])
          );
        } else if (detail.label) {
          // The pin moved, so the name follows the new point rather than
          // keeping the previous pin's barangay or establishment.
          setLocationName(detail.label);
        }
        setPinNote(pin.note || "");
      } catch {
        // Administrative boundaries are reference data; a missing match must
        // not stop the operator from recording the coordinates.
        setArea("");
      }
    } catch (err) {
      if (pickSequence.current === sequence) {
        setError(err.message || "Could not resolve that point.");
      }
    } finally {
      if (pickSequence.current === sequence) setResolving(false);
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
          <Button
            disabled={!position || resolving}
            onClick={() =>
              onApply({
                latitude: position[0].toFixed(6),
                longitude: position[1].toFixed(6),
                mgrs,
                location_name: locationName.trim() || area,
              })
            }
          >
            Use this location
          </Button>
        </>
      }
    >
      <Field
        label="Search a place"
        hint="Search a building, facility or business (e.g. SM City Butuan), a province or city, or a location already recorded in TOWEMS. Clicking a facility pins it; clicking a boundary moves the map there for you to place the point."
      >
        <Input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="e.g. SM City Butuan, Camp Evangelista, Butuan City"
          autoFocus
        />
      </Field>

      {searching && <p className="hint">Searching…</p>}
      {searchNote && <p className="hint">{searchNote}</p>}

      {hits.length > 0 && (
        <div className="selection-results" role="list">
          {hits.map((hit) => (
            <button
              type="button"
              key={hit.key}
              role="listitem"
              className="selection-row"
              onClick={() => choosePlace(hit)}
            >
              <span>
                <strong>{hit.label}</strong>
                <span className="hint">{hit.detail}</span>
              </span>
              <span className="hint">{hit.source}</span>
            </button>
          ))}
        </div>
      )}

      <p className="hint">
        Click the map to place the location. The grid reference and the official administrative area
        are derived from the selected point.
      </p>

      <div className="btn-row" style={{ justifyContent: "flex-end", marginBottom: 6 }}>
        <BasemapControl
          config={config.data}
          value={basemapId}
          onChange={chooseBasemap}
          compact
        />
      </div>

      <div className="map-canvas" style={{ height: 340 }}>
        <MapContainer
          center={start || unitPoint || DEFAULT_CENTER}
          zoom={start || unitPoint ? UNIT_ZOOM : DEFAULT_ZOOM}
          className="map-canvas"
          scrollWheelZoom
        >
          <MapBasemapLayer basemap={basemap} />
          <ClickToPick onPick={pick} />
          <Recenter position={position} />
          <MapFocus target={focus} />
          {unitPoint && (
            <CircleMarker
              center={unitPoint}
              radius={8}
              pathOptions={{ color: "#2f6fed", fillColor: "#2f6fed", fillOpacity: 0.35 }}
            >
              <Tooltip>{unitName} location</Tooltip>
            </CircleMarker>
          )}
          {position && (
            <CircleMarker center={position} radius={9} pathOptions={{ color: "#f0b429", fillOpacity: 0.6 }} />
          )}
        </MapContainer>
      </div>

      {error && <p className="field-message">{error}</p>}
      {resolving && <p className="hint">Resolving grid reference…</p>}
      {pinNote && <p className="hint">{pinNote}</p>}

      <div className="form-grid" style={{ marginTop: 14 }}>
        <Field label="Selected coordinates">
          <Input
            readOnly
            value={position ? `${position[0].toFixed(6)}, ${position[1].toFixed(6)}` : "No point selected"}
          />
        </Field>
        <Field label="MGRS">
          <Input readOnly value={mgrs} placeholder="Derived from the selected point" />
        </Field>
        <Field
          label="Administrative area"
          hint="Province where the pin falls, and the unit AOR that covers it."
        >
          <Input readOnly value={area} placeholder="No matching boundary" />
        </Field>
        <Field
          label="Location name"
          hint="Barangay or establishment at the pin; edit it if the mission uses a local name."
        >
          <Input value={locationName} onChange={(event) => setLocationName(event.target.value)} />
        </Field>
      </div>
    </Modal>
  );
}
