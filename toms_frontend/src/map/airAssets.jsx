import { divIcon } from "leaflet";

/**
 * Air-asset map symbols.
 *
 * The shape follows the register's aircraft type - rotary-wing, fixed-wing,
 * UAV, tiltrotor - so an operator can tell a helicopter from an aeroplane at a
 * glance. The operational status is a separate coloured dot, never the shape.
 */
export const AIR_ASSET_TYPE_GROUPS = [
  {
    value: "rotary_wing",
    label: "Rotary-wing",
    icon: "rotary",
    color: "#3fb950",
  },
  {
    value: "fixed_wing",
    label: "Fixed-wing",
    icon: "fixed",
    color: "#4f9cf9",
  },
  {
    value: "uav",
    label: "Unmanned aerial vehicle",
    icon: "uav",
    color: "#a371f7",
  },
  {
    value: "tiltrotor",
    label: "Tiltrotor",
    icon: "tiltrotor",
    color: "#39c5cf",
  },
  {
    value: "other",
    label: "Other aircraft",
    icon: "other",
    color: "#8b949e",
  },
];

const TYPE_GROUP_BY_VALUE = Object.fromEntries(
  AIR_ASSET_TYPE_GROUPS.map((group) => [group.value, group])
);
const FALLBACK_GROUP = TYPE_GROUP_BY_VALUE.other;

export const AIR_ASSET_GLYPHS = {
  rotary:
    '<circle cx="24" cy="21" r="17" fill="none" stroke="currentColor" stroke-width="1.3" opacity="0.45"/>' +
    '<path d="M21.8 5.5h4.4l2.1 10.5 6.7 4v4.2l-6.7-1.4v10.7l5.7 4.6v3.2l-7.4-2-7.4 2v-3.2l5.7-4.6V23.6l-6.7 1.4v-4.2l6.7-4Z"/>' +
    '<path d="M20 41.5h8" fill="none" stroke="currentColor" stroke-width="1.6"/>',
  fixed:
    '<path d="M23.2 3h1.6v14l17 8.5v3.4l-17-2.7v9.4l7 4.8v3.2l-8.8-1.7-8.8 1.7v-3.2l7-4.8v-9.4l-17 2.7v-3.4l17-8.5Z"/>',
  uav: '<path d="M24 4 26.4 17.5 45 27 45 30 26.4 26.8 24 40 21.6 26.8 3 30 3 27 21.6 17.5Z"/>',
  tiltrotor:
    '<path d="M22.5 6h3v12l12.5 6v3.8l-12.5-2.1v8.8l5.2 4v3.1l-7.2-1.6-7.2 1.6v-3.1l5.2-4v-8.8l-12.5 2.1V24l12.5-6Z"/>' +
    '<circle cx="8.5" cy="25" r="5.5" fill="none" stroke="currentColor" stroke-width="1.6"/>' +
    '<circle cx="39.5" cy="25" r="5.5" fill="none" stroke="currentColor" stroke-width="1.6"/>',
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

function safeColour(value, fallback) {
  return /^#[0-9a-f]{6}$/i.test(value || "") ? value : fallback;
}

export function aircraftTypeGroup(asset) {
  return TYPE_GROUP_BY_VALUE[asset?.aircraft_type] || FALLBACK_GROUP;
}

export function AirAssetGlyph({ icon, size = 16 }) {
  return (
    <svg
      viewBox="0 0 48 48"
      width={size}
      height={size}
      fill="currentColor"
      stroke="none"
      aria-hidden="true"
      dangerouslySetInnerHTML={{
        __html: AIR_ASSET_GLYPHS[icon] || AIR_ASSET_GLYPHS.other,
      }}
    />
  );
}

function markerHtml(group, status) {
  const colour = safeColour(group.color, "#57606a");
  const statusColour = safeColour(AIR_ASSET_STATUS_COLORS[status], "#8b949e");
  return (
    '<span class="air-asset-marker">' +
    `<svg viewBox="0 0 48 48" width="36" height="36" fill="${colour}" color="${colour}" ` +
    'stroke="#ffffff" stroke-width="1.25" stroke-linejoin="round" aria-hidden="true">' +
    `${AIR_ASSET_GLYPHS[group.icon] || AIR_ASSET_GLYPHS.other}</svg>` +
    `<span class="air-asset-status-dot" style="background:${statusColour}"></span>` +
    "</span>"
  );
}

export function aircraftTypeMarker(asset, offset = [0, 0]) {
  const [dx = 0, dy = 0] = offset || [];
  return divIcon({
    className: "air-asset-map-icon",
    html: markerHtml(aircraftTypeGroup(asset), asset?.operational_status),
    iconSize: [36, 36],
    // A pixel offset keeps an aircraft visible when it shares a grid point with
    // a unit seal. Adjusting the anchor moves the icon without changing the
    // stored coordinate, and the popup follows the shifted symbol.
    iconAnchor: [18 - dx, 18 - dy],
    popupAnchor: [dx, dy - 17],
  });
}
