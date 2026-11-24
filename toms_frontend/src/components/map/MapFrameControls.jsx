import BasemapControl from "./BasemapControl.jsx";

/** Corners out for full screen, corners in to leave it. */
export function ExpandGlyph({ expanded }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="16"
      height="16"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {expanded ? (
        <path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5" />
      ) : (
        <path d="M4 9V5a1 1 0 0 1 1-1h4M20 9V5a1 1 0 0 0-1-1h-4M4 15v4a1 1 0 0 0 1 1h4M20 15v4a1 1 0 0 1-1 1h-4" />
      )}
    </svg>
  );
}

/**
 * The map's own two floating buttons: the style swatch and full screen.
 *
 * They sit inside the map frame in the same place on every map, and carry no
 * label over the picture.
 */
export default function MapFrameControls({
  config,
  value,
  onChangeBasemap,
  fullScreen,
  onToggleFullScreen,
}) {
  return (
    <div className="map-frame-tools">
      <BasemapControl
        config={config}
        value={value}
        onChange={onChangeBasemap}
        iconOnly
      />
      <button
        type="button"
        className="map-frame-expand"
        onClick={onToggleFullScreen}
        aria-pressed={fullScreen}
        aria-label={fullScreen ? "Leave full screen" : "Show the map full screen"}
        title={fullScreen ? "Leave full screen (Esc)" : "Full screen"}
      >
        <ExpandGlyph expanded={fullScreen} />
      </button>
    </div>
  );
}
