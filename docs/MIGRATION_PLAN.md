# Migration Plan — Flask MRMS → Django/DRF TOWEMS

Status: **Stages 1–8 complete in this workspace. Stage 9 (deployment) prepared but NOT executed.
Stage 10 (production cutover) awaits your explicit approval.**

Read together with `TECHNICAL_AUDIT.md`, `DATABASE_COMPATIBILITY.md`, `MIGRATION_PLAN.md`
(this file), `TOWEMS_MODULES.md`, `DEPLOYMENT_AND_ROLLBACK.md`, `CHANGES.md` and
`PROJECT_CLEANUP_REPORT.md`.

> **Note on paths.** Legacy Flask files referenced below were archived on 2026-10-28 into
> `Flask MRM OLD/` with their relative structure intact; `config.py` and the asset directories
> (`signatures/`, `static/`, `approved_pdfs/`, `uploads/`) deliberately stayed in the root
> because the Django backend and `tools/` still read them.

---

## 0. Ground rules honoured by this plan

1. The production database `mrm_system` (PostgreSQL 18.4, DigitalOcean) is the source of truth.
2. No `DROP`, `ALTER`, `TRUNCATE`, `DELETE`, `RENAME` or type change was executed against any
   legacy table, column, constraint, index, enum or row — during any stage of this work.
3. The only database created was the staging clone `toms_staging`, by
   `tools/staging_db.py --create` using `CREATE DATABASE toms_staging TEMPLATE mrm_system`
   (an exact, read-only-on-source copy; it refuses to overwrite an existing clone and refuses
   to run when `DATABASE_URL` already points at staging).
4. The only DDL executed anywhere was Django's `0001_initial`, and only through
   `tools/staging_manage.py migrate`, which hard-fails unless the resolved database name is
   exactly `toms_staging`.
5. Tests run against a SQLite test database (`toms.settings.test`), never PostgreSQL.
6. No credentials are printed, logged or committed; `.env.example` files contain names only.

---

## Stage 1 — Inspection ✅

* Verified the exact path `C:\Users\ceist\Documents\FREEBUFF\mrm-system`.
* Inventoried the legacy app: `app.py` (Flask factory + `create_app`), `routes/`
  (`main`, `mrm`, `admin`, `unit_management`, …), `services/` (auth, pdf, mrm_backend,
  edit_permissions, filter/import), `templates/` (Jinja + Bootstrap), `models.py`
  (Flask-SQLAlchemy), `config.py` (per-environment classes), `migrations/` (Alembic scaffold,
  effectively unused — the schema is created by `db.create_all()` at boot and has drifted),
  `migrate_*.py` ad-hoc data scripts, `tests/`.
* Extracted the live schema to `docs/database_schema.json`
  (11 tables, 9 enums, server `18.4`, database `mrm_system`) → `DATABASE_SCHEMA_INVENTORY.md`.
* Authentication: session cookie `mrm_session`, Werkzeug **scrypt** hashes, four authorities,
  unit-scoped permissions via `unit_assignments` + `units` hierarchy.

**Deliverables:** `docs/TECHNICAL_AUDIT.md`, `docs/DATABASE_SCHEMA_INVENTORY.md`,
`docs/database_schema.json`, `docs/DATABASE_COMPATIBILITY.md`.

## Stage 2 — Audit, compatibility matrix, plan ✅

* Compatibility matrix maps every legacy table to a Django model and states the ownership
  decision (`managed = False` for all 11 production tables).
* Documented the evaluator-selection defect cluster and the security findings (S1–S4).
* Backup of pre-existing project files written to `_backups/` (source + config, **excluding**
  `venv/`, `myenv/`, `env/`, `.venv*`, `node_modules/`, `__pycache__/`, `.env`, secrets,
  uploaded signatures and generated PDFs).

## Stage 3 — Django ↔ existing database in staging ✅

* `tools/staging_db.py --create` → `toms_staging`.
* `toms_backend/` created: Django 5.2.18 + DRF 3.15.2 + simplejwt + django-filter +
  drf-spectacular + cors-headers + psycopg2-binary + werkzeug (hash verification).
* Settings split: `toms.settings.{base,dev,staging,prod,test}` — `prod` refuses
  `DEBUG`, requires `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, CSRF/CORS origins.
* Model mapping verified against the live schema by
  `manage.py verify_legacy_mapping` (presence, types, nullability, length limits, FK columns,
  enum labels) — **passes with zero mismatches** after fixing the three real differences it
  found (`users.unit` NOT NULL, two FK attribute names, two `max_length`s, one false positive
  from enum internal size).
* `ops/migrations/0001_initial.py` applied **only** to `toms_staging`.
  Result: **32 tables = 11 legacy (untouched) + 21 additive**; **9,810 legacy rows intact**;
  no legacy table dropped, renamed or altered.

## Stage 4 — MRMS functionality + regression tests ✅

* Risk register API: list/detail/create/submit/review/approve/recalculate/assign-evaluator,
  legacy risk scale and `get_risk_category` thresholds reproduced with golden values from
  real rows.
* Evaluator bug fixed at the server (see `TOWEMS_MODULES.md` §7).
* 128 pytest cases green.

## Stage 5 — Personnel, inventory, missions, units ✅

Personnel (`users` mapped, deactivation over deletion), inventory with transactional stock
movements, Air/Ground missions with mutually exclusive detail rows, unit hierarchy read from
the legacy `units` table.

## Stage 6 — Vehicle trips, GIS/AOR, reports ✅

Trips with M2M vehicles + itinerary legs; Leaflet map, MGRS ⇄ WGS84 conversion and AOR
polygons stored as GeoJSON in new tables (no PostGIS extension added); seven report endpoints
with CSV export.

## Stage 7 — React integration, permissions, interface ✅

* `toms_frontend/` — React 18.3.1 + Vite 5.4.8 + react-router 6 + leaflet/react-leaflet,
  Node.js tooling only (no second backend).
* 15 routes (login, dashboard, missions list/new/detail, risk list/detail, personnel, units,
  inventory, vehicles, trips, AOR, reports, audit), protected by a real session/JWT check
  against `/api/auth/me/`; menu items are filtered by permission but **every** rule is
  enforced server-side.
* Dropdown/selection correctness: selected values are seeded from the record, kept in state,
  submitted as the id the API expects, and re-seeded after reload (evaluator, unit, personnel,
  vehicle, category selects).
* Dev proxy `/api → 127.0.0.1:8010`; production build emits static assets Django serves.

## Stage 8 — Comprehensive testing ✅

| Check | Result |
| --- | --- |
| `pytest` (unit/integration, SQLite test DB) | **128 passed / 0 failed / 0 skipped** |
| `manage.py verify_legacy_mapping` (staging) | **0 mismatches** |
| `manage.py check` (staging) | **no issues** |
| `vite build` | **success** — 99 modules, 393 kB (118 kB gzip) |
| `tools/smoke_test.py` against a live server on `127.0.0.1:8010` | **all endpoints OK** — 485 personnel, 292 MRMs, 39 evaluators, MGRS round-trip, unauthenticated requests correctly refused (`403`) |
| Browser walkthrough (Vite on `127.0.0.1:5180`) | login → dashboard (484 personnel, 71 risk items) → risk list → evaluator assign/restore, error path verified → **MRM PDF opened from the UI** (7-page document, 573 KB, `%PDF-1.4`) |
| Flask app | untouched and still runnable (rollback path) |
| Production database | **never written to; no migration run** |
| Deployment to DigitalOcean | **not attempted** (requires your approval) |

## Stage 9 — Deployment & rollback documentation ✅ (documentation only)

`docs/DEPLOYMENT_AND_ROLLBACK.md` contains the DO deployment steps, backup/restore
procedure, cutover checklist and rollback procedure. Nothing was deployed.

## Stage 10 — Production cutover ⏸ awaiting approval

Prerequisites before I ask for the go/no-go:

1. Re-run schema introspection against production and diff against
   `docs/database_schema.json` (drift check).
2. Take and **verify** a fresh `pg_dump` + restore test.
3. Run the additive migration against production (this is the one irreversible-ish step and
   needs your explicit written approval).
4. Dual-run window: Flask serves users while Django serves `/api` for comparison.
5. Switch traffic (reverse proxy), monitor, and keep the Flask app and the pre-cutover dump
   ready for rollback.

---

## Why this order

The legacy schema is not under migration control, so it had to be introspected first and
frozen as the contract. Staging was cloned before any model was written, so every mapping
claim could be machine-verified rather than assumed. Only after the mapping proved clean did
feature work start — otherwise bugs would have been built on a wrong schema. The frontend
came last because its contract is the API, not the database.

## Known gaps (tracked in `CHANGES.md`)

* Mission **editing** UI exists only for vehicle trips and risk; the mission header edit form
  is API-ready but not built.
* Notifications are read/mark-read only (no email/SMS channel).
* No PostGIS: spatial search is Python-side and fine at current data volumes.
* Production deployment and production migration are unstarted by design.
