# Project cleanup report — TOWEMS directory reorganisation

**Date:** 2026-10-28
**Project root:** `C:\Users\ceist\Documents\FREEBUFF\mrm-system`
**Archive folder:** `C:\Users\ceist\Documents\FREEBUFF\mrm-system\Flask MRM OLD`

**Scope and safety:** files were **moved, never deleted**; nothing was overwritten (every move
used no-clobber semantics into a freshly created, empty archive folder); no database was
connected to, migrated, or altered; no deployment was performed; no secret value is reproduced
in this report.

---

## 1. How each item was classified

A file was moved only when **all** of the following held:

1. it is Flask-era code or a Flask-era dependency manifest, **and**
2. it is not imported, executed or referenced by `toms_backend/`, `toms_frontend/`, `tools/`
   or `docs/` (verified by recursive `grep` over those trees for `app.py`, `models.py`,
   `config.py`, `routes.`, `services.`, `templates/`, `tasks.`, `migrations/`, `instance/`,
   `uploads/`, `signatures`, `unit_logos`, `approved_pdfs`, `static/`), **and**
3. it is not one of the read-only legacy asset locations declared in
   `toms_backend/toms/settings/base.py` (`LEGACY_UPLOAD_DIR`, `LEGACY_SIGNATURE_DIR`,
   `LEGACY_PDF_DIR`, `LEGACY_LOGO_DIR`, `LEGACY_STATIC_DIR`).

Two files failed rule 2 and stayed in the root: `config.py` (imported by the three staging/
introspection tools) and the `static/` tree (the PDF generator reads `Page_Footer.png`,
`logo.png`, `risk_matrix.png` and `unit_logos/`). Anything ambiguous was left in place and is
listed in §5.

---

## 2. Files and folders moved

Original and new locations are relative to the project root; **relative structure is preserved
inside the archive**.

### 2.1 Legacy application code

| Original | New | Reason |
| --- | --- | --- |
| `app.py` | `Flask MRM OLD/app.py` | Flask app factory; TOWEMS runs on Django (`toms_backend/manage.py`, `tools/local_server.py`). No TOWEMS file imports it. |
| `models.py` | `Flask MRM OLD/models.py` | Flask-SQLAlchemy models. Django maps the same tables independently in `toms_backend/apps/legacy/models.py`. |
| `routes/` (16 files) | `Flask MRM OLD/routes/` | Jinja blueprints (`main`, `mrm`, `admin`, `unit_safety`, `unit_management`, `hazards`, `users`) — all replaced by DRF viewsets in `apps/ops/views.py` and `apps/accounts/views.py`. |
| `services/` (22 files) | `Flask MRM OLD/services/` | Flask-side auth/CSRF/limiter, edit-permissions, filters/imports, caching, charts, `pdf_service.py`. The PDF logic was re-implemented for Django in `toms_backend/apps/ops/pdf.py` and no longer imports this package. |
| `tasks/` (2 files) | `Flask MRM OLD/tasks/` | Password-expiry background task for the Flask app; nothing in TOWEMS references `tasks`. |
| `templates/` (51 files) | `Flask MRM OLD/templates/` | Jinja2 + Bootstrap UI, fully replaced by the React frontend in `toms_frontend/src/`. |
| `logging_config.py` | `Flask MRM OLD/logging_config.py` | Imported only by `app.py` (`setup_logging`); Django uses the `LOGGING` dict in `toms/settings/base.py`. |
| `database_utils.py` | `Flask MRM OLD/database_utils.py` | Legacy DB helper; imported by nothing, not even the Flask app (`grep` found no importer). |
| `fix.py` | `Flask MRM OLD/fix.py` | One-off find-and-replace script over Jinja templates; its input files moved with it. |

### 2.2 Legacy migration / dependency artefacts

| Original | New | Reason |
| --- | --- | --- |
| `migrate_MRMforms.py` | `Flask MRM OLD/migrate_MRMforms.py` | One-off pre-Django data script. Must not be re-run against production. |
| `migrate_existing_hazards.py` | `Flask MRM OLD/migrate_existing_hazards.py` | Same — one-off data script. |
| `migrate_to_unit_hierarchy.py` | `Flask MRM OLD/migrate_to_unit_hierarchy.py` | Same — one-off unit-hierarchy backfill. |
| `migrations/` (5 files, Alembic) | `Flask MRM OLD/migrations/` | Scaffold that never held a real revision (schema came from `db.create_all()`); documented as drift item D5. Django migrations live in `toms_backend/apps/*/migrations/`. |
| `tests/` (2 files) | `Flask MRM OLD/tests/` | `test_mrms_evaluator.py` does `from routes.mrm import …`, so it can only run next to the archived Flask code. TOWEMS's evaluator coverage is in `toms_backend/apps/ops/tests/`. |
| `requirements.txt` | `Flask MRM OLD/requirements.txt` | Flask 2.3 / SQLAlchemy 2.0 pin set. TOWEMS installs from `toms_backend/requirements.txt`; keeping both at root invited the wrong one to be installed. |

### 2.3 Legacy data snapshots (preserved, not deleted)

| Original | New | Reason |
| --- | --- | --- |
| `instance/` (2 files: `mission_risk.bak`, `users.bak`) | `Flask MRM OLD/instance/` | Old SQLite snapshots from the Flask era. Not the PostgreSQL database, not read by Django. Kept for reference. |

### 2.4 Generated / stray items

| Original | New | Reason |
| --- | --- | --- |
| `__pycache__/` (root) | `Flask MRM OLD/__pycache__/` | Byte-compiled copies of `app.py`, `config.py`, `models.py`, `logging_config.py` — regenerable, and only meaningful next to the archived sources. Note: a fresh root `__pycache__/config.cpython-311.pyc` reappears by itself whenever `tools/staging_*.py` import `config.py`; that is expected, not a leftover. |
| `python` (0 bytes) | `Flask MRM OLD/python` | Empty file left by a broken shell redirect (shell writes `> python` on Windows); not a script, referenced nowhere. |
| `static/default_logo.css` | `Flask MRM OLD/static/default_logo.css` | Legacy stylesheet linked by **no** template and read by **no** route (`grep` for `default_logo` matches only `default_logo.png`, which does not exist). The rest of `static/` stayed (see §3). |

### 2.5 Unused virtualenvs

All five were genuine Python 3.11 venvs (confirmed by `pyvenv.cfg`). Only `.venv-toms` is used by
TOWEMS.

| Original | New | Reason |
| --- | --- | --- |
| `.venv/` | `Flask MRM OLD/.venv/` | Legacy Flask env. `grep` over `tools/`, `toms_backend/` and `toms_frontend/package.json` found no reference to any of these four; every documented command uses `.venv-toms`. |
| `venv/` | `Flask MRM OLD/venv/` | Same. |
| `env/` | `Flask MRM OLD/env/` | Same. |
| `myenv/` | `Flask MRM OLD/myenv/` | Same. |

> `venv/` refused to move on the first attempt (`Permission denied` — a transient file handle,
> not an ACL problem: no process had its executable there, and the same move succeeded
> immediately afterwards via `Move-Item`). Nothing was left behind.

These environments are **not relocatable** in the usual sense: `Scripts/activate` still records
the old absolute path, so run `Scripts/python.exe` directly or rebuild from the archived
`requirements.txt`. Nothing functional was lost — they are pip-installed copies of public
packages.

---

## 3. Deliberately kept in the project root

| Item | Why it must stay |
| --- | --- |
| `config.py` | `tools/staging_db.py`, `tools/staging_manage.py` and `tools/introspect_schema.py` all do `from config import Config` to resolve the database URL. Moving it breaks the staging and introspection tooling. |
| `signatures/` (16 files) | `settings.LEGACY_SIGNATURE_DIR` — signature images embedded in generated MRM PDFs. |
| `static/` (`Page_Footer.png`, `logo.png`, `risk_matrix.png`, `unit_logos/`) | `settings.LEGACY_STATIC_DIR` / `LEGACY_LOGO_DIR` — PDF header/footer/logo/matrix assets. |
| `approved_pdfs/` | `settings.LEGACY_PDF_DIR` — the store of signed PDFs of record (currently empty on this machine). |
| `uploads/` | `settings.LEGACY_UPLOAD_DIR`; currently empty, kept so the setting does not dangle. |
| `toms_backend/`, `toms_frontend/`, `tools/`, `docs/`, `logs/`, `_backups/`, `.venv-toms/` | The live system. Nothing inside them was moved or reorganised. |

---

## 4. Verification after the move (all executed, not inferred)

| Check | Command | Result |
| --- | --- | --- |
| Backend test suite | `cd toms_backend && ../.venv-toms/Scripts/python -m pytest` | **128 passed, 0 failed, 0 skipped, 0 errors** (exit 0) — identical to the pre-cleanup baseline |
| Legacy → Django schema mapping | `.venv-toms/Scripts/python tools/staging_manage.py verify_legacy_mapping` | **Exit 0**, every one of the 11 legacy tables `OK`, **9,823 rows** counted, `module_enum` 6 labels, "All legacy mappings verified" — proves `config.py` still resolves from the root and the Django models still match the real schema |
| Frontend production build | `cd toms_frontend && npm run build` | **Success (exit 0)** — 99 modules, `dist/index-6_PZcOus.js` 393.61 kB (117.79 kB gzip), CSS 26.33 kB — same output as before the move |
| Live dev server still serving | `curl 127.0.0.1:8010/api/health/` and `/api/risk-register/1/pdf/` | `health=200`, unauthenticated PDF request `403` (correctly refused) — the running server was undisturbed |
| Dangling references | recursive `grep` of `toms_backend/`, `toms_frontend/src/`, `tools/` for every moved path | **No hits.** The only textual mentions are docstrings/comments that name `routes.mrm` and `services/pdf_service.py` as the historical origin of ported logic, e.g. `apps/ops/services.py:185`, `apps/ops/pdf.py:7` |

Checked and **not** affected: `tools/*` import paths, `toms_backend/pytest.ini` `testpaths = apps`
(the archived `tests/` was never collected by pytest), Vite config/proxy, `apps/ops/pdf.py`
asset resolution, `apps/core/management/commands/verify_legacy_mapping.py`.

No database object was touched: the test suite runs on in-memory SQLite
(`toms/settings/test.py`), and `verify_legacy_mapping` is read-only against `toms_staging`.

---

## 5. Left in place for your review (not moved)

| Item | Situation | Recommendation |
| --- | --- | --- |
| `config.py` hardcoded fallback credential | `SQLALCHEMY_DATABASE_URI` falls back to a literal `postgresql://user:password@localhost/mrm_system` when `DATABASE_URL` is unset. This predates the cleanup. | Replace with an empty default / raise, and set `DATABASE_URL` in the environment. Needs approval because the staging tools read this value. |
| `logs/mrm_system.log` | Flask-era log, still alongside `toms_django.log` and `toms_vite.log`. | Harmless; delete or move with the archive if you prefer a tidier `logs/`. |
| Root `__pycache__/` | Recreated automatically (an empty `__pycache__/config.cpython-311.pyc`) because `tools/` imports `config.py` from the root. | Leave it, or add `__pycache__/` to a `.gitignore` if the folder is ever put under version control. |
| `uploads/` | Empty, but named by `LEGACY_UPLOAD_DIR`. | Keep, or delete the setting and the folder together. |
| `approved_pdfs/` | Empty on this machine (production holds the real signed PDFs). | Never delete in production — it is the evidence store. |
| `_backups/source_pre_toms_20261027_162028.tar.gz` | The pre-change tarball of the legacy sources — the rollback copy. | Keep; it now overlaps with `Flask MRM OLD/`, which is fine as belt-and-braces. |
| `pytest` warning | `RemovedInDjango60Warning: Converter 'drf_format_suffix' is already registered` from DRF's router under Django 5.2. | Upstream/DRF issue, not caused by the move; revisit at the Django 6 upgrade. |

---

## 6. Final structure

```
mrm-system/
├── toms_backend/            Django 5.2 + DRF, SimpleJWT, drf-spectacular
│   ├── manage.py  pytest.ini  conftest.py  requirements.txt  .env.example
│   ├── toms/                settings/{base,dev,staging,prod,test}.py, urls/wsgi/asgi
│   └── apps/{accounts,core,legacy,ops}/   models, serializers, views, migrations, tests
├── toms_frontend/           React 18 + Vite 5 + react-router + Leaflet
│   ├── index.html  package.json  package-lock.json  vite.config.js  .env.example
│   ├── src/{api,components,pages,styles}
│   └── dist/                production build
├── tools/                   introspect_schema.py, staging_db.py, staging_manage.py,
│                            local_server.py, smoke_test.py
├── docs/                    TECHNICAL_AUDIT, DATABASE_COMPATIBILITY, DATABASE_SCHEMA_INVENTORY,
│                            MIGRATION_PLAN, TOMS_MODULES, API_REFERENCE, DEVELOPMENT_SETUP,
│                            DEPLOYMENT_AND_ROLLBACK, CHANGES, PROJECT_CLEANUP_REPORT,
│                            database_schema.json
├── config.py                retained — imported by tools/
├── signatures/              legacy assets read by the PDF generator
├── static/                  Page_Footer.png, logo.png, risk_matrix.png, unit_logos/
├── approved_pdfs/           signed PDF store (read-only)
├── uploads/                 legacy upload dir (empty)
├── logs/                    toms_django.log, toms_vite.log, mrm_system.log
├── _backups/                source_pre_toms_20261027_162028.tar.gz
├── .venv-toms/              Python env for Django, tools and pytest
└── Flask MRM OLD/           archived legacy Flask MRMS (read-only reference)
    ├── README_ARCHIVE.md
    ├── app.py  models.py  requirements.txt  logging_config.py  fix.py  database_utils.py
    ├── migrate_MRMforms.py  migrate_existing_hazards.py  migrate_to_unit_hierarchy.py
    ├── routes/  services/  tasks/  templates/  tests/  instance/
    ├── migrations/          Alembic scaffold (never used)
    ├── static/default_logo.css
    ├── python               zero-byte stray file
    ├── __pycache__/
    └── .venv/  venv/  env/  myenv/      four legacy environments
```

The root now contains only what the current TOWEMS stack needs, plus the retained legacy asset
directories and `config.py` that Django and `tools/` actively read.
