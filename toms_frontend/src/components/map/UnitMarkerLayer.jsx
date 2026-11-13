import L from "leaflet";
import { Marker, Popup } from "react-leaflet";

/**
 * Units pinned on an operational map.
 *
 * The pin is the unit's own logo or seal, uploaded in Unit Management. When no
 * seal has been uploaded the marker falls back to the unit acronym, so a unit
 * with a coordinate is never invisible. Pins come from `/units/markers/` (or
 * the operational-picture payload) and are already limited to the caller's
 * authorised scope by the server.
 *
 * Unit pins carry a large z-index offset so a unit is never buried beneath a
 * feature or airfield pin. Popups live in their own Leaflet pane and stay above
 * all markers.
 */
export default function UnitMarkerLayer({ units, compact = false, offsets = null }) {
  return (units ?? [])
    .filter((unit) => unit.latitude != null && unit.longitude != null)
    .map((unit) => (
      <Marker
        key={`unit-${unit.unit_id}`}
        position={[Number(unit.latitude), Number(unit.longitude)]}
        icon={pinIcon(unit, compact, offsets?.[unit.unit_id])}
        zIndexOffset={1000000}
      >
        <Popup>
          <strong>{unit.unit_acronym || unit.unit_name}</strong>
          {unit.place_name && (
            <>
              <br />
              {unit.place_name}
            </>
          )}
          {unit.mgrs && (
            <>
              <br />
              {unit.mgrs}
            </>
          )}
          <br />
          {Number(unit.latitude).toFixed(5)}, {Number(unit.longitude).toFixed(5)}
        </Popup>
      </Marker>
    ));
}

function escapeHtml(text) {
  return String(text ?? "").replace(/[&<>"']/g, (character) => {
    const map = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
    return map[character];
  });
}

function pinIcon(unit, compact, offset = [0, 0]) {
  const [dx = 0, dy = 0] = offset || [];
  const size = compact ? 20 : 28;
  const wrapperClass = compact
    ? "unit-pin-wrap unit-pin-wrap-compact"
    : "unit-pin-wrap";
  const iconAnchor = [size / 2 - dx, size / 2 - dy];
  const popupAnchor = [dx, dy - size / 2];
  if (unit.logo_url) {
    return L.divIcon({
      className: wrapperClass,
      html: `<span class="unit-pin"><img src="${escapeHtml(unit.logo_url)}" alt="" /></span>`,
      iconSize: [size, size],
      iconAnchor,
      popupAnchor,
    });
  }
  const label = escapeHtml((unit.unit_acronym || unit.unit_name || "?").slice(0, 5));
  return L.divIcon({
    className: wrapperClass,
    html: `<span class="unit-pin unit-pin-text">${label}</span>`,
    iconSize: [size, size],
    iconAnchor,
    popupAnchor,
  });
}
