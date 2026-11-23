import { useCallback, useEffect, useMemo, useState } from "react";
import L from "leaflet";
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
  Select,
} from "../components/ui.jsx";
import BasemapControl from "../components/map/BasemapControl.jsx";
import MapBasemapLayer from "../components/map/MapBasemapLayer.jsx";
import MapLayersPanel from "../components/map/MapLayersPanel.jsx";
import { AdminBoundariesLayer, BoundaryPreview } from "../components/map/Boundaries.jsx";
import UnitMarkerLayer from "../components/map/UnitMarkerLayer.jsx";
import {
  FuelGlyph,
  FuelPopup,
  fuelMarker,
  groupFuelPlaces,
} from "../map/fuel.jsx";
import ManageAORModal from "./ManageAORModal.jsx";
import AORViewer from "./AORViewer.jsx";
import { useApi } from "../hooks/useApi.js";
import { formatDateTime } from "../utils/format.js";
import { useTheme } from "../theme/ThemeContext.jsx";
import {
  BASEMAP_STORAGE_KEY,
  LAYERS_STORAGE_KEY,
  availableBasemaps,
  readStored,
  resolveBasemap,
  writeStored,
} from "../map/config.js";

const DEFAULT_CENTER = [12.8797, 121.774];
const AOR_PAGE_LAYERS = ["aor", "units", "admin_boundaries"];

export default function AORPage() {
  const { permissions, user } = useAuth();
  const { resolvedTheme } = useTheme();
  // The Administrator manages the coverage record. Command, operations and
  // safety posts get the map-only viewer below, with everything scoped by the
  // server; nothing else fetches the management data.
  const isAdmin = Boolean(permissions.is_admin);
  const canView = Boolean(permissions.can_view_aor);
  const { data, loading, error, reload } = useApi("/aor/?page_size=200", {
    enabled: isAdmin,
  });
  const units = useApi("/units/?is_active=true&page_size=200", { enabled: isAdmin });
  // Unit pins are already limited to the caller's authorised scope server-side.
  const unitPins = useApi("/units/markers/", { enabled: isAdmin });
  const config = useApi("/map/config/", { enabled: isAdmin });
  // Roll-up totals only: subordinate coverage counts towards its mother unit in
  // the listing, without drawing a second polygon on the map.
  const coverage = useApi("/aor/coverage/", { enabled: isAdmin });
  // Fuel pins come from the same scoped operational picture the viewer reads,
  // so both AOR pages draw the same aviation-fuel stores, in the same places.
  const fuelPicture = useApi("/gis/operational-picture/?layers=fuel", {
    enabled: isAdmin,
  });

  const canManage = Boolean(permissions.manage_aor);
  // The unit's Operations Center may tidy up an existing AOR (rename, recolour,
  // archive, restore) but cannot move coverage between units or delete records.
  const canMaintain = canManage || Boolean(permissions.maintain_aor);

  const [drawing, setDrawing] = useState(false);
  const [points, setPoints] = useState([]);
  const [picked, setPicked] = useState(null);
  const [focus, setFocus] = useState(null);
  const [fitTarget, setFitTarget] = useState(null);
  const [saveOpen, setSaveOpen] = useState(false);
  const [form, setForm] = useState({ name: "", code: "", unit: "", color: "#4f9cf9", description: "" });
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  const [manageOpen, setManageOpen] = useState(false);
  const [previewFeatures, setPreviewFeatures] = useState([]);
  const [viewing, setViewing] = useState(null);
  const [editing, setEditing] = useState(null);
  const [editForm, setEditForm] = useState({ name: "", code: "", unit: "", color: "", description: "" });
  const [confirming, setConfirming] = useState(null);
  const [confirmReason, setConfirmReason] = useState("");
  const [rowBusy, setRowBusy] = useState(false);

  const [mgrs, setMgrs] = useState("");
  const [search, setSearch] = useState("");
  const [results, setResults] = useState([]);
  const [containsInfo, setContainsInfo] = useState(null);

  const [basemapId, setBasemapId] = useState(() => readStored(BASEMAP_STORAGE_KEY, ""));
  const [visibleLayers, setVisibleLayers] = useState(() => {
    const stored = readStored(LAYERS_STORAGE_KEY, null);
    if (!Array.isArray(stored)) return stored;
    // A layer added after this operator last chose their layers still appears.
    return stored.includes("units") ? stored : [...stored, "units"];
  });
  const [basemapFailure, setBasemapFailure] = useState("");
  // Like every other AOR layer, the fuel pins start off: the map opens on the
  // unit picture and the operator asks for stores.
  const [showFuel, setShowFuel] = useState(false);

  const aors = data?.results ?? [];
  const fuelPositions = fuelPicture.data?.fuel_positions ?? [];
  const fuelPlaces = useMemo(
    () => groupFuelPlaces(fuelPositions),
    [fuelPositions]
  );
  const polygons = useMemo(
    () =>
      aors
        .filter((aor) => aor.geometry && aor.is_active)
        .map((aor) => ({
          id: aor.id,
          name: aor.name,
          code: aor.code,
          unit: aor.unit_acronym || aor.unit_name,
          // The mother unit this area rolls up to, shown as a description line.
          parentUnit: aor.unit_parent_acronym || aor.unit_parent_name || "",
          color: aor.color || "#4f9cf9",
          positions: toLeafletPositions(aor.geometry),
        })),
    [aors]
  );

  const layerCatalogue = useMemo(
    () => (config.data?.layers ?? []).filter((layer) => AOR_PAGE_LAYERS.includes(layer.id)),
    [config.data]
  );

  useEffect(() => {
    if (visibleLayers !== null) return;
    const defaults = layerCatalogue.filter((layer) => layer.default_on).map((layer) => layer.id);
    setVisibleLayers(defaults.length ? defaults : ["aor"]);
  }, [layerCatalogue, visibleLayers]);

  const activeLayers = visibleLayers ?? ["aor"];
  const basemap = useMemo(
    () => resolveBasemap(config.data, basemapId, resolvedTheme),
    [config.data, basemapId, resolvedTheme]
  );

  useEffect(() => {
    // Follow the theme only while the operator has not chosen a style.
    setBasemapFailure("");
  }, [basemap?.id]);

  function chooseBasemap(id) {
    setBasemapId(id);
    writeStored(BASEMAP_STORAGE_KEY, id);
    setBasemapFailure("");
  }

  function toggleLayer(id, on) {
    const next = on
      ? [...new Set([...activeLayers, id])]
      : activeLayers.filter((layer) => layer !== id);
    setVisibleLayers(next);
    writeStored(LAYERS_STORAGE_KEY, next);
  }

  async function convertMgrs() {
    setActionError("");
    const payload = mgrs
      ? { mgrs }
      : picked
        ? { latitude: picked.lat, longitude: picked.lng }
        : null;
    if (!payload) {
      setActionError("Enter an MGRS grid, or click a point on the map first.");
      return;
    }
    try {
      const result = await api.post("/gis/mgrs/", payload);
      setPicked({ lat: result.latitude, lng: result.longitude });
      setFocus([result.latitude, result.longitude]);
      setMgrs(result.mgrs);
    } catch (err) {
      setActionError(err.message);
    }
  }

  async function runSearch() {
    setActionError("");
    try {
      const result = await api.get(`/gis/search/?q=${encodeURIComponent(search)}`);
      setResults(result.results ?? []);
      if (!result.results?.length) setNotice("No matching locations in TOWEMS records.");
    } catch (err) {
      setActionError(err.message);
    }
  }

  async function lookupAor() {
    if (!picked) return;
    setActionError("");
    try {
      const result = await api.get(
        `/aor/at-point/?latitude=${picked.lat}&longitude=${picked.lng}`
      );
      setContainsInfo(result);
    } catch (err) {
      setActionError(err.message);
    }
  }

  async function saveAor(event) {
    event.preventDefault();
    setBusy(true);
    setActionError("");
    try {
      const ring = points.map(([lat, lng]) => [lng, lat]);
      ring.push(ring[0]);
      await api.post("/aor/", {
        name: form.name,
        code: form.code,
        unit: form.unit ? Number(form.unit) : null,
        color: form.color,
        description: form.description,
        geometry: { type: "Polygon", coordinates: [ring] },
      });
      setSaveOpen(false);
      setDrawing(false);
      setPoints([]);
      setForm({ name: "", code: "", unit: "", color: "#4f9cf9", description: "" });
      setNotice("Area of responsibility saved.");
      reload();
    } catch (err) {
      setActionError(err.message);
    } finally {
      setBusy(false);
    }
  }

  const zoomToMyUnit = useCallback(() => {
    const ids = new Set();
    if (user?.unit_id) ids.add(user.unit_id);
    const scoped = aors.filter(
      (aor) => aor.is_active && (ids.size ? ids.has(aor.unit) : true)
    );
    if (!scoped.length) {
      setNotice("No active AOR is recorded for your unit yet.");
      return;
    }
    const bounds = L.latLngBounds([]);
    scoped.forEach((aor) => {
      try {
        bounds.extend(L.geoJSON(aor.geometry).getBounds());
      } catch {
        /* skip a malformed polygon rather than move the map somewhere wrong */
      }
    });
    if (bounds.isValid()) setFitTarget({ bounds, key: Date.now() });
  }, [aors, user]);

  // After every hook declaration: a direct link from another role shows the
  // restriction instead of an empty map, and no AOR request was made.
  if (!canView) {
    return (
      <>
        <div className="page-head">
          <div>
            <h1>AOR / GIS</h1>
            <p>Areas of responsibility and their coverage map.</p>
          </div>
        </div>
        <Card>
          <p className="hint" style={{ margin: 0 }}>
            AOR and GIS coverage is available to command, operations and safety
            posts.
          </p>
        </Card>
      </>
    );
  }

  // The non-Administrator roles never load the management cards above; the
  // viewer fetches its own scoped operational picture.
  if (!isAdmin) return <AORViewer />;

  function onManageSaved() {
    reload();
    coverage.reload();
    setNotice("AOR coverage updated.");
  }

  function openEdit(aor) {
    setEditing(aor);
    setEditForm({
      name: aor.name,
      code: aor.code,
      unit: aor.unit ? String(aor.unit) : "",
      color: aor.color || "#4f9cf9",
      description: aor.description || "",
    });
    setActionError("");
  }

  async function saveEdit(event) {
    event.preventDefault();
    if (!editing) return;
    setRowBusy(true);
    setActionError("");
    try {
      const payload = {
        name: editForm.name.trim(),
        color: editForm.color,
        description: editForm.description,
      };
      // Only an Administrator may re-code a record or move it to another unit.
      if (canManage) {
        payload.code = editForm.code.trim();
        payload.unit = editForm.unit ? Number(editForm.unit) : null;
      }
      await api.patch(`/aor/${editing.id}/`, payload);
      setEditing(null);
      setNotice(`${payload.name} updated.`);
      reload();
    } catch (err) {
      setActionError(err.message);
    } finally {
      setRowBusy(false);
    }
  }

  async function runRowAction() {
    if (!confirming) return;
    const { kind, aor } = confirming;
    setRowBusy(true);
    setActionError("");
    try {
      if (kind === "archive") {
        const result = await api.post(`/aor/${aor.id}/archive/`, { reason: confirmReason });
        setNotice(result.detail || `${aor.name} archived.`);
      } else if (kind === "restore") {
        const result = await api.post(`/aor/${aor.id}/restore/`);
        setNotice(result.detail || `${aor.name} restored.`);
      } else {
        await api.del(`/aor/${aor.id}/`);
        setNotice(`${aor.name} deleted permanently. The audit trail keeps a full copy.`);
      }
      setConfirming(null);
      setConfirmReason("");
      reload();
      coverage.reload();
    } catch (err) {
      setActionError(err.message);
    } finally {
      setRowBusy(false);
    }
  }

  const manageUnits = units.data?.results ?? [];

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Area of Responsibility</h1>
          <p>
            Official province and city boundaries, unit coverage, and map layers.
            {canManage
              ? " You can assign or transfer areas from Manage AOR."
              : " AOR changes are made by an Administrator."}
          </p>
        </div>
        <div className="btn-row">
          <Button variant="ghost" onClick={zoomToMyUnit}>
            Zoom to my unit
          </Button>
          {canManage && (
            <>
              <Button
                variant={drawing ? "ghost" : "secondary"}
                onClick={() => {
                  setDrawing((value) => !value);
                  setPoints([]);
                }}
              >
                {drawing ? "Cancel drawing" : "Draw AOR"}
              </Button>
              <Button onClick={() => setManageOpen(true)}>Manage AOR</Button>
            </>
          )}
        </div>
      </div>

      <ErrorBanner message={error || actionError} onRetry={reload} />
      {notice && <div className="alert alert-success">{notice}</div>}
      {basemapFailure && (
        <div className="alert alert-warning">
          <span>
            The “{basemapFailure}” basemap could not be reached. Operational data is unaffected —
            pick another map style.
          </span>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              const fallback = availableBasemaps(config.data).find(
                (item) => item.id !== basemapFailure
              );
              if (fallback) chooseBasemap(fallback.id);
            }}
          >
            Use another basemap
          </Button>
        </div>
      )}

      <div className="map-shell">
        <div>
          <CollapsibleCard
            title="Map style"
            summary={basemap?.label || basemap?.id || "Choose a basemap"}
          >
            <BasemapControl
              config={config.data}
              value={basemap?.id}
              onChange={chooseBasemap}
            />
            {config.error && <p className="hint">{config.error}</p>}
          </CollapsibleCard>

          <CollapsibleCard
            title="Map layers"
            summary={`${activeLayers.length} of ${layerCatalogue.length} shown`}
          >
            <MapLayersPanel
              layers={layerCatalogue}
              visible={activeLayers}
              onToggle={toggleLayer}
            />
            <p className="hint" style={{ marginTop: 8 }}>
              Province and city outlines are reference geography. Unit ownership is drawn from AOR
              records and your authorised scope.
            </p>
          </CollapsibleCard>

          <CollapsibleCard title="Fuel locations" summary={showFuel ? "Shown" : "Hidden"}>
            <label className="legend-toggle">
              <input
                type="checkbox"
                checked={showFuel}
                onChange={(event) => setShowFuel(event.target.checked)}
              />
              <span className="legend-icon" style={{ color: "#d97706" }}>
                <FuelGlyph />
              </span>
              <span>Fuel Locations</span>
              <span className="hint">({fuelPlaces.length})</span>
            </label>
            <p className="hint" style={{ marginTop: 8 }}>
              POL-01 aviation fuel (Jet A1 and AVGas) inside your unit scope. Drums
              are counted in drums and bulk stock in litres; an empty batch is not
              drawn.
            </p>
            {fuelPicture.error && (
              <p className="hint">Fuel locations could not be loaded.</p>
            )}
          </CollapsibleCard>

          <CollapsibleCard title="Legend" summary="Map symbols">
            <ul className="map-legend">
              <li>
                <span className="legend-line" data-kind="aor" /> Unit AOR
              </li>
              <li>
                <span className="legend-line" data-kind="boundary" /> Province / city boundary
              </li>
              <li>
                <span className="legend-line" data-kind="preview" /> Selected area
              </li>
              <li>
                <label className="legend-toggle">
                  <input
                    type="checkbox"
                    checked={showFuel}
                    onChange={(event) => setShowFuel(event.target.checked)}
                  />
                  <span className="legend-icon" style={{ color: "#d97706" }}>
                    <FuelGlyph />
                  </span>
                  <span>Fuel Locations</span>
                  <span className="hint">({fuelPlaces.length})</span>
                </label>
              </li>
              <li>
                <span className="legend-dot" /> Picked point
              </li>
            </ul>
          </CollapsibleCard>

          <CollapsibleCard
            title={`Recorded AORs (${aors.length})`}
            defaultOpen
            summary={
              aors.length
                ? `${aors.filter((aor) => aor.is_active).length} active`
                : "No AORs defined yet"
            }
          >
            {loading ? (
              <Loading />
            ) : aors.length === 0 ? (
              <p className="hint">No AORs defined yet.</p>
            ) : (
              aors.map((aor) => (
                <div key={aor.id} className={`aor-row ${aor.is_active ? "" : "aor-row-archived"}`}>
                  <button
                    type="button"
                    className="aor-row-main"
                    onClick={() => setViewing(aor)}
                    title="View this area"
                  >
                    <span className="swatch" style={{ background: aor.color }} />
                    <span>
                      <strong>{aor.code}</strong>
                      <br />
                      <span className="hint">
                        {aor.name} · {aor.unit_acronym || aor.unit_name || "Unassigned"}
                        {aor.admin_area_name ? ` · ${aor.admin_area_name}` : ""}
                        {aor.unit_parent_acronym ? ` · rolls up to ${aor.unit_parent_acronym}` : ""}
                      </span>
                    </span>
                  </button>
                  <span className="aor-row-actions">
                    {aor.is_active ? (
                      <Badge tone="success">Active</Badge>
                    ) : (
                      <Badge>Archived</Badge>
                    )}
                    {canMaintain && (
                      <>
                        <Button size="sm" variant="ghost" onClick={() => setViewing(aor)}>
                          View
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => openEdit(aor)}>
                          Edit
                        </Button>
                        {aor.is_active ? (
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => {
                              setConfirmReason("");
                              setConfirming({ kind: "archive", aor });
                            }}
                          >
                            Archive
                          </Button>
                        ) : (
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => setConfirming({ kind: "restore", aor })}
                          >
                            Restore
                          </Button>
                        )}
                        {canManage && (
                          <Button
                            size="sm"
                            variant="danger"
                            onClick={() => setConfirming({ kind: "delete", aor })}
                          >
                            Delete
                          </Button>
                        )}
                      </>
                    )}
                  </span>
                </div>
              ))
            )}
          </CollapsibleCard>

          {canManage && (
            <Card
              title="Coverage by unit"
              actions={<Badge tone="muted">includes subordinates</Badge>}
            >
              {(coverage.data?.results ?? []).length === 0 ? (
                <p className="hint">No administrative areas are assigned yet.</p>
              ) : (
                (coverage.data?.results ?? []).map((entry) => (
                  <div key={entry.unit_id || "none"} className="coverage-row">
                    <div>
                      <strong>{entry.unit_acronym || entry.unit_name}</strong>
                      <span className="hint">
                        {entry.aor_count} own
                        {entry.inherited_count
                          ? ` + ${entry.inherited_count} from ${entry.inherited_from.join(", ")}`
                          : ""}{" "}
                        · {entry.total_aor_count} area(s) ·{" "}
                        {Math.round(entry.total_coverage_km2).toLocaleString()} km²
                      </span>
                    </div>
                    <button
                      type="button"
                      className="link-button"
                      onClick={() => setManageOpen(true)}
                    >
                      Manage
                    </button>
                  </div>
                ))
              )}
            </Card>
          )}

          <Card title="Find location">
            <Field label="Search TOWEMS records">
              <div className="inline-row">
                <Input
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="AOR or mission location…"
                />
                <Button variant="ghost" size="sm" onClick={runSearch}>
                  Search
                </Button>
              </div>
            </Field>
            {results.map((result, index) => (
              <div
                key={`${result.label}-${index}`}
                className="aor-item"
                onClick={() => {
                  setPicked({ lat: result.latitude, lng: result.longitude });
                  setFocus([result.latitude, result.longitude]);
                }}
              >
                <span>{result.label}</span>
                <Badge tone="muted">{result.source}</Badge>
              </div>
            ))}

            <Field label="MGRS" hint="Enter an MGRS grid, or convert the picked point.">
              <div className="inline-row">
                <Input value={mgrs} onChange={(event) => setMgrs(event.target.value)} placeholder="e.g. 51PTS8284814992" />
                <Button variant="ghost" size="sm" onClick={convertMgrs}>
                  Convert
                </Button>
              </div>
            </Field>

            <div className="btn-row">
              <Button variant="ghost" size="sm" onClick={lookupAor} disabled={!picked}>
                Which AOR contains this point?
              </Button>
            </div>
            {containsInfo && (
              <p className="hint" style={{ marginTop: 8 }}>
                {containsInfo.count
                  ? `Inside: ${containsInfo.results.map((item) => item.code).join(", ")}`
                  : "Point is outside all defined AORs."}
              </p>
            )}
          </Card>
        </div>

        <div className="map-canvas">
          <MapContainer center={DEFAULT_CENTER} zoom={6} scrollWheelZoom>
            <MapBasemapLayer
              basemap={basemap}
              onError={(id) => setBasemapFailure(id)}
            />
            <ClickCatcher
              drawing={drawing}
              onAdd={(latlng) => setPoints((current) => [...current, [latlng.lat, latlng.lng]])}
              onPick={(latlng) => setPicked({ lat: latlng.lat, lng: latlng.lng })}
            />
            <FlyTo target={focus} />
            <FitTo target={fitTarget} />
            <BoundaryPreview features={previewFeatures} />
            <AdminBoundariesLayer
              enabled={activeLayers.includes("admin_boundaries")}
              level="province"
            />

            {activeLayers.includes("units") && (
              <UnitMarkerLayer units={unitPins.data?.results ?? []} />
            )}

            {showFuel &&
              fuelPlaces.map((place) => (
                <Marker
                  key={`fuel-${place.key}`}
                  position={[Number(place.latitude), Number(place.longitude)]}
                  icon={fuelMarker(place)}
                  zIndexOffset={300000}
                >
                  <Popup
                    className="pol-popup"
                    maxWidth={360}
                    minWidth={240}
                  >
                    <FuelPopup place={place} />
                  </Popup>
                </Marker>
              ))}

            {activeLayers.includes("aor") &&
              polygons.map((polygon) => (
                <Polygon
                  key={polygon.id}
                  positions={polygon.positions}
                  pathOptions={{ color: polygon.color, weight: 2, fillOpacity: 0.15 }}
                >
                  <Popup>
                    <strong>{polygon.code}</strong>
                    <br />
                    {polygon.name}
                    <br />
                    {polygon.unit || "Unassigned"}
                    {polygon.parentUnit && (
                      <>
                        <br />
                        {polygon.parentUnit}
                      </>
                    )}
                  </Popup>
                </Polygon>
              ))}

            {points.length > 0 && (
              <Polygon
                positions={points}
                pathOptions={{ color: "#f0b429", dashArray: "6 4", fillOpacity: 0.1 }}
              />
            )}
            {points.map(([lat, lng], index) => (
              <CircleMarker key={`pt-${index}`} center={[lat, lng]} radius={5} pathOptions={{ color: "#f0b429" }} />
            ))}

            {picked && (
              <CircleMarker
                center={[picked.lat, picked.lng]}
                radius={8}
                pathOptions={{ color: "#f85149", fillColor: "#f85149", fillOpacity: 0.6 }}
              >
                <Popup>
                  {picked.lat.toFixed(5)}, {picked.lng.toFixed(5)}
                </Popup>
              </CircleMarker>
            )}
          </MapContainer>
        </div>
      </div>

      {drawing && (
        <div className="alert alert-warning" style={{ marginTop: 16 }}>
          <span>
            Click the map to add boundary vertices ({points.length} added, 3 minimum). The ring closes
            automatically. Overlaps with another unit's AOR are refused when you save.
          </span>
          <Button size="sm" disabled={points.length < 3} onClick={() => setSaveOpen(true)}>
            Save AOR
          </Button>
        </div>
      )}

      {saveOpen && (
        <Modal
          title="Save area of responsibility"
          onClose={() => setSaveOpen(false)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setSaveOpen(false)}>
                Cancel
              </Button>
              <Button onClick={saveAor} disabled={busy}>
                Save AOR
              </Button>
            </>
          }
        >
          <ErrorBanner message={actionError} />
          <form onSubmit={saveAor}>
            <div className="form-grid">
              <Field label="Name" required>
                <Input
                  value={form.name}
                  onChange={(event) => setForm({ ...form, name: event.target.value })}
                  required
                />
              </Field>
              <Field label="Code" required>
                <Input
                  value={form.code}
                  onChange={(event) => setForm({ ...form, code: event.target.value })}
                  required
                />
              </Field>
              <Field
                label="Owning unit"
                hint="Selecting a unit adopts its AOR colour; you can still override it."
              >
                <Select
                  value={form.unit}
                  onChange={(event) => {
                    const unit = manageUnits.find(
                      (row) => String(row.id) === String(event.target.value)
                    );
                    setForm({
                      ...form,
                      unit: event.target.value,
                      color: unit?.aor_color || form.color,
                    });
                  }}
                >
                  <option value="">Unassigned</option>
                  {manageUnits.map((unit) => (
                    <option key={unit.id} value={unit.id}>
                      {unit.acronym}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Colour" hint="Defaults to the owning unit's colour.">
                <Input
                  type="color"
                  value={form.color}
                  onChange={(event) => setForm({ ...form, color: event.target.value })}
                />
              </Field>
              <Field label="Description" className="span-2">
                <Input
                  value={form.description}
                  onChange={(event) => setForm({ ...form, description: event.target.value })}
                />
              </Field>
            </div>
          </form>
        </Modal>
      )}

      {viewing && (
        <Modal
          title={`${viewing.code} — ${viewing.name}`}
          onClose={() => setViewing(null)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setViewing(null)}>
                Close
              </Button>
              {viewing.centroid_lat != null && (
                <Button
                  variant="secondary"
                  onClick={() => {
                    setFocus([viewing.centroid_lat, viewing.centroid_lon]);
                    setViewing(null);
                  }}
                >
                  Show on map
                </Button>
              )}
              {canMaintain && (
                <Button
                  onClick={() => {
                    openEdit(viewing);
                    setViewing(null);
                  }}
                >
                  Edit
                </Button>
              )}
            </>
          }
        >
          <dl className="details">
            <div className="detail">
              <dt>Status</dt>
              <dd>{viewing.is_active ? "Active coverage" : "Archived"}</dd>
            </div>
            <div className="detail">
              <dt>Owning unit</dt>
              <dd>{viewing.unit_acronym || viewing.unit_name || "Unassigned"}</dd>
            </div>
            <div className="detail">
              <dt>Rolls up to</dt>
              <dd>
                {viewing.unit_parent_acronym || viewing.unit_parent_name || "No mother unit"}
              </dd>
            </div>
            <div className="detail">
              <dt>Source</dt>
              <dd>
                {viewing.admin_area_name
                  ? `${viewing.admin_area_name} (${viewing.admin_area_level || "area"})`
                  : "Drawn boundary"}
              </dd>
            </div>
            <div className="detail">
              <dt>Colour</dt>
              <dd>
                <span className="swatch" style={{ background: viewing.color }} /> {viewing.color}
              </dd>
            </div>
            <div className="detail">
              <dt>Centroid</dt>
              <dd>
                {viewing.centroid_lat != null
                  ? `${Number(viewing.centroid_lat).toFixed(4)}, ${Number(viewing.centroid_lon).toFixed(4)}`
                  : "—"}
              </dd>
            </div>
            <div className="detail">
              <dt>Created</dt>
              <dd>{formatDateTime(viewing.created_at) || "—"}</dd>
            </div>
            <div className="detail">
              <dt>Notes</dt>
              <dd>{viewing.description || "—"}</dd>
            </div>
          </dl>
          <p className="hint">
            {viewing.is_active
              ? "Archive takes this area out of active coverage but keeps the record and its history. Deleting removes the record permanently (Administrator only) and keeps a full copy in the audit trail."
              : "Restore returns this area to active coverage. Another unit cannot hold the same administrative area at the same time."}
          </p>
        </Modal>
      )}

      {editing && (
        <Modal
          title={`Edit ${editing.code}`}
          onClose={() => setEditing(null)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setEditing(null)}>
                Cancel
              </Button>
              <Button onClick={saveEdit} disabled={rowBusy}>
                {rowBusy ? "Saving…" : "Save changes"}
              </Button>
            </>
          }
        >
          <ErrorBanner message={actionError} />
          <form onSubmit={saveEdit}>
            <div className="form-grid">
              <Field label="Name" required>
                <Input
                  value={editForm.name}
                  onChange={(event) => setEditForm({ ...editForm, name: event.target.value })}
                  required
                />
              </Field>
              <Field
                label="Code"
                required
                hint={
                  canManage
                    ? "Unique across all AORs. Change it only if it is genuinely wrong."
                    : "Record codes are set by an Administrator."
                }
              >
                <Input
                  value={editForm.code}
                  onChange={(event) => setEditForm({ ...editForm, code: event.target.value })}
                  readOnly={!canManage}
                  disabled={!canManage}
                  required
                />
              </Field>
              <Field
                label="Owning unit"
                hint={canManage ? "Moving coverage is checked for overlaps." : "Unit changes are made by an Administrator."}
              >
                <Select
                  value={editForm.unit}
                  onChange={(event) => setEditForm({ ...editForm, unit: event.target.value })}
                  disabled={!canManage}
                >
                  <option value="">Unassigned</option>
                  {manageUnits.map((unit) => (
                    <option key={unit.id} value={unit.id}>
                      {unit.acronym || unit.name}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Colour">
                <Input
                  type="color"
                  value={editForm.color}
                  onChange={(event) => setEditForm({ ...editForm, color: event.target.value })}
                />
              </Field>
              <Field label="Description / notes" className="span-2">
                <Input
                  value={editForm.description}
                  onChange={(event) =>
                    setEditForm({ ...editForm, description: event.target.value })
                  }
                />
              </Field>
            </div>
            <p className="hint">
              The boundary itself is not edited here. {canManage
                ? "Draw a new AOR, or reassign administrative areas from Manage AOR."
                : "Ask an Administrator to redraw or reassign this coverage."}
            </p>
          </form>
        </Modal>
      )}

      {confirming && (
        <Modal
          title={
            confirming.kind === "delete"
              ? `Delete ${confirming.aor.code}?`
              : confirming.kind === "archive"
                ? `Archive ${confirming.aor.code}?`
                : `Restore ${confirming.aor.code}?`
          }
          onClose={() => setConfirming(null)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setConfirming(null)}>
                Cancel
              </Button>
              <Button
                variant={confirming.kind === "delete" ? "danger" : "primary"}
                onClick={runRowAction}
                disabled={rowBusy}
              >
                {rowBusy
                  ? "Working…"
                  : confirming.kind === "delete"
                    ? "Delete permanently"
                    : confirming.kind === "archive"
                      ? "Archive area"
                      : "Restore area"}
              </Button>
            </>
          }
        >
          <ErrorBanner message={actionError} />
          {confirming.kind === "delete" ? (
            <p>
              <strong>{confirming.aor.name}</strong> ({confirming.aor.code}) will be removed from
              the records. Unlike archiving this cannot be undone from the interface; the audit
              trail keeps a full copy of the boundary and details so it can be reconstructed.
            </p>
          ) : confirming.kind === "archive" ? (
            <>
              <p>
                <strong>{confirming.aor.name}</strong> ({confirming.aor.code}) stops counting as
                coverage. The record and its history stay, and it can be restored at any time.
              </p>
              <Field label="Reason (recorded in the audit trail)">
                <Input
                  value={confirmReason}
                  onChange={(event) => setConfirmReason(event.target.value)}
                  placeholder="e.g. boundary superseded by the new assignment"
                />
              </Field>
            </>
          ) : (
            <p>
              <strong>{confirming.aor.name}</strong> ({confirming.aor.code}) returns to active
              coverage.
            </p>
          )}
        </Modal>
      )}

      {manageOpen && canManage && (
        <ManageAORModal
          units={manageUnits}
          initialUnitId={user?.unit_id ?? ""}
          onClose={() => {
            setManageOpen(false);
            setPreviewFeatures([]);
          }}
          onSaved={onManageSaved}
          onPreview={setPreviewFeatures}
        />
      )}
    </>
  );
}

function ClickCatcher({ drawing, onAdd, onPick }) {
  useMapEvents({
    click(event) {
      if (drawing) onAdd(event.latlng);
      else onPick(event.latlng);
    },
  });
  return null;
}

function FlyTo({ target }) {
  const map = useMap();
  useEffect(() => {
    if (target) map.flyTo(target, Math.max(map.getZoom(), 10));
  }, [target, map]);
  return null;
}

function FitTo({ target }) {
  const map = useMap();
  useEffect(() => {
    if (target?.bounds?.isValid?.()) {
      map.fitBounds(target.bounds.pad(0.1), { animate: true });
    }
  }, [target, map]);
  return null;
}

/** GeoJSON [lon, lat] -> Leaflet [lat, lon] for Polygon / MultiPolygon. */
function toLeafletPositions(geometry) {
  if (!geometry) return [];
  if (geometry.type === "Polygon") {
    return geometry.coordinates.map((ring) => ring.map(([lon, lat]) => [lat, lon]));
  }
  if (geometry.type === "MultiPolygon") {
    return geometry.coordinates.flatMap((polygon) =>
      polygon.map((ring) => ring.map(([lon, lat]) => [lat, lon]))
    );
  }
  return [];
}
