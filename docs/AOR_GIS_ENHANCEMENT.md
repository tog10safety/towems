# AOR management, Philippine boundaries, basemaps and GIS layers

**Scope:** enhancement of the existing AOR / GIS module — Administrator-only AOR management,
search and assignment of official PSGC province/city boundaries, geometric conflict detection,
safe reassignment, configurable basemaps and independent operational layers.

**Status:** implemented and verified against `toms_staging` and in the browser. **The production
database was not contacted, migrated or deployed.** One additive migration and one data import
remain to be approved for production (see §9).

---

## 1. What existed before (gap analysis)

| Area | Before | Gap | Now |
| --- | --- | --- | --- |
| AOR writes | Free-drawn GeoJSON polygons; any Unit Safety Officer or Administrator could create them | Not Administrator-only (spec §1) | `IsAdministrator` on every write; Unit Safety Officer lost the ability (tests updated) |
| AOR data | One polygon per row, GeoJSON JSONB, no link to official geography | No province/city source, no boundary polygons | `AdministrativeArea` table (1,730 PSGC areas) + `AreaOfResponsibility.admin_area` |
| Coverage conflicts | None — overlapping AORs could be created freely | No geometric checks, no transfers | Shapely-backed overlap detection, partial unique index, confirmed transfers, overlap report |
| Boundary search | None | No search endpoint/UI | `/admin-areas/` search + Manage AOR modal |
| Map basemaps | Hard-coded OSM (light) / CARTO dark (theme-driven) | One style, no choice | Server-configured providers: streets, night streets, satellite, hybrid, terrain, organisation |
| GIS layers | Dashboard had 6 checkboxes bound to one endpoint; AOR page had none | No shared panel, no administrative boundaries layer | Shared `MapLayersPanel`, province/city boundary layer on both maps |
| Audit | Generic create/update/destroy audit | No assignment/transfer/removal trail | `aor_assign`, `aor_transfer`, `aor_remove` entries with before/after |

Existing behaviour that was deliberately kept: free-form AOR polygons (still supported for
Administrators, now overlap-checked), `/aor/at-point/`, MGRS conversion, mission/AOR search,
hazard and vehicle layers, and all unit scoping.

---

## 2. Data source and provenance

* **Dataset:** PSA **PSGC** administrative boundaries, Philippine Standard Geographic Code
  (3Q 2026 release).
* **Distribution:** `faeldon/philippines-json-maps` v1.0.0, low-resolution GeoJSON (MIT licence),
  reprojected to WGS84 (EPSG:4326) from the source shapefiles (EPSG:32651).
* **Bundled file:** `toms_backend/apps/ops/data/ph_admin_boundaries.geojson` — 1.20 MB,
  **1,730 areas**: 82 provinces, 6 districts/special areas (NCR districts, "Special Geographic
  Area", "City of Isabela (Not a Province)"), 149 cities, 1,493 municipalities. Barangays are
  excluded on purpose: they are not needed for unit AORs and would multiply the file size.
* **Provenance is reproducible:** `tools/build_admin_boundaries.py <extracted-release-dir>
  <output.geojson>` merges the per-region/per-province files, normalises properties, rounds
  coordinates to 4 decimals (~11 m, far below the source's low resolution) and repairs the single
  self-intersecting source polygon (`1606707000`, Las Nieves, Agusan del Norte) with
  `make_valid`, keeping its largest piece. Every repair is printed for review.
* **Import:** `python manage.py import_admin_boundaries` (idempotent; `--dry-run` available).
  It never deletes an area an AOR points at, and skips (reporting) any feature with a missing
  code, name or level or an invalid geometry.
* **No duplicate dataset:** the file is the only boundary dataset in the project; the importer and
  the build script are the only writers.

`AdministrativeArea` stores the authoritative polygon plus derived bbox/centroid and the source
`area_km2`. Nothing is approximated with circles, points or hand-drawn shapes.

---

## 3. Conflict rules (implemented)

The default policy is **no shared territory between different units**, evaluated on the actual
polygons with Shapely (not on names):

* `identical` — same geometry held by another unit → **transfer** (needs explicit confirmation).
* `contained` / `contains` — one area covers the other (e.g. a city inside a province, or a
  province inside a larger unit AOR) → **refused** when the units differ.
* `partial` — any other geometric intersection larger than a sliver → **refused**.
* Same unit may stack areas (a unit covering a province and one of its cities is allowed).
* Shared edges are not conflicts: a tolerant area comparison ignores zero-area intersections.

Behaviour guarantees:

1. `assign` runs in one transaction; if any selected area conflicts, nothing is written (verified
   by a test that mixes a free area with a conflicting one).
2. Area rows are locked (`SELECT … FOR UPDATE OF self`) while validating, so two concurrent
   requests cannot both pass. The partial unique index `ops_aor_active_adminarea_uniq`
   (`admin_area IS NOT NULL AND is_active`) is the database backstop.
3. Transfers re-use the existing row: the owning unit changes, the previous assignment is captured
   in the audit entry (`before.unit` → `after.unit`) and in the note.
4. Existing overlapping AORs are never changed automatically. `GET /aor/overlaps/` reports every
   pair (unit, areas, relation, shared km²) for Administrator review.
5. Removal deactivates the row (`is_active=False`) instead of deleting it, so history survives and
   the area can be assigned again later (the same row is reactivated).

---

## 4. API

| Method | Path | Access | Purpose |
| --- | --- | --- | --- |
| GET | `/admin-areas/?q=&level=&region=&parent=&limit=` | authenticated | Partial, case-insensitive search; returns name, level, parent, region, bbox, centroid (no geometry) |
| GET | `/admin-areas/{psgc}/` | authenticated | The area **with its real boundary geometry** |
| GET | `/admin-areas/boundaries/?level=province\|city\|local\|district\|all&psgc=1,2` | authenticated | GeoJSON FeatureCollection for the map layer (`assigned_unit` included) |
| POST | `/aor/assign/` | **Administrator** | `{unit, psgcs[], remove_psgcs[], confirm_transfer, color}`; returns assigned/transferred/removed/unchanged, or `400 {code:"overlap", conflicts:[…]}` |
| GET | `/aor/coverage/?unit=` | authenticated (scoped) | Coverage grouped by unit with the assigned areas and km² |
| GET | `/aor/overlaps/` | **Administrator** | Existing overlaps, read-only |
| POST | `/aor/{id}/remove/` | **Administrator** | Soft removal of one AOR |
| DELETE | `/aor/{id}/` | **Administrator** | Same as remove (204, row retained) |
| GET | `/map/config/` | authenticated | Basemap providers + layer catalogue + data-flow notes |

Unit scope is enforced server-side everywhere: the AOR queryset is filtered by
`unit_ids_for_filter`, and `assign` refuses a unit outside the actor's scope. The frontend hides
the controls for non-Administrators, but every write is re-checked.

---

## 5. Basemaps: providers, licensing, cost, data flow

Configuration lives in `apps/ops/map_config.py` (server side), so a provider can be added,
removed or re-pointed without touching AOR, hazard, mission or vehicle code.

| Option | Provider | Licence / terms | Key | Cost |
| --- | --- | --- | --- | --- |
| Standard streets | OpenStreetMap tiles | ODbL 1.0, attribution required | no | free (fair-use policy; heavy use should self-host) |
| Standard streets (night) | Esri World Dark Gray Canvas + reference labels | Esri terms, attribution required | no | free basemap service; tiles cached to zoom 16 and upscaled beyond |
| Satellite imagery | Esri World Imagery | Esri terms, attribution required | no | free basemap service; review Esri terms before commercial use |
| Satellite with labels | Esri World Imagery + Reference labels | as above | no | as above |
| Terrain / topographic | OpenTopoMap | CC-BY-SA 3.0 | no | free |
| Organisation basemap | self-hosted tile service | internal | no | internal hosting |

Google tiles are **not** used and no scraping or unauthorised URLs exist in the codebase.
`TOMS_INTERNAL_TILE_URL` makes the self-hosted option appear; while it is unset the option is
reported as unavailable with its reason, so the UI never offers a basemap that cannot load.

**Data flow:** tile requests contain only `z/x/y`, the client IP and browser metadata. Mission,
personnel, vehicle, hazard, AOR and authentication data are never added to a tile URL — operational
features come from the authenticated TOWEMS API and are drawn locally. No provider key or server
secret is sent to the browser; the config endpoint contains no credentials (asserted by a test).
A provider failure is detected (`tileerror`) and the UI offers the other configured basemaps; the
operational layer keeps working.

---

## 6. Layers

`/map/config/` publishes the catalogue: `aor`, `admin_boundaries`, `missions`, `air`, `ground`,
`vehicles`, `trips`, `hazards`. Toggles are independent and never change records or permissions.
The AOR page draws AOR + province/city outlines + the selection preview; the dashboard draws the
unit-scoped operational picture plus the same boundary layer. Vehicle positions keep their
"last known" label and timestamp; nothing is presented as live tracking.

---

## 7. Files changed or added

**Backend**

* `apps/ops/models.py` — `AdministrativeArea`; `AreaOfResponsibility.admin_area` + partial unique index
* `apps/ops/migrations/0005_aor_admin_areas.py` — additive migration
* `apps/ops/aor.py` — conflict detection, assignment, transfer, removal, overlap report
* `apps/ops/map_config.py` — basemap/layer configuration
* `apps/ops/serializers.py` — admin-area, assignment and AOR serializers; overlap validation for manual polygons
* `apps/ops/views.py` — `AdministrativeAreaViewSet`, `MapConfigView`, Administrator-only AOR writes, `assign`/`remove`/`coverage`/`overlaps`
* `apps/ops/urls.py` — `admin-areas` route and `/map/config/`
* `apps/ops/management/commands/import_admin_boundaries.py` — importer
* `apps/ops/data/ph_admin_boundaries.geojson` — bundled dataset
* `apps/ops/tests/test_aor_admin.py` — 23 tests; `apps/ops/tests/test_gis.py` — AOR-write fixtures updated to Administrator
* `apps/core/geo.py` — Shapely-backed intersection, overlay detail, area, validity
* `apps/core/roles.py` — `manage_aor` capability (Administrator only, non-delegable), payload flag
* `apps/ops/requirements`/`toms_backend/requirements.txt` — `shapely==2.0.6`
* `tools/build_admin_boundaries.py` — reproducible dataset build

**Frontend**

* `src/map/config.js` — basemap resolution and persistence keys
* `src/components/map/BasemapControl.jsx`, `MapBasemapLayer.jsx`, `MapLayersPanel.jsx`, `Boundaries.jsx`
* `src/pages/ManageAORModal.jsx` — search, filters, multi-select, review/removal, conflicts, transfer confirmation
* `src/pages/AORPage.jsx` — administrator gating, coverage summary, basemap/layers, preview
* `src/components/DashboardMap.jsx` — basemap selector and boundary layer
* `src/styles.css` — styles for the new controls

---

## 8. Tests and verification (actual results)

* `python -m pytest -o addopts="" -q` in `toms_backend`: **274 passed, 0 failed, 0 skipped**
  (23 in `test_aor_admin.py`). Coverage for: province/city search and disambiguation, actual
  geometry retrieval, boundary FeatureCollection, assignment with derived bbox/centroid, idempotent
  re-assignment, duplicate assignment refused, transfer requires confirmation and is audited,
  partial overlap refused, city-inside-another-unit's-province refused, same-unit stacking allowed,
  atomic failure, soft removal + re-assignment, existing overlaps reported unchanged, manual polygon
  overlap refused, non-Administrator refused (create/assign/remove/delete/patch) for Creator and
  Unit Safety Officer, unit-scope visibility, unauthenticated refusal, map config (licensed
  providers, unavailable internal basemap, no secrets), dataset import counts and idempotency.
* Frontend production build: success (119 modules, 481.29 kB / 140.62 kB gzip).
* Staging: `migrate ops` applied `0005_aor_admin_areas`; `import_admin_boundaries` imported
  **1,730 areas, 0 skipped**.
* Browser walkthrough (Vite 5180 → Django 8010, staging DB, Administrator):
  * search "Batangas" returned `Batangas` (province) and `Batangas City` (city) with level,
    parent and region; selecting the province previewed its real outline;
  * assigned Batangas to TOG10 → “Coverage updated: 1 assigned”; coverage card showed the area;
  * assigning Batangas City to another unit was refused with the conflict table
    (`TOG10 — Batangas`, partially overlaps, 273.885 km²) and no transfer button;
  * assigning the province itself showed `already assigned` and the **Confirm transfer** action;
    confirming produced “1 transferred” and an `aor_transfer` audit entry
    (`before unit 3 → after unit 2`);
  * basemap switched to satellite (tiles from `server.arcgisonline.com`), AOR polygons stayed
    drawn, selection survived a page reload;
  * province/city boundary layer rendered 82 outlines on both the AOR page and the dashboard;
  * the self-hosted provider was listed as unavailable with its configuration reason; then,
    with `TOMS_INTERNAL_TILE_URL` pointed at a dead endpoint, selecting it produced the
    “could not be reached” banner (tiles requested from that host, operational polygons still
    drawn), and **Use another basemap** switched back to standard streets and cleared the banner;
    the variable was then removed and the provider returned to “not configured”.
* Two defects were found and fixed during verification: `FOR UPDATE` on a nullable joined column
  (PostgreSQL `FeatureNotSupported`) and the coverage total dereferencing a null admin area on a
  manually drawn AOR. Both now have regression coverage.

---

## 9. Deployment, backup, rollback (requires approval)

Nothing below has been run against production.

1. **Backup** the production database (dump + restore test) and re-check `signatures/`,
   `approved_pdfs/`, `static/unit_logos/`.
2. **Review** `migrate --plan`: expect only `ops.0005_aor_admin_areas`
   (`CreateModel ops_administrative_area`, `AddField areaofresponsibility.admin_area`,
   `AddConstraint ops_aor_active_adminarea_uniq`). No legacy object is altered.
3. **Apply** the migration to staging first (already done here), run `verify_legacy_mapping`,
   then apply with `DJANGO_SETTINGS_MODULE=toms.settings.prod` only after approval.
4. **Import boundaries** on the target: `python manage.py import_admin_boundaries` (idempotent;
   expect 1,730 areas with zero skips).
5. **Configuration:** optionally set `TOMS_DEFAULT_BASEMAP` (id from `/map/config/`) and
   `TOMS_INTERNAL_TILE_URL` (+ `TOMS_INTERNAL_TILE_ATTRIBUTION`, `TOMS_INTERNAL_TILE_MAX_ZOOM`)
   for a self-hosted basemap.
6. **Smoke test:** sign in as Administrator, open AOR / GIS, search a province, assign it to a
   unit, verify the audit entry, switch basemaps, toggle the boundary layer; confirm a
   non-Administrator sees no management controls and receives 403 from `POST /api/aor/assign/`.
7. **Rollback:** the previous release ignores the new table and nullable column, so reverting the
   code is safe without a data rollback. If the schema must be reverted, `migrate ops 0004`
   removes only the new objects; AOR rows created from administrative areas are retained (their
   geometry is stored on the AOR row itself, so nothing depends on the dataset once assigned).

---

## 10. Outstanding issues and decisions for approval

1. **Data quality:** the low-resolution dataset generalises coastlines; a city polygon can extend
   slightly beyond its province polygon, which is reported as a `partial` overlap rather than
   `contained`. Using the medium/high-resolution release (11.5 MB / 74 MB) would tighten this at
   the cost of a larger bundle and a slower import.
2. **Policy exceptions:** the no-overlap rule is implemented as the default. A documented
   organisational policy allowing a province on one unit and a city inside it on another would
   need an explicit, audited exception mechanism — not implemented.
3. **Basemap licensing:** Esri World Imagery is used without a key. Before production use confirm
   that the organisation's Esri terms permit it, or set `TOMS_INTERNAL_TILE_URL` to a self-hosted
   service. No external provider is contacted by the backend.
4. **Boundary layer weight:** `level=all` returns ~1.2 MB of GeoJSON; the UI requests provinces by
   default. City-level display is available per selected areas (`psgc=`) but not yet wired to an
   automatic "only assigned cities" mode.
5. **Verification data:** the staging walkthrough assigned Batangas to "IT" (via a confirmed
   transfer from TOG10) and left `TOWEMS-GO-20261028-002` / MRM `2026102801` from the earlier phase.
   These are staging-only records.
