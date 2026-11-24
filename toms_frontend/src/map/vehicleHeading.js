/**
 * Which way a vehicle is pointing, for the monitors' maps.
 *
 * The driver's own screen draws its cone from the heading the phone reports.
 * The Operations Center's maps receive positions differently - the live ground
 * board gets a trail of breadcrumbs, the mission map gets the run's recent
 * pings - so the same answer is derived here from whatever each one has: the
 * reported bearing when a ping carried one, otherwise the course between the
 * last two points that are far enough apart to mean a direction rather than
 * GPS noise.
 */

const EARTH_RADIUS_KM = 6371;

function toRadians(value) {
  return (value * Math.PI) / 180;
}

/** Great-circle distance in kilometres. */
function distanceKm(first, second) {
  const [lat1, lon1] = first;
  const [lat2, lon2] = second;
  const dLat = toRadians(lat2 - lat1);
  const dLon = toRadians(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRadians(lat1)) * Math.cos(toRadians(lat2)) * Math.sin(dLon / 2) ** 2;
  return EARTH_RADIUS_KM * 2 * Math.asin(Math.min(1, Math.sqrt(a)));
}

/** Compass bearing from one point to the next, clockwise from north. */
export function bearingDegrees(first, second) {
  const [lat1, lon1] = first;
  const [lat2, lon2] = second;
  const dLon = toRadians(lon2 - lon1);
  const y = Math.sin(dLon) * Math.cos(toRadians(lat2));
  const x =
    Math.cos(toRadians(lat1)) * Math.sin(toRadians(lat2)) -
    Math.sin(toRadians(lat1)) * Math.cos(toRadians(lat2)) * Math.cos(dLon);
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
}

function usablePoints(points) {
  return (points || []).filter(
    (point) =>
      Array.isArray(point) &&
      Number.isFinite(point[0]) &&
      Number.isFinite(point[1])
  );
}

/**
 * The heading after one new fix, for a live stream of positions.
 *
 * The phone reports a bearing only while it is moving; the course from the
 * previous fix fills the gap, and a vehicle standing still keeps its last known
 * direction instead of losing its arrow between fixes.
 */
export function nextHeading(previous, latitude, longitude, reported) {
  if (Number.isFinite(reported)) return reported;
  if (!previous || !Number.isFinite(previous.latitude) || !Number.isFinite(previous.longitude)) {
    return null;
  }
  const moved = distanceKm(
    [previous.latitude, previous.longitude],
    [latitude, longitude]
  );
  // A few metres of GPS noise is not a turn: keep the arrow still.
  if (moved < 0.01) {
    return Number.isFinite(previous.heading) ? previous.heading : null;
  }
  return bearingDegrees([previous.latitude, previous.longitude], [latitude, longitude]);
}

/**
 * The vehicle's heading from a track of positions, oldest first.
 *
 * A reported bearing wins. Otherwise the newest leg long enough to be a course
 * is used; a cluster of fixes on one spot is not a direction.
 */
export function headingFromTrack(points, reported) {
  if (Number.isFinite(reported)) return reported;
  const track = usablePoints(points);
  if (track.length < 2) return null;
  const last = track[track.length - 1];
  for (let index = track.length - 2; index >= 0; index -= 1) {
    if (distanceKm(track[index], last) >= 0.01) {
      return bearingDegrees(track[index], last);
    }
  }
  return null;
}

/**
 * The heading of a run from the pings it has reported, newest last.
 *
 * A phone reports a bearing only while it is moving, so the newest few pings
 * are searched for one before falling back to the course it has travelled.
 */
export function headingForPings(pings) {
  const rows = (pings || []).filter(
    (ping) => ping && Number.isFinite(ping.latitude) && Number.isFinite(ping.longitude)
  );
  const reported = [...rows]
    .reverse()
    .slice(0, 5)
    .find((ping) => Number.isFinite(ping.heading_deg));
  return headingFromTrack(
    rows.map((ping) => [ping.latitude, ping.longitude]),
    reported ? reported.heading_deg : null
  );
}

/**
 * The wedge drawn around a vehicle marker, turned to its heading.
 *
 * Markup only: the pin's own class keeps its colour, and the CSS sizes the
 * wedge to reach out past a small pin. Without a heading the wedge stays
 * hidden and the marker reads as a plain dot.
 */
export function headingCone(heading) {
  const known = Number.isFinite(heading);
  const turn = known ? Math.round(heading) % 360 : 0;
  return `<i class="vehicle-heading-cone${known ? " is-known" : ""}"${
    known ? ` style="transform: rotate(${turn}deg)"` : ""
  } aria-hidden="true"></i>`;
}
