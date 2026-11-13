# Development setup — TOWEMS

Verified on this machine (Windows, Python 3.11, Node 18+, PostgreSQL 18.4 client).
Every command below was actually run as part of this migration; the ports noted at the end
were chosen because `8000` and `5173` were already occupied by **another** project on this
machine — that project was left untouched.

## 1. Layout

```
mrm-system/
├── toms_backend/        ← Django + DRF
├── toms_frontend/       ← React + Vite/Node
├── tools/               ← staging & verification helpers
├── docs/                ← audit, compatibility, plan, module, cleanup & deployment docs
├── config.py            ← retained: tools/staging_*.py import Config from here
├── signatures/          ← legacy signature images embedded in MRM PDFs (read-only)
├── static/              ← page footer, logo, risk matrix, unit_logos/ (read-only)
├── approved_pdfs/       ← store of signed PDFs (read-only)
├── uploads/             ← legacy upload dir, currently empty (read-only)
├── logs/                ← runtime logs for Django and Vite
├── _backups/            ← pre-change backup of legacy sources (no secrets, no venvs)
├── Flask MRM OLD/       ← archived legacy Flask app (see its README_ARCHIVE.md)
└── .venv-toms/          ← Python env used by everything below
```

The legacy Flask application was archived into `Flask MRM OLD/` during the project cleanup;
see `PROJECT_CLEANUP_REPORT.md` for the full move list and the evidence behind each move.
To start it for reference: `cd "Flask MRM OLD"` then
`PYTHONPATH=.. .venv/Scripts/python.exe app.py` (its `config.py` stayed in the project root).

## 2. Python environment

```bash
python -m venv .venv-toms
.venv-toms/Scripts/pip install -r toms_backend/requirements.txt   # bin/pip on Linux/macOS
```

## 3. Staging database (the only PostgreSQL target for development)

```bash
# Inspect: shows the source database, all databases, whether the clone exists
.venv-toms/Scripts/python tools/staging_db.py --status

# Create the clone (exact copy of production: CREATE DATABASE … TEMPLATE …).
# Refuses to overwrite an existing clone; refuses to run against production itself.
.venv-toms/Scripts/python tools/staging_db.py --create
```

Run Django management commands **through the wrapper** — it hard-fails unless the resolved
database is exactly `toms_staging`:

```bash
.venv-toms/Scripts/python tools/staging_manage.py check
.venv-toms/Scripts/python tools/staging_manage.py migrate --plan
.venv-toms/Scripts/python tools/staging_manage.py migrate
.venv-toms/Scripts/python tools/staging_manage.py verify_legacy_mapping
```

> Never run `manage.py migrate` directly against production. Never point `DATABASE_URL` at
> production from a workstation.

## 4. Backend

```bash
# Local API server, staging DB, dev settings
.venv-toms/Scripts/python tools/local_server.py 127.0.0.1:8000
```

This is the **only** local backend. The frontend always proxies to it on port 8000.

OpenAPI schema and Swagger: `http://127.0.0.1:8000/api/schema/`,
`http://127.0.0.1:8000/api/docs/`.

## 5. Frontend

```bash
cd toms_frontend
npm install                 # first time only (package-lock.json is authoritative)
npm run dev                 # http://127.0.0.1:5173, proxies /api → http://127.0.0.1:8000
```

`8000` + `5173` is the only local pair. Do not start a second frontend on another port with
`VITE_DJANGO_ORIGIN` pointed at a different backend: the second pair can outlive a backend restart
and keep serving an old API from a different port, which looks like a code bug. If a port is busy,
stop the process holding it rather than opening another pair (`netstat -ano | grep :8000`).

Production build:

```bash
npm run build               # → toms_frontend/dist/ (393 kB, 118 kB gzip)
```

## 6. Tests

```bash
cd toms_backend
../.venv-toms/Scripts/python -m pytest          # 128 passed, 0 failed, 0 skipped
```

Tests use `toms.settings.test` (SQLite, disposable). **No test ever touches PostgreSQL.**

Schema-mapping check against the real schema (staging):

```bash
.venv-toms/Scripts/python tools/staging_manage.py verify_legacy_mapping
```

## 7. End-to-end smoke test

```bash
# terminal 1
.venv-toms/Scripts/python tools/local_server.py 127.0.0.1:8000
# terminal 2 — needs a staging login account (create one with staging_manage.py createsuperuser)
.venv-toms/Scripts/python tools/smoke_test.py \
    --base http://127.0.0.1:8000/api \
    --username toms.smoke --password '<staging password>'
```

Covers: health, login, every module list endpoint, pagination, MGRS round-trip, CSV export,
and confirms unauthenticated requests are refused (`403`).

## 8. A staging-only account

```bash
.venv-toms/Scripts/python tools/staging_manage.py createsuperuser
```

Use it only against `toms_staging`. Existing production users also work on the clone because
it is a byte-for-byte copy of the data (Werkzeug hashes and all).

## 9. Environment variables

Copy `toms_backend/.env.example` → `toms_backend/.env` and fill in values; same for
`toms_frontend/.env.example`. `.env` files are git-ignored and contain no real values in this
repository. Settings modules:

| Module | Use |
| --- | --- |
| `toms.settings.dev` | local development (used by `tools/local_server.py`) |
| `toms.settings.staging` | staging server (`tools/staging_manage.py`) |
| `toms.settings.prod` | production — requires `DJANGO_SECRET_KEY`, hosts, CSRF/CORS origins |
| `toms.settings.test` | pytest (SQLite) |
