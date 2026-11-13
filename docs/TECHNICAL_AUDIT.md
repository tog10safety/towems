# TOWEMS — Technical Audit of the Existing MRMS Application

**Audit date:** 2026-10-27
**Target directory:** `C:\Users\ceist\Documents\FREEBUFF\mrm-system`
**Audited by:** implementation agent (static inspection + read-only database introspection)
**Status:** Stage 1–2 complete. No production system was contacted, modified, or deployed to.

> **Paths have since moved.** This audit describes the legacy Flask tree as it stood on
> 2026-10-27, at the project root. On 2026-10-28 the legacy sources were archived, unmodified,
> into `Flask MRM OLD/` (same relative layout) and four unused virtualenvs were moved with them.
> Read every `app.py`, `routes/…`, `services/…`, `templates/…`, `models.py`, `migrations/…`,
> `tests/…` reference below as relative to `Flask MRM OLD/`. See `PROJECT_CLEANUP_REPORT.md`.

---

## 1. Method and evidence base

| Evidence | How it was obtained |
| --- | --- |
| Source tree, file sizes, timestamps | `ls -la` on the project root and subdirectories |
| Python/runtime versions | `python --version`, `.venv/Scripts/python.exe --version` |
| Dependencies | `requirements.txt`, `pip list` inside `.venv` |
| Application wiring, routes | Direct reading of `app.py`, `routes/*.py`, `services/*.py`, `models.py` |
| Live schema, enums, row counts | `tools/introspect_schema.py` — **read-only** catalog queries (`information_schema`, `pg_catalog`) against the database the app is configured to use |
| Frontend | Inspection of `templates/` (Jinja2/Bootstrap), absence of `package.json` |

Every statement below is traceable to one of those sources. Where something could not be verified,
it is labelled **UNVERIFIED**.

> **Scope caveat that matters:** the schema inventory was produced against the database configured
> locally (`localhost:5432/mrm_system`, 11 tables). The DigitalOcean production database was **not**
> reachable from this environment and was **not** queried. Before any production change, `tools/introspect_schema.py`
> must be re-run against production and the diff reviewed — see `DATABASE_COMPATIBILITY.md` §9.

---

## 2. Verified project facts

| Item | Value |
| --- | --- |
| Version control | **None.** `git status` → `fatal: not a git repository`. There is no history, no rollback point, no branch. |
| Python | 3.11.4 |
| Web framework | Flask 2.3.3 (monolithic application factory) |
| ORM | Flask-SQLAlchemy 3.1.1 / SQLAlchemy 2.0.31 |
| Database engine | PostgreSQL (`psycopg2-binary` 2.9.9); local server 18.4 |
| Spatial extension | **PostGIS not installed** (`pg_extension` = `{plpgsql}` only) |
| Templating / UI | Jinja2 + Bootstrap, server-rendered. `templates/base.html` alone is 65 KB. |
| JavaScript tooling | **None.** No `package.json`, no bundler, no npm lockfile. |
| Blueprints | 7 (`main`, `mrm`, `hazards`, `admin`, `users`, `unit_safety`, `unit_management`) |
| HTTP routes | **115** (admin 34, mrm 30, unit_safety 15, hazards 11, main 11, unit_management 8, users 6) |
| Database tables | **11** |
| Database enums | **9** PostgreSQL `ENUM` types |
| Automated tests | **Zero.** No `tests/` directory; no test runner in `requirements.txt`; no test files outside virtualenvs. |
| Deployment artifacts | **None in repo** (`requirements.txt` only — no `Procfile`, `Dockerfile`, gunicorn/uWSGI config, nginx config, or `.env.example`) |
| Virtualenvs in tree | **3** (`.venv`, `venv`, `myenv`) — ~thousands of vendored files, plus a stray empty file named `python` |
| Log file | `logs/mrm_system.log`, ~1.4 MB (single growing file; rotation **UNVERIFIED**) |

### Data volume actually present in the inspected database

| Table | Rows | Significance |
| --- | ---: | --- |
| `users` | 484 | Real personnel/account records — must be preserved byte-for-byte |
| `hazard_registry` | 1,923 | Institutional hazard library |
| `mrm_forms` | 292 | Historical mission risk assessments |
| `mission_hazards` | 4,461 | Per-mission hazard selections/overrides |
| `user_activities` | 2,612 | Existing audit trail |
| `mrm_personnel` | 9 | Tagged personnel |
| `mrm_feedback` | 2 | Post-mission feedback |
| `feedback_notifications` | 6 | Notification records |
| `units` | 6 | Unit hierarchy roots |
| `unit_logos` | 6 | Unit branding |
| `unit_assignments` | 0 | Safety-officer multi-unit assignments (unused so far) |

---

## 3. Current architecture

```
                    ┌──────────────────────────────────────────────┐
   Browser  ─────►  │  Flask 2.3.3 (app.py, app factory)            │
   (Jinja +         │   • Flask-WTF CSRF        • Flask-Limiter     │
    Bootstrap)      │   • Flask-Caching         • Flask-Login       │
                    │   • session cookie `mrm_session`              │
                    └───────────────┬──────────────────────────────┘
                                    │ blueprints (115 routes)
      ┌─────────────┬───────────────┼────────────────┬───────────────┐
      │             │               │                │               │
   main.py       mrm.py         hazards.py        admin.py        users.py
  dashboard   30 routes        11 routes         34 routes        6 routes
      │        (3,373 LOC)          │                │               │
      └─────────────┴───────┬───────┴────────────────┴───────────────┘
                            │
                    ┌───────▼────────┐      ┌──────────────────────────┐
                    │ services/      │      │ tasks/ (password expiry) │
                    │ auth, pdf,     │      └──────────────────────────┘
                    │ chart, import, │
                    │ filter, cache  │
                    └───────┬────────┘
                            │ SQLAlchemy 2.0
                    ┌───────▼──────────────────────────────────────┐
                    │ PostgreSQL — 11 tables, 9 enums, no PostGIS  │
                    └──────────────────────────────────────────────┘
```

### Component inventory

| File | Size | Role |
| --- | ---: | --- |
| `app.py` | 16 KB | App factory, extension init, error handlers, security headers, `/health`, **unauthenticated debug routes** |
| `models.py` | 44 KB | All 11 ORM models, PostgreSQL `ENUM` type definitions, werkzeug password hashing |
| `config.py` | 11 KB | Config classes (dev/prod/testing), password policy, CSP, caching, activity/weather/IMSAPE constants |
| `routes/mrm.py` | 140 KB | Core MRM lifecycle (3,373 lines): create, edit, view, review, sign, approve, PDF, feedback |
| `routes/admin.py` | 81 KB | Admin console, stats, reports, audit trail, backup export, cache ops, DB maintenance |
| `routes/unit_safety.py` | 63 KB | Unit safety officer workspace |
| `routes/hazards.py` | 49 KB | Hazard registry CRUD, import/export |
| `routes/main.py` | 36 KB | Login/logout, dashboard, profile, password change |
| `routes/users.py` | 24 KB | User administration |
| `routes/unit_management.py` | 15 KB | Unit hierarchy management |
| `services/pdf_service.py` | 35 KB | ReportLab PDF generation of approved MRMs |
| `services/auth_service.py` | 19 KB | CSRF, limiter, decorators (`admin_required`, `evaluator_required`, `creator_required`, `unit_safety_officer_required`, `unit_access_required`), input sanitisation |
| `services/import_service.py` | 20 KB | Excel/user & hazard import (pandas, openpyxl, xlrd) |
| `services/edit_permissions.py` | 4 KB | Edit-mode rules by status/role |
| `migrations/` | — | Alembic scaffold with **one** initial revision; schema is effectively unmanaged (`db.create_all()` at boot) |
| `migrate_*.py` (3 files) | 32 KB | Ad-hoc, hand-run data migrations |

---

## 4. Authentication and authorisation (as implemented)

* **Authentication:** server-side session (`mrm_session` cookie, `HttpOnly`, `SameSite=Lax`), credential check via `werkzeug.security.check_password_hash`. Werkzeug 3.0 default is **scrypt** — this matters for Django compatibility (see compatibility report).
* **Authorities** (`authority_enum`): `Administrator`, `Evaluator`, `Creator`, `Unit Safety Officer`.
* **Categories** (`category_enum`): `Officer`, `Enlisted Personnel`, `Civilian Human Resource (CivHR)`.
* **Approval designations** (`authority_level_enum`): `Operator`, `Supervisor`, `Squadron Commander`, `Director for Operations`, `Commander`.
* **Unit scoping:** `User.get_accessible_unit_ids()` walks the `units` hierarchy; `Unit Safety Officer` gets units from `unit_assignments` plus their primary unit; other roles get their own unit and subordinates.
* **Password policy:** min 8 chars, 1 upper/lower/digit/special, max 3 consecutive, 90-day expiry, 5-password history, `force_password_change` flag.
* **Transport/CSRF:** Flask-WTF CSRF on POST/PUT/PATCH/DELETE; CSP and hardening headers applied via `after_request`.

**Weakness:** authorisation is enforced by decorators *inside each route*. With 115 routes and 8 large
modules there is no single choke point; any route that forgets a decorator is unprotected. The Django
port centralises this in DRF permission classes (see §10).

---

## 5. MRMS business workflow (must be preserved)

```
draft ──submit──► submitted ──evaluator review──► reviewed ──authority approval──► approved
   │                   │                              │
   │                   │  review_status:               │  authority_approved = True
   │                   │  pending/approved/            │  authority_signature_date set
   │                   │  rejected/needs_revision      │  PDF generated → approved_pdfs/
   │                   ▼                              ▼
   └── creator_signed                    evaluator_signed required before approval
                                          (MRMForm.is_approvable gates this)
```

* **Risk scale:** likelihood `1–5` (1 Extremely Improbable … 5 Frequent), severity `A–E` (A Catastrophic … E Negligible). Rating is the concatenation (`"4B"`), **not** a numeric product — `HazardRegistry.calculate_risk_rating` returns `f"{likelihood}{severity}"`. This exact string convention is persisted in `hazard_registry.before_risk_rating` / `after_risk_rating` and **must not be changed**.
* **Colour bands:** red = `5A,5B,5C,4A,4B,3A`; yellow = `5D,5E,4C,4D,4E,3B,3C,3D,2A,2B,2C,1A`; else green.
* **Aggregates on `mrm_forms`:** `max_risk`, `residual_risk`, `total_percent`, `risk_category`.
* **Hazards per mission:** `mission_hazards` supports `override_likelihood`, `override_severity`, `additional_mitigations`, `selected` — overrides must survive the port.
* **Signatures:** `creator_signed`, `evaluator_signed` (+ dates); signature image files in `signatures/`.
* **Immutable evidence:** `approved_pdf_filename` → generated PDFs in `approved_pdfs/`.
* **Edit history:** `edit_history` **JSONB** with a GIN index, plus `last_edited`, `last_edited_by`.
* **Unit snapshots:** `original_*` string columns *and* `original_*_id` FK columns are written at creation time so historical records keep the unit they were raised under. Both sets must be preserved.
* **Feedback loop:** `mrm_feedback` + `feedback_notifications` + `feedback_deadline`/`feedback_collection_closed` on `mrm_forms`.

---

## 6. Evaluator-selection bug — root cause analysis

Your instruction was to fix this. Inspection found **four distinct defects**, not one, which is why it
reappears depending on which screen you use.

### 6.1 `filter_by` on a Python property — hard failure on edit/view (severity: critical)

`routes/mrm.py:991-996`:

```python
evaluators = User.query.filter_by(
    authority='Evaluator',
    is_active=True,
    unit=user.unit          # ← BUG
).all()
```

`User.unit` is **not a mapped column**; it is a legacy Python `property` (`models.py:294-303`) that returns
`self.unit_rel.name`. The real column is `unit_id`. SQLAlchemy resolves `filter_by` keywords against the
mapper, so `unit=...` raises `InvalidRequestError: Entity namespace for "users" has no property "unit"`.
The evaluator list on the edit page therefore cannot be built. The correct filter is `unit_id=user.unit_id`.

The same anti-pattern exists in `services/filter_service.py` and in `admin.py`'s unit statistics, where
`db.session.query(User.unit)` is used inside `count()`/`group_by()` — a property is not usable in SQL
there either.

### 6.2 Backend does not validate the selected evaluator (severity: high)

`routes/mrm.py:656-658` and `:783`:

```python
reviewed_by = request.form.get('reviewed_by')
if not reviewed_by:
    flash('Please select an evaluator to review this MRM.', 'danger')
    ...
reviewed_by=int(reviewed_by) if reviewed_by and reviewed_by.isdigit() else None,
```

Consequences:

1. Any non-numeric value is **silently coerced to `None`** — the record saves with no evaluator and no error.
2. Any numeric value is accepted: a *Creator*, an inactive account, a user from an unrelated unit, or a
   non-existent ID all pass. There is no check that the target is an active `Evaluator` inside the
   creator's authorised units.
3. Frontend `required` and the Jinja "no evaluators" warning are the only guards, so the rule exists only
   in the browser.

### 6.3 The edit form's evaluator field is outside the `<form>` (severity: medium)

`templates/edit_mrm.html:242-252` renders a hidden `reviewed_by` input **before** `<form method="POST" id="mrmForm">`
(line 264). It is therefore never submitted. In addition, the POST handler (`routes/mrm.py:1092-1221`) never
reads `reviewed_by` at all, so an evaluator cannot be corrected or re-assigned from the edit screen — and
`services/edit_permissions.py:52` explicitly lists `reviewed_by` as non-editable after submission. Behaviour is
inconsistent between create and edit.

### 6.4 Self-assignment rules disagree between route and template (severity: low)

`create_mrm` excludes the current user from the evaluator list for non-Evaluator creators
(`routes/mrm.py:626-632`), but **does not** exclude self when the creator is an `Evaluator`, while the template
(`templates/create_mrm.html:289-296`) hides self in exactly that case. The route and the template disagree about
who is eligible; whatever the browser hides can still be submitted by hand.

### 6.5 Fragile template dependency

`edit_mrm.html:243` calls `User.query.get(form.reviewed_by)` inside Jinja, relying on `User` being injected into
the render context (`context['User'] = User`). This couples the template to the ORM and breaks silently
(`rev` becomes undefined) whenever the context key is missed on an error path.

**Fix applied in this engagement:** §7 of `TOWEMS_MODULES.md` and the patch recorded in `docs/CHANGES.md`.
The Django API implements the rule server-side from the start
(`apps/ops/services.py::resolve_evaluator`, mirrored in Flask by `routes/mrm.py::resolve_evaluator`).

---

## 7. Security findings

| # | Severity | Finding | Location | Recommendation |
| --- | --- | --- | --- | --- |
| S1 | **Critical** | Hardcoded PostgreSQL password fallback committed in source | `config.py:14` | Rotate the credential; load from env/secret store only; fail closed when absent |
| S2 | **Critical** | Default `admin` / `admin123` account auto-created and printed to stdout on first boot | `app.py` (`initialize_application`) | Remove; require explicit bootstrap; force rotation |
| S3 | High | Unauthenticated debug endpoints expose session/user identifiers and DB URI fragment | `app.py` `/debug/session`, `/test/config`, `/test/logging` | Delete before any deployment; not ported to Django |
| S4 | High | Weak default `SECRET_KEY` fallback (`dev-secret-key-change-in-production-2025`) | `config.py:9` | Require env var; refuse to boot in prod without it |
| S5 | High | `db.create_all()` on every boot — silently mutates schema instead of migrating | `app.py` | Replace with reviewed migrations (Django `migrate`) |
| S6 | Medium | `verify=False`-style API-token path is a stub returning `False` — dead auth code that reads as protection | `services/auth_service.py` | Remove or implement |
| S7 | Medium | CSRF/session cookies not `Secure` in default config; `WTF_CSRF_SSL_STRICT = False` | `config.py` | Enforce HTTPS-only cookies in production settings |
| S8 | Medium | `module_enum` drift: `models.py` defines `unit_management`, the database enum does not contain it | `models.py:85-89` vs live enum | Any insert using that label raises `InvalidTextRepresentation`. Fix in a reviewed additive migration |
| S9 | Medium | Three virtualenvs and a stray `python` file in the repo root | project root | Exclude from backups/packaging; rebuild a single pinned environment |
| S10 | Low | 65 KB `base.html`, inline JS, duplicated `.bak` templates | `templates/` | Retire with the React frontend |
| S11 | Low | MySQL-style `%`-format SQL not used, but ad-hoc `db.session.query(User.unit)` breaks under SQLAlchemy 2 semantics | `admin.py`, `filter_service.py` | Replace with `User.unit_id`/join |
| S12 | Info | No dependency pinning audit / no CVE scan | — | Add `pip-audit` to CI |

---

## 8. Testing and deployment posture

* **No automated tests exist.** Every regression risk in this migration is currently unmeasured. The port
  introduces a real test suite (see `TOWEMS_MODULES.md` §11) so that MRMS behaviour can be compared numerically.
* **No migration history.** Alembic has a single stub revision while the schema is created by `create_all()`;
  the live schema has therefore drifted from the ORM (proven by finding S8). Introduced Django migrations must
  treat the *database* as truth, not `models.py`.
* **No deployment definition in the repo.** DigitalOcean specifics (WSGI server, systemd/gunicorn unit, nginx,
  TLS, database host) are **UNVERIFIED** from this machine. `DEPLOYMENT_AND_ROLLBACK.md` therefore documents a
  procedure with explicit verification steps rather than asserting the current topology.
* **No version control.** The single backup taken at the start of this engagement
  (`_backups/source_pre_toms_*.tar.gz`) is the only rollback point for source. Initialising git is recommended
  but was **not** done (it would alter your workflow without being asked).

---

## 9. Migration risk register

| Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- |
| Django migrations alter existing tables | Medium | Critical | All 11 legacy tables mapped `managed = False`; `migrate` never emits DDL for them |
| Enum/timestamp/JSONB type mismatch | Medium | High | Explicit mapping table + read-only parity tests (compatibility report §4–§6) |
| Password hashes unreadable by Django | High (if naive) | Critical (all users locked out) | Custom backend validating **werkzeug** scrypt hashes; no forced rehash |
| Risk rating semantics changed (`"4B"` string vs product) | Medium | High | Port the exact string algorithm; golden-value tests from real rows |
| Evaluator bug re-introduced in React | Medium | High | Single server-side validator + API contract tests |
| Production schema differs from inspected copy | Medium | High | Re-run introspection on production and diff before cutover |
| GIS work forces PostGIS onto a non-spatial database | Medium | Medium | No PostGIS locally; store GeoJSON + numeric coordinates in **new** tables; PostGIS optional later |
| Dual-running Flask and Django diverges | Medium | Medium | Freeze Flask writes during comparison window; compare counts/hashes |

---

## 10. Target architecture for TOWEMS

```
toms_frontend/  React 18 + Vite + React Router + Leaflet  (Node.js/npm tooling only)
        │  HTTPS, JSON, session cookie + CSRF (or JWT for API clients)
        ▼
toms_backend/   Django 5 + DRF
   apps/accounts   legacy `users` (managed=False) + werkzeug-compatible auth backend
   apps/legacy     read-only mappings of the 11 existing tables (managed=False)
   apps/ops        NEW TOWEMS tables only (managed=True): missions, air/ground detail,
                   inventory, vehicles, trips, AOR, notifications, audit, risk links
   apps/core       permissions, pagination, audit middleware, MGRS/geo utilities
        │  psycopg 3
        ▼
PostgreSQL  existing 11 tables untouched  +  new additive `ops_*` / `core_*` tables
```

Guiding rules adopted:

1. **Existing tables are read/write via Django but never schema-managed by Django** (`managed = False`).
2. **All new state lives in new tables** with `managed = True`; no column is added to a legacy table in this phase.
3. **No destructive operation is run anywhere in this engagement.** Migrations were executed only against a
   purpose-created `toms_staging` database.
4. **Flask remains runnable** and untouched in behaviour except the evaluator fix, so you can roll back by
   starting the old entry point.

---

## 11. Audit conclusions

1. The system is a **single-process Flask monolith** with an enormous, tightly coupled MRM module and no test
   safety net. A like-for-like port is feasible because the domain logic is concentrated in a small number of
   well-named services.
2. The **database is the asset**: 484 users, 292 MRM forms and 4,461 mission-hazard rows. It is also
   **schema-drifted** from the ORM and **not under migration control**, so the port must be driven from live
   schema introspection, which is what `DATABASE_COMPATIBILITY.md` does.
3. The evaluator defect is a **cluster of four defects** (§6) with a single conceptual fix: one authoritative
   server-side resolver, used by every path, with the UI only reflecting it.
4. Security debt is concentrated in configuration (S1–S4) and is cheap to fix because Django's settings split
   removes the fallbacks entirely.
5. Nothing in the existing application prevents the TOWEMS modules from being added **additively**; the GIS
   requirement is the only one that would have tempted a schema change, and it is avoided by keeping geometry
   as GeoJSON in new tables.
