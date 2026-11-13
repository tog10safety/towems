# Deployment, Backup and Rollback — TOWEMS on DigitalOcean

> **Nothing in this document has been executed.** No deployment, no production migration and
> no production data change was performed during this engagement. Deployment topology,
> credentials and the existing host layout are **UNVERIFIED from this machine** — verify each
> step before running it. Production cutover requires your explicit approval (Stage 10 of
> `MIGRATION_PLAN.md`).

---

## 1. Current state

| Component | State |
| --- | --- |
| Legacy Flask MRMS | Unchanged, still runnable, still the production application. |
| `toms_backend` (Django/DRF) | Built and tested; **not deployed**. |
| `toms_frontend` (React/Vite) | Builds clean; **not deployed**. |
| Production DB `mrm_system` | Untouched. No migration has ever run against it. |
| Staging DB `toms_staging` | Clone of production + the 21 additive tables. Where all Django DDL ran. |

## 2. Backup and recovery (do this first, every time)

```bash
# Full logical backup of production (run from a trusted host)
pg_dump --no-owner --no-privileges -h <HOST> -p 5432 -U <USER> -d mrm_system \
  -Fc -f mrm_system_$(date +%Y%m%d_%H%M).dump

# Verify the backup is actually restorable — an untested backup is not a backup
createdb mrm_restore_test
pg_restore --no-owner -d mrm_restore_test mrm_system_YYYYMM_DDMM.dump
psql -d mrm_restore_test -c "SELECT count(*) FROM users; SELECT count(*) FROM mrm_forms;"
psql -d mrm_restore_test -c "SELECT count(*) FROM hazard_registry; SELECT count(*) FROM mission_hazards;"
dropdb mrm_restore_test
```

Retention: keep the pre-cutover dump for at least 30 days and until you have signed off on
the migrated system. Also back up `approved_pdfs/`, `signatures/` and `static/unit_logos/`
(they hold generated evidence and the images embedded in it) — they are **not** in the
database. The legacy `instance/` SQLite `.bak` files are archived under
`Flask MRM OLD/instance/` and are not needed by TOWEMS.

## 3. Configuration (secrets, never in the repo)

`toms_backend/.env.example` lists every variable; copy it to `toms_backend/.env` on the
server and fill in real values:

```
DJANGO_SECRET_KEY=<32+ random bytes>
DJANGO_SETTINGS_MODULE=toms.settings.prod
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/mrm_system
DJANGO_ALLOWED_HOSTS=toms.example.com
DJANGO_CORS_ALLOWED_ORIGINS=https://toms.example.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://toms.example.com
```

`prod` settings enforce `DEBUG=False`, HTTPS redirect, HSTS, secure/session cookies and
`SECURE_PROXY_SSL_HEADER` (behind nginx/DO load balancer). `USE_TZ=False` is deliberate —
legacy timestamps are naive `Asia/Manila` values (see `DATABASE_COMPATIBILITY.md` §5).

Optional mapping configuration (no secrets): `TOMS_DEFAULT_BASEMAP` (id from
`GET /api/map/config/`) and, for a self-hosted tile service, `TOMS_INTERNAL_TILE_URL`,
`TOMS_INTERNAL_TILE_ATTRIBUTION`, `TOMS_INTERNAL_TILE_MAX_ZOOM`. See
`AOR_GIS_ENHANCEMENT.md` §5 for provider licensing and data flow.

## 4. Build

```bash
# Backend (Python 3.11)
python -m venv .venv-toms
.venv-toms/Scripts/pip install -r toms_backend/requirements.txt   # or bin/pip on Linux

# Frontend (Node 18+/npm)
cd toms_frontend && npm ci && npm run build    # → toms_frontend/dist/
```

Serving the SPA: the project has **no `STATICFILES`/`whitenoise` configuration yet**, so the
intended production layout is nginx serving `toms_frontend/dist/` as the site root and
reverse-proxying `/api/` to gunicorn. (Alternative — not implemented: add
`STATIC_ROOT`/`STATICFILES_DIRS` + whitenoise and let Django serve the assets.)

```nginx
server {
    listen 443 ssl;
    server_name toms.example.com;
    root /srv/toms/toms_frontend/dist;
    index index.html;

    location / { try_files $uri /index.html; }          # React Router

    location /api/ {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_set_header   X-Real-IP         $remote_addr;
    }
}
```

```bash
gunicorn toms.wsgi:application --bind 127.0.0.1:8000 --workers 3 --timeout 60
```

## 5. Production migration procedure (REQUIRES YOUR APPROVAL)

This is the only step that writes DDL to production. The migration is **purely additive**:
15 domain tables (`ops_*`, `core_audit_log`) plus Django's own `django_*`/`auth_*` bookkeeping
tables. The UI/MRM/user-administration phase adds one column on the TOWEMS-owned
`accounts_user_profile` table (`theme_preference`) and two TOWEMS tables
(`accounts_password_reset_token`, `ops_mission_risk_team`). The AOR/GIS phase adds one TOWEMS
table (`ops_administrative_area`), one nullable column (`ops_area_of_responsibility.admin_area`)
and one partial unique index. There is no `ALTER`, `DROP` or `RENAME` on any legacy object and
no legacy row is rewritten.

```bash
# 0. Drift check — production schema must still match docs/database_schema.json
python tools/introspect_schema.py            # then diff with docs/database_schema.json

# 1. Fresh, verified backup (section 2)

# 2. Plan — read it. Expect only CreateModel/AddField/CreateModel for new tables.
DATABASE_URL=postgresql://…/mrm_system DJANGO_SETTINGS_MODULE=toms.settings.prod \
  python toms_backend/manage.py migrate --plan

# 3. Apply (your explicit approval required here)
DATABASE_URL=postgresql://…/mrm_system DJANGO_SETTINGS_MODULE=toms.settings.prod \
  python toms_backend/manage.py migrate

# 4. Prove nothing legacy changed
DATABASE_URL=postgresql://…/mrm_system DJANGO_SETTINGS_MODULE=toms.settings.prod \
  python toms_backend/manage.py verify_legacy_mapping
```

Post-conditions to confirm: row counts of `users`, `units`, `hazard_registry`, `mrm_forms`,
`mission_hazards`, `mrm_personnel`, `mrm_feedback`, `feedback_notifications`,
`user_activities`, `unit_assignments`, `unit_logos` are **identical before and after**;
`verify_legacy_mapping` reports zero mismatches.

```bash
# 5. Load the official province/city boundaries (idempotent; ~1,730 rows)
DATABASE_URL=postgresql://…/mrm_system DJANGO_SETTINGS_MODULE=toms.settings.prod \
  python toms_backend/manage.py import_admin_boundaries --dry-run   # then without --dry-run
```

Expect `1730 created, 0 updated, 0 unchanged, 0 skipped`. A non-zero `skipped` count means the
bundled dataset is damaged — stop and re-run `tools/build_admin_boundaries.py` before deploying.

## 6. Cutover checklist

1. [ ] Backup taken **and restore-tested**.
2. [ ] Schema drift diff is empty.
3. [ ] `migrate --plan` reviewed and approved by you.
4. [ ] Migration applied to production; `verify_legacy_mapping` passes.
5. [ ] Django + gunicorn up on the internal port; `curl /api/health/` returns 200.
6. [ ] nginx serves the built SPA; deep links (`/risk/1`) resolve to `index.html`.
7. [ ] Smoke checks on the real host: login with an existing account (Werkzeug hash path),
       dashboard counts match legacy numbers, risk list shows the same 292 MRMs,
       create/edit one draft, MGRS conversion, CSV export.
7a.[ ] GIS: `/api/map/config/` lists the configured basemaps, AOR / GIS page renders the
       province layer, and `POST /api/aor/assign/` as a non-Administrator returns 403.
8. [ ] Old Flask app still running and reachable on its previous port (fallback).
9. [ ] Monitor logs for 24–48 h before decommissioning anything.

## 7. Rollback

**Level 1 — application rollback (minutes, no data loss).** Point nginx back at the Flask
app (or revert the proxy config). Django's additive tables are inert and harmless to Flask;
no schema change needs undoing. Flask still runs against the same database unchanged.

**AOR data note.** AOR rows created from administrative boundaries store their own geometry, so
reverting the code leaves them fully usable as ordinary AOR polygons; `ops_administrative_area`
is only needed for search, preview and boundary layers.

**Level 2 — undo the additive migration (only if you decide the new tables must go).**

```bash
python manage.py migrate ops zero      # drops ONLY ops_* tables Django created
python manage.py migrate               # bookkeeping tables; legacy untouched
```

Run this only after confirming no production traffic depends on them.

**Level 3 — database restore (last resort).** Drop the target DB and
`pg_restore` from the verified dump (section 2). This loses everything written since the
backup — take a fresh dump immediately before cutover so the window is minutes, not days.

Rollback triggers: login failures, wrong dashboard/risk counts, any
`verify_legacy_mapping` mismatch, or performance regression versus the Flask baseline.

## 8. Ongoing operations

* **Backups:** rely on the managed database's automated backups *and* a nightly `pg_dump`
  to off-host storage; test a restore monthly.
* **Logs:** Django logging level via `TOMS_LOG_LEVEL`; legacy `logs/` remains in place for
  the Flask app.
* **Secrets:** rotate `DJANGO_SECRET_KEY` and DB credentials through the DO secrets store;
  never commit `.env`.
* **Monitoring:** `GET /api/health/` is unauthenticated and safe as a probe.

---

## Offices & Sections catalogue migration (pending approval)

`command/migrations/0003_default_offices.py` adds `command_office_section.is_custom` and seeds the
twelve standard offices for every unit. It is **additive and non-destructive**: pre-catalogue office
rows are deactivated (not deleted) and their active personnel assignments are ended with a
timestamp, so assignment and mission history stays intact. The Operations Center office of each unit
is never touched.

Before running it:

1. Back up the database (`pg_dump -Fc`), and verify the dump restores into a scratch database.
2. Apply to a development/staging copy first:
   `python manage.py migrate command 0003` and then
   `python manage.py migrate --plan` to confirm nothing else is pending.
3. Check the result with `SELECT unit_id, code, name, is_active FROM command_office_section
   ORDER BY unit_id, code;` — twelve active catalogue rows per unit plus any active Operations
   Center, and the previous entries inactive.
4. Only then apply to production, during a maintenance window, and keep the dump until the
   acceptance checks pass.

Rollback: restore the dump. The migration's reverse step is intentionally a no-op (it cannot know
which deactivated rows to revive), so the database backup is the rollback path for the data step.
Reverting the code alone leaves the extra offices in place, which is harmless: the previous code
ignored `is_custom` and still lists offices by unit.
