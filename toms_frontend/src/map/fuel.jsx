import L from "leaflet";
import { formatDate, number } from "../utils/format.js";

/** A fuel drum, used by the legend and its checkbox. */
export function FuelGlyph() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="13"
      height="13"
      fill="currentColor"
      stroke="none"
      aria-hidden="true"
    >
      <path d="M8 2.5h8L17.5 5v14.5a2 2 0 0 1-2 2h-7a2 2 0 0 1-2-2V5L8 2.5Zm.5 5.5v3h7V8h-7Zm0 6v3h7v-3h-7Z" />
    </svg>
  );
}

/**
 * A fuel store, standing above its coordinate in the POL amber.
 *
 * It is anchored above the point so a unit seal at the same coordinate stays
 * readable and clickable, exactly as the aircraft pins are offset.
 */
export function fuelMarker(place) {
  const form = place.rows?.[0]?.form ?? place.form;
  const colour = form === "drum" ? "#d97706" : "#0e7490";
  return L.divIcon({
    className: "fuel-map-icon",
    html:
      '<span class="fuel-map-marker">' +
      `<svg viewBox="0 0 24 24" width="16" height="16" fill="${colour}" ` +
      'stroke="#ffffff" stroke-width="1.3" stroke-linejoin="round" aria-hidden="true">' +
      '<path d="M8 3h8l1.5 2.5V20a1 1 0 0 1-1 1h-9a1 1 0 0 1-1-1V5.5L8 3Z"/>' +
      '<path d="M8 8h8M8 16h8" fill="none" opacity="0.6"/>' +
      "</svg></span>",
    iconSize: [22, 22],
    iconAnchor: [11, 26],
    popupAnchor: [0, -24],
  });
}

/**
 * One pin per place, as the fuel map does it: every store standing at the same
 * coordinate shares a pin, so two batches at one POL dump are never stacked on
 * top of each other and cannot hide one another.
 */
export function groupFuelPlaces(rows) {
  const byPoint = new Map();
  for (const row of rows ?? []) {
    if (row.latitude == null || row.longitude == null) continue;
    const key = `${Number(row.latitude).toFixed(4)},${Number(row.longitude).toFixed(4)}`;
    const place = byPoint.get(key) || {
      key,
      latitude: row.latitude,
      longitude: row.longitude,
      position_source: row.position_source,
      position_label: row.position_label,
      unit_acronym: row.unit_acronym,
      rows: [],
    };
    place.rows.push(row);
    byPoint.set(key, place);
  }
  return [...byPoint.values()];
}

/** The popup every AOR map shows for a fuel pin: the fuel map's batch table. */
export function FuelPopup({ place }) {
  const first = place.rows[0] || {};
  const label =
    first.position_label || first.storage_location || "Fuel store";
  return (
    <>
      <strong>{label}</strong>
      {place.position_source === "unit" && (
        <>
          <br />
          <span className="pol-popup-note">At the unit's map marker</span>
        </>
      )}
      <table className="pol-popup-table">
        <thead>
          <tr>
            <th>Grade</th>
            <th>Quantity (Drums/Liters)</th>
            <th>Expiry</th>
          </tr>
        </thead>
        <tbody>
          {place.rows.map((row) => (
            <tr key={row.id}>
              <td>{row.grade_label || row.category_code || "—"}</td>
              <td>
                {row.form === "drum"
                  ? `${number(row.quantity_available)} Drums`
                  : `${number(row.litres_available)} Liters`}
              </td>
              <td>{row.expiry_date ? formatDate(row.expiry_date) : "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <span className="hint">{place.unit_acronym || "Unassigned"}</span>
    </>
  );
}
