# Changes, new database objects and remaining issues

Companion to `MIGRATION_PLAN.md`. Everything below was performed inside
`C:\Users\ceist\Documents\FREEBUFF\mrm-system` — nothing was moved, nothing outside this
directory was touched, and no deployment was performed.

---

## 1. Backup taken before any change

`_backups/source_pre_toms_20261027_162028.tar.gz` — the complete legacy source tree
(`app.py`, `config.py`, `models.py`, `routes/`, `services/`, `templates/`, `migrations/`,
`migrate_*.py`, `tests/`, `requirements.txt`, `logging_config.py`, `database_utils.py`,
`fix.py`) **excluding** `venv/`, `myenv/`, `env/`, `.venv*`, `node_modules/`,
`__pycache__/`, `.env` and any secret, uploaded signature, generated PDF or log.

Restore: `tar -xzf _backups/source_pre_toms_20261027_162028.tar.gz -C <project-root>`.

Verified by diffing every file in the archive against the working tree (see §2).

## 2. Legacy files modified (2 only)

Both changes are the evaluator-selection fix; both are additive and behaviour-preserving
everywhere else. The Flask app still runs.

| File | Change |
| --- | --- |
| `routes/mrm.py` | Added `parse_evaluator_id()`, `validate_evaluator()` and `resolve_evaluator()` — one authoritative server-side check (exists → active → authority `Evaluator` → inside the actor's authorised units → not self). Create path now calls it instead of `int(x) if x.isdigit() else None`. Evaluator list now filters on `unit_id.in_(accessible_unit_ids)` instead of the non-column `unit` property (which raised `InvalidRequestError` under SQLAlchemy 2.x). Edit path can correct the evaluator **only while the form is `draft`**. Passes `reviewer` into both render contexts. |
| `templates/edit_mrm.html` | The hidden `reviewed_by` input was **outside** `<form method="POST">` and therefore never submitted — moved inside the form; the duplicate `User.query.get()` display block replaced with the `reviewer` object supplied by the route. |

No other legacy source file differs from the backup (verified file-by-file with `cmp`).

## 3. Files created

### `toms_backend/` — Django + DRF

```
manage.py  pytest.ini  conftest.py  requirements.txt  .env.example
toms/__init__.py  toms/urls.py  toms/wsgi.py  toms/asgi.py
toms/settings/{__init__,base,dev,staging,prod,test}.py
apps/core/{__init__,apps,audit,csrf,errors,factories,geo,legacy_audit,mgrs,
           middleware,pagination,permissions,scoping,testing}.py
apps/core/management/commands/verify_legacy_mapping.py
apps/accounts/{__init__,apps,backends,models,serializers,urls,views}.py
apps/accounts/tests/{__init__,test_auth.py,test_personnel.py}
apps/legacy/{__init__,apps,models}.py            # 11 unmanaged legacy models
apps/ops/{__init__,apps,models,pdf,risk,serializers,services,urls,views}.py
apps/ops/migrations/0001_initial.py
apps/ops/tests/{__init__,test_evaluator,test_risk,test_missions,test_inventory,
                test_trips,test_gis,test_audit,test_dashboard,test_pdf}.py
```

### `toms_frontend/` — React + Vite (Node tooling)

```
package.json  vite.config.js  index.html  .env.example
src/main.jsx  src/App.jsx  src/styles.css
src/api/client.js  src/auth/AuthContext.jsx  src/hooks/useApi.js  src/utils/format.js
src/components/{ui,Layout,ProtectedRoute}.jsx
src/pages/{Login,Dashboard,Missions,MissionForm,MissionDetail,Risk,RiskDetail,
           Personnel,Units,Inventory,Vehicles,Trips,AOR,Reports,Audit}Page.jsx
```

### `tools/` — verification helpers

```
staging_db.py        # CREATE DATABASE toms_staging TEMPLATE mrm_system (never overwrites)
staging_manage.py    # manage.py wrapper that refuses anything but toms_staging
local_server.py      # runserver against staging, dev settings
smoke_test.py        # end-to-end HTTP smoke test of every module
introspect_schema.py # re-introspect production schema for the pre-cutover drift check
```

### `docs/`

```
TECHNICAL_AUDIT.md              DATABASE_SCHEMA_INVENTORY.md   database_schema.json
DATABASE_COMPATIBILITY.md       MIGRATION_PLAN.md              TOWEMS_MODULES.md
DEPLOYMENT_AND_ROLLBACK.md      API_REFERENCE.md               DEVELOPMENT_SETUP.md
CHANGES.md                      (this file)
```

**Dependencies added** to `toms_backend/requirements.txt` (installed into `.venv-toms`):
`reportlab==4.2.0` and `qrcode==7.4.2` — the *same* versions the legacy Flask app already
used, so MRM PDF output stays byte-comparable in layout. The frontend gained nothing new.

## 4. New database objects (staging only so far)

Created by `toms_backend/apps/ops/migrations/0001_initial.py`, applied **only** to
`toms_staging`. Production `mrm_system` has never had a migration run against it.

**21 new tables** — 15 domain + 6 Django bookkeeping:

| Domain (owned by TOWEMS) | | |
| --- | --- | --- |
| `ops_mission` | `ops_air_mission_detail` | `ops_ground_mission_detail` |
| `ops_mission_personnel` | `ops_inventory_category` | `ops_inventory_item` |
| `ops_inventory_transaction` | `ops_vehicle` | `ops_vehicle_trip` |
| `ops_trip_itinerary` | `ops_vehicle_trip_vehicle` | `ops_area_of_responsibility` |
| `ops_risk_assessment` | `ops_notification` | `core_audit_log` |

| Django bookkeeping (required by the framework) |
| --- |
| `django_migrations`, `django_content_type`, `django_session`, `auth_group`, `auth_group_permissions`, `auth_permission` |

**Indexes / constraints added (all on the new tables only):**

* `ops_mission_cat_status_idx`, `ops_mission_unit_idx`, `ops_mission_start_idx`
* `ops_item_cat_svc_idx`, `ops_item_unit_idx`
* `ops_txn_item_time_idx`, `ops_txn_kind_idx`
* `ops_trip_status_idx`, `ops_aor_unit_idx`, `ops_notif_recipient_idx`
* `core_audit_entity_idx`, `core_audit_actor_idx`, `core_audit_time_idx`
* Unique: `ops_mission_personnel_unique (mission, user)`,
  `ops_itinerary_trip_seq_unique (trip, sequence)`, `ops_trip_vehicle_unique (trip, vehicle)`
* Check: `ops_item_qty_available_non_negative`, `ops_item_qty_total_non_negative`
* Plus PostgreSQL's default FK indexes on every new foreign key column

**Not created:** no new PostgreSQL enum types (Django choice fields are plain `varchar`),
no PostGIS/extension, no new columns on any legacy table, no `ALTER`/`DROP`/`RENAME`
anywhere.

**Legacy objects after migration (staging):** 11 production tables, **9,810 rows**, unchanged
`users` (485), `mrm_forms` (292), `hazard_registry` (1,923), `mission_hazards` (4,461),
`user_activities` (2,620), `units` (6), and the rest — verified by count and by
`manage.py verify_legacy_mapping` (0 mismatches).

## 5. Test results (actual)

| Check | Command | Result |
| --- | --- | --- |
| Backend suite | `cd toms_backend && ../.venv-toms/Scripts/python -m pytest` | **128 passed, 0 failed, 0 skipped** (exit 0) |
| Schema mapping | `tools/staging_manage.py verify_legacy_mapping` | **0 mismatches** (types, nullability, lengths, FKs, enum labels) |
| Django checks | `tools/staging_manage.py check` | **no issues** |
| Frontend build | `cd toms_frontend && npm run build` | **success** — 99 modules, 393.34 kB (117.69 kB gzip), exit 0 |
| HTTP smoke test | `tools/smoke_test.py` vs `127.0.0.1:8010` | **all checks pass** — 485 personnel, 292 MRMs, 39 evaluators, MGRS round-trip, CSV export, unauthenticated requests refused (`403`) |
| Browser walkthrough | Vite dev `127.0.0.1:5180` | login, dashboard counts, risk list (292 rows), evaluator display/assign/reject-after-review, MRM PDF opened from the UI (7 pages, 573 KB), all verified |
| PDF content check | decompressed content streams of the generated PDF | title, vision header, document verification, MRM number, hazard/I'M SAFE/assumptions blocks, authority band, risk-matrix legend all present |
| Flask regression | `"Flask MRM OLD"/app.py` (archived; needs `PYTHONPATH=..`) | Verified startable **before** archiving, with only the two evaluator-fix files (§2) changed. Not re-started after the move, so as not to open a connection to the production database. |
| Production DB | — | **never migrated, never written** |
| Deployment | — | **not performed** (needs your approval) |

## 6. Remaining issues and unresolved risks

| # | Issue | Impact | Suggested next step |
| --- | --- | --- | --- |
| 1 | **Production cutover not done.** No migration has run against `mrm_system`. | Expected — Stage 10 needs your approval. | Follow `DEPLOYMENT_AND_ROLLBACK.md` §5–7. |
| 2 | **Mission header editing UI not built.** Missions can be created, approved, assigned personnel and linked to risk/trips; there is no edit form for the shared header fields yet (the PATCH API exists). | Medium — users cannot correct a typo in a mission title from the UI. | Add an edit route reusing `MissionFormPage`. |
| 3 | **No `STATICFILES`/asset pipeline for Django** (no whitenoise). Production layout assumes nginx serves `toms_frontend/dist/`. | Medium at deploy time. | Decide: nginx-serves-SPA (documented) or add whitenoise. |
| 4 | **Deployment topology unverified.** Whether the current production app runs in App Platform, a Droplet, or behind a specific proxy could not be confirmed from this machine. | Medium — the nginx/gunicorn block in the deployment doc is a template, not a match to live config. | Confirm the live host layout before Stage 10. |
| 5 | **PostGIS absent.** AOR geometry is GeoJSON + numeric columns; point-in-polygon runs in Python. | Low at current volumes; would need revisiting for large spatial queries. | Keep as-is; add PostGIS later only with approval (it is a schema-level change). |
| 6 | **Notifications are in-app only** (no email/SMS/push channel). | Low. | Add a delivery backend when a channel is chosen. |
| 7 | **Schema drift risk.** The legacy schema was unmanaged (`db.create_all()` at boot), so production may have drifted from `docs/database_schema.json` since introspection. | High if uncaught. | Run `tools/introspect_schema.py` against production and diff before migrating. |
| 8 | **Reports are JSON/CSV only.** The **MRM PDF itself is now covered** (`GET /api/risk-register/{id}/pdf/`, ported from `services/pdf_service.py`), but the legacy bulk ZIP export of many PDFs and ReportLab output for non-MRM reports are not ported. | Low. | Port `routes/unit_safety.py`'s bulk ZIP export if unit safety still needs it. |
| 9 | **`USE_TZ=False`** — timestamps are naive `Asia/Manila` to match legacy rows. | Low, deliberate. | Revisit only with a full data backfill plan. |
| 10 | **Evaluator authority is enforced only for `Evaluator`/`Administrator`** in `validate_evaluator`/`resolve_evaluator`; the legacy template also implied `Unit Safety Officer` participation in review. | Low — review/approve still enforce signatures and authority levels. | Confirm intended eligibility with the safety office before cutover. |
| 11 | Only **two** legacy files were changed, but they are hand-patched in place (no git history in this folder). | Low. | The `_backups` tarball is the rollback for source; consider `git init` for future change tracking. |
| 12 | **Dashboard risk counters are global**, not unit-scoped: `pending_reviews`, `pending_approvals` and `approved` are counted from all `mrm_forms`. Individual risk records *are* properly scoped in `/api/risk-register/`. | Low — three aggregate numbers can hint at activity outside a user's units. | Apply `unit_ids_for_filter` to the `MRMForm` query in `DashboardView` (and re-test `test_dashboard_counts_reflect_records`). |

## 7. Environment variables added (names only — no values)

`toms_backend/.env.example` (exact key list): `DJANGO_SECRET_KEY`, `DJANGO_SETTINGS_MODULE`,
`DATABASE_URL`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CORS_ALLOWED_ORIGINS`,
`DJANGO_CSRF_TRUSTED_ORIGINS`, `DJANGO_DEBUG`, `TOMS_SESSION_AGE`, `TOMS_PAGE_SIZE`,
`TOMS_LOG_LEVEL`, `TOMS_REHASH_ON_LOGIN`.

Read from the environment with defaults (no template entry needed): `DJANGO_SECURE_SSL_REDIRECT`,
`DJANGO_HSTS_SECONDS`, `TOMS_LOGIN_THROTTLE`, and — when `DATABASE_URL` is absent — the local
`PGUSER`/`PGPASSWORD`/`PGHOST`/`PGPORT`/`TOMS_DEV_DB_NAME` shortcuts.

`toms_frontend/.env.example`: `VITE_API_BASE_URL` (optional; the dev proxy defaults to
`VITE_DJANGO_ORIGIN=http://127.0.0.1:8000`).

No real credential appears in any committed file; secrets are read from the environment or
from untracked `.env` files and are never printed by the tools.

---

## 8. Project cleanup (2026-10-28)

The legacy Flask sources and the four unused virtualenvs were **moved** (never deleted) into
`Flask MRM OLD/`, leaving the project root containing only what TOWEMS needs. Nothing in
`toms_backend/`, `toms_frontend/`, `tools/` or `docs/` was reorganised, and no database was
contacted. Full move list, reasons and evidence: `PROJECT_CLEANUP_REPORT.md`.

Retained in the root because Django still reads it: `config.py` (imported by
`tools/staging_db.py`, `tools/staging_manage.py`, `tools/introspect_schema.py`) plus the asset
directories `signatures/`, `static/`, `approved_pdfs/` and `uploads/`.

Re-verified after the move: **128 passed** (pytest), `verify_legacy_mapping` **0 mismatches /
9,823 legacy rows**, `npm run build` **success (99 modules, 393.61 kB)**, live server on
`127.0.0.1:8010` still `health=200` and `403` for unauthenticated PDF requests.

---

## 9. Login repair and test-harness restoration (2026-10-28)

**Symptom:** correct credentials were accepted by the API, but the SPA returned to the login
page with no error message.

**Root cause:** `src/api/client.js` prepended the `/api` base twice for every GET.
`api.get("/auth/me/")` built `/api/auth/me/`, then `request()` prepended the base again and
fetched `/api/api/auth/me/`, which Django answered with **404**. `POST /auth/login/` was
unaffected (it never passed through `api.get`), so the session was created successfully and
then immediately discarded: `refresh()` caught the 404, set `user = null`, and
`ProtectedRoute` redirected back to `/login`. `login()` also swallowed the error and
`LoginPage` rendered only its own local error state, so nothing was ever displayed.

**Fix (frontend):**
- `src/api/client.js` — single URL resolver (`resolveUrl`) applied once; paths that already
  contain the base are left untouched, so the double prefix is impossible. Query strings are
  built with the existing `qs()` helper. A 403 whose detail mentions CSRF now re-fetches the
  token and retries the request once. `FormData` bodies no longer get a JSON content type
  (profile/avatar/signature uploads).
- `src/auth/AuthContext.jsx` — `login()` now establishes the user from the login response
  before the guard evaluates, re-throws so the form can display the failure, and only treats
  401/403 on `/auth/me/` as "signed out"; other statuses surface as an error.
- `src/pages/LoginPage.jsx` — removed a leftover debug `console.log`.

**Fix (test harness, `toms_backend/conftest.py`):** the shared fixtures had lost the wiring
needed to run at all. Legacy (`managed = False`) tables are now created once in
`django_db_setup` — inside a per-test transaction SQLite's schema editor refuses to run.
`TomsAPIClient` carries the CSRF token the account views enforce explicitly. Restored the
missing `commander`, `ops_officer` and `evaluator_other_unit` fixtures and corrected three
wrong fixtures (`safety` used a non-enum authority value; `mrm_form` used `apps.ops.models`
and `creator`/`unit` columns that do not exist — the table has `created_by`).

`apps/accounts/tests/test_csrf.py` was rewritten: it previously called
`django.middleware.csrf.get_token()` with an 11-character "secret" (Django requires 64) and
never exercised a view. It now drives the real endpoints.

**Verified:** backend suite **187 passed**; `npm run build` succeeds; in the browser the
network log shows `GET /api/auth/me/` → 403 (anonymous), `GET /api/auth/csrf/` → 200,
`POST /api/auth/login/` → 401 for a wrong password *with* the CSRF check satisfied, and the
page displays "Invalid username or password." No production database was contacted.

---

## 10. TOWEMS enhancement phase (2026-10-28)

Full detail, including the requirement-by-requirement status, workflow diagrams, notification
semantics, GIS scoping and deployment/rollback steps, is in `ENHANCEMENT_REPORT.md`.
The role matrix is `ROLE_PERMISSION_MATRIX.md`; the pre-implementation audit is
`ENHANCEMENT_GAP_ANALYSIS.md`.

**Scope delivered**

* Dashboard activity feed returns and renders the acting account's **username**
  (`actor`/`actor_username`), with the full name only as `actor_name`.
* Self-service **profile** for every authenticated user: contact number, bio, avatar
  (upload/preview/replace/remove, type + 2 MiB validation) and signature written to the existing
  `users.signature_filename`; password change reused. The Add Personnel form was not changed.
* **Notification bell** with a pending-action badge, dropdown, inbox page, summary endpoint,
  acknowledgement and completion separated from reading, and history that is never deleted.
* **Office/Section management**: additive tables, primary/secondary assignments, duplicate and
  cross-unit guards, deactivation instead of deletion, assignment history retained.
* **Dashboard operational map**: unit-scoped operational picture with AOR, air/ground, vehicle
  last-known positions, trip routes and georeferenced hazards; layer toggles, legend, filters,
  and honest empty/stale states. No coordinates are invented and nothing is labelled live.
* **Role-based authority** as unit appointments on top of the untouched legacy enum, with
  capability checks in one policy layer, one-Commander/Deputy/DFO per unit, auditable acting
  authority that expires automatically, and configurable Decision Authority.
* **Workflows**: mission (Operations Center → Operations Officer → Decision Authority with
  non-skippable routing and no self-approval), MRM (preserved, notifications extended),
  inventory requests (stock changes only on approval), and instructions
  (issue → acknowledge → complete → verify).
* **Governance**: before/after audit records, refusal logging without secrets, and permission
  changes/appointments/acting/sections audited.

**Bugs found and fixed while testing**

* `verify_instruction` raised `NameError` (missing `Instruction` import) → verification returned
  500. Fixed; covered by `test_only_the_sender_or_admin_can_verify`.
* The same section could be assigned to one person as both primary and secondary. The duplicate
  check now ignores assignment type; covered by `test_same_section_cannot_be_assigned_twice`.
* A non-recipient calling `acknowledge`/`complete` received 400; it now returns 403.
* The profile form re-submitted legacy values such as `N/A` and failed validation; it now sends
  only changed fields.
* Avatar preview used a doubled `/api/api/...` URL; the absolute URL from the API is used as-is.
* The bell only refreshed on a 60 s timer; explicit actions now dispatch a refresh event.

**Database**

Additive only — no legacy table altered, no enum value added, no production change. New tables:
`accounts_user_profile`, `command_unit_appointment`, `command_acting_appointment`,
`command_office_section`, `command_section_assignment`, `command_instruction`,
`command_instruction_recipient`, `command_mission_routing`, `command_approval_rule`,
`command_hazard_location`, `command_vehicle_position`, `ops_inventory_request`. New columns on
`ops_notification` (priority, action state, acknowledgement/completion, due date).

**Tests and checks (actual)**

* Backend: **210 passed, 0 failed, 0 skipped**.
* Frontend production build: success (107 modules, 433.89 kB / 127.64 kB gzip).
* Staging: additive migrations applied; legacy rows unchanged (9,846; `verify_legacy_mapping`
  0 mismatches). A pre-existing inconsistent history (`ops.0001` before `accounts.0001`) was
  repaired by creating the new `accounts_user_profile` table with the migration's own SQL and
  recording it; nothing was dropped.
* Browser walkthrough against staging verified the map, username feed, profile save,
  avatar/signature upload, section assignment, instruction issue/acknowledge/complete/verify,
  and the bell count clearing only on completion.
* Production database: **not contacted, not migrated, not deployed.**

---

## Enhancement phase 2 — UI themes, mission–MRM integration, user administration

Implemented in the existing TOWEMS codebase; no rebuild and no production change.

* **Appearance:** light/dark/system themes persisted per account (`accounts_user_profile.theme_preference`),
  applied before first paint, with a Settings page, top-bar toggle, SVG navigation icons, grouped
  menus, responsive layout and theme-aware map tiles.
* **Mission–MRM:** additive `ops_mission_risk_team`, MRM team picker on Air and Ground mission forms,
  server-side eligibility rules (active, command-tree, MRM capability), persistent "prepare the MRM"
  notifications, `POST /missions/{id}/start-mrm/` with legacy numbering, two-way mission/MRM links,
  derived statuses (not created / pending / in progress / submitted / approved / returned), overdue
  indicators, duplicate-link prevention, approved-assessment freeze, and evaluator assignment now
  synced to `mrm_forms.reviewed_by`.
* **User administration:** `manage_users` capability (Administrator only), `/admin/users` page with
  search/filter, audited deactivation/reactivation with a reason, guarded account edits (username,
  serial and passwords immutable), and single-use password-reset links stored only as a digest
  (`accounts_password_reset_token`). Inactive accounts lose existing sessions immediately.
* **Schema:** three additive objects only — one column on `accounts_user_profile`, two new tables.
  Legacy tables and rows untouched. Migrations: accounts 0003/0004, ops 0004.
* **Mission–MRM link integrity:** selecting an existing assessment on the mission form now creates
  the same `RiskAssessment` link as `start-mrm` (status shows `In progress`, not `Not yet created`),
  and a form already held by another mission is refused so one assessment cannot describe two
  missions.
* **Tests:** backend **251 passed, 0 failed, 0 skipped**; frontend build success (464.59 kB /
  135.91 kB gzip); staging migrations applied; browser walkthrough covered both themes, account
  persistence, mission→MRM creation and back-links, linking an existing assessment, the cross-mission
  link refusal, reset-link issue, and deactivate/reactivate. Production never contacted.

Full details: `docs/UI_MRM_USER_ADMIN_REPORT.md`, `docs/API_REFERENCE.md`,
`docs/ROLE_PERMISSION_MATRIX.md`.

---

## Enhancement phase 3 — AOR management, PSGC boundaries, basemaps and layers

Implemented inside the existing AOR/GIS module; no rebuild, no production change.

* **Administrator-only AOR management:** `manage_aor` capability (Administrator only,
  non-delegable); AOR create/update/delete/assign/transfer/remove now require it. Unit Safety
  Officers keep read access but lost write access (the previous behaviour; two GIS tests were
  updated deliberately). Every change is audited (`aor_assign`, `aor_transfer`, `aor_remove`) with
  before/after unit and geometry metadata.
* **Official boundaries:** additive `ops_administrative_area` (1,730 PSGC areas: 82 provinces,
  6 districts/special areas, 149 cities, 1,493 municipalities) imported by
  `manage.py import_admin_boundaries` from the bundled 1.2 MB dataset built by
  `tools/build_admin_boundaries.py` (PSA PSGC 3Q 2026 via faeldon/philippines-json-maps v1.0.0,
  MIT, WGS84). `AreaOfResponsibility` gained a nullable `admin_area` link plus a partial unique
  index, so one area cannot be held by two units.
* **Conflict detection:** Shapely-based intersection against the actual polygons — identical,
  contains/contained, partial. Different units may not share territory; same-area changes are
  explicit transfers (confirmation required, audited, transactional); existing overlaps are
  reported by `GET /aor/overlaps/` and never altered automatically.
* **Map experience:** server-configured basemaps (`/map/config/`) — streets, night streets,
  satellite, satellite with labels, terrain, and an optional self-hosted service — with per-user
  persistence, provider-failure fallback, independent operational layer toggles and a province/city
  boundary layer on both maps.
* **Schema:** one additive migration (`ops.0005_aor_admin_areas`) creating one table, one nullable
  column and one constraint. No legacy table, column or row touched.
* **Tests:** backend **274 passed, 0 failed, 0 skipped** (23 new in `test_aor_admin.py`); frontend
  build success (481.29 kB / 140.62 kB gzip); staging migration and import verified; browser
  walkthrough covered search, assignment, conflict refusal, confirmed transfer with audit, basemap
  switching and layer toggles. Production never contacted.

Full details: `docs/AOR_GIS_ENHANCEMENT.md`, `docs/API_REFERENCE.md`,
`docs/ROLE_PERMISSION_MATRIX.md`.

---

## Enhancement phase 4 — complete role management and administrator role assignment

Implemented inside the existing authority model; no rebuild, no production change, no data
migration beyond a choices-only field update.

* **Why only four roles appeared.** The Administrator interface (and the Add Personnel form) read
  `users.authority`, a legacy PostgreSQL enum with exactly four values. The eight TOWEMS command and
  staff posts already existed as unit appointments in `command_unit_appointment`, but no interface
  assigned them, so the system *looked* like it had four roles.
* **Operations Center is now a real office.** `operations_center` was a routing stage with no
  standing authority; it is now an office of the unit (additive
  `command_office_section.function`, values `general` / `operations_center`, one active Operations
  Center per unit) whose active members - primary or secondary - hold the role for that unit only.
  It can screen, comment and route; a new `review_missions` capability gates the Operations Officer
  stage, so the office cannot review or approve what it routed, and it never reaches another unit
  or subordinate units. The submit notification now reaches the office's members (Operations
  Officer and Admin Officer remain recipients, so nothing regresses when no office exists).
* **Authoritative role catalogue.** `GET /api/roles/` describes all 12 roles — label, kind
  (account / appointment / office / both), the legacy `account_value` to save, authority scope,
  record visibility, one-holder-per-unit flag, description and capabilities. The admin interface
  builds every picker from it; hardcoded frontend role lists are gone from the user-management page,
  and the Operations Center is never offered as a post.
* **Administrator role assignment.** `/admin/users` gained a **Roles & appointments** dialog: current
  account role, effective roles, office memberships, active posts per unit, assign a post (with scope
  and capability preview, and a warning naming the current holder of an exclusive post), and end a
  post. Account role changes stay on the account itself, and the Operations Center is designated by
  creating the office and assigning personnel under Offices & Sections. All paths are audited
  (`update_user_account`, `create_appointment`, `end_appointment`).
* **Stale-authority fix.** `roles.active_appointments` no longer caches on the user object, so a
  revoked post stops granting authority immediately instead of for the lifetime of that object.
* **Schema:** one additive migration (`command.0002_officesection_function_and_more`): a
  `function` column with a default, plus a partial unique constraint for one active Operations
  Center per unit. No new table, no legacy table altered, no row rewritten.
* **Tests:** backend **309 passed, 0 failed, 0 skipped** (35 new); frontend build success
  (488.16 kB / 142.27 kB gzip); browser walkthrough on staging covered the catalogue-driven
  filters, the office function selector and badge, office membership appearing in the user payload,
  the second-Operations-Center refusal, the "office, not a post" refusal from `/appointments/`,
  assigning/ending posts, the exclusive-post warning, the dialog refreshing after each change, and
  the audit entries.

Full details: `docs/ROLE_MANAGEMENT_ENHANCEMENT.md`, `docs/API_REFERENCE.md`,
`docs/ROLE_PERMISSION_MATRIX.md`.

---

## Mission Planning (Ground Operations) — planning authority, restricted visibility, selector rules

Implemented against the existing TOWEMS modules; **no database migration, no schema change and no
data rewrite**. Every legacy table, column and record is untouched.

### Planning authority

* `manage_missions` is now held by **only** the Operations Center office members and the
  Administrator. Commanders, Deputy Commanders, Directors for Operations, Operations Officers,
  Admin Officers, Logistics Officers and the Safety Officer keep `view_missions` but cannot create,
  edit or remove a mission (`403`). The Operations Center no longer inherits `create`/`edit`, so
  membership does not make someone an MRM author.
* `destroy` moved from approval authority to `manage_missions`: oversight can no longer delete a
  unit's mission record.
* Routing is its own permission (`CanRouteMissions`): the Operations Center routes at its stage and
  the Operations Officer at theirs, without either needing planning rights.
* Vehicle trips keep their previous rule through `CanPlanMovements` — restricting mission planning
  did not remove a logistics capability nobody asked to change.

### Mission visibility

`apps/ops/services.visible_missions(user)` is now the single visibility rule, used by the mission
list/detail queryset, the dashboard mission counters, the MRM counters and the operational map.
Command and staff officer posts see their unit tree; everyone else sees only missions they created,
are assigned to as personnel, or are on the MRM team for. An unrelated mission returns `404`, so
its existence is not even disclosed. The dashboard and map therefore cannot leak a mission the
list hides.

### Planning form (Air and Ground)

* The "Linked risk assessment (MRM)" selector is gone from the form. The API still accepts
  `risk_form` for compatibility and `start-mrm` remains the normal path.
* **UPR** is loaded from `/units/?scope=mine`: the operator's own unit and its authorised
  subordinates only.
* **O/SPR** is a selector over the offices/sections of the chosen UPR (disabled until a unit is
  chosen; changing the unit clears the choice).
* **Start/End** inputs removed. The form collects Estimated Start/End (ground) or Scheduled
  Departure/Arrival (air); the header `start_at`/`end_at` are derived server-side.
* **Operational area** is chosen on a map (`components/LocationPickerModal.jsx`). The point is
  converted to MGRS by `/gis/mrgs/` and to the official PSGC area by the new
  `/admin-areas/at/?lat=&lon=` lookup; the location name is prefilled from that area and can be
  edited. No coordinates are invented and no external geocoder is called.
* **MRM creator** may only be chosen from the mission's Personnel Involved list; **MRM evaluator**
  only from personnel assigned to the mission's unit with evaluation authority
  (`/personnel/assignable/?unit=&purpose=evaluator`). The server re-checks both, plus one creator
  and one evaluator per assessment, the creator's membership of the mission personnel, and the
  evaluator's unit.
* The submit flow is: create mission → `assign-personnel` → `PATCH /missions/{id}/` with
  `mrm_team` (`lead` = creator, `reviewer` = evaluator), then the existing trip handling.

### Interface

`Missions` is hidden from the navigation for users without `view_missions`, the mission list shows
an explanatory state instead of an empty table, and `/missions/new` renders an authority notice for
non-planners. The "Plan mission" button appears only with `manage_missions`.

### Tests

Backend: **329 passed, 0 failed, 0 skipped** (new: planning authority and visibility, derived
times, creator/evaluator rules, unit-only evaluator selector, `?scope=mine`, `/admin-areas/at/`,
role-catalogue capability assertions). Frontend: production build succeeds.

### Deployment and rollback

No migration to run. Deploy backend + frontend together (the frontend depends on the new
`can_plan_missions` / `can_view_missions` flags and the new lookup endpoint). Rollback is a code
revert; no data is transformed, so there is nothing to unwind in the database.

### Known limitations

* The O/SPR value is stored as the office name in the existing `office_section` text column (and
  mirrored into `ground_detail.osp_section`). Storing a foreign key would need a new nullable
  column plus a backfill; that was deliberately not done in this batch.
* Missions created before this change that have no personnel recorded cannot have an MRM creator
  assigned until personnel are added — the creator must be someone the mission actually lists.

---

## Offices & Sections catalogue, A3 mission planning, and Philippine time standard

Batch covering the Offices & Sections enhancement (§1–§5) and the time standard (§7).
No existing column, row or identifier was altered.

### Database (one additive migration — not yet applied to any database)

`command/migrations/0003_default_offices.py`:

| Change | Notes |
| --- | --- |
| `command_office_section.is_custom` | `BooleanField(default=False)`. Additive; existing rows need no rewrite. Distinguishes a standard staff office from a "Specify" entry. |
| Data step | For every existing unit, create the twelve catalogue entries (matched on `(unit, code)`, so re-running is a no-op) and **deactivate** any pre-catalogue office. Active `command_section_assignment` rows pointing at those offices are ended (`is_active=False`, `ended_at` set) — nothing is deleted, so history survives. The unit's Operations Center office is skipped: it carries routing authority and is not part of the staff catalogue. |

The legacy `units` table is unmanaged; the data step is guarded so it skips a database where the
table does not exist yet (as in a freshly migrated test database).

### Default office list

`A1 Personnel / Administration`, `A2 Intelligence`, `A3 Operations`, `A4 Logistics`, `A5 Plans`,
`A6 CEIS`, `A7 CMO`, `A8 Training`, `A9 Reservist Affairs`, `A10 Finance`, `SAFETY Safety Office`,
`OTHER Specify – Other Office/Section`.

The list lives in `apps/command/offices.py` and is served by `GET /api/offices/catalogue/`; the
React form no longer hard-codes it. Choosing `OTHER` requires `custom_name`; the row is stored with
an allocated `X-nn` code and `is_custom=True`. Choosing a standard code fixes its code, name and
kind server-side. Duplicate names inside a unit are rejected, and so are duplicate codes.

### Permissions

* `GET /offices/` and `GET /offices/catalogue/` are readable by any authenticated account
  (personnel and mission forms need the list).
* Create, edit, delete and `POST /offices/seed-defaults/` require `manage_sections` — the
  **Administrator** and the **Admin Officer** only, and only inside their authorised units.
* DELETE succeeds only for a row with no assignment history, no children and no Operations Center
  function; otherwise it returns `400` with a deactivate instruction. Every delete is audited.
* **A3 – Operations** membership now confers `view_missions` + `manage_missions`
  (`roles.OFFICE_CODE_CAPABILITIES`). Mission Planning therefore appears for those members (and for
  the Operations Center office and the Administrator) and is enforced by `CanPlanOperations` on
  every mission write. Ending the assignment or deactivating the office removes the grant on the
  next request. Commanders and staff officers keep read-only `view_missions`.

### Time standard

* `apps/core/timeutils.py` is the single source: `now()` (naive Philippine time regardless of the
  host zone), `to_manila()`, and `format_date`/`format_time`/`format_datetime` producing
  `29 Oct 2026, 1430H (PST)`.
* `settings/base.py` pins `TIME_ZONE = "Asia/Manila"` and normalises the process time zone
  (`TZ` + `tzset`) so Django's own `auto_now_add` columns and `timezone.now()` agree with it.
  `USE_TZ` stays **False**: turning it on would reinterpret every legacy row as UTC and shift the
  whole history by eight hours, which the data-preservation rules forbid
  (`docs/DATABASE_COMPATIBILITY.md` §5). This deviation from "USE_TZ = True" is deliberate and
  reported in the enhancement report.
* Frontend `utils/format.js` pins output to `Asia/Manila`, treats the API's naive values as
  Philippine wall clock (no device-zone shift) and converts aware instants explicitly. The five
  pages that each carried a private `formatWhen` with `toLocaleString()` now delegate to it.
* Mission forms use the new `DateTimeInput` (typed `29 Oct 2026 1430`) instead of
  `datetime-local`, whose rendering and AM/PM follow the device locale. Values are sent as naive
  Philippine wall clock, which is what the backend stores.
* MRM PDF dates (`Date`, `Document Verification`, authority approval) now use the same format.

### Verification

Backend: **365 passed, 0 failed, 0 skipped** (`pytest -q`, isolated SQLite test database).
New: `apps/command/tests/test_offices_catalogue.py` (35 assertions across catalogue, Specify,
permissions, deletion, seeding and A3 planning) and `apps/core/tests/test_timeutils.py`
(format, midnight/noon, UTC→Manila, midnight-crossing, host-zone independence).
Frontend: `npm run build` succeeds (498.24 kB / 145.81 kB gzip).
`manage.py makemigrations --check` reports no drift.

### Not done / requires approval

* Migration `0003_default_offices` has **not** been applied to any database. Applying it replaces
  the current per-unit office entries, so it needs explicit approval and a backup first.
* `USE_TZ` remains `False` (see above); changing it is a separate, data-migration project.

---

## Offices & Sections — deletion and catalogue reset

Adjustment to the office module after the first review: deletion was blocked with
"Offices/sections are deactivated, not deleted", which the unit asked to change.

* **Deletion actually deletes.** `DELETE /api/offices/{id}/` now removes the office row and the
  personnel assignments made to it, in one transaction (`apps/command/views.py`,
  `OfficeSectionViewSet.perform_destroy`). Child offices keep their own rows and are detached
  (`parent` is `SET_NULL`). The audit entry records the office's fields **and** the assignments
  that went with it, so the removal stays traceable after the row is gone. Deleting the
  Operations Center office ends that unit's routing authority until a successor is designated.
* **The table can be reset from the interface.** `POST /api/offices/reset-catalogue/` (Administrator
  only) clears every office and assignment row and seeds the standard list for all units, audited as
  one entry carrying what was removed. Migration `0004_reset_office_catalogue` performs the same
  reset once, on deployment.
* **New office defaults to A1** — the picker opens on `A1 - Personnel / Administration`, or the
  first standard office the unit does not yet have.
* Missions and MRM forms store their office as text, so they keep their labels; nothing outside
  `command_office_section` and `command_section_assignment` is touched.

Tests: `apps/command` 74 passed; full backend **369 passed, 0 failed**; frontend build succeeds.
`0004` has **not** been applied to any database.

## Unit map pins, unit seals and the profile photo

Unit management now gives each unit a map position, and the profile photo shows beside the user
name everywhere the shell appears.

* **Unit grid coordinate.** The legacy `units` table cannot gain columns, so a unit's pin lives in
  the additive side table `command_unit_map_marker` (migration `command.0005_unit_map_marker`,
  one row per unit, `unit_id` references `units.id` with `db_constraint=False`). It keeps
  `latitude`, `longitude`, `mgrs`, an optional place description and the seal's filename. The grid
  reference and the point are reconciled on write: a served MGRS converts to the point, a picked
  point derives the MGRS, and contradictory input is refused.
* **Unit seal is the pin.** `POST /api/units/{id}/marker-logo/` stores the unit's logo or seal
  (PNG/JPEG/WebP, 2 MiB, verified by magic bytes) in the configured legacy upload directory, and
  every map draws it as the marker. Without a seal the pin falls back to the unit
  acronym, so a pinned unit is never invisible.
* **Unit Management** (`/units`) lists the grid and location per unit, flags units that are not
  pinned, and gives an Administrator a "Set pin / Edit pin" dialog: type the MGRS, or pick the spot
  on the map with the existing place-search picker, then upload, replace or remove the seal.
* **Operational maps.** `GET /api/units/markers/` feeds the dashboard operational picture and the
  AOR map; both gained a "Unit locations" layer (the dashboard already draws it), and the AOR page
  adds it to existing saved layer selections so it is not silently missing.
* **Profile photo.** `/auth/me/` already returned `avatar_url`; `/auth/login/` now does too, so the
  picture beside the user name appears immediately after signing in instead of after a refresh.

Existing rows are untouched: no legacy table, column or record changes, and no data is deleted.
Tests: `test_unit_markers.py`, `test_gis.py`, `test_operational_picture.py` and `test_profile.py`
all pass; frontend `vite build` succeeds. `command.0005` is **not** applied to production.

### Unit Management becomes an Administrator module with add / edit / deactivate

First review of the unit pins found two things: the seal upload answered `404`, and the Unit
Management module was visible to every role and could not add or retire a unit.

* **Why the upload failed.** Three things, all environment rather than code:
  1. `command.0005` had never been applied to `toms_staging`;
  2. `tools/local_server.py` runs Django with `--noreload`, so a running server never picks up
     an edit;
  3. the browser was on the Vite dev server at `:5180`, whose `VITE_DJANGO_ORIGIN` points at a
     backend on `:8010` — a process started before the marker routes existed. That stale server is
     what answered `GET /api/units/{id}/marker-logo/` with `404` and unit creation with
     `405 Method "POST" not allowed`, while the freshly restarted server on `:8000` had the routes.

  Fixed by applying the migration to the staging copy only, then restarting **both** local
  backends (`:8000`, `:8010`) from the current code. Verified through `:5180` with a real
  Administrator session: create `201`, edit `200`, pin `200` (MGRS derived), seal upload `200`,
  seal served `200 image/png`, the sealed unit appeared in `/units/markers/` and in the
  operational picture, deactivate `200`, reactivate `200`; the test unit, its marker, its file and
  the temporary session were then removed. **Any further backend edit needs the same restart** —
  or drop `--noreload` while developing, and check which port the dev frontend proxies to.
* **Administrator-only module.** `UnitViewSet` now uses `CanManageUnits`
  (`apps/core/permissions.py`): reads stay open for the mission/office/AOR selectors, every write
  needs `system_config`, which only the Administrator holds. The page and the sidebar item are
  hidden for everyone else, and the page itself refuses to render its controls.
* **Add and edit.** `POST /api/units/` and `PATCH /api/units/{id}/` (`UnitWriteSerializer`) write
  the existing `units` table in place — no column added, renamed or dropped. Name is unique
  case-insensitively, the acronym is normalised, and a parent may not be the unit itself or one of
  its descendants, so the hierarchy cannot loop.
* **Deactivate / reactivate.** `POST /api/units/{id}/deactivate/` requires a reason, refuses while
  the unit has active subordinate units or planned/approved/active missions, and keeps every
  personnel record, mission, MRM form and audit row. `POST /api/units/{id}/activate/` reverses it.
  `DELETE` is refused (`405`) because history references the unit by id.
* **Interface.** The register shows status, grid, location and seal; the header offers *New unit*,
  each row offers *Edit*, *Set/Edit pin* and *Deactivate* (or *Reactivate*), deactivation asks for
  a reason in a dialog and shows the server's refusal verbatim.

Tests: new `apps/ops/tests/test_unit_admin.py` (14 tests: create/edit authorisation, duplicate name,
self-parent and loop refusal, delete refusal, reason required, subordinate/mission refusals,
deactivate-and-reactivate with records intact, register still readable by selectors) — all pass;
`apps/ops`, `apps/accounts` and `apps/command` together **420 passed, 0 failed**; `vite build`
succeeds. `command.0005` is applied to `toms_staging` only, never to production.

---

## Mission approval workflow, execution gate and planned vehicle routes

Additive throughout: `ops.0007_mission_approval_assigned_at_and_more` adds eight nullable/defaulted
columns to `ops_mission` and nine to `ops_vehicle_trip`. No column was altered, renamed or dropped,
existing rows stay valid (approval is `not_routed`, revision `1`), and the migration was applied to
`toms_staging` only.

**Why the assigned MRM team could not start an assessment.** `start-mrm` inherited the mission-level
`CanPlanOperations` permission, which requires `manage_missions` — held by the Operations Center and
Administrators, not by the MRM team. The action now allows an active member of that mission's
`MissionRiskTeam` (checked against the mission itself), so team members can start their own
assessment while everybody else is still refused, and missions they cannot see still return 404.
Local servers must be restarted: `tools/local_server.py` runs with `--noreload`.

**Approving authority (`spec §2`).** `ops_mission.approving_authority` + `approving_authority_role`
store the post *and* the person; the role alone never authorises approval. Eligibility is resolved
from live `UnitAppointment`/`ActingAppointment` rows through `roles.effective_roles`, scoped to the
mission's unit; inactive accounts, holders without the post and the mission's creator are refused.
`POST /api/missions/{id}/approving-authority/` assigns or reassigns (audited as
`assign_approver`/`reassign_approver`, notification opens an action item); `GET
/api/missions/{id}/approving-authorities/` lists the roster and `GET
/api/missions/approving-authorities/?unit=` serves the planning form. Routing at the Operations
Center stage now names the authority in the same call, and is refused without one.

**Decisions.** `POST /api/missions/{id}/approval/` with `approve|return|reject` is accepted only from
the assigned person, takes a row lock, refuses a second decision, and requires a note for
return/reject. Each decision writes a `MissionRouting` row plus an audit entry and notifies the
creator. The legacy `approve` action still works for missions with no assignment and now records the
same approval fields, so both paths agree.

**Execution gate (`spec §3`).** `apps/ops/approvals.py` is the single rule: a mission may only be
executed when its MRM form is `authority_approved` and not returned/rejected, and an approval covers
the current revision. Enforced by `POST /api/missions/{id}/dispatch/`, by `PATCH status="active"` and
by `status="approved"` in the write serializer. Blockers are returned with a readable sentence and a
`blockers` list. Material changes (title, purpose, unit, dates, location, coordinates, MRM link,
personnel, areas, category detail, itinerary) bump `revision`, so an old approval stops covering the
mission and the table shows *Reapproval required*.

**Monitoring (`spec §4`).** The serializer adds `approval`, `execution` and `vehicles`; the mission
list accepts `approval_status`, `mrm_status`, `readiness`, `approving_authority=me`, `start_after`
and `start_before`, all filtered in the database with the same facts the gate uses.

**Ground vehicle routes (`spec §5`).** `POST /api/missions/{id}/vehicles/` assigns vehicles from the
existing inventory, giving each its own trip (and therefore its own itinerary and route); refusals
cover unknown, inactive, unserviceable and already-committed vehicles. `PUT
/api/trips/{id}/itinerary/` replaces the ordered stop list atomically and discards any cached route.
`POST /api/trips/{id}/route/` calls a road-network engine through `apps/ops/routing.py` (OSRM by
default; `TOMS_ROUTING_BASE_URL` for a self-hosted instance, `TOMS_ROUTING_ENABLED=0` to disable) and
stores geometry, per-leg distance/duration and totals. An unroutable leg fails with the reason and
stores no geometry — a straight line is never substituted. Nothing claims live tracking: there is no
GPS source, and the payload is labelled a planned route.

Tests: `apps/ops/tests/test_mission_approval.py` (17) and `test_mission_routing.py` (12), plus four
existing workflow tests updated for the new routing step. Whole backend suite passes; live OSRM
call for three Philippine stops returned 22.67 km / 24.7 min over 503 geometry points; `vite build`
succeeds.

- **Administrator-only mission deletion.** `DELETE /api/missions/{id}/` now
  requires the Administrator capability instead of the Operations Center's
  planning permission, so planning and erasure stay separated (the mission
  creator and the Operations Center are refused with 403). Deletion still
  refuses anything that entered service: only `draft` and `cancelled` missions
  can be removed, everything else must be cancelled. The missions table grows a
  Delete action in its Actions column, rendered only for Administrators and
  disabled with an explanation for missions that must be cancelled instead.

- **Mission dates are picked, not typed.** `DateTimeInput` (used by the planning form for
  estimated start/end, scheduled departure/arrival and the vehicle trip dates) is now a native
  calendar plus a quarter-hour time list in 24-hour PST instead of a free-text field. Days before
  today are disabled in the calendar and clamped if one arrives from the keyboard, so a plan can
  no longer be scheduled into the past by mistyping; the value handed to the form is still
  `YYYY-MM-DDTHH:mm` in Philippine wall-clock time.

- **Paired mission dates now constrain each other.** `DateTimeInput` takes `minDateTime`; on the
  start's own day the time list begins at the start time, so an Estimated end (and, for the same
  reason, an Air arrival or a vehicle trip return) can no longer be picked earlier than the value
  it must follow. The floor disappears on any later day, and a start moved past an already-chosen
  end raises an inline error on the end field instead of reaching the server.

- **The time half is a clock, not a list.** `DateTimeInput` now renders a native time control
  (`lang="en-GB"`, `step="60"`) beside the calendar instead of the quarter-hour dropdown, so any
  minute can be set and the control formats in 24-hour time rather than the device's locale. The
  earlier-than-start rule survives: on the floor's own day the control carries `min` and a chosen
  time below it is pulled up to the floor.

- **Personnel pickers list senior rank first.** New `utils/ranks.js` holds the rank seniority
  order (LTGEN … AM/AW) with the accepted spellings of each step, and the mission planning form
  sorts its personnel options with it: highest rank first, then surname order for equal ranks, with
  an unrecognised or blank rank at the end rather than at the top. The list was previously the
  server's surname ordering, which put a Sergeant above a Colonel.

- **The time is typed on a 24-hour clock, with no AM/PM.** The browser's own time control was still
  rendering 12-hour with AM/PM on machines whose locale asks for it, so `DateTimeInput` now shows two
  number fields — hour 00-23 and minute 00-59 — that read 0000 to 2359 whatever the device locale.
  Each has a spinner and takes arrow keys; digits are padded on blur ("9" becomes "09"), values
  outside the range are clamped, an incomplete entry never reaches the parent, and a commit below the
  paired floor is replaced by the floor. The backend time field cannot hold 24:xx, so midnight is
  00:00, the same instant the rest of the app prints as 0000H.

- **Personnel options no longer repeat the rank.** `User.full_name` is already rank-prefixed
  ("MSg Joelito S Uriarte"), so the mission form's personnel pickers were printing the rank twice
  ("MSg Joelito S Uriarte (MSg) — 784512"). The parenthetical is gone; options read
  "MSg Joelito S Uriarte — 784512" in both the Personnel involved list and the MRM evaluator list.

- **The operational-area picker opens on the unit's own post.** `LocationPickerModal` accepts a
  `unitId` (the mission form passes the selected UPR; otherwise the signed-in user's unit is used)
  and reads `/api/units/{id}/marker/`, so the map centres at zoom 12 on that unit instead of the
  whole country, with the unit drawn as a blue circle and its acronym on hover. A pinned point
  passed in still wins over the unit; a unit with no stored coordinates leaves the opening view
  unchanged. The move happens once the marker request returns, and never after the operator has
  already searched or clicked.

- **Officer In Charge added to mission personnel designations.** `MissionPersonnel.ROLE_CHOICES` had
  Team Leader, Assistant Team Leader, Member, Driver, Crew, Medic and Other but no Officer In Charge,
  so that designation could not be recorded. It is now the first choice in the model (migration
  `ops.0008`, choices only — no column change) and in the planning form's designation list, which
  reads Officer In Charge, Team Leader, Assistant Team Leader, Driver, Member, Crew, Medic, Other.

- **MRM evaluator list follows the same rank order.** The evaluator options on the planning form are
  built from `/personnel/assignable/?purpose=evaluator`, which returns surname order; they are now
  sorted with the shared `byRank` comparator, so the selector reads LTGEN … AM/AW like the personnel
  picker instead of putting a Sergeant above a Colonel.

- **Approving post list is never an empty dropdown.** The four posts that may hold approval
  authority are now also defined in the planning form and used whenever
  `/api/missions/approving-authorities/` has not answered (or was refused), matching what the mission
  detail panel already did. The card shows one message at a time: the server's refusal, the "no
  active holder" warning, or the note that a draft may be saved without an authority.
- **Dev server note:** the browser's dev server (port 5180) proxies `/api` to Django on **8010**, not
  8000. A backend restart must target the port the dev server actually points at, or the browser
  keeps running the old code while the restarted process looks healthy.

- **Vehicle trips inherit the mission's window and its ends of the route.** Opening the trip copies
  the mission's estimated start and end into Departure and Return (only when those fields are still
  empty, and again if the mission's dates change before the planner has set its own), so a trip no
  longer needs the same two dates typed a second time.
- **Itineraries start with the origin and the destination already placed.** Turning a vehicle on
  seeds two stops: the point of origin from the vehicle's own unit marker (falling back to the
  mission's UPR marker), and the destination from the pinned operational area — the latter also
  filled in later if the area is pinned after the vehicle was chosen, without overwriting anything
  typed. **Add stop** now inserts between those ends rather than after the destination, and
  **Return to origin** appends the first stop again as the closing leg, so a round trip needs no map
  work at all.

- **Starting an MRM opens it.** `start-mrm` returns the new assessment, so the mission page now
  navigates straight to `/risk/{mrm_form}` instead of showing a notice and staying put, and the
  confirmation prompt is gone - one click creates and opens the draft. The button already refused to
  run without an MRM team, so nothing is created by accident.

- **The MRM assessment can finally be filled in.** New `apps/ops/mrm_editor.py` ports the Flask
  create/edit form onto the existing legacy tables, so a TOWEMS assessment is the same record the old
  screens and PDF read:

  * `GET /api/risk-register/hazards/?activity_code=&q=` searches the unit's own `hazard_registry`
    (591 rows for TOG10 in staging) - the same scope as the legacy `/mrm/search_hazards`.
  * `GET|PATCH /api/risk-register/{id}/details/` reads or saves the assessment body: safety
    objectives, mission statement, aircraft/vehicle, the four environment choices (stored as the
    legacy "Weather. Lighting. Workspace. Tempo." sentence), the I'M SAFE answers as `imsafes` JSON,
    the selected hazards in `mission_hazards` with their after-mitigation overrides and additional
    mitigations, and the personnel tagged for feedback in `mrm_personnel`.
  * Saving recomputes `max_risk`, `residual_risk`, `total_percent`, `authority_level` and
    `risk_category` with `apps.ops.risk` - the already-ported legacy algorithm - and mirrors the
    figures onto the mission's `RiskAssessment` link.
  * Editing is limited to the creator, the mission's active MRM team, the unit safety officer and an
    Administrator, and only while the assessment is a draft. An evaluator reviews; they do not write.
  * Deselecting a hazard or untagging a person deactivates the row instead of deleting it, so the
    assessment keeps its history.

  No new tables and no migration: everything lands in the legacy columns. The risk detail page grows
  the editor (`MrmAssessmentEditor`), with library search, after-likelihood/severity, additional
  mitigations, live risk, personnel tagging and the I'M SAFE checklist.

- **The assessment now describes the mission it covers.** `mrm_editor.mission_context(form)` reads
  the linked mission, and the editor copies it in:

  * the **Aircraft / Vehicle** field starts from the mission - the aircraft type and tail number of an
    air mission, or the registration numbers of the vehicles on its trips - and a `Use mission
    aircraft / vehicle` button re-applies it.
  * the **mission statement** starts from the mission's **combined itinerary**: each trip's own stop
    order (`Camp Aguinaldo → Checkpoint 2 → Forward Base`), or an air mission's departure and
    destination airfields when it has no ground trips. Existing values are never overwritten.
  * the same context feeds the forecast below, so the assessment, the planner and the weather all
    describe one route.

- **Weather can be taken from a real forecast.** New `apps/ops/weather.py` asks an
  Open-Meteo-compatible service (`TOMS_WEATHER_BASE_URL`, no key needed; `TOMS_WEATHER_ENABLED=0`
  switches it off) for the hourly weather at the mission's pinned location and every itinerary stop,
  over the mission's own time window, and maps the result onto the MRM's six weather choices -
  thunderstorm and any measurable rain are `Rainy / Wet`, fog or ≤18 °C is `Cold / Foggy`, ≥30 km/h
  wind is `Windy`, ≥33 °C is `Hot / Humid`, and clear/cloudy otherwise, with the worst condition
  across the samples reported. `GET /api/risk-register/{id}/forecast/` returns the condition plus the
  sentence shown under the Weather field; `Use forecast weather` sets the select. A lookup that
  cannot answer - no coordinates, a mission beyond the 15-day horizon, the provider down - returns
  `available: false` with the reason and the planner picks by hand. Nothing is ever invented.

  Staging check (MRM 333, mission 7): the route resolves to three stops around Butuan; Open-Meteo
  answered **Rainy / Wet** in 9 of 15 forecast hours pending the mission window.

- **Hazard selection is by activity code.** `GET /api/risk-register/hazard-codes/` lists the activity
  codes held by the caller's own unit registry (44 codes for TOG11 in staging), with how many hazards
  each holds. The free-text activity-code box and the hazard-description filter are gone; picking a
  code loads that code's hazards.

- **The assessment form now reads like the mission brief.** Following the planner's review:

  * **Aircraft / Vehicle** shows the full equipment: model and registration (`KIA Nr 12345567`,
    `C-130 Nr 12345567`), for every vehicle on the mission's trips or the aircraft on an air mission.
  * **Environmental conditions is one text box.** The weather / lighting / workspace / tempo dropdowns
    are gone; the planner writes the conditions in their own words and `Use forecast weather` fills
    the box from the route forecast. The stored `mrm_forms.environment` column was always free text,
    so old records still open unchanged, and clients sending the four old choices still save (they are
    composed into the legacy sentence).
  * **Personnel tagging starts from the mission.** The candidates are the people assigned to the
    mission, each labelled with their mission role; tagging is a toggle per person and nobody has to
    be tagged.
  * **Hazards are picked in a modal and read at a glance.** `Open hazard library` opens the activity
    code picker and result list with before/after ratings; selected hazards are cards with a
    green/amber/red edge and badge for their after-mitigation rating, plus the controls beside them.
    The header shows the live residual, percentage and authority band in the same colours.

- **Refinements after using the form on a phone:**

  * `Use forecast weather` now writes only the condition (`Rainy / Wet`) into the environmental conditions
    box; the forecast hours and locations stay as the note beside the button instead of being pasted in.
  * The hazard library shows an entry as activity code plus hazard description only - the library
    mitigations no longer appear in the picker (they remain on the selected hazard where they are
    assessed).
  * The library is phone-shaped: the modal becomes a full-height sheet at <=640px, rows wrap with a
    20px checkbox and their ratings on their own line, the picker list scrolls with the modal instead
    of inside it, and the hazard controls and personnel toggles take the full width.
  * Risk colour is the unit's matrix exactly, with an explicit green list (`3E, 2D, 2E, 1B, 1C, 1D, 1E`);
    anything not in the red/amber/green lists (an unrated `N/A`, for instance) is shown grey instead of
    falling through to green.

- **The evaluator comes from the Operations Center, not a picklist.** The mission's MRM team already
  carries the evaluator (role `reviewer`), but `start-mrm` ignored it, so assessment 333 started with
  no evaluator and the risk page offered a dropdown of every evaluator in scope.

  * `start-mrm` now copies the mission team's reviewer onto the legacy form (`reviewed_by`,
    `reviewer_name`, `original_reviewer_unit`) and onto the mission link, so a new assessment is
    already evaluated by the person the Operations Center named.
  * `GET /api/risk-register/{id}/` exposes `mission_evaluator` / `mission_evaluator_name`, and the risk
    page shows that evaluator instead of a picklist. Where an older draft still lacks it (333 did), one
    button applies it; the picklist survives only for a mission whose MRM team names no evaluator.
  * MRM 333 on staging was set to its mission's evaluator, 1LT ARNEL ANTHONY S ARAÑEZ, through the
    audited `assign-evaluator` endpoint as its creator.

- **Phone modal scrolling and a denser hazard row.** The hazard library could not be scrolled on a
  phone: `.modal-body` had `overflow-y: auto` but no `flex: 1 1 auto; min-height: 0`, so the list grew
  past the dialog instead of scrolling inside it. The body is now the single scroller (modal overflows
  hidden), and `.library-list` no longer keeps a second scroller on small screens. A row is now two
  lines: `Air to Ground Operations Training  (Before 3C)  (After 2D)` over the hazard description,
  instead of three with the ratings in a right-hand column.

  Verified at 390x844 with the real stylesheet: body scrollHeight 3600 vs clientHeight 725, scrollTop
  moves, the footer stays on screen, and the page has no horizontal overflow.

- **`Use forecast weather` writes into the conditions box.** Pressing it now puts the forecast sentence
  in the environmental conditions text area (editable like typed text) and leaves no label beside it;
  only an unavailable forecast still reports its reason under the button.

- **The selected hazard now reads like the assessment sheet.** Each card is a single column:

  ```
  Aircraft Ground Collision (Aircraft vs Aircraft or Vehicle) (5A Unacceptable)
  -Library mitigations: Enforce marshaller and wing-walker procedures
  -Use pushback tugs and tow bars properly
  ...
  5 - Frequent            |            A - Catastrophic
  Additional mitigations               |   Remove
  ```

  The activity code is gone from the selected hazard (it stays in the library picker), the inherent
  rating is printed beside the description with its matrix word (Unacceptable / Tolerable / Acceptable),
  and each library mitigation is its own bullet. The two after-mitigation selects sit on one line, with
  additional mitigations beside Remove. The card's left edge still carries the after-mitigation colour,
  so the assessed risk stays visible at a glance.

- **Hazard card colours follow the printed rating.** The rating beside a hazard is its inherent
  (library) rating, printed as `(4A Unacceptable)` and coloured from the matrix: green acceptable,
  amber tolerable, red unacceptable. The card's left edge keeps the after-mitigation colour that feeds
  the mission's risk, and the Hazards header carries a one-line legend explaining the difference, so
  the two colours can no longer be read as a contradiction. Library mitigation lines also lose their
  stored `*` / `-` markers (and `* bullet * bullet` runs on one line split into separate bullets)
  before the `-` prefix is drawn.

- **The rating in parentheses on a selected hazard is the after-mitigation one.** The planner is right:
  the hazard already carries mitigations, so the number printed beside it must be the assessed risk,
  not the inherent risk. It is now derived from the effective likelihood/severity (the planner's
  override, else the library's after value), so it updates as the two selects change and always equals
  the rating the card's edge is coloured by - green acceptable, amber tolerable, red unacceptable. The
  library picker still shows Before and After side by side for reference.

- **Detail cards are compact on a phone.** Below 860px `dl.details` switches from a stacked
  2-column grid to key/value rows - label left, value right in its semibold weight, a hairline between
  rows - so the 15-field Assessment card reads as a short list instead of a tall wall of label blocks.
  Measured at 390x844 with the real stylesheet: every row is 26-34px, all values share one right edge,
  no label/value overlap, no horizontal overflow, and the card is 582px tall (desktop keeps the
  two-column layout at 1281px). The page header actions also take two per line below 560px so the
  buttons keep their labels.

- **Evaluator assignment card removed from the risk page.** The evaluator is fixed by the Operations
  Center on the mission's MRM team and is written onto the assessment when it starts, so the separate
  assignment card (its evaluator list fetch, selection state and save button) is gone and the evaluator
  is shown once, in the Assessment summary. `POST /risk-register/{id}/assign-evaluator/` and the
  `mission_evaluator` fields remain on the API for other clients and for correcting a record, but the
  page no longer offers a second place to choose the reviewer.

- **Personnel tagging is a checklist.** The designation is already set by the Operations Center on the
  mission, so the form no longer offers a role dropdown: each person on the mission has one checkbox
  (ticked = tagged for post-mission feedback, unticked = not tagged), shown with their mission
  designation as read-only text. Saving writes that mission designation into `mrm_personnel.role`, so
  the two records cannot drift apart. Anyone tagged before this change who is no longer on the mission
  still appears in the list, so they can be unticked.

- **The Assessment summary follows the saved assessment.** `MrmAssessmentEditor` now hands its save
  response to the page, which merges `max_risk`, `residual_risk`, `total_percent`, `authority_level`
  and `risk_category` into the header card immediately and then reloads the record. Verified by
  `test_assessment_summary_reflects_saved_hazards`: after saving one hazard at 2 x D the MRM endpoint
  itself returns max 3, residual 1, 33.3%, matching the PATCH response.

- **The PDF is rendered from the saved assessment, every time it is opened.** There is no cached draft
  document - `stored_pdf_path` only answers for an authority-approved copy on disk - so the hazards,
  their after-mitigation overrides, additional mitigations, environment, aircraft/vehicle and mission
  statement all come from the record at request time. `test_pdf_is_generated_from_the_saved_assessment`
  saves an assessment and asserts the PDF builder receives exactly that saved data.

- **Submitting an MRM asks the creator to sign now or later.** `POST /risk-register/{id}/submit/`
  takes `sign`, and the risk page opens a dialog on Submit instead of submitting silently: "Sign now
  and submit" applies the uploaded signature, "Sign later" submits unsigned. Signing without an
  uploaded signature never fails the submission - the dialog offers the upload, and a
  `signature_required` refusal from the server opens a prompt that links to Profile → Signature
  (`/profile#signature`, scrolled into view). The signature step itself is
  `POST /risk-register/{id}/sign/`, offered on the risk page whenever the creator still owes it.

- **An approved review reminds the creator to sign.** When the evaluator approves and
  `creator_signed` is still false, `_notify_creator_to_sign` leaves an action notification ("Sign MRM
  <number>") with a deep link to the assessment; it is closed by the `sign` action, not by reading it.
  The risk page shows the same reminder as a banner with the signature button.

- **The Decision Authority is routed from the residual risk band, then signs the MRM.** The unit
  policy (≤50% creator, 51-65% supervisor/evaluator, 66-75% Squadron Commander - routing to the
  Director for Operations when the unit has none, 76-85% Director for Operations, 86-100% Commander)
  is mapped by `apps/ops/decision_authority.py` onto the people who actually hold the post:
  `UnitAppointment` rows first, then a user whose designation names the post, searching up the unit
  chain so a flight reaches its parent squadron's Commander and a squadron its group's Director. The
  risk page now shows the band, the signer and why they were chosen, and only that person (or an
  administrator) is offered the signature button - `can_approve_authority` follows the same rule the
  approve endpoint enforces, so the button is never a dead end. Below 66% the creator's signature is
  the last one, and the assessment closes itself as authority-approved without a separate approval
  step.

- **The risk register's read scope now follows the unit's policy per role.** `apps/ops/mrm_access.py`
  is the single source: the Administrator sees every MRM in every unit; the Unit Safety Officer sees
  their own unit, its subordinates and the units they are assigned to; Commander, Deputy Commander,
  Director for Operations, Operations Officer and Operations Center members see their own unit and
  subordinates; everybody else sees only the assessments they raised, were assigned (evaluator or
  routed decision authority) or were tagged on. The narrowing means a colleague in the same unit who
  is neither attached nor an oversight post now gets a 404 on the detail instead of a 403, which is
  the point: the register no longer discloses assessments the policy does not give them.

- **The register filters by unit and by the dates the MRM covers.** `GET /api/risk-register/?unit=<id>`
  narrows to one unit, and `date_from`/`date_to` match the linked mission's `start_at`-`end_at`
  window (an open-ended mission counts as still covered), falling back to `date_created` for legacy
  rows that were never linked to a TOWEMS mission. `GET /api/risk-register/units/` returns the filter
  choices, already scoped to what the caller may read, so the dropdown can never offer a unit whose
  records the list would not return.

- **MRMs can be deleted, singly or in bulk, by the roles allowed to.** `DELETE
  /api/risk-register/{id}/` and `POST /api/risk-register/bulk-delete/` (`{"ids": [...]}`) are open to
  the Administrator for any assessment and to the Unit Safety Officer only for one that is not
  approved. Deletion removes the legacy dependants (hazards, tagged personnel, feedback), the TOWEMS
  mission link and the stored approved PDF, detaches `Mission.risk_form`, and writes the full
  snapshot to both audit trails first. The bulk endpoint reports `deleted` and `skipped` with the
  reason for each refusal, so a blocked selection is explained instead of silently ignored.
  `can_delete` on the serializer decides whether the interface offers the button at all.

- **The /risk page gained the unit, date-covered, delete and bulk-delete controls**, with the unit
  column and the covered dates in the table and a per-page "select all deletable" toggle. Tests:
  `apps/ops/tests/test_mrm_access.py` (23 cases across every role, both filters and both delete
  paths).

- **The MRM PDF opens in an in-app pdf.js reader instead of a frame.** The old modal embedded
  `/api/risk-register/{id}/pdf/` in an `<iframe>`, but Django sends `X-Frame-Options: DENY` with every
  response, so the browser refused the frame ("127.0.0.1 refused to connect"). The header is left
  alone: `components/PdfViewer.jsx` fetches the PDF with the session cookie, renders every page to a
  canvas at `devicePixelRatio`, and offers fit-width, zoom (40-300%), rotate, download and multi-page
  scrolling. It is used by the register's PDF button and preview, the assessment page's View PDF, and
  the Safety Audit register, so phones work without relying on a browser's built-in PDF plugin.
  `pdfjs-dist` is a frontend dependency; a small `vite.config.js` plugin serves its standard fonts and
  cmaps in development and copies them to `dist/pdfjs/` at build time, so non-embedded standard fonts
  (including ZapfDingbats) render with the correct metrics. Verified live at 1280x860 (fit width 98%)
  and 303x799 (fit width 26%): three pages rendered with ink, zoom re-rendered all pages, and the
  console was free of pdf.js font warnings.

## Vehicle drivers and searchable personnel pickers (2026-10-31)

- **A vehicle cannot be added to a trip until its driver is designated.** In
  `MissionFormPage`, each inventory vehicle is a row with a driver picker. The
  "add to trip" checkbox stays disabled until a driver is chosen, and the row
  says so. One driver cannot be selected for two vehicles on the same trip, and
  clearing a driver removes the vehicle from the trip (its itinerary and route
  plan go with it). No vehicle is sent to the API without a driver.
- **Personnel involved is chosen from a dialog, then given designations.** The
  "Add person" button opens a modal roster that can be typed into (rank, name or
  serial), with checkboxes for one or more people and an "Add selected (n)"
  action; already-assigned people are not offered again. Closing the dialog
  returns numbered rows in the form `1. [MSg France S Dabatian-823629] [Team
  Leader] [×]`, where the designation is selected per person and the × removes
  that person. The same searchable behaviour remains in the driver picker, so a
  325-person staging roster never has to be scrolled end to end.
- **A single designated Driver is assigned automatically for a one-vehicle trip.**
  When exactly one person in Personnel involved has the Driver designation and
  exactly one vehicle is added, `MissionFormPage` fills that driver in without
  another selection. The vehicle checkbox is enabled for that case. A driver the
  planner already chose is never overwritten. With two or more vehicles no
  automatic assignment is made: the second vehicle stays blocked until its own
  different driver is chosen, and the first driver cannot be reused.
- **The server checks the driver assignment.** `POST
  /api/missions/{id}/vehicles/` now accepts `drivers` as an object keyed by
  vehicle id. Every selected vehicle must have one, each driver must be an
  active person already assigned to that mission's personnel, and duplicate
  drivers are refused. A mission that already has an active personnel roster
  cannot save vehicles without drivers. New links store `driver_id`, existing
  links are updated when the driver changes, and the audit entry records both
  the old and new driver map. `MissionSerializer` now returns `driver_name` for
  each planned vehicle, shown on the mission detail page.
- **Tests:** `apps/ops/tests/test_mission_routing.py` gained five cases covering
  per-vehicle drivers, a mission with personnel but no driver map, a selected
  vehicle with no driver, duplicate drivers and a driver who is not on the
  mission roster. The mission, routing and trip suites pass (56 tests), and
  `npm run build` succeeds.

## Driver view fix, robust GPS sharing, delay alerts and the movement log (2026-10-31)

- **The driver page renders again.** `/missions/:id/track/driver` called
  `useApi(...)` without importing it, so the component threw a
  `ReferenceError` before drawing anything and the user saw the empty page
  background ("blank black"). The import is restored. No data was affected:
  `/api/missions/{id}/tracking/` kept answering 200 throughout.
- **Breadcrumbs survive a dead spot, a locked screen and a reload.**
  `DriverTrackingPage` writes its queued positions to local storage
  (`toms.track.queue.<mission>`) after every fix, restores them on mount,
  drops anything older than a day, and sends the backlog as soon as the
  `online` event fires. Switching vehicle flushes what belongs to the old run
  first and only then clears the queue, so a position is never attributed to
  the wrong vehicle. The queue is labelled on screen ("N positions waiting to
  send") instead of failing silently.
- **The screen stays awake while sharing.** The page requests a Screen Wake
  Lock, re-acquires it when the tab becomes visible, and tells the driver when
  the lock is held. Geopositioning is refused up front - with an explanation -
  when the page is not a secure context, instead of failing silently on a
  phone that opened a bare IP over HTTP.
- **A vehicle behind its plan is reported, not just coloured.** New
  `delay_for_run` / `delay_report` / `alert_delays` in
  `apps/ops/tracking.py` compare each active run against the times a person
  planned: an unconfirmed departure past the ETD, the next stop whose planned
  arrival has passed without a recorded arrival, or a late final arrival. A
  vehicle counts as late past 15 minutes (`DELAY_GRACE_MINUTES`). The
  Operations Center, Commander, Deputy Commander, Director for Operations,
  Operations Officer and Unit Safety Officer receive one `movement_delay`
  notification per run per 45-minute window, so a long delay is a reminder
  rather than a stream. Alerts are evaluated when the driver's phone reports,
  when a movement event is recorded, and when a monitor opens the board;
  `manage.py check_vehicle_delays` is the scheduled backstop for a vehicle
  whose phone is off.
- **The board shows the same judgement it alerts on.** `GET
  /api/missions/{id}/tracking/` returns `delays` (per run: status, minutes,
  kind, the stop concerned and the reason) and `delay_grace_minutes`. The
  convoy map shows a warning listing every vehicle behind plan, a per-vehicle
  line in the side panel, and the driver page shows the driver's own lateness.
  Nothing here promotes a GPS breadcrumb to an official time.
- **The movement log is a document.** `GET
  /api/missions/{id}/tracking/log/` renders a landscape A4 PDF (inline, or
  `?download=1`) with, per vehicle, ETD/ATD including whether the ATD was
  confirmed, departure variance, each stop's planned and actual arrival and
  departure with the variance, the append-only event list with its source and
  recorder, the note, and the ping count and last fix. Monitoring roles, the
  Operations Center and that mission's own drivers may read it; anyone else is
  refused. The convoy map opens it in the existing pdf.js viewer and the driver
  page links to it.
- **Tests:** new `apps/ops/tests/test_movement_log.py` (13 cases) covers a
  missed stop alerting the monitoring roles, de-duplication, an unconfirmed
  departure, punctual and early runs not being called late, the board payload,
  the scheduled sweep, PDF rendering, download disposition, driver and outsider
  access, a log with no recorded event, and cancelled runs. A dispatch case was
  added to `test_mission_approval.py` proving that dispatching opens one run per
  planned vehicle with the dispatch time as an unconfirmed ATD and notifies the
  driver. The full backend suite passes (617 tests) and `npm run build`
  succeeds.

## Map no longer paints over dialogs (2026-10-31)

- Opening the movement log on `/missions/{id}/track` drew the modal *under*
  the map. Leaflet gives its panes and controls z-index values from 400 to
  1000; the map box created no stacking context, so those panes competed with
  the page's own overlays and beat the dialog's `z-index: 100`. `.map-canvas`
  is now `position: relative; isolation: isolate`, which keeps Leaflet's
  z-index range inside the map box, and `.modal-backdrop` moved to `1200` so a
  dialog stays on top of any map, including one not wrapped in `.map-canvas`.
  Verified in the running app by probing the real stylesheet: with a pane at
  z-index 1000 inside the map, the element at the dialog's corner is the
  dialog. `npm run build` succeeds.

## Hazard registry and audited post-mission feedback (2026-10-31)

- **The hazard library now has a module.** `/hazards` reads and writes the preserved
  legacy `hazard_registry` table - no new table, no altered rating rule. The Unit
  Safety Officer and the Administrator can add, edit and delete a hazard, import a
  workbook and export the current selection. Commander, Deputy Commander, Director
  for Operations and Operations Officer open the same page read-only; every other
  account has no menu entry and gets 403 from the API. Ratings are always calculated
  (`4B` = likelihood 4, severity B) exactly as the 1,923 legacy rows were.
- **Import, export and template share one format.** `GET /api/hazards/template/`
  returns an `.xlsx` with dropdown validation for likelihood (1-5) and severity
  (A-E); `POST /api/hazards/import/` validates every row and imports **all or
  nothing**, listing each bad row by its spreadsheet row number; `GET
  /api/hazards/export/` writes the caller's current filter as a workbook that can be
  imported again. The Administrator names a unit per row; a Safety Officer may only
  import into their own unit. The MRM assessment's hazard picker reads the same
  table, so a row added or corrected here appears in the next assessment.
- **Deletion protects the record.** A hazard selected by an MRM is refused with
  "used by N MRM assessment(s)" rather than cascading into the assessment, its PDF
  and the mission's risk history. Map pins that only georeference the row are
  removed with it.
- **Post-mission feedback is open at `/feedback`.** The people tagged on an MRM
  (`mrm_personnel`) see what is waiting on them, what is not open yet and why, and
  what they already filed. Feedback opens once the assessment is approved and its
  mission is completed or cancelled; one submission per person per assessment, with
  ratings, free text, an optional anonymous flag and an optional recommendation to
  grow the hazard library. Rows are written to the preserved `mrm_feedback` table.
- **The Unit Safety Officer audits it.** `GET /api/feedback/audit/` is scoped to the
  units that officer covers, with unit, status, date and text filters plus summary
  counts and the average mitigation-effectiveness rating. Each submission can be
  marked reviewed, actioned or dismissed (a dismissal needs a reason), and the
  submitter is notified of the outcome unless they filed anonymously. A recommended
  hazard can be adopted straight into the registry with an activity code and
  assessed likelihood/severity; the feedback is then marked actioned and records the
  new hazard's id.
- **Permissions.** New capabilities `view_hazards`, `manage_hazards` and
  `audit_post_mission_feedback`, exposed to the interface as `can_view_hazards`,
  `can_manage_hazards` and `can_audit_feedback`; the matrix in
  `docs/ROLE_PERMISSION_MATRIX.md` lists every role.
- **Tests:** `apps/ops/tests/test_hazard_registry.py` (20 cases) and
  `apps/ops/tests/test_post_mission_feedback.py` (19 cases) cover the read/write
  matrix, unit isolation, rating recalculation, the delete guard, template,
  import/export and its all-or-nothing behaviour, the feedback window, duplicate and
  untagged submissions, notification of the safety office, audit scoping,
  review/dismissal rules and hazard adoption. The full backend suite passes and
  `npm run build` succeeds.

## Dashboard traces, hazard workbook units, numbered pagination (2026-10-31)

* **Completed missions leave no route trace on the dashboard map.** The operational picture
  (`GET /api/gis/operational-picture/`) excludes trips whose mission is `completed` or `cancelled`
  from `trip_routes`, so the map shows live work instead of every finished convoy. The mission pin
  itself is still returned — one status filter away — and the mission track page keeps its history.
* **The hazard import template's Unit column is a dropdown of real units.** `build_template()` now
  takes the caller and writes the units they may import into on a `Units` sheet, with a list
  validation covering the column (not an inline list, which Excel truncates at 255 characters). The
  Safety Officer sees their own unit; the Administrator every active unit. The export carries the
  same dropdown, so it round-trips. An import still refuses a unit name TOWEMS does not record.
* **Export is restricted to the Unit Safety Officer and the Administrator.** Command and operations
  posts keep read access; `GET /api/hazards/export/` answers 403 for them, and the page hides the
  button. Docstring, role matrix and tests updated.
* **Numbered pagination on every paginated page.** The shared `Pagination` component renders
  `1 2 3 4 … 42` with the current page marked, so a long list is one click from any page instead of
  repeated "Next" presses. Applies to the risk register, hazards, missions, personnel, inventory,
  trips, users, audit, feedback and safety audit.
* **The hazard table stacks on a phone.** `Table` gained a `stack` prop that turns each row into a
  labelled card under 640px; the hazard library uses it, and its three filters stack too.

## Ending a mission from the mission list (2026-10-31)

The Operations Center now has an **End mission** action on `/missions` for a mission in progress
once the plan itself says the work is over: its estimated end date/time has passed, or every
vehicle trip it planned is completed. The rule lives in the serializer (`closing` on every mission
row: `available`, `reasons`, a human `reason`, and `open_runs`), so the list and the endpoint cannot
disagree. The button posts to `POST /api/missions/{id}/tracking/complete/`, naming the reason in the
override note; when a run is still open the confirmation says how many will be cancelled and the
close is recorded as an Operations Center override in the movement log. Missions that are drafted,
planned, already closed, or still inside their window with trips running stay without the action.

## Inventory and the vehicle register belong to the Logistics Officer (2026-11-01)

Writing to the inventory register and the vehicle register is now the Logistics Officer's, enforced
by a capability rather than by the account type.

* **Inventory (`/inventory`).** Adding an item, editing it, deleting it, changing its condition and
  recording a stock movement all require `manage_inventory`. `InventoryPermission` no longer
  special-cases the legacy Unit Safety Officer authority, and `ROLE_SAFETY_OFFICER` no longer holds
  `manage_inventory` — so the Safety Officer, command posts and every other account keep read access
  (controlled items included, for the Safety Officer) but get 403 on writes, and the page hides
  *Add item* and *Move stock*. Inventory requests are unchanged: the Logistic NCOIC proposes and the
  Logistics Officer decides.
* **Vehicles (`/vehicles`).** A new `manage_vehicles` capability, held by the Logistics Officer post
  and the Administrator, gates `VehicleViewSet` through the new `CanManageVehicles` permission.
  Reads stay open to every authenticated account because mission planning, dispatch and the tracking
  board all need the register; the Operations Center can still assign existing vehicles to a trip
  (`POST /api/missions/{id}/vehicles/`, unchanged) but can no longer add, edit or retire a vehicle.
  The *Add vehicle* button follows `permissions.can_manage_vehicles`.
* **Scope is unchanged.** Both registers stay inside the caller's unit tree; a Logistics Officer
  acting on another unit's item gets 404, not a silent success.
* **Tests:** `apps/ops/tests/test_inventory.py` now exercises the movements as a Logistics Officer
  and adds the safety-officer read-only, logistics add/edit/delete and cross-unit cases;
  `apps/ops/tests/test_vehicle_register.py` covers add/edit/delete, the Operations Center's read-only
  access and the other roles' refusal. 673 backend tests pass and `npm run build` succeeds.
* **Flake repaired:** `test_the_lead_vehicle_is_unique_and_may_record_convoy_milestones` read the
  departure event with `.last()`, which follows `MovementEvent.Meta.ordering = ["actual_at", "id"]`;
  dispatch's fallback event is stamped with the server clock and sorted after the test's hardcoded
  06:05 whenever the suite ran later that day. It now takes the newest row by id.

## Inventory asset classes: materiel, equipment, AGE, firearms, ammunition, vehicles (2026-11-01)

The register had **no categories at all**, so no item could be filed — the "Add item" form had
nothing to select. It now covers the classes the unit accounts for.

* **Aerospace Ground Equipment (AGE)** is a new class (`kind = "age"`), separate from general
  equipment because it carries its own custodianship and inspection cycle. The class labels were
  also brought in line with staff vocabulary: *Materiel*, *Equipment*, *Aerospace Ground Equipment
  (AGE)*, *Firearms*, *Ammunition*, *Vehicles*, *Communications Equipment*, *Other Authorised
  Assets*. Stored keys are unchanged, so existing rows keep their class.
* **The catalogue is seeded** by migration `0011_inventory_asset_classes` — one category per class
  (`MAT`, `EQP`, `AGE`, `COMMS`, `FA`, `AMMO`), with firearms and ammunition marked controlled.
  Seeding is `get_or_create` on the unique code: re-running cannot duplicate a row and never
  overwrites a category an officer has renamed.
* **Vehicles are deliberately not seeded as an inventory category.** The vehicle register is the
  record of truth for a vehicle — it is what dispatch, trip planning and tracking read — so a second
  row for the same truck would drift. The inventory page lists the register under its Vehicles
  class, read directly from `/api/vehicles/`, with the register one click away.
* **Items can be filtered by class:** `GET /api/inventory/items/?kind=age` (`kind` filters on
  `category__kind`; `filterset_fields` cannot express a related lookup, so it is applied beside the
  unit and controlled-item scoping). Every item and category row now carries its human label
  (`category_kind_label`, `kind_label`).
* **Category management in the interface** for the Logistics Officer: add a category (code, name,
  class, controlled flag) and remove one. Removing a category that still holds items now answers a
  readable 400 naming the count instead of a database `ProtectedError` 500.
* **Controlled items follow the capability, not the legacy account type.** `InventoryItemViewSet`,
  `InventoryTransactionViewSet`, the dashboard's `sensitive_visible` flag and the inventory report
  all use `_can_see_sensitive` (`view_sensitive_inventory`) instead of `users.authority`, so the
  Logistics Officer — who now maintains firearms and ammunition — can see the rows they maintain.
  A Creator account still cannot.
* **Interface:** the inventory page gained asset-class chips, class and category filters, a class
  badge and unit-of-measure on every row, and a fuller item form (description, unit of measure,
  condition, storage location); the table stacks on a phone.
* **Tests:** `apps/ops/tests/test_inventory.py` covers the seeded classes, the `kind` filter, the
  controlled-category rule, Logistics Officer visibility, category add/delete and the delete guard.
  681 backend tests pass and `npm run build` succeeds.

## Inventory spreadsheets and figures for the Logistics Officer (2026-11-01)

The register could only be filled one item at a time, and the page opened on an empty table with
no sense of what the unit actually holds.

* **Excel workflow, Logistics Officer only.** `GET /api/inventory/template/`,
  `GET /api/inventory/export/` and `POST /api/inventory/import/` all require the `manage_inventory`
  capability, so the Safety Officer keeps reading the register but cannot download or load a
  workbook. The Administrator retains it. Logic lives in `apps/ops/inventory_excel.py`, with the
  endpoints in `apps/ops/inventory_views.py`.
* **Every field an encoder could mistype is a dropdown.** The workbook carries a hidden `Lists`
  sheet (Excel refuses an inline list over 255 characters, and a real unit's category list passes
  that) and data validation on Asset Class, Category, Condition, Unit of Measure and Unit; Quantity
  is a whole number of 0 or more. The Lists sheet is built from the register itself — categories
  with their codes, the caller's own unit tree, real conditions — so the picker cannot offer
  something TOWEMS does not record.
* **Import is all-or-none and never overwrites.** Each row is resolved against the database (a
  `CODE - Name` cell, a bare code or a unique name; the declared class must agree with the
  category). A misspelled condition, unknown unit of measure, unit outside the caller's scope, an
  existing or duplicated asset tag, or a negative quantity is reported per row and nothing is
  written. Stock on hand is opened as a **receipt** in the ledger, so balances reconcile from day
  one; an existing item is edited in the app, because changing `quantity_total` outside a
  transaction would leave unexplained stock.
* **`GET /api/inventory/stats/`** aggregates the same scoped queryset the table reads: line items,
  quantity on hand vs on the books, controlled stores, low stock (≤ 5 available, the dashboard's
  threshold) and out of stock, 30-day movements, plus per-class and per-condition breakdowns, every
  class listed including empty ones.
* **Interface:** header figures and two breakdown cards with share bars, class chips that carry
  their counts, low/out markers on the quantity column, and Import (modal with the rows to fix),
  Export and Template buttons for the Logistics Officer. The export link carries the table's
  current filters.
* **Tests:** `apps/ops/tests/test_inventory_excel.py` (23 tests) covers the template's pickers and
  unit scoping, the Logistics-only rule for all three endpoints, import success with its opening
  receipt, blank-unit defaulting, foreign unit, unknown/sibling category, duplicate asset tag,
  negative quantity, misspelled condition, controlled category, missing columns, non-xlsx upload,
  export filtering and round-trip safety, and the statistics' scope and controlled-item rules.
  704 backend tests pass, `npm run build` succeeds, and the endpoints were checked against the
  staging copy (template pickers, export workbook, Safety Officer 403).

## Dashboard focus and a presence rail (2026-11-01)

The dashboard carried two cards that were not part of the operational picture, and nothing showed
who was actually using the system.

* **Recent activity moved to Settings.** The feed is unchanged (last ten audit entries in the
  caller's scope, scoped exactly as before) but it now renders on `/settings` under *Recent
  activity*, with a link to the full audit trail. **Quick actions** is gone: the sidebar already
  carries every one of those destinations, so the card was a duplicate menu.
* **"Online now" rail on the dashboard.** The map keeps the width; a 300 px rail sits beside it
  (stacking below 1180 px) listing the people currently signed in — **serial number plus a small
  picture**, with rank, name and designation in the tooltip. A `you` badge marks the caller and a
  green count sits in the card header. It refreshes every 30 s, and skips the poll while the tab is
  hidden.
* **Presence is stamped from the request, not from a heartbeat.** `UserProfile.last_seen` (new,
  nullable) is updated by `PresenceMiddleware` on each authenticated request, at most once a minute
  per account (per-process throttle), so a page full of polls is not a write per request. "Online"
  means *seen in the last 5 minutes*; a closed browser or expired session simply ages out, and
  nothing needs cleaning up. Migration `accounts.0006_userprofile_last_seen` is additive and
  backfills nothing — guessing from `date_created` or the audit log would show people online who
  are not.
* **`GET /api/presence/`** returns `{window_minutes, count, users[]}` for the caller's unit tree
  (own unit plus subordinates; `unit_id IS NULL` included, like the rest of the personnel data),
  most recent first, capped at 40 rows. Each row carries `serial_number` (never username or email),
  `avatar_url` through the existing unit-scoped `/personnel/{id}/avatar/` endpoint, `rank`,
  `full_name`, `designation`, `last_seen` and `is_self`. An unauthenticated caller gets 403; a
  caller with no unit information gets an empty list rather than everyone's.
* **Tests:** `apps/accounts/tests/test_presence.py` (11 tests) covers the session stamp, the
  one-minute throttle, per-account throttling, the five-minute window, serial-number/avatar payload,
  unit scoping including subordinates, the unauthenticated refusal, profile creation on a first
  request, and DRF-only callers being stamped by the view. 715 backend tests pass, `npm run build`
  succeeds, and the endpoints were checked against the staging copy (`last_seen` stamped, presence
  returned the administrator with `ADMIN001` and their avatar URL, dashboard still returns 10
  activity entries for the Settings card).

## Instructions restricted to command posts (2026-11-02)

The Instructions & Action Items module is now closed to everyone except the Administrator, the
Commander, the Deputy Commander and the unit's Operations Center office.

* **New capability** `view_instructions` (`apps/core/roles.py::CAP_VIEW_INSTRUCTIONS`), held by those
  four roles - the Administrator through `ALL_CAPABILITIES`, the two command appointments and
  `ROLE_OPERATIONS_CENTER` explicitly. `permissions_payload` exposes it as
  `can_view_instructions`.
* **Server-enforced.** `CanIssueInstructions` (`apps/core/permissions.py`) now requires the
  capability for *every* method, not just writes, so the list, the unit picker, individual records,
  `acknowledge`/`complete`/`verify` and attachment downloads all answer `403` to anyone else. Being
  named as a recipient does not grant access.
* **Frontend.** The sidebar entry carries `permission: "can_view_instructions"`, `ProtectedRoute`
  gained a generic `capability` prop, and both `/instructions` routes are wrapped in it, so typing
  the URL sends the account back to the dashboard.
* **Tests.** `apps/command/tests/test_instructions.py` was rewritten for the new policy: responders
  are Operations Center members (the only non-command recipients who can open a record), and new
  tests pin that the module is open to Commander/Deputy/Operations Center/Administrator and refused
  to Creator, Evaluator and Safety Officer - including a Creator who is a named recipient.
  `docs/ROLE_PERMISSION_MATRIX.md` gained section 9 recording the rule and its consequence.
* Also installed `openpyxl==3.1.5` (already pinned in `toms_backend/requirements.txt`) into
  `.venv-toms`, which was missing it; without it the whole backend suite failed to collect.

## Correction: only the "All visible" tab is restricted (2026-11-02)

The entry above over-applied the rule: it closed the whole Instructions module, which broke delivery
to recipients. The final rule is narrower.

* **Everyone keeps the module.** The sidebar entry, both `/instructions` routes and `GET
  /api/instructions/` are available to every signed-in account again, so an addressed recipient can
  open the record and acknowledge/complete it.
* **Only the "All visible" view is restricted** - the tab that lists every instruction in scope
  rather than the caller's own. Capability `view_all_instructions`
  (`apps/core/roles.py::CAP_VIEW_ALL_INSTRUCTIONS`, exposed as `permissions.can_view_all_instructions`),
  held by the Administrator, Commander, Deputy Commander and the unit's Operations Center office.
  The tab is filtered out of the page for everyone else, and the server refuses a list request with
  no `scope` parameter - which is exactly what that tab sends - with `403`.
* `CanIssueInstructions` is back to its original shape (any authenticated account may read; writes
  need `comment`); the scope rule lives in `InstructionViewSet.get_queryset` where the request tells
  the two apart. `ProtectedRoute` is back to its original signature.
* Tests: `test_instructions.py` restored to recipient-based coverage, plus new cases pinning that
  "All visible" is open to Commander/Deputy/Operations Center/Administrator and refused to Creator,
  Evaluator and Safety Officer, while a recipient still sees and acts on their own item.
