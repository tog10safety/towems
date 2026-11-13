import { useEffect, useMemo, useState } from "react";
import { divIcon } from "leaflet";
import {
  CircleMarker,
  MapContainer,
  Marker,
  Polygon,
  Popup,
  useMap,
  useMapEvents,
} from "react-leaflet";
import { api } from "../api/client.js";
import { useAuth } from "../auth/AuthContext.jsx";
import {
  Badge,
  Button,
  Card,
  CollapsibleCard,
  ErrorBanner,
  Field,
  Input,
  Loading,
  Modal,
  Pagination,
  Select,
  StatusBadge,
  Table,
  Textarea,
} from "../components/ui.jsx";
import BasemapControl from "../components/map/BasemapControl.jsx";
import MapBasemapLayer from "../components/map/MapBasemapLayer.jsx";
import UnitMarkerLayer from "../components/map/UnitMarkerLayer.jsx";
import { useApi } from "../hooks/useApi.js";
import { useTheme } from "../theme/ThemeContext.jsx";
import {
  BASEMAP_STORAGE_KEY,
  readStored,
  resolveBasemap,
  writeStored,
} from "../map/config.js";
import { formatDateTime } from "../utils/format.js";

const DEFAULT_CENTER = [12.8797, 121.774];
const FEATURES_PAGE_SIZE = 10;

const FEATURE_TYPES = [
  {
    value: "friendly_troop",
    label: "Friendly troops",
    short: "Troops",
    icon: "troops",
    color: "#3fb950",
    kind: "point",
  },
  {
    value: "frequent_location",
    label: "Frequently searched location",
    short: "Location",
    icon: "location",
    color: "#f0b429",
    kind: "point",
  },
  {
    value: "operational_area",
    label: "Operational area",
    short: "Area",
    icon: "area",
    color: "#4f9cf9",
    kind: "polygon",
  },
];

const TYPE_BY_VALUE = Object.fromEntries(FEATURE_TYPES.map((entry) => [entry.value, entry]));

// Each plotted feature type has its own glyph and colour, so the same symbol
// marks it in the toolbar, the legend and on the map.
const GLYPH_PATHS = {
  troops:
    '<path d="M9 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z"/>' +
    '<path d="M3.5 19a5.5 5.5 0 0 1 11 0"/>' +
    '<path d="M16 5.5a2.5 2.5 0 1 1 0 5"/>' +
    '<path d="M17 13.5a4.5 4.5 0 0 1 3.5 4.5"/>',
  location:
    '<path d="M12 21s7-5.6 7-11a7 7 0 1 0-14 0c0 5.4 7 11 7 11Z"/>' +
    '<circle cx="12" cy="10" r="2.5"/>',
  area: '<path d="M12 3 4.5 7.5v9L12 21l7.5-4.5v-9L12 3Z"/>',
  unit:
    '<path d="M12 3 5 6v5c0 4.4 2.9 8.4 7 9.5 4.1-1.1 7-5.1 7-9.5V6l-7-3Z"/>' +
    '<path d="m9 12 2 2 4-4"/>',
  plane:
    '<path d="M21 16v-2l-8-5V3.5a1.5 1.5 0 0 0-3 0V9l-8 5v2l8-2.5V19l-2.5 1.7V22l4-1 4 1v-1.3L13 19v-5.5l8 2.5Z"/>',
  runway:
    '<path d="M4 21 20 3"/><path d="M8 21 24 3"/><path d="M2 21h20"/>',
};

function PlotGlyph({ name, size = 16 }) {
  return (
    <svg
      className="plot-glyph"
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      dangerouslySetInnerHTML={{ __html: GLYPH_PATHS[name] || "" }}
    />
  );
}

const PIN_PATH =
  "M16 1.5C8 1.5 1.5 8 1.5 16c0 10.5 12.9 21.6 13.6 22.3a1.4 1.4 0 0 0 1.8 0c.7-.7 13.6-11.8 13.6-22.3C30.5 8 24 1.5 16 1.5Z";

function safeMarkerColor(value, fallback) {
  return /^#[0-9a-f]{6}$/i.test(value || "") ? value : fallback;
}

/** A point marker: a coloured pin carrying the type's glyph. */
function pinMarker(name, color) {
  return divIcon({
    className: "map-point-icon",
    html:
      '<svg viewBox="0 0 32 40" width="21" height="26" aria-hidden="true">' +
      `<path d="${PIN_PATH}" fill="${safeMarkerColor(color, "#57606a")}" ` +
      'stroke="#ffffff" stroke-width="1.6"/>' +
      '<g transform="translate(8 8)" fill="none" stroke="#ffffff" ' +
      'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
      `${GLYPH_PATHS[name] || ""}</g></svg>`,
    iconSize: [21, 26],
    iconAnchor: [10.5, 24],
    popupAnchor: [0, -24],
  });
}

function LegendIcon({ name, color }) {
  return (
    <span className="legend-icon" style={{ color }}>
      <PlotGlyph name={name} size={13} />
    </span>
  );
}

const FEATURE_MARKERS = Object.fromEntries(
  FEATURE_TYPES.filter((entry) => entry.kind === "point").map((entry) => [
    entry.value,
    pinMarker(entry.icon, entry.color),
  ])
);

// Airfields are reference data rather than plotted features: every account sees
// the same airports and strips, drawn in their own colours.
const AIRFIELD_TYPES = [
  { value: "airport", label: "Airport", icon: "plane", color: "#e5484d" },
  { value: "airstrip", label: "Air strip", icon: "runway", color: "#8b5cf6" },
];

const AIRFIELD_MARKERS = Object.fromEntries(
  AIRFIELD_TYPES.map((entry) => [
    entry.value,
    pinMarker(entry.icon, entry.color),
  ])
);

// Air assets use top-down aircraft silhouettes rather than pins. A category
// group fixes the shape, so the same symbol reads in the legend and on the map.
const AIR_ASSET_GROUPS = [
  {
    value: "strike",
    label: "Fighter & attack",
    icon: "fighter",
    color: "#e5484d",
    categories: ["fighter_interceptor", "attack_cas", "light_attack"],
  },
  {
    value: "transport",
    label: "Transport & refueling",
    icon: "transport",
    color: "#4f9cf9",
    categories: ["transport_cargo", "aerial_refueling"],
  },
  {
    value: "utility",
    label: "Utility, SAR & patrol",
    icon: "support",
    color: "#3fb950",
    categories: [
      "utility",
      "search_rescue",
      "combat_search_rescue",
      "maritime_patrol",
      "special_operations",
    ],
  },
  {
    value: "isr",
    label: "ISR & unmanned",
    icon: "uav",
    color: "#a371f7",
    categories: ["reconnaissance_surveillance", "unmanned_aerial_vehicle"],
  },
  {
    value: "training",
    label: "Training",
    icon: "trainer",
    color: "#f0b429",
    categories: ["training"],
  },
  {
    value: "medevac",
    label: "Medical evacuation",
    icon: "medevac",
    color: "#39c5cf",
    categories: ["air_ambulance_medevac"],
  },
  {
    value: "other",
    label: "Other aircraft",
    icon: "other",
    color: "#8b949e",
    categories: ["other_unclassified"],
  },
];

const AIR_ASSET_GROUP_BY_CATEGORY = Object.fromEntries(
  AIR_ASSET_GROUPS.flatMap((group) =>
    group.categories.map((category) => [category, group])
  )
);
const AIR_ASSET_FALLBACK_GROUP = AIR_ASSET_GROUPS[AIR_ASSET_GROUPS.length - 1];

const AIR_ASSET_GLYPHS = {
  fighter:
    '<path d="M24 2.5 27 13.5 44.5 21.5 44.5 25.5 27 22.6 26.4 34.7 33.2 39.7 33.2 42.7 24 40 14.8 42.7 14.8 39.7 21.6 34.7 21 22.6 3.5 25.5 3.5 21.5 21 13.5Z"/>',
  transport:
    '<path d="M20.5 4.5h7v11.5l16.5 7.2v3.8l-16.5-2.8v9.3l6.5 4.8v2.7L24 39.6l-10 1.4v-2.7l6.5-4.8v-9.3L4 27v-3.8l16.5-7.2Z"/>' +
    '<rect x="7.2" y="18.4" width="4.6" height="6.2" rx="1.4"/>' +
    '<rect x="36.2" y="18.4" width="4.6" height="6.2" rx="1.4"/>',
  support:
    '<path d="M21.5 5h5v11l15.5 6.5v4l-15.5-2.3v9.2l6 4.5v2.6L24 39.5l-8.5 1v-2.6l6-4.5v-9.2L6 26.5v-4L21.5 16Z"/>' +
    '<rect x="7" y="16.4" width="2.8" height="8.6" rx="1.4"/>' +
    '<rect x="38.2" y="16.4" width="2.8" height="8.6" rx="1.4"/>',
  uav: '<path d="M24 4 26.4 17.5 45 27 45 30 26.4 26.8 24 40 21.6 26.8 3 30 3 27 21.6 17.5Z"/>',
  trainer:
    '<path d="M22.5 4h3v12l12 7.5v3.6l-12-2.4v8.5l5 4v2.8L24 38.8l-6.5 1.2v-2.8l5-4v-8.5l-12 2.4v-3.6l12-7.5Z"/>',
  medevac:
    '<path fill-rule="evenodd" d="M21.5 5h5v11l15.5 6.5v4l-15.5-2.3v9.2l6 4.5v2.6L24 39.5l-8.5 1v-2.6l6-4.5v-9.2L6 26.5v-4L21.5 16Z M21 22h6v4h4v4h-4v4h-6v-4h-4v-4h4Z"/>',
  other:
    '<path d="M23 4h2v13l14 7.5v3.5l-14-2.5v9l6 4.2v2.8l-8-1.4-8 1.4v-2.8l6-4.2v-9L7 28v-3.5L21 17Z"/>',
};

const AIR_ASSET_STATUS_COLORS = {
  available: "#3fb950",
  scheduled: "#4f9cf9",
  in_flight: "#39c5cf",
  maintenance: "#d29922",
  grounded: "#e5484d",
  unserviceable: "#8b949e",
  reserved: "#a371f7",
};

function airAssetGroup(asset) {
  for (const category of asset.categories ?? []) {
    const group = AIR_ASSET_GROUP_BY_CATEGORY[category];
    if (group) return group;
  }
  return AIR_ASSET_FALLBACK_GROUP;
}

function AirAssetGlyph({ icon, size = 16 }) {
  return (
    <svg
      viewBox="0 0 48 48"
      width={size}
      height={size}
      fill="currentColor"
      stroke="none"
      aria-hidden="true"
      dangerouslySetInnerHTML={{ __html: AIR_ASSET_GLYPHS[icon] || AIR_ASSET_GLYPHS.other }}
    />
  );
}

function aircraftMarker(asset) {
  const group = airAssetGroup(asset);
  const colour = safeMarkerColor(group.color, "#57606a");
  const statusColour = safeMarkerColor(
    AIR_ASSET_STATUS_COLORS[asset.operational_status],
    "#8b949e"
  );
  return divIcon({
    className: "air-asset-map-icon",
    html:
      '<span class="air-asset-marker">' +
      `<svg viewBox="0 0 48 48" width="36" height="36" fill="${colour}" ` +
      'stroke="#ffffff" stroke-width="1.25" stroke-linejoin="round" aria-hidden="true">' +
      `${AIR_ASSET_GLYPHS[group.icon] || AIR_ASSET_GLYPHS.other}</svg>` +
      `<span class="air-asset-status-dot" style="background:${statusColour}"></span>` +
      "</span>",
    iconSize: [36, 36],
    iconAnchor: [18, 18],
    popupAnchor: [0, -17],
  });
}

// Friendly troops carry the unit particulars an air mission needs when it names
// the unit it supports; the other feature types use only name and description.
const EMPTY_FEATURE = {
  name: "",
  description: "",
  service_category: "",
  unit_category: "",
  acronym: "",
  address: "",
  contact_number: "",
};

const SERVICE_CATEGORIES = [
  { value: "army", label: "Philippine Army" },
  { value: "navy", label: "Philippine Navy" },
  { value: "pnp", label: "Philippine National Police" },
  { value: "coast_guard", label: "Coast Guard" },
];

const UNIT_CATEGORIES = [
  { value: "division", label: "Division" },
  { value: "brigade", label: "Brigade" },
  { value: "battalion", label: "Battalion" },
  { value: "company", label: "Company" },
];

function featureCentre(feature) {
  if (feature.latitude != null && feature.longitude != null) {
    return [feature.latitude, feature.longitude];
  }
  const geometry = feature.geometry || {};
  if (geometry.type === "Point" && (geometry.coordinates || []).length >= 2) {
    return [geometry.coordinates[1], geometry.coordinates[0]];
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

export default function OperationalMappingPage() {
  const { permissions } = useAuth();
  const { resolvedTheme } = useTheme();
  const canEdit = Boolean(permissions.manage_aor || permissions.maintain_aor);

  const { data, loading, error, reload } = useApi("/operational-mapping/?page_size=500");
  const config = useApi("/map/config/");
  // Unit pins carry each unit's own seal and are limited to the caller's
  // authorised scope by the server.
  const unitPins = useApi("/units/markers/");
  // Airports and air strips, from the shared airfield register.
  const airfields = useApi("/airfields/?page_size=500");
  const airfieldRows = airfields.data?.results ?? [];
  // Air assets plot at the MGRS grid embedded in their recorded current location.
  const airAssets = useApi("/inventory/air-assets/?page_size=200");
  const airAssetRows = airAssets.data?.results ?? [];
  const locatedAirAssets = airAssetRows.filter(
    (asset) =>
      asset.map_location?.latitude != null && asset.map_location?.longitude != null
  );
  const airAssetGroups = AIR_ASSET_GROUPS.map((group) => ({
    ...group,
    count: locatedAirAssets.filter(
      (asset) => airAssetGroup(asset).value === group.value
    ).length,
  })).filter((group) => group.count > 0);

  const [activeType, setActiveType] = useState("");
  const [vertices, setVertices] = useState([]);
  const [pending, setPending] = useState(null);
  const [saveOpen, setSaveOpen] = useState(false);
  const [form, setForm] = useState({ ...EMPTY_FEATURE });
  const [editing, setEditing] = useState(null);
  const [editForm, setEditForm] = useState({ ...EMPTY_FEATURE });
  const [confirming, setConfirming] = useState(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");
  const [focus, setFocus] = useState(null);
  const [featuresPage, setFeaturesPage] = useState(1);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState([]);
  const [searching, setSearching] = useState(false);
  const [searchNote, setSearchNote] = useState("");
  const [searchPick, setSearchPick] = useState(null);
  // Legend checkboxes hide data layers without discarding them; drawing and
  // search markers stay live while a layer is hidden.
  const [visibleLayers, setVisibleLayers] = useState({
    units: true,
    air_assets: true,
    friendly_troop: true,
    frequent_location: true,
    operational_area: true,
    airport: true,
    airstrip: true,
  });

  const [basemapId, setBasemapId] = useState(() => readStored(BASEMAP_STORAGE_KEY, ""));
  const [basemapFailure, setBasemapFailure] = useState("");

  const features = data?.results ?? [];
  const basemap = useMemo(
    () => resolveBasemap(config.data, basemapId, resolvedTheme),
    [config.data, basemapId, resolvedTheme]
  );

  function toggleLayer(layer) {
    setVisibleLayers((current) => ({ ...current, [layer]: !current[layer] }));
  }

  useEffect(() => {
    setBasemapFailure("");
  }, [basemap?.id]);

  // Search places, official boundaries and locations already recorded in TOWEMS,
  // so an operator can plot from a name instead of hunting for it on the map.
  // The external place index degrades to empty, leaving the offline sources.
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
          api.get(`/admin-areas/?q=${encodeURIComponent(term)}&limit=8`),
          api.get(`/gis/search/?q=${encodeURIComponent(term)}`).catch(() => ({ results: [] })),
          api.get(`/gis/places/?q=${encodeURIComponent(term)}`).catch(() => ({ results: [] })),
        ]);
        if (!active) return;
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
          }));
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
          }));
        const combined = [...placeHits, ...geoHits, ...known];
        setHits(combined);
        setSearchNote(
          combined.length
            ? ""
            : (found.note || "No place, province, city or recorded location matches that name.")
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

  function chooseBasemap(id) {
    setBasemapId(id);
    writeStored(BASEMAP_STORAGE_KEY, id);
    setBasemapFailure("");
  }

  function cancelPlot() {
    setActiveType("");
    setVertices([]);
    setPending(null);
    setSaveOpen(false);
    setForm({ ...EMPTY_FEATURE });
    setActionError("");
  }

  function startPlot(type) {
    setActionError("");
    setNotice("");
    if (type === "operational_area") {
      setActiveType(type);
      setVertices([]);
      setPending(null);
      return;
    }
    setActiveType(type);
    // A searched location is already a point: offer to save it straight away
    // instead of making the operator find the same spot on the map again.
    if (searchPick) {
      setPending({
        feature_type: type,
        geometry: {
          type: "Point",
          coordinates: [searchPick.lng, searchPick.lat],
        },
      });
      setForm({ ...EMPTY_FEATURE, name: searchPick.label || "" });
      setSaveOpen(true);
    }
  }

  function handleMapClick(latlng) {
    if (!activeType) return;
    if (activeType === "operational_area") {
      setVertices((current) => [...current, [latlng.lat, latlng.lng]]);
      return;
    }
    setPending({
      feature_type: activeType,
      geometry: { type: "Point", coordinates: [latlng.lng, latlng.lat] },
    });
    setForm({ ...EMPTY_FEATURE });
    setActionError("");
    setSaveOpen(true);
  }

  function chooseHit(hit) {
    const centre = [Number(hit.latitude), Number(hit.longitude)];
    setFocus({
      center: centre,
      bbox: hit.bbox || null,
      zoom: hit.bbox ? 11 : 15,
      key: Date.now(),
    });
    setSearchPick({ lat: centre[0], lng: centre[1], label: hit.label });
    if (activeType === "operational_area") {
      setSearchNote(`${hit.label} centred. Click the map to add its vertices.`);
      return;
    }
    if (activeType) {
      setPending({
        feature_type: activeType,
        geometry: { type: "Point", coordinates: [centre[1], centre[0]] },
      });
      setForm({ ...EMPTY_FEATURE, name: hit.label });
      setActionError("");
      setSaveOpen(true);
      return;
    }
    setSearchNote(
      `${hit.label} selected. Choose Plot troops or Plot location to place it here.`
    );
  }

  function openAreaSave() {
    if (vertices.length < 3) return;
    setPending({ feature_type: "operational_area" });
    setForm({ ...EMPTY_FEATURE });
    setActionError("");
    setSaveOpen(true);
  }

  async function saveFeature(event) {
    event?.preventDefault?.();
    if (!pending) return;
    if (!form.name.trim()) {
      setActionError("Give the plotted feature a name.");
      return;
    }
    let geometry = pending.geometry;
    if (pending.feature_type === "operational_area") {
      if (vertices.length < 3) {
        setActionError("Add at least three vertices before saving the area.");
        return;
      }
      const ring = vertices.map(([lat, lng]) => [lng, lat]);
      ring.push([...ring[0]]);
      geometry = { type: "Polygon", coordinates: [ring] };
    }
    setBusy(true);
    setActionError("");
    try {
      // Unit particulars are sent for friendly troops only; the other feature
      // types have no unit, so their empty strings would be noise.
      const troop = pending.feature_type === "friendly_troop";
      await api.post("/operational-mapping/", {
        name: form.name.trim(),
        description: form.description,
        feature_type: pending.feature_type,
        geometry,
        ...(troop
          ? {
              service_category: form.service_category,
              unit_category: form.unit_category,
              acronym: form.acronym.trim(),
              address: form.address.trim(),
              contact_number: form.contact_number.trim(),
            }
          : {}),
      });
      const label = TYPE_BY_VALUE[pending.feature_type]?.label || "Feature";
      setSearchPick(null);
      cancelPlot();
      setNotice(`${label} saved to operational mapping.`);
      reload();
    } catch (err) {
      setActionError(err.message);
    } finally {
      setBusy(false);
    }
  }

  function openEdit(feature) {
    setEditing(feature);
    setEditForm({
      ...EMPTY_FEATURE,
      name: feature.name,
      description: feature.description || "",
      service_category: feature.service_category || "",
      unit_category: feature.unit_category || "",
      acronym: feature.acronym || "",
      address: feature.address || "",
      contact_number: feature.contact_number || "",
    });
    setActionError("");
  }

  async function saveEdit(event) {
    event?.preventDefault?.();
    if (!editing) return;
    if (!editForm.name.trim()) {
      setActionError("Give the plotted feature a name.");
      return;
    }
    setBusy(true);
    setActionError("");
    try {
      await api.patch(`/operational-mapping/${editing.id}/`, {
        name: editForm.name.trim(),
        description: editForm.description,
        ...(editing.feature_type === "friendly_troop"
          ? {
              service_category: editForm.service_category,
              unit_category: editForm.unit_category,
              acronym: editForm.acronym.trim(),
              address: editForm.address.trim(),
              contact_number: editForm.contact_number.trim(),
            }
          : {}),
      });
      setNotice(`${editForm.name.trim()} updated.`);
      setEditing(null);
      reload();
    } catch (err) {
      setActionError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function removeFeature() {
    if (!confirming) return;
    setBusy(true);
    setActionError("");
    try {
      await api.del(`/operational-mapping/${confirming.id}/`);
      setNotice(`${confirming.name} removed from operational mapping.`);
      setConfirming(null);
      reload();
    } catch (err) {
      setActionError(err.message);
    } finally {
      setBusy(false);
    }
  }

  const pendingType = pending ? TYPE_BY_VALUE[pending.feature_type] : null;
  // The map always shows every saved feature; only the list underneath is
  // paginated, ten records at a time.
  const featurePages = Math.max(1, Math.ceil(features.length / FEATURES_PAGE_SIZE));
  const featurePage = Math.min(featuresPage, featurePages);
  const pagedFeatures = features.slice(
    (featurePage - 1) * FEATURES_PAGE_SIZE,
    featurePage * FEATURES_PAGE_SIZE
  );

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Operational Mapping</h1>
          <p>
            Plot friendly troop dispositions, frequently searched locations and operational areas.
            Everything saved here stays in the database for planning and coordination.
          </p>
        </div>
        {canEdit && (
          <div className="btn-row">
            <Button
              variant="ghost"
              aria-pressed={activeType === "friendly_troop"}
              onClick={() => startPlot("friendly_troop")}
            >
              <PlotGlyph name="troops" />
              Plot troops
            </Button>
            <Button
              variant="ghost"
              aria-pressed={activeType === "frequent_location"}
              onClick={() => startPlot("frequent_location")}
            >
              <PlotGlyph name="location" />
              Plot location
            </Button>
            <Button
              variant="ghost"
              aria-pressed={activeType === "operational_area"}
              onClick={() => startPlot("operational_area")}
            >
              <PlotGlyph name="area" />
              Draw operational area
            </Button>
          </div>
        )}
      </div>

      <ErrorBanner message={error || config.error || actionError} onRetry={reload} />
      {notice && <div className="alert alert-success">{notice}</div>}
      {!canEdit && (
        <div className="alert alert-warning">
          Operational mapping is maintained by the unit Operations Center and Administrators. You can
          read the saved picture but not change it.
        </div>
      )}
      {basemapFailure && (
        <div className="alert alert-warning">
          <span>
            The “{basemapFailure}” basemap could not be reached. Saved operational data is unaffected —
            pick another map style.
          </span>
        </div>
      )}

      <div className="map-shell">
        <div>
          <Card title="Legend">
            <ul className="map-legend">
              <li className={visibleLayers.units ? "" : "is-hidden"}>
                <label className="legend-toggle">
                  <input
                    type="checkbox"
                    checked={visibleLayers.units}
                    onChange={() => toggleLayer("units")}
                  />
                  <LegendIcon name="unit" color="var(--accent)" />
                  <span>Unit location (unit seal)</span>
                </label>
              </li>
              <li className="map-legend-heading">
                Disposition and Locations of Air Assets
              </li>
              <li className={visibleLayers.air_assets ? "" : "is-hidden"}>
                <label className="legend-toggle">
                  <input
                    type="checkbox"
                    checked={visibleLayers.air_assets}
                    onChange={() => toggleLayer("air_assets")}
                  />
                  <LegendIcon name="plane" color="var(--text-muted)" />
                  <span>Air assets</span>
                  <span className="hint">({locatedAirAssets.length})</span>
                </label>
              </li>
              {airAssetGroups.map((group) => (
                <li
                  key={`air-asset-${group.value}`}
                  className={`air-asset-legend-row ${
                    visibleLayers.air_assets ? "" : "is-hidden"
                  }`}
                >
                  <span
                    className="air-asset-legend-glyph"
                    style={{ color: group.color }}
                  >
                    <AirAssetGlyph icon={group.icon} size={14} />
                  </span>
                  <span>{group.label}</span>
                  <span className="hint">({group.count})</span>
                </li>
              ))}
              {airAssets.error && (
                <li className="air-asset-legend-row">
                  <span className="hint">Air assets could not be loaded.</span>
                </li>
              )}
              {!airAssets.loading && !airAssets.error && locatedAirAssets.length === 0 && (
                <li className="air-asset-legend-row">
                  <span className="hint">No air asset has a mapped current location.</span>
                </li>
              )}
              {FEATURE_TYPES.map((entry) => (
                <li
                  key={entry.value}
                  className={visibleLayers[entry.value] ? "" : "is-hidden"}
                >
                  <label className="legend-toggle">
                    <input
                      type="checkbox"
                      checked={visibleLayers[entry.value] !== false}
                      onChange={() => toggleLayer(entry.value)}
                    />
                    <LegendIcon name={entry.icon} color={entry.color} />
                    <span>{entry.label}</span>
                  </label>
                </li>
              ))}
              {AIRFIELD_TYPES.map((entry) => (
                <li
                  key={entry.value}
                  className={visibleLayers[entry.value] ? "" : "is-hidden"}
                >
                  <label className="legend-toggle">
                    <input
                      type="checkbox"
                      checked={visibleLayers[entry.value] !== false}
                      onChange={() => toggleLayer(entry.value)}
                    />
                    <LegendIcon name={entry.icon} color={entry.color} />
                    <span>{entry.label}</span>
                    <span className="hint">
                      ({airfieldRows.filter((row) => row.kind === entry.value).length})
                    </span>
                  </label>
                </li>
              ))}
              <li>
                <span className="legend-line" style={{ borderColor: "#f0b429", borderStyle: "dashed" }} />
                Area being drawn
              </li>
            </ul>
          </Card>

          <Card title="Find a location">
            <Field label="Search place, boundary or TOWEMS record">
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="e.g. SM City Butuan, Camp Evangelista"
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
                    onClick={() => chooseHit(hit)}
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
          </Card>

        </div>

        <div className="map-canvas">
          <div className="map-style-overlay">
            <span className="hint">Map style</span>
            <BasemapControl
              config={config.data}
              value={basemap?.id}
              onChange={chooseBasemap}
            />
          </div>

          <MapContainer center={DEFAULT_CENTER} zoom={6} scrollWheelZoom>
            <MapBasemapLayer basemap={basemap} onError={(id) => setBasemapFailure(id)} />
            <ClickCatcher onClick={handleMapClick} />
            <MapFocus target={focus} />
            <UnitMarkerLayer
              units={visibleLayers.units ? unitPins.data?.results ?? [] : []}
              compact
            />

            {features.map((feature) => {
              if (visibleLayers[feature.feature_type] === false) return null;
              const type = TYPE_BY_VALUE[feature.feature_type];
              const colour = safeMarkerColor(
                feature.color,
                type?.color || "#4f9cf9"
              );
              if (feature.feature_type === "operational_area") {
                const positions = polygonLatLngs(feature.geometry);
                if (positions.length < 3) return null;
                return (
                  <Polygon
                    key={feature.id}
                    positions={positions}
                    pathOptions={{ color: colour, weight: 2, fillOpacity: 0.18 }}
                  >
                    <Popup>
                      <FeaturePopup feature={feature} />
                    </Popup>
                  </Polygon>
                );
              }
              const centre = featureCentre(feature);
              if (!centre) return null;
              return (
                <Marker
                  key={feature.id}
                  position={centre}
                  icon={
                    feature.color
                      ? pinMarker(type?.icon || "location", colour)
                      : FEATURE_MARKERS[feature.feature_type] ||
                        FEATURE_MARKERS.friendly_troop
                  }
                >
                  <Popup>
                    <FeaturePopup feature={feature} />
                  </Popup>
                </Marker>
              );
            })}

            {airfieldRows.map((field) => {
              if (visibleLayers[field.kind] === false) return null;
              return (
                <Marker
                  key={`airfield-${field.id}`}
                  position={[field.latitude, field.longitude]}
                  icon={
                    AIRFIELD_MARKERS[field.kind] || AIRFIELD_MARKERS.airport
                  }
                >
                  <Popup>
                    <strong>
                      {field.code} — {field.name}
                    </strong>
                    <br />
                    {field.kind_label}
                    {field.location ? ` · ${field.location}` : ""}
                    <br />
                    {field.runway_length_m
                      ? `Runway ${field.runway_length_m} m × ${field.runway_width_m ?? "—"} m`
                      : "Runway data not recorded"}
                    {field.runway_orientation ? ` · ${field.runway_orientation}` : ""}
                    <br />
                    <span className="hint">Grid {field.grid_coordinate || "not recorded"}</span>
                  </Popup>
                </Marker>
              );
            })}

            {visibleLayers.air_assets &&
              locatedAirAssets.map((asset) => {
                const point = asset.map_location;
                const group = airAssetGroup(asset);
                return (
                  <Marker
                    key={`air-asset-${asset.id}`}
                    position={[Number(point.latitude), Number(point.longitude)]}
                    icon={aircraftMarker(asset)}
                    zIndexOffset={500000}
                  >
                    <Popup>
                      <AirAssetPopup asset={asset} group={group} />
                    </Popup>
                  </Marker>
                );
              })}

            {vertices.length > 0 && (
              <Polygon
                positions={vertices}
                pathOptions={{ color: "#f0b429", dashArray: "6 4", fillOpacity: 0.1 }}
              />
            )}
            {vertices.map(([lat, lng], index) => (
              <CircleMarker
                key={`vertex-${index}`}
                center={[lat, lng]}
                radius={5}
                pathOptions={{ color: "#f0b429" }}
              />
            ))}

            {searchPick && (
              <CircleMarker
                center={[searchPick.lat, searchPick.lng]}
                radius={10}
                pathOptions={{
                  color: "#ffffff",
                  weight: 2,
                  fillColor: "#f0b429",
                  fillOpacity: 0.4,
                }}
              >
                <Popup>{searchPick.label}</Popup>
              </CircleMarker>
            )}

            {pending?.feature_type !== "operational_area" && pending && (
              <Marker
                position={[pending.geometry.coordinates[1], pending.geometry.coordinates[0]]}
                icon={pinMarker(
                  pendingType?.icon || "location",
                  pendingType?.color || "#4f9cf9"
                )}
              />
            )}
          </MapContainer>
        </div>
      </div>

      {activeType && (
        <div className="alert alert-warning" style={{ marginTop: 16 }}>
          <span>
            {activeType === "operational_area"
              ? `Click the map to add operational-area vertices (${vertices.length} added, 3 minimum).`
              : `Click the map to place the ${TYPE_BY_VALUE[activeType].label.toLowerCase()}.`}
          </span>
          <div className="btn-row">
            {activeType === "operational_area" && (
              <Button size="sm" disabled={vertices.length < 3} onClick={openAreaSave}>
                Save area
              </Button>
            )}
            <Button size="sm" variant="ghost" onClick={cancelPlot}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      <CollapsibleCard
        title={`Saved features (${features.length})`}
        summary={`${features.length} plotted feature${features.length === 1 ? "" : "s"}`}
      >
        {loading && !data ? (
          <Loading />
        ) : (
          <>
            <Table
              rows={pagedFeatures}
              empty="Nothing has been plotted yet."
            stack
            columns={[
              {
                key: "name",
                label: "Feature",
                render: (row) => (
                  <>
                    <strong>{row.name}</strong>
                    {row.acronym ? ` (${row.acronym})` : ""}
                    {row.description ? (
                      <>
                        <br />
                        <span className="hint">{row.description}</span>
                      </>
                    ) : null}
                  </>
                ),
              },
              {
                key: "feature_type_label",
                label: "Type",
                render: (row) => <Badge tone="info">{row.feature_type_label}</Badge>,
              },
              {
                key: "location",
                label: "Location",
                render: (row) =>
                  row.latitude != null && row.longitude != null
                    ? `${row.latitude.toFixed(5)}, ${row.longitude.toFixed(5)}`
                    : "—",
              },
              {
                key: "unit",
                label: "Unit",
                render: (row) => row.unit_acronym || row.unit_name || "Unassigned",
              },
              {
                key: "updated_at",
                label: "Updated",
                render: (row) => formatDateTime(row.updated_at),
              },
              {
                key: "actions",
                label: "",
                render: (row) => {
                  const centre = featureCentre(row);
                  return (
                    <div className="btn-row">
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={!centre}
                        onClick={() =>
                          setFocus({ center: centre, bbox: null, zoom: 13, key: Date.now() })
                        }
                      >
                        Show
                      </Button>
                      {canEdit && (
                        <>
                          <Button size="sm" variant="ghost" onClick={() => openEdit(row)}>
                            Edit
                          </Button>
                          <Button
                            size="sm"
                            variant="danger"
                            onClick={() => setConfirming(row)}
                          >
                            Delete
                          </Button>
                        </>
                      )}
                    </div>
                  );
                },
              },
              ]}
            />
            <Pagination
              page={featurePage}
              pageSize={FEATURES_PAGE_SIZE}
              count={features.length}
              onPage={setFeaturesPage}
            />
          </>
        )}
      </CollapsibleCard>

      <CollapsibleCard
        title={`Airports & air strips (${airfieldRows.length})`}
        summary={`${airfieldRows.length} airfield${
          airfieldRows.length === 1 ? "" : "s"
        } in the shared register`}
      >
        <Table
          rows={airfieldRows}
          empty="The airfield register is empty."
          stack
          columns={[
            { key: "code", label: "Code" },
            { key: "name", label: "Name" },
            {
              key: "kind_label",
              label: "Type",
              render: (row) => (
                <Badge tone={row.kind === "airport" ? "danger" : "info"}>
                  {row.kind_label}
                </Badge>
              ),
            },
            {
              key: "location",
              label: "Location",
              render: (row) => row.location || "—",
            },
            {
              key: "grid_coordinate",
              label: "Grid",
              render: (row) => row.grid_coordinate || "—",
            },
            {
              key: "runway",
              label: "Runway",
              render: (row) =>
                row.runway_length_m
                  ? `${row.runway_length_m} × ${row.runway_width_m ?? "—"} m · ${
                      row.runway_orientation || "—"
                    }`
                  : "—",
            },
          ]}
        />
      </CollapsibleCard>

      {saveOpen && pending && (
        <Modal
          title={`Save ${pendingType?.label || "feature"}`}
          onClose={cancelPlot}
          footer={
            <>
              <Button variant="ghost" onClick={cancelPlot} disabled={busy}>
                Cancel
              </Button>
              <Button onClick={saveFeature} disabled={busy}>
                Save to map
              </Button>
            </>
          }
        >
          <ErrorBanner message={actionError} />
          <form onSubmit={saveFeature}>
            <Field label="Name" required hint="How this feature reads on the map and in the list.">
              <Input
                value={form.name}
                onChange={(event) => setForm({ ...form, name: event.target.value })}
                placeholder={
                  pending.feature_type === "friendly_troop"
                    ? "e.g. 1st Squad — north ridge"
                    : pending.feature_type === "frequent_location"
                      ? "e.g. Alternate landing site"
                      : "e.g. Objective area"
                }
                required
              />
            </Field>
            <Field label="Description">
              <Textarea
                value={form.description}
                onChange={(event) => setForm({ ...form, description: event.target.value })}
                placeholder="What is plotted here, and anything the next planner should know."
              />
            </Field>
            {pending.feature_type === "friendly_troop" && (
              <>
                <div className="form-grid">
                  <Field label="Service category">
                    <Select
                      value={form.service_category}
                      onChange={(event) =>
                        setForm({ ...form, service_category: event.target.value })
                      }
                    >
                      <option value="">Not specified</option>
                      {SERVICE_CATEGORIES.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </Select>
                  </Field>
                  <Field label="Unit category">
                    <Select
                      value={form.unit_category}
                      onChange={(event) =>
                        setForm({ ...form, unit_category: event.target.value })
                      }
                    >
                      <option value="">Not specified</option>
                      {UNIT_CATEGORIES.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </Select>
                  </Field>
                  <Field label="Acronym" hint="How the unit is cited on a mission.">
                    <Input
                      value={form.acronym}
                      onChange={(event) => setForm({ ...form, acronym: event.target.value })}
                      placeholder="e.g. 10ID"
                    />
                  </Field>
                  <Field label="Unit address">
                    <Input
                      value={form.address}
                      onChange={(event) => setForm({ ...form, address: event.target.value })}
                    />
                  </Field>
                  <Field label="Contact number" hint="Optional.">
                    <Input
                      value={form.contact_number}
                      onChange={(event) =>
                        setForm({ ...form, contact_number: event.target.value })
                      }
                    />
                  </Field>
                </div>
                <p className="hint">
                  The MGRS is derived from the plotted point when the unit is saved.
                </p>
              </>
            )}
          </form>
        </Modal>
      )}

      {editing && (
        <Modal
          title={`Edit ${editing.name}`}
          onClose={() => setEditing(null)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setEditing(null)} disabled={busy}>
                Cancel
              </Button>
              <Button onClick={saveEdit} disabled={busy}>
                Save changes
              </Button>
            </>
          }
        >
          <ErrorBanner message={actionError} />
          <form onSubmit={saveEdit}>
            <Field label="Name" required>
              <Input
                value={editForm.name}
                onChange={(event) => setEditForm({ ...editForm, name: event.target.value })}
                required
              />
            </Field>
            <Field label="Description">
              <Textarea
                value={editForm.description}
                onChange={(event) =>
                  setEditForm({ ...editForm, description: event.target.value })
                }
              />
            </Field>
            {editing.feature_type === "friendly_troop" && (
              <div className="form-grid">
                <Field label="Service category">
                  <Select
                    value={editForm.service_category}
                    onChange={(event) =>
                      setEditForm({ ...editForm, service_category: event.target.value })
                    }
                  >
                    <option value="">Not specified</option>
                    {SERVICE_CATEGORIES.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label="Unit category">
                  <Select
                    value={editForm.unit_category}
                    onChange={(event) =>
                      setEditForm({ ...editForm, unit_category: event.target.value })
                    }
                  >
                    <option value="">Not specified</option>
                    {UNIT_CATEGORIES.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label="Acronym">
                  <Input
                    value={editForm.acronym}
                    onChange={(event) =>
                      setEditForm({ ...editForm, acronym: event.target.value })
                    }
                  />
                </Field>
                <Field label="Unit address">
                  <Input
                    value={editForm.address}
                    onChange={(event) =>
                      setEditForm({ ...editForm, address: event.target.value })
                    }
                  />
                </Field>
                <Field label="Contact number" hint="Optional.">
                  <Input
                    value={editForm.contact_number}
                    onChange={(event) =>
                      setEditForm({ ...editForm, contact_number: event.target.value })
                    }
                  />
                </Field>
              </div>
            )}
            <p className="hint">
              The plotted position or outline stays as saved. Remove and re-plot the feature to move
              it.
            </p>
          </form>
        </Modal>
      )}

      {confirming && (
        <Modal
          title={`Remove ${confirming.name}?`}
          onClose={() => setConfirming(null)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setConfirming(null)} disabled={busy}>
                Cancel
              </Button>
              <Button variant="danger" onClick={removeFeature} disabled={busy}>
                Remove feature
              </Button>
            </>
          }
        >
          <ErrorBanner message={actionError} />
          <p>
            This permanently removes the plotted feature from operational mapping. The audit trail
            keeps a record of the change.
          </p>
        </Modal>
      )}
    </>
  );
}

function AirAssetPopup({ asset, group }) {
  const point = asset.map_location;
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
      {asset.opcon_unit_acronym ? ` · OPCON ${asset.opcon_unit_acronym}` : ""}
      {asset.current_location && (
        <>
          <br />
          {asset.current_location}
        </>
      )}
      {point?.mgrs && (
        <>
          <br />
          <span className="hint">Grid {point.mgrs}</span>
        </>
      )}
    </>
  );
}

function FeaturePopup({ feature }) {
  return (
    <>
      <strong>{feature.name}</strong>
      <br />
      {feature.feature_type_label}
      {feature.description && (
        <>
          <br />
          {feature.description}
        </>
      )}
      {feature.feature_type === "friendly_troop" && (
        <>
          {[feature.acronym, feature.unit_category_label, feature.service_category_label]
            .filter(Boolean)
            .length > 0 && (
            <>
              <br />
              {[feature.acronym, feature.unit_category_label, feature.service_category_label]
                .filter(Boolean)
                .join(" · ")}
            </>
          )}
          {feature.address && (
            <>
              <br />
              {feature.address}
            </>
          )}
          {feature.mgrs && (
            <>
              <br />
              Grid {feature.mgrs}
            </>
          )}
          {feature.contact_number && (
            <>
              <br />
              {feature.contact_number}
            </>
          )}
        </>
      )}
      <br />
      <span className="hint">
        {feature.created_by_name ? `Plotted by ${feature.created_by_name}` : "Plotted record"}
        {feature.updated_at ? ` · ${formatDateTime(feature.updated_at)}` : ""}
      </span>
    </>
  );
}

function ClickCatcher({ onClick }) {
  useMapEvents({
    click(event) {
      onClick(event.latlng);
    },
  });
  return null;
}

function MapFocus({ target }) {
  const map = useMap();
  useEffect(() => {
    if (!target) return;
    if (Array.isArray(target.bbox) && target.bbox.length === 2) {
      map.fitBounds(target.bbox, { animate: true, padding: [20, 20] });
      return;
    }
    if (target.center) {
      map.setView(target.center, target.zoom ?? Math.max(map.getZoom(), 12), {
        animate: true,
      });
    }
  }, [target, map]);
  return null;
}
