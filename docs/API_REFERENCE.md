# TOWEMS API Reference

Base path: `/api/` · Schema: `GET /api/schema/` (OpenAPI 3, YAML) ·
Interactive docs: `GET /api/docs/` (Swagger UI) · Version `1.0.0`.

## Authentication

Two schemes, both enabled:

| Use | Scheme |
| --- | --- |
| React app | Session cookie `toms_session` + CSRF header |
| Integrations / scripts | `POST /api/auth/token/` → `Authorization: Bearer <access>` |

Session flow used by the frontend:

```
GET  /api/auth/csrf/          → sets the csrftoken cookie
POST /api/auth/login/         {username, password}  + X-CSRFToken: <cookie>
GET  /api/auth/me/            → current user + permissions (200) or 401/403 when signed out
POST /api/auth/logout/
POST /api/auth/password/      {current_password, new_password}   (policy enforced)
POST /api/auth/token/         {username, password}           (JWT, stateless)
POST /api/auth/token/refresh/ {refresh}
```

Login is throttled at `10/min` per scope (`TOMS_LOGIN_THROTTLE`).

## Conventions

* **Pagination** (default `page_size=25`, `?page_size=` up to 200):
  `{count, next, previous, results}`
* **Errors** — every failure returns the same envelope
  (`apps/core/errors.py`):

  ```json
  {"detail": "Human readable message", "errors": {"field": ["…"]}}
  ```

  Status codes: `400` validation, `403` unauthenticated or forbidden (DRF answers `403`
  because `SessionAuthentication` supplies no `WWW-Authenticate` header; the JWT login
  endpoints answer `401` for bad credentials), `404` not found, `429` throttle.
* **Filtering/search/ordering** via query params, e.g.
  `?search=coast&status=planned&category=air&ordering=-start_at&page=2`
* Timestamps are naive `Asia/Manila` (`USE_TZ = False`, deliberately matching legacy data).
  User-facing dates use the 24-hour military format `29 Oct 2026, 1430H (PST)`; the frontend
  formats them through `utils/format.js`, which pins them to `Asia/Manila` regardless of the
  device's zone (spec §7).
* Every mutating request is unit-scoped by the caller; the frontend menu is never the guard.

## Endpoints

### Auth & personnel — `apps/accounts`

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/auth/csrf/` | Bootstrap CSRF |
| POST | `/auth/login/` · `/auth/logout/` | Session login/logout |
| GET | `/auth/me/` | User + `permissions` (admin, can_approve_operations, manage_users, unit ids, …) + `preferences` |
| GET/PATCH | `/auth/preferences/` | Per-account appearance (`theme`: `system` \| `light` \| `dark`); applies on every device |
| POST | `/auth/password/` | Change password (legacy policy preserved) |
| GET | `/auth/password-reset/?token=` | Validate a single-use reset link (`{valid, expires_at}`) |
| POST | `/auth/password-reset/` | Complete a reset: `{token, new_password}`; anonymous, CSRF-protected, single use, 60 min expiry |
| POST | `/auth/token/` · `/auth/token/refresh/` | SimpleJWT |
| GET/POST | `/personnel/` | List (search/pagination) / create |
| GET/PATCH/DELETE | `/personnel/{id}/` | Read / update / **deactivate** (never a hard delete) |
| GET | `/personnel/assignable/` | Active personnel. `?mrm_unit=<id>` narrows to the unit's command tree (MRM team editor). `?unit=<id>&purpose=creator\|evaluator` lists people **assigned to that one unit** who hold the required authority there — the mission form's MRM creator/evaluator selectors. A unit outside the caller's scope returns `403` |
| GET | `/units/tree/` | Nested unit hierarchy |
| GET | `/units/?scope=mine` | The caller's own unit and its authorised subordinates — the UPR selector contract |
| POST | `/units/` | **Administrator only.** Create a unit: `name`, `acronym`, `category` (`Command`/`Wing`/`Group`/`Center`/`Squadron`), `location`, optional `parent`, `commanding_officer`. The name must be unique (case-insensitive); the acronym is upper-cased; `date_created` is set and `is_active` starts true |
| PATCH | `/units/{id}/` | **Administrator only.** Edit those descriptive fields. The legacy `units` table keeps its columns — nothing is added or renamed. A parent cannot be the unit itself or one of its descendants (hierarchy loops are refused) |
| POST | `/units/{id}/deactivate/` | **Administrator only.** `{reason}` is required. Refused while the unit still has active subordinate units or `planned`/`approved`/`active` missions. Personnel keep their unit assignment and every record is preserved; the status change, actor, time and reason are audited |
| POST | `/units/{id}/activate/` | **Administrator only.** Return a deactivated unit to use; audited |
| DELETE | `/units/{id}/` | Refused (`405`): missions, MRM forms, personnel and audit history reference the unit — deactivate it instead |
| GET | `/units/markers/` | Every active unit with a map pin (reference data, same visibility as the unit register), shaped for the maps: `{unit_id, unit_name, unit_acronym, latitude, longitude, mgrs, place_name, has_logo, logo_url}` |
| GET/PATCH | `/units/{id}/marker/` | Read the pin, or set it with `{latitude, longitude}` and/or `{mgrs}` plus `place_name`. The grid reference and the point are kept consistent (a grid alone is converted). Administrator only for writes |
| GET/POST/DELETE | `/units/{id}/marker-logo/` | Serve, upload (`logo` multipart: PNG/JPEG/WebP, 2 MiB) or remove the unit's seal. The seal is drawn as the unit's map pin; without one the pin shows the acronym. Administrator only for writes |

The unit list (`/units/`) also returns the pin as `map_latitude`, `map_longitude`, `map_mgrs`, `map_place_name` and `map_logo_url`, so Unit Management can show which units are still unpinned without a second request. Reads stay open to every authenticated account because the mission, office and AOR selectors need the register; only writes are Administrator-only (`system_config`, a capability no delegation can grant).

### Role catalogue — `/roles/` (any authenticated user)

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/roles/` | Every assignable role: `{value, label, kind (account\|appointment\|office\|both), account_value, appointment, scope, scope_label, read_scope, read_scope_label, exclusive, description, capabilities[], capability_labels[]}` plus `capability_labels` and `exclusive_roles` |

The interface builds its role pickers from this payload; a role cannot exist on the server and be
missing from the interface. `account_value` is the legacy `users.authority` value to PATCH for an
account role; `appointment: true` means the role is assignable through `/appointments/`.

### User administration — `/admin/users/` (Administrator only · `manage_users`)

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/admin/users/` | Paginated accounts; `?search=&authority=&is_active=&unit=&category=`. Each row carries `roles`, `role_labels` (account role plus active appointments and acting delegations) and `appointments[]` (`id, role, role_label, unit_id, unit_acronym`); no password field is ever serialised |
| GET | `/admin/users/{id}/` | One account |
| PATCH | `/admin/users/{id}/` | Name, rank, designation, contact, role, category, unit. Username/serial/password are immutable here; changing your own role is refused |
| POST | `/admin/users/{id}/deactivate/` | `{reason}` required; blocks sign-in, preserves every record and audit entry; cannot target yourself |
| POST | `/admin/users/{id}/reactivate/` | `{reason?}`; restores sign-in with the existing credentials |
| POST | `/admin/users/{id}/reset-password/` | Issues a single-use reset link. Returns `{reset_url, expires_at}` **once**; no password value is returned or stored in plaintext |

### Unit appointments — `/appointments/` (`manage_appointments`, Administrator only)

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/appointments/` | `?unit=&role=&is_active=&user=`; unit-scoped |
| POST | `/appointments/` | `{unit_id, user_id, role, notes}`. `role` must be one of `commander, deputy_commander, director_operations, safety_officer, logistics_officer, operations_officer, admin_officer, logistics_ncoic`. Account roles are refused ("change it on the account") and `operations_center` is refused ("is an office, not a post"). Inactive accounts, duplicate posts and a second holder of an exclusive post are refused. Audited with actor, unit, post and note |
| DELETE | `/appointments/{id}/` | Ends the post (never deletes it): sets `ended_at`/`ended_by`, keeps it for history, and frees an exclusive post. Audited as `end_appointment` with the previous assignment |
| GET/POST | `/acting-appointments/` | Time-boxed delegation of Commander/Deputy authority; `POST /acting-appointments/{id}/revoke/` ends it early |

### Offices, sections and office roles

| Method | Path | Notes |
| --- | --- | --- |
| GET/POST | `/offices/` | `{unit, name, code, kind: office\|section, function, parent, description}`. `function` is `general` or `operations_center`; a unit may have one active Operations Center (partial unique index, 400 on a second) |
| GET/PATCH/DELETE | `/offices/{id}/` | Delete removes the row and its personnel assignments (audited); deactivate with `is_active=false` to keep it for history |
| POST | `/offices/` | A catalogue entry the unit already has is refused with a `catalogue_code` error (`400`), so duplicates cannot be created from the interface or by hand |
| GET/POST | `/section-assignments/` | `{user_id, section_id, assignment_type: primary\|secondary, notes}`. The section must belong to the member's own unit. **Membership of an `operations_center` office grants the Operations Center role for that unit**; ending the assignment (or deactivating the office) removes it |
| DELETE | `/section-assignments/{id}/` | Ends the assignment (history retained) |

### Dashboard, GIS, reports — `apps/ops`

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/dashboard/` | All counters, unit-scoped |
| POST | `/gis/mgrs/` | Send either `{mgrs}` or `{latitude, longitude}` → `{latitude, longitude, mgrs}` |
| GET | `/gis/search/?q=` | Offline location search over AOR names/centroids and mission locations → `{count, results:[{label, latitude, longitude, source}]}` (no external geocoding service is contacted) |
| GET | `/gis/places/?q=&limit=` | Place search for the map picker: buildings, facilities and businesses ("SM City Butuan") → `{provider, count, results:[{label, detail, latitude, longitude, source, provider_id}], note}`. Uses **Google Places** when `TOMS_GOOGLE_MAPS_API_KEY` is set (the key is sent from the server in a header and never reaches the browser) and **OpenStreetMap Nominatim** otherwise; `TOMS_GEOCODER=off` disables external lookup. Failures and rate limits return an empty list with a `note`, never a 5xx; responses are cached for `TOMS_GEOCODER_CACHE_SECONDS` and queries shorter than 3 characters are not sent out |
| GET | `/admin-areas/at/?lat=&lon=` | Official area(s) containing a point, smallest first (city/municipality → province → region) from the bundled PSGC boundaries. Used by the mission form's map picker; no external geocoder |
| GET/POST/PATCH | `/aor/` | AOR records. Reads are unit-scoped. `POST /aor/{id}/archive/` (`{reason}`) takes an area out of active coverage but keeps the row; `POST /aor/{id}/restore/` returns it. Archive, restore and edits of `name`/`color`/`description` need `maintain_aor` (Administrator, or the Operations Center of that AOR's own unit); moving a record to another unit, changing its code or boundary, assigning/transferring coverage and `DELETE` need `manage_aor` (Administrator only). `DELETE /aor/{id}/` removes the record permanently and writes the full row, geometry included, to the audit trail first. Selecting provinces/cities in `/aor/assign/` colours each row with the **unit's own colour** (`apps.ops.aor.unit_color`, exposed as `aor_color` on `/units/`), and a transfer recolours the area to the receiving unit, so neighbouring units stay distinguishable. An overlap smaller than **0.5% of the smaller area** is treated as shared-border imprecision — the bundled PSGC boundaries are simplified and neighbouring provinces overlap by a few square kilometres — and does not block an assignment; real overlaps are still refused |
| GET | `/reports/{report}/` | `report` ∈ `missions, risk, inventory, personnel, trips, units, aor`; `?export=csv` |
| GET | `/health/` | Public liveness probe |

### Missions (Air + Ground) — `/missions/`

| Method | Path | Notes |
| --- | --- | --- |
| GET/POST | `/missions/` | `?category=air\|ground`. **Create/edit/delete require `manage_missions`** — members of the unit's Operations Center office and the Administrator. Reads are scoped by `services.visible_missions`: command/officer posts see their unit tree, everyone else sees only missions they are attached to, and an unrelated mission returns `404` |
| GET/PATCH/DELETE | `/missions/{id}/` | Detail includes air or ground detail, personnel, trips, linked risk. Editing is `manage_missions`; the final decision is `/approve/` |
| POST | `/missions/{id}/approve/` | Authority approval. Refused until the mission's MRM is completed and authority-approved |
| POST | `/missions/{id}/assign-personnel/` | `{personnel: [{user_id, role}]}` |
| — | `operational_areas` on a ground mission write | `[{name, latitude, longitude, mgrs}]` — *additional* areas only. The primary area remains `ground_detail.operational_area` plus the mission coordinates, so nothing about existing records changes. Sending it on an air mission is refused. Each save replaces the list in order (`sequence` 1..n) |
| GET/POST | `/missions/{id}/risk/` | Link an MRM assessment / assign its evaluator (the evaluator is also written to `mrm_forms.reviewed_by`). Linking an MRM already tied to another mission is refused |
| POST | `/missions/{id}/start-mrm/` | Creates a draft legacy MRM with `YYYYMMDDnn` numbering, links it both ways and notifies the MRM lead. **Only the mission's assigned MRM lead** may call it (`403` for other team members and planners); refuses when a team has not been assigned or a form is already linked. The same rule appears in `/approval/`: an approve decision needs the completed MRM |

A created mission accepts `mrm_team: [{user, role}]` on update (`role` = `lead \| assessor \| reviewer`); supplying it on create is refused, because the creator must be one of the mission's personnel and those are recorded afterwards. The planning form records personnel first (`assign-personnel`) and then assigns `lead` = **MRM creator**, `reviewer` = **evaluator**; the server enforces that the creator is one of those personnel and that the evaluator belongs to the mission's unit and holds `evaluate` there, and that there is at most one creator and one evaluator. Each member must be active and hold MRM create/evaluate capability for the unit.

`risk_form` remains accepted for API compatibility (the planning form no longer offers it — the assessment is linked by `start-mrm`, or by `POST /missions/{id}/risk/`), and still creates the same `RiskAssessment` link. A form already linked to a different mission is refused (`400`, `risk_form`) — one assessment describes one mission. Reads return `mrm` (`status`, `label`, `requires_mrm`, `overdue`, `due_soon`, `mrm_form`, `mrm_number`) and `mrm_team`. Status is derived from the legacy form, never duplicated: `not_created` → `pending` → `in_progress` → `submitted` → `approved`, or `returned`. Approved assessments freeze team and MRM-link changes.

**Times.** The ground form collects `ground_detail.estimated_start/estimated_end` and the air form `air_detail.scheduled_departure/scheduled_arrival`; the mission header's `start_at`/`end_at` are derived from them (when not explicitly supplied) so ordering, reporting, dashboard and map date filters stay consistent. There is no separate "start"/"end" input on the mission form.

### Risk register (legacy MRMS) — `/risk-register/`

| Method | Path | Notes |
| --- | --- | --- |
| GET/POST | `/risk-register/` | Legacy `mrm_forms` rows; `?status=`, `?search=` |
| GET/PATCH | `/risk-register/{id}/` | Detail: risk scale, signatures, hazard overrides, evaluator, linked `mission`/`mission_number` |
| GET | `/risk-register/evaluators/` | Eligible evaluators for this user (**the fixed selector**) |
| POST | `/risk-register/{id}/assign-evaluator/` | `{evaluator_id}` — validated by `resolve_evaluator`; `400` after review |
| POST | `/risk-register/{id}/submit/` | draft → submitted |
| POST | `/risk-register/{id}/review/` | `{decision: approved\|rejected\|needs_revision, notes}` |
| POST | `/risk-register/{id}/approve/` | Authority approval; requires signatures (`is_approvable`) |
| POST | `/risk-register/{id}/recalculate/` | Re-run the preserved legacy risk algorithm |
| GET | `/risk-register/{id}/pdf/` | The MRM as a PDF. Serves the stored approved copy when `approved_pdf_filename` exists on disk, otherwise renders the document with the preserved legacy layout (unit header, hazard table, authority matrix, signatures + QR). `?download=1` returns an attachment. Access: unit scope **and** (admin / creator / evaluator / authority / authority-approved). |

### Inventory — `/inventory/`

| Method | Path | Notes |
| --- | --- | --- |
| GET/POST | `/inventory/categories/` | Tree of materiel/firearms/ammunition/… |
| GET/POST | `/inventory/items/` | `?category=&serviceability=&unit=&is_sensitive=&search=` (sensitivity enforced server-side) |
| GET/PATCH | `/inventory/items/{id}/` | Item detail |
| POST | `/inventory/items/{id}/move/` | `{kind, quantity, to_unit?, reference?, notes?}` — atomic, refuses negative stock |
| GET | `/inventory/items/{id}/transactions/` | Full accountability history |
| GET/POST | `/inventory/transactions/` | Global transaction log |
| GET | `/inventory/stats/` | Register figures: totals, per-class and per-condition breakdowns, low/out of stock, 30-day movements. Scoped like the register (unit tree + controlled items). |
| GET | `/inventory/template/` | Import template `.xlsx` with dropdowns for asset class, category, condition, unit of measure and unit. **Logistics Officer or Administrator.** |
| GET | `/inventory/export/` | Current selection as a re-importable workbook; same filters as the register (`kind`, `category`, `serviceability`, `unit`, `search`). **Logistics Officer or Administrator.** |
| POST | `/inventory/import/` | Multipart `file` — all rows or none. Creates items (never overwrites) and opens stock on hand as a ledger receipt. **Logistics Officer or Administrator.** |

### Vehicles & trips — `/vehicles/`, `/trips/`

| Method | Path | Notes |
| --- | --- | --- |
| GET/POST | `/vehicles/` | Fleet with status `available \| on_trip \| maintenance \| unserviceable` |
| GET/POST | `/trips/` | `?mission=`, `?status=` |
| POST | `/trips/{id}/approve/` | Approve a planned trip |
| POST | `/trips/{id}/itinerary/` | Append legs (departure/stops/destination, times, MGRS) |

### AOR / GIS — `/aor/`, `/admin-areas/`, `/map/`

AOR **writes are Administrator-only** (`manage_aor`); every other role reads within its unit
scope. Overlap detection uses the real polygons, not names — see
`docs/AOR_GIS_ENHANCEMENT.md` §3 for the rules.

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/aor/` | AORs in the caller's unit scope (`?unit=&is_active=&search=`) |
| POST | `/aor/` | Administrator: GeoJSON polygon + optional unit; rejected if it overlaps another unit's AOR |
| GET/PATCH/DELETE | `/aor/{id}/` | Administrator writes (`DELETE` deactivates the row; history is retained) |
| POST | `/aor/{id}/remove/` | Administrator: soft removal with an audit entry |
| POST | `/aor/assign/` | Administrator: `{unit, psgcs[], remove_psgcs[], confirm_transfer, color}` → assigns/transfers official boundaries, or `400 {code:"overlap", conflicts:[…]}`. Atomic: a conflict writes nothing |
| GET | `/aor/coverage/?unit=&geometry=1` | Coverage grouped by unit. Each entry keeps `areas`/`aor_count`/`coverage_km2` for what the unit holds itself, and adds the **automatic roll-up**: `inherited_areas`, `inherited_count`, `inherited_from` (subordinate acronyms), `total_aor_count`, `total_coverage_km2`, plus `effective_geometry` — the union of the unit's own and subordinate polygons — when `geometry=1`. A mother unit therefore has its subordinates' AOR without any duplicate rows; AOR reads also carry `unit_parent_acronym`/`unit_parent_name` |
| GET | `/aor/overlaps/` | Administrator: existing overlapping assignments, read-only |
| GET | `/aor/at-point/?latitude=&longitude=` | Which AOR(s) contain the point |
| POST | `/aor/{id}/contains/` | Point-in-polygon test |
| GET | `/admin-areas/?q=&level=&region=&parent=&limit=` | Search official PSGC provinces/cities/municipalities/districts (partial, case-insensitive; no geometry) |
| GET | `/admin-areas/{psgc}/` | One area **with its boundary geometry** (preview) |
| GET | `/admin-areas/boundaries/?level=province\|city\|local\|district\|all&psgc=1,2` | GeoJSON FeatureCollection for the map layer |
| GET | `/map/config/` | Basemap providers (availability, attribution, licence, max zoom), layer catalogue, data-flow notes |

### Self-service profile — every authenticated user

| Method | Path | Notes |
| --- | --- | --- |
| GET/PATCH | `/auth/profile/` | Own profile only. Editable: `contact_number`, `bio`. Any other field → 400. |
| GET/POST/DELETE | `/auth/profile/avatar/` | PNG/JPEG/WebP ≤ 2 MiB; upload, replace, remove, stream |
| GET/POST/DELETE | `/auth/profile/signature/` | Writes `users.signature_filename` used by MRM PDFs |
| GET | `/personnel/{id}/avatar/` | Another person's picture, unit-scoped (404 outside) |
| GET | `/presence/` | Who has TOWEMS open now: `{window_minutes, count, users[]}` with serial number, rank, name, designation, avatar URL and `is_self`. Unit-scoped, most recent first, max 40 rows. Shown in the top bar beside Settings. "Online" = seen in the last 5 minutes (`UserProfile.last_seen`, stamped by `PresenceMiddleware` at most once a minute per account). |

### Command structure — `apps/command`

| Method | Path | Notes |
| --- | --- | --- |
| GET/POST | `/appointments/` | Unit posts; one active Commander/Deputy/DFO per unit |
| DELETE | `/appointments/{id}/` | Ends the appointment (history retained) |
| GET/POST | `/acting-appointments/` | Time-boxed delegation with reason and capabilities |
| POST | `/acting-appointments/{id}/revoke/` | Early revocation, audited |
| GET/POST/PATCH/DELETE | `/offices/` | Offices/sections under a unit, always limited to the caller's authorised units (Administrator: every active unit; Admin Officer: own unit and subordinates). Create accepts `catalogue_code` (A1–A10, SAFETY, OTHER) or `custom_name` for "Specify – Other"; a code the unit already holds is rejected on `catalogue_code`. DELETE removes the row **and its personnel assignments** (the audit entry records both). Requires `manage_sections` (Administrator, Admin Officer) for writes. |
| GET | `/offices/catalogue/` | The authoritative 12-entry standard office list |
| POST | `/offices/seed-defaults/` | Idempotently create the standard offices for `{unit}`; `retire_others` also deactivates pre-catalogue rows (never deletes) |
| POST | `/offices/reset-catalogue/` | Administrator only: clear every office row and its assignments, then seed the standard list for all units. Missions, MRM forms and audit history keep their stored office labels |
| GET/POST | `/section-assignments/` | Primary/secondary assignments; `?unit=&assignment_type=` |
| DELETE | `/section-assignments/{id}/` | Ends an assignment, does not delete it |
| GET/POST | `/instructions/` | Issue notes/action items; `?scope=outstanding\|mine\|issued` |
| POST | `/instructions/{id}/acknowledge/` · `/complete/` · `/verify/` | Recipient-only; verification by sender/admin |
| GET/POST | `/approval-rules/` | Configurable Decision Authority (admin only) |
| GET/POST | `/hazard-locations/` | Georeference for legacy hazards |
| GET/POST | `/vehicle-positions/` | Last-known positions with `reported_at`/`source` |
| GET | `/mission-routing/` | Append-only routing history |

### Operational map

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/gis/operational-picture/` | Unit-scoped layers: `?layers=aor,missions,air,ground,vehicles,trips,hazards`; optional `unit`, `category`, `status`, `from`, `to`. A `unit` outside scope → 403. Positions carry `reported_at`, `source`, `is_stale`, `is_live:false`. |
| GET | `/gis/search/?q=` | Searches AOR names and mission locations already in TOWEMS |

### Notifications & audit

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/notifications/` | Current user's notifications; `?scope=action\|unread\|history` |
| GET | `/notifications/summary/` | `{unread, pending_actions, open, in_progress, completed, total}` |
| POST | `/notifications/{id}/mark-read/` · `/notifications/mark-all-read/` | Read state only — never completes an action |
| POST | `/notifications/{id}/acknowledge/` | Records acknowledgement, moves to `in_progress` |
| POST | `/notifications/{id}/complete/` | Only for non-workflow actions; workflow-backed types return 400 |
| GET | `/audit/` | `?action=&entity_type=&user=&from=&to=` — actor, time, record, before/after |

### Inventory requests (approval workflow)

| Method | Path | Notes |
| --- | --- | --- |
| GET/POST | `/inventory/requests/` | Proposal only — stock is untouched while pending |
| POST | `/inventory/requests/{id}/decide/` | Logistics Officer approves/rejects/returns; approval commits the ledger entry |

## Compatibility with the legacy Flask routes

`docs/DATABASE_COMPATIBILITY.md` §"route parity" maps each legacy Flask endpoint
(`/create_mrm`, `/edit_mrm`, `/review_mrm`, `/approve_mrm`, unit pages, …) to its API
equivalent, so behaviour can be compared endpoint-for-endpoint during the dual-run window.
