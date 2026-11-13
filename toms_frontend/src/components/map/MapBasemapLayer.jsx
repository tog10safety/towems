import { useEffect, useRef, useState } from "react";
import { TileLayer } from "react-leaflet";

/**
 * The chosen basemap's tile layer, plus its label overlays (hybrid mode).
 *
 * Tile failures are detected client-side: if the provider is unreachable the
 * component reports it once and the page offers the other configured basemaps
 * instead of showing a blank grid with no explanation. Operational data is not
 * affected - it lives in a separate layer drawn from the TOWEMS API.
 */
export default function MapBasemapLayer({ basemap, onError }) {
  const [failed, setFailed] = useState(false);
  const reported = useRef("");
  const overlayKey = basemap?.overlays?.length ? "overlay" : "plain";

  useEffect(() => {
    setFailed(false);
    reported.current = "";
  }, [basemap?.id]);

  useEffect(() => {
    if (!failed || !basemap) return;
    if (reported.current === basemap.id) return;
    reported.current = basemap.id;
    onError?.(basemap.id);
  }, [failed, basemap, onError]);

  if (!basemap) return null;

  return (
    <>
      <TileLayer
        key={`${basemap.id}-${overlayKey}`}
        url={basemap.url}
        attribution={basemap.attribution}
        maxZoom={basemap.max_zoom || 19}
        maxNativeZoom={basemap.max_native_zoom || undefined}
        eventHandlers={{ tileerror: () => setFailed(true) }}
      />
      {(basemap.overlays || []).map((overlay) => (
        <TileLayer
          key={`${basemap.id}-overlay-${overlay.url}`}
          url={overlay.url}
          attribution={overlay.attribution || ""}
          maxZoom={basemap.max_zoom || 19}
          maxNativeZoom={basemap.max_native_zoom || undefined}
          opacity={0.9}
          eventHandlers={{ tileerror: () => setFailed(true) }}
        />
      ))}
    </>
  );
}
