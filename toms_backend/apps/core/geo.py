"""GeoJSON helpers for AOR polygons.

The legacy database has no PostGIS, so AOR geometry is stored as GeoJSON in a
JSONB column. These helpers validate geometry before it is persisted and
provide the small set of spatial operations the API needs (centroid, bounding
box, point-in-polygon), which keeps the module portable to any PostgreSQL
instance without extensions.
"""

from __future__ import annotations

from typing import Iterable

MAX_RING_POINTS = 20000


class GeometryError(ValueError):
    """Raised when GeoJSON geometry is structurally invalid."""


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_position(position, *, what: str = "coordinate") -> list[float]:
    if not isinstance(position, (list, tuple)) or len(position) < 2:
        raise GeometryError(f"Each {what} must be [longitude, latitude].")
    lon, lat = position[0], position[1]
    if not (_is_number(lon) and _is_number(lat)):
        raise GeometryError(f"{what.capitalize()} values must be numbers.")
    if not -180.0 <= float(lon) <= 180.0:
        raise GeometryError(f"Longitude {lon} is outside -180..180.")
    if not -90.0 <= float(lat) <= 90.0:
        raise GeometryError(f"Latitude {lat} is outside -90..90.")
    return [float(lon), float(lat)]


def _validate_ring(ring, *, ring_index: int) -> list[list[float]]:
    if not isinstance(ring, (list, tuple)):
        raise GeometryError("Polygon rings must be arrays of positions.")
    if len(ring) > MAX_RING_POINTS:
        raise GeometryError(f"A ring may contain at most {MAX_RING_POINTS} points.")
    positions = [_validate_position(p, what="ring vertex") for p in ring]

    # Auto-close an open ring (Leaflet's default drawing behaviour).
    if positions and positions[0] != positions[-1]:
        positions.append(list(positions[0]))

    if len(positions) < 4:
        raise GeometryError(
            "A polygon ring needs at least 3 distinct vertices (4 positions "
            "once closed)."
        )

    distinct = {tuple(p) for p in positions[:-1]}
    if len(distinct) < 3:
        raise GeometryError("A polygon ring needs at least 3 distinct vertices.")
    return positions


def validate_geometry(geometry: dict) -> dict:
    """Validate and normalise a GeoJSON Polygon/MultiPolygon.

    Returns a normalised geometry with closed rings and float coordinates.
    Raises :class:`GeometryError` on any structural problem.
    """
    if not isinstance(geometry, dict):
        raise GeometryError("Geometry must be a GeoJSON object.")

    geo_type = geometry.get("type")
    coords = geometry.get("coordinates")

    if geo_type == "Polygon":
        rings = [_validate_ring(r, ring_index=i) for i, r in enumerate(coords or [])]
        if not rings:
            raise GeometryError("A polygon requires at least one ring.")
        return {"type": "Polygon", "coordinates": rings}

    if geo_type == "MultiPolygon":
        polygons = []
        for polygon in coords or []:
            rings = [
                _validate_ring(r, ring_index=i) for i, r in enumerate(polygon or [])
            ]
            if not rings:
                raise GeometryError("Each polygon in a MultiPolygon needs a ring.")
            polygons.append(rings)
        if not polygons:
            raise GeometryError("A MultiPolygon requires at least one polygon.")
        return {"type": "MultiPolygon", "coordinates": polygons}

    raise GeometryError(
        f"Unsupported geometry type {geo_type!r}; use Polygon or MultiPolygon."
    )


def iter_rings(geometry: dict) -> Iterable[list[list[float]]]:
    """Yield every ring in a validated Polygon/MultiPolygon."""
    if geometry.get("type") == "Polygon":
        yield from geometry.get("coordinates", [])
    elif geometry.get("type") == "MultiPolygon":
        for polygon in geometry.get("coordinates", []):
            yield from polygon


def bounding_box(geometry: dict) -> dict:
    """Return the geographic bounding box of a geometry."""
    points = [p for ring in iter_rings(geometry) for p in ring]
    if not points:
        raise GeometryError("Geometry has no coordinates.")
    lons = [p[0] for p in points]
    lats = [p[1] for p in points]
    return {
        "min_lon": min(lons),
        "min_lat": min(lats),
        "max_lon": max(lons),
        "max_lat": max(lats),
    }


def centroid(geometry: dict) -> tuple[float, float]:
    """Approximate centroid: area-weighted for polygons, mean of bbox as fallback."""
    outer_rings = []
    if geometry.get("type") == "Polygon":
        if geometry.get("coordinates"):
            outer_rings.append(geometry["coordinates"][0])
    else:
        for polygon in geometry.get("coordinates", []):
            if polygon:
                outer_rings.append(polygon[0])

    total_area = 0.0
    sum_lon = 0.0
    sum_lat = 0.0
    for ring in outer_rings:
        area = 0.0
        cx = 0.0
        cy = 0.0
        for i in range(len(ring) - 1):
            x0, y0 = ring[i][0], ring[i][1]
            x1, y1 = ring[i + 1][0], ring[i + 1][1]
            cross = x0 * y1 - x1 * y0
            area += cross
            cx += (x0 + x1) * cross
            cy += (y0 + y1) * cross
        area *= 0.5
        if abs(area) < 1e-12:
            continue
        cx /= 6 * area
        cy /= 6 * area
        total_area += abs(area)
        sum_lon += cx * abs(area)
        sum_lat += cy * abs(area)

    if total_area < 1e-12:
        box = bounding_box(geometry)
        return (
            (box["min_lon"] + box["max_lon"]) / 2,
            (box["min_lat"] + box["max_lat"]) / 2,
        )
    return sum_lon / total_area, sum_lat / total_area


def _point_in_ring(lon: float, lat: float, ring: list[list[float]]) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat):
            x_intersect = (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi
            if lon < x_intersect:
                inside = not inside
        j = i
    return inside


def _shape(geometry: dict):
    """Return a Shapely geometry for validated GeoJSON (WGS84 lon/lat)."""
    from shapely.geometry import shape

    return shape(geometry)


def planar_area_km2(geometry) -> float:
    """Approximate area in km\u00b2 for a lon/lat geometry.

    Areas are computed on a locally equal-area projection (degrees scaled at the
    geometry's mean latitude). That is accurate to well under a percent at
    Philippine latitudes and needs no GIS database extensions.
    """
    if hasattr(geometry, "geom_type"):
        shape_obj = geometry
    else:
        shape_obj = _shape(geometry)
    if shape_obj.is_empty:
        return 0.0
    min_lon, min_lat, max_lon, max_lat = shape_obj.bounds
    mean_lat = (min_lat + max_lat) / 2
    import math

    km_per_degree = 111.32
    return abs(shape_obj.area) * (km_per_degree**2) * math.cos(math.radians(mean_lat))


def geometries_intersect(first: dict, second: dict, *, tolerance: float = 1e-9) -> bool:
    """True when two polygons share more than a boundary."""
    a, b = _shape(first), _shape(second)
    if not a.intersects(b):
        return False
    overlap = a.intersection(b)
    return not overlap.is_empty and overlap.area > tolerance


def overlap_detail(first: dict, second: dict) -> dict:
    """Describe how two polygons overlap.

    Returns ``{"overlaps": bool, "area_km2": float, "ratio": float,
    "relation": str}`` where ``ratio`` is the shared area relative to the
    smaller of the two areas (1.0 = the smaller area is fully covered) and
    ``relation`` is one of ``identical|contains|contained|partial|none``.
    """
    a, b = _shape(first), _shape(second)
    if not a.intersects(b):
        return {"overlaps": False, "area_km2": 0.0, "ratio": 0.0, "relation": "none"}
    shared = a.intersection(b)
    if shared.is_empty:
        return {"overlaps": False, "area_km2": 0.0, "ratio": 0.0, "relation": "none"}

    shared_area = planar_area_km2(shared)
    area_a = planar_area_km2(a)
    area_b = planar_area_km2(b)
    smaller = min(area_a, area_b) or 1.0
    ratio = min(1.0, shared_area / smaller)

    if ratio > 0.999:
        relation = "identical" if abs(area_a - area_b) / max(area_a, area_b) < 0.01 else (
            "contains" if area_a > area_b else "contained"
        )
    else:
        relation = "partial"
    return {
        "overlaps": shared_area > 0.0,
        "area_km2": round(shared_area, 3),
        "ratio": round(ratio, 4),
        "relation": relation,
    }


def union_geometries(geometries: Iterable[dict], *, simplify: float = 0.0) -> dict | None:
    """Merge polygons into one GeoJSON geometry (for "mother unit" coverage).

    Returns ``None`` when nothing usable was supplied. The result is simplified
    slightly by default so a union of many provinces stays a reasonable payload
    for the browser; the tolerance is in degrees (~0.002 deg is about 200 m),
    far below what matters for an area-of-responsibility outline.
    """
    from shapely.geometry import mapping
    from shapely.ops import unary_union

    shapes = []
    for geometry in geometries:
        if not geometry:
            continue
        try:
            shape_obj = _shape(geometry)
        except Exception:  # pragma: no cover - malformed stored geometry
            continue
        if shape_obj.is_empty:
            continue
        if not shape_obj.is_valid:
            shape_obj = shape_obj.buffer(0)
        if shape_obj.is_empty:
            continue
        shapes.append(shape_obj)

    if not shapes:
        return None

    merged = unary_union(shapes)
    if simplify:
        merged = merged.simplify(simplify, preserve_topology=True)
    if merged.is_empty:
        return None
    payload = mapping(merged)
    return {"type": payload["type"], "coordinates": payload["coordinates"]}


def geometry_is_valid(geometry: dict) -> bool:
    """True when Shapely considers the polygon structurally valid."""
    from shapely.validation import explain_validity

    shape_obj = _shape(geometry)
    return shape_obj.is_valid or explain_validity(shape_obj) == "Valid Geometry"


def point_in_geometry(lon: float, lat: float, geometry: dict) -> bool:
    """Return True when the point falls inside the polygon/MultiPolygon."""
    polygons = []
    if geometry.get("type") == "Polygon":
        polygons.append(geometry.get("coordinates", []))
    elif geometry.get("type") == "MultiPolygon":
        polygons.extend(geometry.get("coordinates", []))

    for polygon in polygons:
        if not polygon:
            continue
        if _point_in_ring(lon, lat, polygon[0]):
            # Respect holes.
            if not any(_point_in_ring(lon, lat, hole) for hole in polygon[1:]):
                return True
    return False
