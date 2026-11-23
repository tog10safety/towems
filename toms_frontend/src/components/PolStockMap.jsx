import { useMemo } from "react";
import { CircleMarker, MapContainer, Popup, Tooltip } from "react-leaflet";
import { useApi } from "../hooks/useApi.js";
import { useTheme } from "../theme/ThemeContext.jsx";
import {
  BASEMAP_STORAGE_KEY,
  readStored,
  resolveBasemap,
} from "../map/config.js";
import MapBasemapLayer from "./map/MapBasemapLayer.jsx";
import { formatDate, number } from "../utils/format.js";

const DEFAULT_CENTER = [12.8797, 121.774];

//: Only the two aviation grades are pinned: this is the POL-01 picture, not the
//: whole fuel store.
const GRADE_COLORS = {
  jet_a1: "#f59e0b",
  avgas: "#38bdf8",
};
const GRADE_FALLBACK = "#a371f7";

/**
 * The quantity a hover shows for one place: drums are counted as drums, bulk
 * stock in litres. A place holding both lists each, so a drum store is never
 * reported in litres it cannot dispense.
 */
function placeQuantity(rows) {
  const drums = rows.filter((row) => row.form === "drum");
  const bulk = rows.filter((row) => row.form !== "drum");
  const parts = [];
  if (drums.length) {
    const count = drums.reduce(
      (sum, row) => sum + (Number(row.quantity_available) || 0),
      0
    );
    parts.push(`${number(count)} Drums`);
  }
  if (bulk.length) {
    const litres = bulk.reduce(
      (sum, row) => sum + (Number(row.litres_available) || 0),
      0
    );
    parts.push(`${number(litres)} L`);
  }
  return parts.join(" · ");
}

function hasPosition(row) {
  return (
    row.latitude !== null &&
    row.latitude !== undefined &&
    row.longitude !== null &&
    row.longitude !== undefined
  );
}

/**
 * Where the POL-01 Aviation Fuel is held, pinned at its airport.
 *
 * The pin sits on the airfield the store's location names ("Name (CODE)"), so
 * the map cannot invent a position. A store that is not on an airfield - the
 * unit's own POL dump, for instance - sits on that unit's map marker instead,
 * drawn hollow so the two are never confused. Only a store with neither is
 * listed under the map. Every pin is drawn at the same size, so a pin means a
 * place and never a quantity.
 *
 * One pin is one place: every store standing there shares it, and the popup
 * lists their quantities and expiry dates. Drums are batched, so two drums of
 * the same grade at the same dump can expire on different days: the popup
 * gives each batch its own line rather than one date for a whole grade. There
 * is no table under the map - the pin is the only way in.
 */
export default function PolStockMap({ rows }) {
  const { resolvedTheme } = useTheme();
  const config = useApi("/map/config/");
  const basemap = useMemo(
    () =>
      resolveBasemap(
        config.data,
        readStored(BASEMAP_STORAGE_KEY, ""),
        resolvedTheme
      ),
    [config.data, resolvedTheme]
  );

  const pol01 = useMemo(
    () =>
      (rows ?? []).filter(
        (row) =>
          row.category_code === "POL-01" &&
          // A batch that has been entirely transferred away holds nothing, so
          // it is not a place on the map any more.
          (Number(row.quantity_total) > 0 || Number(row.quantity_available) > 0)
      ),
    [rows]
  );
  const placed = pol01.filter(hasPosition);
  const unmapped = pol01.filter((row) => !hasPosition(row));

  // Rows sharing a point are one place on the ground, so they get one pin.
  const places = useMemo(() => {
    const byPoint = new Map();
    for (const row of placed) {
      const key = `${Number(row.latitude).toFixed(4)},${Number(row.longitude).toFixed(4)}`;
      const place = byPoint.get(key) || {
        key,
        latitude: row.latitude,
        longitude: row.longitude,
        position_source: row.position_source,
        position_label: row.position_label,
        rows: [],
      };
      place.rows.push(row);
      byPoint.set(key, place);
    }
    return [...byPoint.values()];
  }, [placed]);

  if (!places.length) {
    return (
      <div className="pol-map-empty">
        <p>
          No POL-01 Aviation Fuel store is sited on an airport yet, so there is
          nothing to pin. A bulk tank takes its position from the airport chosen
          when the stock was added.
        </p>
        {unmapped.length > 0 && (
          <p className="hint">
            Not on the map:{" "}
            {[...new Set(unmapped.map((row) => row.storage_location || "no location"))].join(", ")}.
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="pol-map">
      <MapContainer
        center={[places[0].latitude, places[0].longitude]}
        bounds={places.map((place) => [place.latitude, place.longitude])}
        boundsOptions={{ padding: [40, 40] }}
        zoom={places.length > 1 ? undefined : 9}
        scrollWheelZoom
        className="pol-map-canvas"
      >
        <MapBasemapLayer basemap={basemap} />
        {places.map((place) => {
          const label = place.rows[0].storage_location || "No location recorded";
          const grade = place.rows.map((row) => row.grade).find(Boolean);
          return (
            <CircleMarker
              key={place.key}
              center={[place.latitude, place.longitude]}
              radius={7}
              pathOptions={{
                color: "#0f172a",
                weight: 2,
                fillColor: GRADE_COLORS[grade] || GRADE_FALLBACK,
                // A hollow pin sits at the unit's marker, not on an airfield.
                fillOpacity: place.position_source === "unit" ? 0.3 : 0.88,
              }}
            >
              <Tooltip direction="top">
                {label} · {placeQuantity(place.rows)}
              </Tooltip>
              <Popup
                className="pol-popup"
                maxWidth={360}
                minWidth={240}
                autoPan
              >
                <strong>{label}</strong>
                {place.position_source === "unit" && (
                  <>
                    <br />
                    <span className="pol-popup-note">
                      At the unit's map marker
                      {place.position_label ? ` — ${place.position_label}` : ""}
                    </span>
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
                    {place.rows.length === 0 && (
                      <tr>
                        <td colSpan={3}>No stock recorded at this location.</td>
                      </tr>
                    )}
                    {place.rows.map((row) => (
                      <tr key={row.id}>
                        <td>{row.grade_label || "—"}</td>
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
              </Popup>
            </CircleMarker>
          );
        })}
      </MapContainer>

      <div className="pol-map-legend">
        <span>
          <i className="pol-map-swatch" style={{ background: GRADE_COLORS.jet_a1 }} />
          Jet A1
        </span>
        <span>
          <i className="pol-map-swatch" style={{ background: GRADE_COLORS.avgas }} />
          AVGas
        </span>
        <span>
          <i
            className="pol-map-swatch"
            style={{ background: "transparent", borderColor: GRADE_COLORS.jet_a1 }}
          />
          Hollow pin = unit location, no airfield
        </span>
        <span>Every pin is the same size</span>
      </div>

      {unmapped.length > 0 && (
        <p className="hint">
          Not on the map (no airport or unit marker):{" "}
          {[...new Set(unmapped.map((row) => row.storage_location || "no location"))].join(", ")}.
        </p>
      )}
    </div>
  );
}
