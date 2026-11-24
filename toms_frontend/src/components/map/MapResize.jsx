import { useLayoutEffect } from "react";
import { useMap } from "react-leaflet";

/**
 * Leaflet caches its own size, so any change of the map's box - full screen in
 * particular - has to be measured again or it keeps painting at the old size.
 *
 * The container is observed rather than trusted to the mode flag: entering or
 * leaving full screen, opening a panel or a window resize all change the box,
 * and every one of them needs the same re-measure.
 */
export default function MapResize({ expanded }) {
  const map = useMap();

  useLayoutEffect(() => {
    const container = map.getContainer();
    let pending = 0;

    function measure() {
      window.cancelAnimationFrame(pending);
      // One frame later, when the new box has been laid out.
      pending = window.requestAnimationFrame(() => map.invalidateSize());
    }

    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(container);

    return () => {
      window.cancelAnimationFrame(pending);
      observer.disconnect();
    };
  }, [expanded, map]);

  return null;
}
