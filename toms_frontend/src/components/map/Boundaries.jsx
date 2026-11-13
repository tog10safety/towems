import { useEffect } from "react";
import L from "leaflet";
import { GeoJSON, useMap } from "react-leaflet";
import { useApi } from "../../hooks/useApi.js";

const ADMIN_STYLE = {
  color: "#8b93a7",
  weight: 1,
  opacity: 0.75,
  fillOpacity: 0.02,
  dashArray: "3 3",
};

const ASSIGNED_STYLE = {
  color: "#4f9cf9",
  weight: 1.5,
  opacity: 0.9,
  fillOpacity: 0.05,
};

/**
 * Official PSGC province (or city) outlines as a reference layer.
 *
 * This is public administrative geography served by the TOWEMS API, not
 * operational data: ownership of an area is only shown where an authorised AOR
 * assignment exists.
 */
export function AdminBoundariesLayer({ enabled, level = "province", onCount }) {
  const { data, error } = useApi(
    enabled ? `/admin-areas/boundaries/?level=${encodeURIComponent(level)}` : "",
    { enabled }
  );

  useEffect(() => {
    if (onCount && data?.features) onCount(data.features.length);
  }, [data, onCount]);

  if (!enabled || error || !data?.features?.length) return null;

  return (
    <GeoJSON
      key={`${level}-${data.features.length}`}
      data={data}
      style={(feature) =>
        feature?.properties?.assigned_unit ? ASSIGNED_STYLE : ADMIN_STYLE
      }
      onEachFeature={(feature, layer) => {
        const props = feature.properties || {};
        layer.bindTooltip(
          `${props.name}${props.assigned_unit ? ` · ${props.assigned_unit}` : ""}`,
          { sticky: true }
        );
      }}
    />
  );
}

/** Selected administrative boundaries being reviewed before saving. */
export function BoundaryPreview({ features, fit = true }) {
  const map = useMap();

  useEffect(() => {
    if (!fit || !features?.length) return;
    const bounds = L.latLngBounds([]);
    features.forEach((feature) => {
      try {
        bounds.extend(L.geoJSON(feature.geometry).getBounds());
      } catch {
        /* a single malformed preview must not break the map */
      }
    });
    if (bounds.isValid()) map.fitBounds(bounds.pad(0.15), { animate: true });
  }, [features, fit, map]);

  if (!features?.length) return null;

  return (
    <GeoJSON
      key={`preview-${features.map((f) => f.psgc).join("-")}`}
      data={{ type: "FeatureCollection", features }}
      style={{
        color: "#f0b429",
        weight: 2,
        fillColor: "#f0b429",
        fillOpacity: 0.18,
      }}
      onEachFeature={(feature, layer) => {
        const props = feature.properties || {};
        layer.bindTooltip(`${props.name} (${props.level})`, { permanent: false, sticky: true });
      }}
    />
  );
}
