"""Build TOWEMS' bundled Philippine administrative-boundary dataset.

Source: ``faeldon/philippines-json-maps`` release v1.0.0 (PSGC 3Q 2026),
low-resolution GeoJSON, MIT licensed, derived from PSA/NAMRIA boundaries.
TOWEMS only needs provinces (adm2) and cities/municipalities (adm3); barangays
are deliberately excluded, so the bundled file stays small.

    python tools/build_admin_boundaries.py <extracted-release-dir> \
        toms_backend/apps/ops/data/ph_admin_boundaries.geojson

The output is a single FeatureCollection with one feature per administrative
area. Properties are normalised (``psgc``, ``name``, ``level``, parents, area),
coordinates are rounded to 4 decimals (~11 m, well below the source's low
resolution) and consecutive duplicate vertices are dropped.
"""

from __future__ import annotations

import glob
import json
import os
import sys

LEVELS = {
    "Prov": "province",
    "Dist": "district",
    "City": "city",
    "Mun": "municipality",
}

ROUND_TO = 4


def _round_ring(ring):
    out = []
    for point in ring:
        rounded = [round(float(point[0]), ROUND_TO), round(float(point[1]), ROUND_TO)]
        if not out or out[-1] != rounded:
            out.append(rounded)
    if len(out) >= 3 and out[0] != out[-1]:
        out.append(list(out[0]))
    return out


REPAIRED = []


def _repair(geometry, psgc):
    """Fix a self-intersecting source polygon, keeping its largest piece.

    One source area (Agusan del Norte's Las Nieves) ships a self-intersecting
    ring. `make_valid` + keeping the largest resulting polygon removes the
    sliver artefact while preserving the real outline; the repair is reported
    so it can be reviewed rather than silently assumed.
    """
    from shapely.geometry import mapping, shape
    from shapely.validation import make_valid

    shape_obj = shape(geometry)
    if shape_obj.is_valid:
        return geometry

    fixed = make_valid(shape_obj)
    pieces = [
        part
        for part in getattr(fixed, "geoms", [fixed])
        if part.geom_type in {"Polygon", "MultiPolygon"} and not part.is_empty
    ]
    if not pieces:
        raise SystemExit(f"{psgc}: geometry could not be repaired")
    largest = max(pieces, key=lambda part: part.area)
    REPAIRED.append(psgc)
    return mapping(largest)


def _round_geometry(geometry):
    kind = geometry["type"]
    coords = geometry["coordinates"]
    if kind == "Polygon":
        return {"type": kind, "coordinates": [_round_ring(r) for r in coords]}
    if kind == "MultiPolygon":
        return {
            "type": kind,
            "coordinates": [[_round_ring(r) for r in poly] for poly in coords],
        }
    raise SystemExit(f"Unexpected geometry type {kind}")


def _finalize(geometry, psgc):
    """Round, then repair if rounding introduced a self-intersection."""
    from shapely.geometry import shape

    rounded = _round_geometry(geometry)
    if shape(rounded).is_valid:
        return rounded
    return _round_geometry(_repair(rounded, psgc))


def load_region_names(extracted: str) -> dict[int, str]:
    path = glob.glob(
        os.path.join(extracted, "geojson/country/lowres/*.json")
    )[0]
    data = json.load(open(path, encoding="utf-8"))
    return {
        int(f["properties"]["adm1_psgc"]): f["properties"].get("adm1_en", "")
        for f in data["features"]
    }


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    extracted, destination = sys.argv[1], sys.argv[2]
    regions = load_region_names(extracted)

    features: dict[int, dict] = {}

    for path in sorted(glob.glob(os.path.join(extracted, "geojson/regions/lowres/*.json"))):
        for feature in json.load(open(path, encoding="utf-8"))["features"]:
            props = feature["properties"]
            psgc = int(props["adm2_psgc"])
            level = LEVELS.get(props.get("geo_level") or "", "district")
            features[psgc] = {
                "type": "Feature",
                "properties": {
                    "psgc": psgc,
                    "name": props.get("adm2_en") or str(psgc),
                    "level": level,
                    "parent_psgc": None,
                    "parent_name": None,
                    "region_psgc": int(props["adm1_psgc"]),
                    "region_name": regions.get(int(props["adm1_psgc"]), ""),
                    "area_km2": props.get("area_km2"),
                },
                "geometry": _finalize(feature["geometry"], psgc),
            }

    for path in sorted(glob.glob(os.path.join(extracted, "geojson/provdists/lowres/*.json"))):
        for feature in json.load(open(path, encoding="utf-8"))["features"]:
            props = feature["properties"]
            psgc = int(props["adm3_psgc"])
            if psgc in features:
                continue
            parent = int(props["adm2_psgc"])
            features[psgc] = {
                "type": "Feature",
                "properties": {
                    "psgc": psgc,
                    "name": props.get("adm3_en") or str(psgc),
                    "level": LEVELS.get(props.get("geo_level") or "", "municipality"),
                    "parent_psgc": parent,
                    "parent_name": features.get(parent, {})
                    .get("properties", {})
                    .get("name"),
                    "region_psgc": int(props["adm1_psgc"]),
                    "region_name": regions.get(int(props["adm1_psgc"]), ""),
                    "area_km2": props.get("area_km2"),
                },
                "geometry": _finalize(feature["geometry"], psgc),
            }

    collection = {
        "type": "FeatureCollection",
        "name": "ph_admin_boundaries",
        "source": (
            "PSA PSGC 3Q 2026 boundaries, via faeldon/philippines-json-maps "
            "v1.0.0 (low resolution, MIT licence)"
        ),
        "features": [features[key] for key in sorted(features)],
    }

    os.makedirs(os.path.dirname(destination), exist_ok=True)
    with open(destination, "w", encoding="utf-8") as handle:
        json.dump(collection, handle, ensure_ascii=False, separators=(",", ":"))

    count = len(collection["features"])
    size = os.path.getsize(destination) / 1048576
    levels: dict[str, int] = {}
    for feature in collection["features"]:
        levels[feature["properties"]["level"]] = (
            levels.get(feature["properties"]["level"], 0) + 1
        )
    print(f"wrote {count} areas to {destination} ({size:.2f} MB)")
    print("levels:", levels)
    print("repaired self-intersections:", sorted(REPAIRED) or "none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
