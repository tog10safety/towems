"""Load the bundled Philippine administrative boundaries into TOWEMS.

    python manage.py import_admin_boundaries
    python manage.py import_admin_boundaries --dry-run
    python manage.py import_admin_boundaries --file path/to/file.geojson

The source file is ``apps/ops/data/ph_admin_boundaries.geojson``, built by
``tools/build_admin_boundaries.py`` from the PSA PSGC dataset (see
``docs/AOR_GIS_ENHANCEMENT.md``). The command is idempotent: it inserts new
PSGC codes, updates changed names/geometries and never deletes an area that an
AOR already points at.
"""

from __future__ import annotations

import json
import os

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core import geo
from apps.ops.models import AdministrativeArea

DEFAULT_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "ph_admin_boundaries.geojson",
)

LEVEL_ALIASES = {
    "prov": AdministrativeArea.LEVEL_PROVINCE,
    "province": AdministrativeArea.LEVEL_PROVINCE,
    "dist": AdministrativeArea.LEVEL_DISTRICT,
    "district": AdministrativeArea.LEVEL_DISTRICT,
    "city": AdministrativeArea.LEVEL_CITY,
    "mun": AdministrativeArea.LEVEL_MUNICIPALITY,
    "municipality": AdministrativeArea.LEVEL_MUNICIPALITY,
}


class Command(BaseCommand):
    help = "Import Philippine province/city boundaries for AOR assignment."

    def add_arguments(self, parser):
        parser.add_argument("--file", default=DEFAULT_FILE, help="GeoJSON FeatureCollection")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate and report without writing anything",
        )

    def handle(self, *args, **options):
        path = options["file"]
        if not os.path.exists(path):
            raise CommandError(f"Boundary file not found: {path}")

        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        features = payload.get("features") or []
        if not features:
            raise CommandError("The boundary file contains no features.")

        created = updated = unchanged = 0
        skipped: list[str] = []

        with transaction.atomic():
            for feature in features:
                props = feature.get("properties") or {}
                psgc = props.get("psgc")
                name = (props.get("name") or "").strip()
                level = LEVEL_ALIASES.get(str(props.get("level") or "").lower())
                if not psgc or not name or level is None:
                    skipped.append(f"{psgc or '?'}: missing psgc/name/level")
                    continue

                try:
                    geometry = geo.validate_geometry(feature.get("geometry"))
                    if not geo.geometry_is_valid(geometry):
                        skipped.append(f"{psgc}: invalid geometry")
                        continue
                    box = geo.bounding_box(geometry)
                    centre = geo.centroid(geometry)
                except geo.GeometryError as exc:
                    skipped.append(f"{psgc}: {exc}")
                    continue

                parent_name = props.get("parent_name") or ""
                region_name = props.get("region_name") or ""
                search_text = " ".join(
                    part
                    for part in [
                        name.lower(),
                        level,
                        parent_name.lower(),
                        region_name.lower(),
                        str(psgc),
                    ]
                    if part
                )
                defaults = {
                    "name": name,
                    "level": level,
                    "parent_psgc": props.get("parent_psgc"),
                    "parent_name": parent_name,
                    "region_psgc": props.get("region_psgc"),
                    "region_name": region_name,
                    "area_km2": props.get("area_km2"),
                    "geometry": geometry,
                    "search_text": search_text,
                    "min_lon": box["min_lon"],
                    "min_lat": box["min_lat"],
                    "max_lon": box["max_lon"],
                    "max_lat": box["max_lat"],
                    "centroid_lon": centre[0],
                    "centroid_lat": centre[1],
                }

                existing = AdministrativeArea.objects.filter(pk=psgc).first()
                if existing is None:
                    if not options["dry_run"]:
                        AdministrativeArea.objects.create(psgc=psgc, **defaults)
                    created += 1
                    continue

                changed = [key for key, value in defaults.items() if getattr(existing, key) != value]
                if changed:
                    if not options["dry_run"]:
                        for key in changed:
                            setattr(existing, key, defaults[key])
                        existing.save(update_fields=changed)
                    updated += 1
                else:
                    unchanged += 1

        prefix = "DRY RUN — " if options["dry_run"] else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}{created} created, {updated} updated, {unchanged} unchanged, "
                f"{len(skipped)} skipped"
            )
        )
        for line in skipped[:20]:
            self.stdout.write(self.style.WARNING(f"  skipped {line}"))
        if skipped[20:]:
            self.stdout.write(self.style.WARNING(f"  … {len(skipped) - 20} more"))
        return None
