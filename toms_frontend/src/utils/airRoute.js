const MGRS_IN_TEXT = /\b\d{1,2}[C-HJ-NP-X][A-HJ-NP-Z][A-HJ-NP-V]\d{2,10}\b/i;

/** Keep only the place name, dropping an address and any trailing grid. */
export function shortLocationName(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  const first = text.split(",")[0].trim();
  // A location recorded as a grid reference has no place name to shorten to,
  // so keep the grid instead of reducing the label to the bare word "MGRS".
  if (/^MGRS\b/i.test(first)) {
    const grid = mgrsFromLocation(first);
    return grid ? `MGRS ${grid}` : first;
  }
  const withoutParenthetical = first
    .replace(/\s*\([^)]*\)\s*$/, "")
    .trim();
  const withoutGrid = withoutParenthetical
    .replace(MGRS_IN_TEXT, "")
    .replace(/[\s,;:–-]+$/, "")
    .trim();
  if (!withoutGrid) {
    const grid = mgrsFromLocation(first);
    return grid ? `MGRS ${grid}` : first || text;
  }
  return withoutGrid;
}

export function mgrsFromLocation(value) {
  return String(value || "").match(MGRS_IN_TEXT)?.[0] || "";
}

export function pointRoleCode(leg) {
  if (leg?.point_role === "target") return "TGT";
  if (leg?.point_role === "landing_zone") return "LZ";
  return "";
}

/**
 * A target or landing zone is identified by its role and grid, not by the
 * location text that may also contain an address. Every other point is only
 * its place name.
 */
export function legDestinationLabel(leg) {
  if (!leg?.destination) return leg?.overflight ? "Over" : "";
  const role = pointRoleCode(leg);
  if (role) {
    const grid = mgrsFromLocation(leg.destination);
    return grid ? `${role} (${grid})` : role;
  }
  return shortLocationName(leg.destination);
}

export function legOriginLabel(leg, previous = null) {
  if (previous?.overflight) {
    const role = pointRoleCode(previous);
    if (role) {
      const grid = mgrsFromLocation(previous.destination);
      return grid ? `${role} (${grid})` : role;
    }
    return "Over";
  }
  return (
    shortLocationName(leg?.origin) ||
    shortLocationName(previous?.destination) ||
    ""
  );
}

export function legRouteLabel(leg, previous = null) {
  const origin = legOriginLabel(leg, previous);
  const destination = legDestinationLabel(leg);
  if (!origin && !destination) return "—";
  return `${origin || "—"} - ${destination || "—"}`;
}

export function itineraryRoute(legs) {
  const chained = (legs || []).map((leg, index) =>
    index === 0
      ? leg
      : { ...leg, origin: legs[index - 1]?.destination || "" }
  );
  if (!chained.length) return "";
  return [
    shortLocationName(chained[0].origin),
    ...chained.map((leg) => legDestinationLabel(leg)),
  ]
    .filter(Boolean)
    .join(" - ");
}
