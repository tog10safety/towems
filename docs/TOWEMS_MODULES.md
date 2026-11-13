# TOWEMS Modules — Tactical Operations Wings Enterprise Management System

Scope: the Django (`toms_backend/`) + React (`toms_frontend/`) implementation that upgrades the
legacy Flask MRMS. Every module below is implemented against the **existing production schema**
(11 legacy tables, preserved) plus 21 additive tables created by `ops/migrations/0001_initial.py`.

Conventions used throughout:

| Symbol | Meaning |
| --- | --- |
| **Legacy** | Table/model owned by the old Flask app. Django maps it with `managed = False` and never emits DDL for it. |
| **Additive** | New table created by the TOWEMS migration. Never touches legacy structures. |
| **UI** | React route in `toms_frontend/src/App.jsx`. |
| **API** | DRF route under `/api/` (see `docs/API_REFERENCE.md`). |

---

## 1. Dashboard and Operational Monitoring

* **API:** `GET /api/dashboard/`
* **UI:** `/`
* **Tables:** `ops_mission`, `ops_mission_personnel`, `users`, `ops_inventory_item`,
  `ops_vehicle`, `ops_vehicle_trip`, `ops_area_of_responsibility`, `ops_notification`,
  `mrm_forms` (**Legacy**), `core_audit_log`
* **Behaviour:** every counter is a live `COUNT()`/`AGGREGATE()` over real rows, unit-scoped by
  `apps/core/scoping.py::unit_ids_for_filter` so a user only ever sees their own units
  (sensitive inventory excluded unless the caller holds a command/safety role).
* **Fields shown:** ground missions (active/approved), air missions (planned/completed),
  risk awaiting action (to review / to approve), personnel available, inventory items
  (unserviceable, low stock), vehicles available, trips in progress, AORs mapped.
  The recent-activity panel is the last 10 rows of `core_audit_log` (actor + action + entity).
* **Scope:** missions, personnel, inventory, vehicles and AORs are unit-filtered; the three
  risk counters are currently **global** (see `CHANGES.md` #12) because legacy `mrm_forms`
  rows are also visible to their creator/evaluator regardless of unit.

## 2. Personnel Management

* **API:** `GET/POST /api/personnel/`, `GET/PATCH/DELETE /api/personnel/{id}/`
* **UI:** `/personnel`
* **Tables:** `users` (**Legacy**, mapped by `apps/accounts/models.py::User`,
  `db_table="users"`, `managed=False`), `units` (**Legacy**)
* **Behaviour:**
  * Rank, designation (office/section), assigned unit, category (Officer / Enlisted / CivHR),
    serial number, availability, operational role.
  * Roles: `Administrator`, `Evaluator`, `Creator`, `Unit Safety Officer` (the legacy
    `authority_enum` values — unchanged) plus mission roles `Team Leader`,
    `Assistant Team Leader`, `Member`, `Driver`, `Crew`, `Medic`.
  * Deactivation, not deletion: `is_active = false` keeps the row and every historical link.
  * Legacy password hashes (Werkzeug **scrypt**) are verified by
    `apps/accounts/backends.py`; `TOMS_REHASH_ON_LOGIN` (default `false`) controls whether
    a modern Django hash is written back. Nobody is locked out by the migration.

## 3. Inventory and Logistics Management

* **API:** `/api/inventory/categories/`, `/api/inventory/items/`, `/api/inventory/transactions/`
* **UI:** `/inventory`
* **Tables (all Additive):** `ops_inventory_category`, `ops_inventory_item`,
  `ops_inventory_transaction`
* **Behaviour:**
  * Categories: materiel/general supplies, equipment & tools, firearms, ammunition,
    vehicles, communications equipment, other.
  * Item identity: code, serial number, custodian (`users`), unit (`units`), storage
    location, serviceability (`serviceable | unserviceable | maintenance | condemned`),
    stock level, `is_sensitive` flag.
  * Transactions: `receipt | issue | return | transfer | maintenance | inspection |
    adjustment`, each written inside a `transaction.atomic()` block with a `SELECT … FOR
    UPDATE`-style row lock, so concurrent issues cannot drive stock negative
    (`apps/ops/services.py`).
  * Sensitive records (firearms/ammunition) are filtered server-side by
    `apps/ops/views.py::InventoryItemViewSet.get_queryset` + `InventoryPermission`
    (`?is_sensitive=` is a filterset field); a request from an unauthorised role receives
    **403**/empty results and the record is not merely hidden in the UI.

## 4. Air Operations

* **API:** `POST /api/missions/` with `category="air"`, `GET /api/missions/?category=air`
* **UI:** `/missions/new` (Air Operations tab), `/missions/{id}`
* **Tables (Additive):** `ops_mission` + `ops_air_mission_detail`
* **Behaviour:** shared mission header (title, purpose, unit, office/section, dates,
  status, location, MGRS/lat-lon) plus flight-specific fields: aircraft/tail number, flight
  identification, departure/destination, route, flight schedule, crew, operational
  considerations, hazards, risk controls, approvals, completion.
* Validation: `end_at >= start_at`, mission dates required before `approved`, unit required.

## 5. Ground Operations

* **API:** same `/api/missions/` endpoint with `category="ground"`
* **UI:** `/missions/new` (Ground Operations tab)
* **Tables (Additive):** `ops_mission` + `ops_ground_mission_detail` + `ops_mission_personnel`
* **Fields:** Mission/Activity, Estimated Start Date & Time, Estimated End Date & Time,
  Unit Primary Responsible (UPR), Office/Section Primary Responsible (O/SPR), personnel
  involved (selected from the real `users` inventory), role per person
  (Team Leader / Assistant Team Leader / Member / Driver), location & operational area,
  required vehicles/equipment/supplies, ground-specific risk assessment, approval status.
* Air and Ground are **mutually exclusive by `category`**: a mission cannot carry both detail
  rows; `Mission.save()` keeps the two detail tables consistent (`test_missions.py` asserts
  this).

## 6. Mission Risk Management (MRMS preserved)

* **API:** `/api/risk-register/` (+ `submit`, `review`, `approve`, `recalculate`,
  `assign-evaluator` actions)
* **UI:** `/risk`, `/risk/{id}`
* **Tables:** `mrm_forms`, `mission_hazards`, `mrm_personnel`, `mrm_feedback`,
  `feedback_notifications`, `hazard_registry` (**all Legacy**) + `ops_risk_assessment`
  (Additive, links a TOWEMS mission to a legacy `mrm_forms` row)
* **Preserved workflow:**

  ```
  draft ─submit→ submitted ─evaluator review→ reviewed ─authority approval→ approved
  ```

  * Risk scale: likelihood `1–5` × severity `A–E`, rating is the concatenated **string**
    (`"4B"`) — never a product. `apps/ops/risk.py` reproduces the legacy algorithm exactly
    and `test_evaluator.py` pins golden values taken from real rows.
  * `max_risk`, `residual_risk`, `total_percent`, `risk_category` (Low ≤ 50, Medium ≤ 75,
    High > 75) mirror `routes/mrm.py::get_risk_category`.
  * Signatures (`creator_signed`, `evaluator_signed`) and `is_approvable` gating are
    reproduced before authority approval can succeed.
  * Hazard overrides (`override_likelihood`, `override_severity`, `additional_mitigations`,
    `selected`) and unit snapshots (`original_*` strings **and** `original_*_id` FKs) are
    preserved.
  * Historic MRMs stay in `mrm_forms`: **292** rows visible through
    `/api/risk-register/` in staging.

### 7. Evaluator-selection bug — root cause and fix

The legacy defect was a cluster of four problems (full analysis in
`docs/TECHNICAL_AUDIT.md` §6). The Django/React implementation fixes them structurally:

| # | Legacy defect | Fix in TOWEMS |
| --- | --- | --- |
| 1 | Evaluator list filtered on `user.unit` (a hybrid property, not a column) → the query itself crashed, so the dropdown could not build. | `GET /api/risk-register/evaluators/` filters on real columns (`unit_id__in`, `authority='Evaluator'`, `is_active=True`, self excluded). Tested by `test_evaluators_endpoint_excludes_self_and_returns_fields` and `test_evaluator_outside_authorised_units_is_rejected`. |
| 2 | Backend coerced any non-numeric `reviewed_by` to `None` and accepted any numeric id — creators, inactive users, strangers, or nonexistent ids passed. | One authoritative resolver, `apps/ops/services.py::resolve_evaluator`, used by **both** create and `assign-evaluator`. It validates: exists → active → authority is `Evaluator`/`Administrator` → inside the actor's authorised units → (optionally) not self. Failure raises `400 {"errors": {"evaluator_id": …}}`. |
| 3 | On the edit screen the `reviewed_by` input sat **outside** `<form>`, so the value was never submitted and never re-read by the POST handler. | The React detail page owns a single state field `evaluatorId` seeded from `data.reviewed_by`; the `<select>`, the payload `{evaluator_id: Number(evaluatorId)}` and the reloaded record all read the **same** source field. There is no second hidden input. |
| 4 | Create and edit disagreed about self-assignment; the template hid options the route still accepted. | The rule lives only on the server (`resolve_evaluator(…, allow_self=False)`); the UI merely renders what the API returns. |

Additional server-side guard carried over from the legacy intent: **the evaluator cannot be
changed after review** — `assign-evaluator` returns
`400 {"detail": "The evaluator cannot be changed after review."}` once `status != "draft"`.

### 7.1 View MRM in PDF

* **API:** `GET /api/risk-register/{id}/pdf/` (add `?download=1` for an attachment)
* **UI:** **View PDF** / **Download PDF** buttons on `/risk/{id}`
* **Implementation:** `apps/ops/pdf.py`, a Django port of the legacy
  `services/pdf_service.py`; `reportlab==4.2.0` + `qrcode==7.4.2` (the same versions the
  Flask app already used) were added to `toms_backend/requirements.txt`.
* **Behaviour:**
  1. If `mrm_forms.approved_pdf_filename` points at a file inside `approved_pdfs/`, that
     file is served verbatim (it is the signed evidence of record). The path is resolved and
     must stay inside the storage directory — traversal attempts return the generated
     document instead of a foreign file.
  2. Otherwise the document is rendered from the record: unit-hierarchy header with the
     mother/current unit logos, mission block with *Compiled by* / *Reviewed by*
     signatures and their verification QR codes, document-verification strip, objective,
     assumptions, I'M SAFE / environment, aircraft / mission, the hazard table with
     before-after likelihood, severity, risk colouring and mitigations, the risk summary,
     the five-band authority matrix with the active band highlighted, the verification
     footer, and the risk-matrix legend — matching the legacy layout page for page.
* **Access:** the record must be in the caller's unit scope **and** the caller must be an
  administrator, the creator, the assigned evaluator, the approval authority, or be viewing
  an authority-approved document (mirrors legacy `serve_approved_pdf`).

**Verified end to end in the browser** (staging, admin session):

1. `/risk/1` (REVIEWED/APPROVED) — the `<select>` renders with the stored evaluator
   `1LT ARNEL ANTHONY S ARAÑEZ` selected (the historical bug showed a blank/other value).
2. Attempting a change on that reviewed record → the API 400s and the page shows
   *"cannot be changed after review"*.
3. `/risk/316` (DRAFT, evaluator id 6) → changed to id 9, **Save evaluator** → `Saved.`, and
   after reload both the read-only `EVALUATOR` detail and the `<select>` show
   `2LT RONEL M ALMARIO`. Record then restored to its original evaluator id 6.

## 8. Vehicle Trip and Itinerary Management

* **API:** `/api/vehicles/`, `/api/trips/`
* **UI:** `/vehicles`, `/trips`, and **Add Vehicle Trip** inside `/missions/new`
* **Tables (Additive):** `ops_vehicle`, `ops_vehicle_trip`, `ops_trip_itinerary`,
  `ops_vehicle_trip_vehicle`
* **Behaviour:** one trip → many vehicles (M2M through `ops_vehicle_trip_vehicle`) and many
  itinerary legs (departure, stop, destination; planned date/time; MGRS or lat/lon; remarks).
  A trip may link to a mission (`ops_vehicle_trip.mission_id` → `ops_mission`); the mission
  detail page lists its trips. Statuses `draft → planned → approved → in_progress →
  completed | cancelled`, editable only while draft by the creator or an admin.

## 9. Unit Management

* **API:** `/api/units/`
* **UI:** `/units`
* **Tables:** `units`, `unit_assignments`, `unit_logos` (**all Legacy**)
* **Behaviour:** hierarchy (Command → Wing → Group → Center → Squadron) read from the real
  parent/child rows, leadership, assigned personnel, office/section, inventory
  accountability (`ops_inventory_item.unit_id`), missions, and unit-scoped reporting.
  Unit identifiers and the `original_*` snapshots on MRM records are untouched.

## 10. AOR and GIS Mapping

* **API:** `/api/aor/`, `POST /api/gis/mgrs/`, `POST /api/gis/search/`
* **UI:** `/aor`
* **Tables (Additive):** `ops_area_of_responsibility` — `GeoJSON` polygon text plus numeric
  `latitude`/`longitude`/`mgrs` columns. **PostGIS is not installed** on the target server
  (`docs/DATABASE_COMPATIBILITY.md`), so no extension was added; spatial operations run in
  Python (`apps/core/geo.py`, `apps/core/mgrs.py`).
* **Behaviour:** polygon create/edit/visualise (Leaflet + react-leaflet), unit-to-AOR
  assignment, mission locations, MGRS ⇄ WGS84 conversion (bidirectional, tested round-trip),
  map search, layer visibility, GeoJSON import/export, permission-gated writes and an audit
  entry for every boundary change.

## 11. Reports and Analytics

* **API:** `GET /api/reports/{report}/` with `?export=csv`
* **UI:** `/reports`
* **Reports:** `missions`, `risk`, `inventory`, `personnel`, `trips`, `units`, `aor`
* Every report is permission-scoped by unit and supports date range / status filters.
  CSV export returns `text/csv`; JSON returns `{report, count, results}`.

## 12. Notifications and Approvals

* **API:** `/api/notifications/`
* **UI:** bell in the layout header
* **Tables:** `ops_notification` (Additive) + `feedback_notifications` (Legacy)
* Raised on submit / review / approve / evaluator assignment / trip status change; mark-read
  writes `is_read`/`read_at`.

## 13. Audit Trails and System Administration

* **API:** `/api/audit/`
* **UI:** `/audit`
* **Tables:** `user_activities` (Legacy, written by `apps/core/legacy_audit.py` so old and new
  activity share one feed) and `core_audit_log` (Additive, `apps/core/audit.py`)
* Every mutating endpoint records: actor, timestamp, entity type, entity id, action,
  `before`/`after` snapshot of the changed fields. Actions include `create`, `update`,
  `delete`, `approve`, `review`, `submit`, `assign_evaluator`, `login`, `password_changed`.

## 14. Authentication and authorisation

* Session auth (CSRF cookie + `X-CSRFToken`) for the React app, plus stateless
  `POST /api/auth/token/` (SimpleJWT) for integrations.
* Password policy carried over: min 8, upper/lower/digit/special, 90-day expiry,
  5-password history, `force_password_change`.
* Server-side unit scoping (`apps/core/scoping.py`) is applied to every list endpoint;
  frontend menu visibility is never the security boundary.
* CORS/CSRF origins come from `DJANGO_CORS_ALLOWED_ORIGINS` / `DJANGO_CSRF_TRUSTED_ORIGINS`;
  no secret is hard-coded (`.env.example` templates only).

## 15. Test coverage (`toms_backend/*/tests`)

Run: `.venv-toms/Scripts/python.exe -m pytest` → **128 passed, 0 failed, 0 skipped**
(settings: `toms.settings.test`, SQLite test database — never production).

| Area | File |
| --- | --- |
| Login/logout/CSRF/me/password, health | `apps/accounts/tests/test_auth.py` |
| Personnel CRUD + permissions | `apps/accounts/tests/test_personnel.py` |
| Evaluator list, resolver rules, self-assignment, invalid ids | `apps/ops/tests/test_evaluator.py` |
| Risk workflow, golden risk values, signatures | `apps/ops/tests/test_risk.py` |
| MRM PDF: view, download, stored copies, access, traversal | `apps/ops/tests/test_pdf.py` |
| Air/Ground separation, mission CRUD | `apps/ops/tests/test_missions.py` |
| Inventory transactions, stock atomicity, sensitivity | `apps/ops/tests/test_inventory.py` |
| Trips + itinerary | `apps/ops/tests/test_trips.py` |
| MGRS round-trip, AOR boundaries | `apps/ops/tests/test_gis.py` |
| Audit logging of every mutation | `apps/ops/tests/test_audit.py` |
| Dashboard figures | `apps/ops/tests/test_dashboard.py` |
| Legacy mapping (`verify_legacy_mapping`) | `manage.py verify_legacy_mapping` (staging) |
