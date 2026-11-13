# TOWEMS Enhancement — Gap Analysis and Implementation Plan

**Specification:** `Promp.txt` — *TOWEMS Development Update: Dashboard, User Profiles, Notifications, Unit Management, GIS, and Role-Based Authority*
**Audit date:** 2026-10-28
**Baseline:** TOWEMS (Django 5 / DRF + React 18 / Vite), 128 backend tests green.
**Database:** production PostgreSQL (`mrm_system` on DigitalOcean) is **read-only for this work**; all development and tests use `toms_staging` (copy) and in-memory SQLite respectively.

Status legend:
`W` = already implemented and working · `I` = implemented but needs improvement ·
`M` = missing · `D` = implemented but defective · `B` = blocked / needs clarification

---

## 1. Dashboard Recent Activity — display username

| # | Requirement | Status | Evidence / Notes |
|---|---|---|---|
| 1.1 | Show the acting account's **username** instead of full name | `D` | `DashboardView.get` in `apps/ops/views.py` emits `"actor": entry.actor.full_name`. `apps/ops/views.py` → `DashboardView`, consumed by `toms_frontend/src/pages/DashboardPage.jsx`. |
| 1.2 | Apply to mission create, MRM submit, approvals, inventory, comments | `I` | Mission/MRM/inventory actions already write `core_audit_log` rows, so they appear in the feed; instruction/comment actions do not exist yet (see §4). |
| 1.3 | Preserve description, timestamp, record | `W` | Feed already returns `action`, `entity_type`, `entity_id`, `created_at`. |
| 1.4 | No duplicate identity field | `W` | `AuditLog.actor` FK to `users.id` is reused; no new column. |
| 1.5 | Test with users whose username ≠ full name | `M` | No dashboard-feed test asserts the actor field's format. |

**Fix:** return `actor` = `actor.username` and add `actor_name` (full name) for accessibility; add a regression test where the two differ.

---

## 2. Edit Profile for all users

| # | Requirement | Status | Evidence / Notes |
|---|---|---|---|
| 2.1 | Profile page/modal for every authenticated role | `M` | No `/api/auth/profile/` route; `apps/accounts/urls.py` has csrf/login/logout/me/password only. No React page. |
| 2.2 | View + update own permitted fields | `M` | `PersonnelWriteSerializer` is the *administrative* record editor (`PersonnelViewSet`), restricted to `ADMIN`/`Unit Safety Officer`. Not usable for self-service. |
| 2.3 | Profile picture upload/preview/replace/remove | `M` | `users` table has **no** avatar column. Requires one additive table. |
| 2.4 | Signature image upload | `I` | `users.signature_filename` exists and is read by `apps/ops/pdf.py`; no upload endpoint. Legacy files live in `signatures/`. |
| 2.5 | File type/size validation | `M` | No upload path exists at all. |
| 2.6 | Cannot edit another user via manipulated request | `M` | Must be enforced by a self-only endpoint (`request.user`), not by an `id` parameter. |
| 2.7 | Cannot change own role/unit/permissions | `M` | `PersonnelWriteSerializer` allows `authority`, `unit_id`; must not be reused. |
| 2.8 | Password change | `W` | `ChangePasswordView` + policy validator already correct. |
| 2.9 | Add Personnel form untouched | `W` (constraint) | `PersonnelViewSet.create` + `PersonnelWriteSerializer` left exactly as-is. |

**Plan:** new additive table `accounts_user_profile` (avatar, bio, timestamps) + `GET/PATCH /api/auth/profile/` + `POST /api/auth/profile/avatar/`, `/signature/` (multipart). Signature writes `users.signature_filename` (existing column, existing directory). New `ProfilePage.jsx`.

---

## 3. Notification bell and persistent action notifications

| # | Requirement | Status | Evidence / Notes |
|---|---|---|---|
| 3.1 | Bell in main navigation | `M` | `Layout.jsx` has no bell. |
| 3.2 | Notify on MRM submit/review/approve | `I` | `RiskRegisterViewSet.submit/review/assign_evaluator` call `notify()`; `approve` does **not** notify anyone. |
| 3.3 | Notify on mission submission/routing/decision | `I` | Only `approve` notifies the creator. No Operations Center → Operations Officer → Decision Authority routing exists. |
| 3.4 | Notify on inventory approval, safety comments, instructions | `M` | No inventory request/approval workflow; no instruction model. |
| 3.5 | Only relevant recipients, no broadcast | `W` | `notify()` writes one row per named recipient. |
| 3.6 | Message content: sender, related record, priority, action, link, read state | `I` | `ops_notification` has title/message/level/category/related/created_by/read. Missing: priority, deep link, required action, workflow status. |
| 3.7 | **Action notifications persist until the action is completed** | `M` | Only `is_read` exists; there is no open/actioned distinction, no acknowledgement, no completion audit. |
| 3.8 | Reading must not clear a pending action | `M` | Would be impossible today — no action state exists. |
| 3.9 | Record who completed the action and when | `M` | — |
| 3.10 | Counts, unread indicator, filters | `I` | `is_read`/`level`/`category` filters and a dashboard `unread` count exist; no summary endpoint, no UI. |
| 3.11 | Persisted server-side (not browser state) | `W` (architecture) | `ops_notification` is a real table; the UI just has to use it. |

**Plan:** additive columns on `ops_notification` (`priority`, `action_url`, `requires_action`, `action_status`, `acknowledged_*`, `completed_*`, `due_at`) + `GET /notifications/summary/`, `POST /{id}/acknowledge/`, `POST /{id}/complete/`. New `NotificationsPage.jsx` + `NotificationBell.jsx`.

---

## 4. Unit Management — Office/Section Management

| # | Requirement | Status | Evidence / Notes |
|---|---|---|---|
| 4.1 | Create/manage offices & sections under a unit | `M` | `Unit` (legacy, `managed=False`) has no office/section child. `GroundMissionDetail.osp_section` is free text only. |
| 4.2 | Primary + optional secondary assignment | `M` | No assignment table. |
| 4.3 | One primary and one secondary per person | `M` | — |
| 4.4 | No duplicate same-section assignment | `M` | — |
| 4.5 | Reject sections from unrelated units | `M` | — |
| 4.6 | Show assignments in personnel/unit views | `M` | `PersonnelPage.jsx` has no section columns. |
| 4.7 | Filters by unit / section / assignment type | `M` | — |
| 4.8 | Preserve assignment history | `M` | Needs soft-ended rows, not deletes. |
| 4.9 | Admin Officer may manage within authorised unit | `B` → resolved | `users.authority` is a PostgreSQL enum with 4 values (`Administrator`, `Evaluator`, `Creator`, `Unit Safety Officer`). Adding roles there means `ALTER TYPE`, which the preservation rules forbid. **Resolution:** model the new roles as *appointments* in a new additive table rather than new enum values (see §7). |
| 4.10 | Admin Officer must never see credentials | `M` | Must be explicit; `UserSummarySerializer` already omits `password_hash`, so the risk is a future field leak — add a test. |

**Plan:** additive `command_office_section` + `command_section_assignment` with partial unique constraints; `/api/offices/`, `/api/section-assignments/`; `OfficeSectionsPage.jsx`; sections shown on personnel rows.

---

## 5. Dashboard GIS map (current unit + subordinate units)

| # | Requirement | Status | Evidence / Notes |
|---|---|---|---|
| 5.1 | Interactive map on the dashboard | `M` | Leaflet exists and is used only on `AORPage.jsx`. |
| 5.2 | Scope = user's unit + authorised subordinates | `I` | `accessible_unit_ids()` already computes unit + descendants; no map endpoint uses it. |
| 5.3 | AOR boundary layer | `I` | `AreaOfResponsibility` + `/api/aor/` exist; no combined endpoint. |
| 5.4 | Hazard layer | `M` | `HazardRegistry` (legacy) has **no coordinates**. Needs an additive georeference table; layer must render "no georeferenced hazards" until populated. |
| 5.5 | Vehicle positions | `M` | `Vehicle` has no position and `VehicleTrip` has no live position. Needs additive `command_vehicle_position`; display last-known + timestamp, never labelled live. |
| 5.6 | Air Operations layer | `I` | `AirMissionDetail` has departure/destination **names only**, no coordinates; mission header has lat/lon. Layer can show mission location + route text. |
| 5.7 | Ground Operations layer | `W` (data) | `Mission.latitude/longitude/mgrs` populated by the form. |
| 5.8 | Routes / itinerary points | `W` (data) | `TripItinerary` has lat/lon per stop. |
| 5.9 | Layer toggles, legend, filters, states | `M` | — |
| 5.10 | No invented coordinates; stale ≠ live | `B` → resolved | Enforced by contract: every position carries `reported_at` / `position_source`; absence of data renders an empty layer. |
| 5.11 | Unit scoping not bypassable via params | `I` | Serverside `unit_ids_for_filter` is the pattern; the new endpoint must ignore client-supplied `unit` unless the caller is an administrator. |

**Plan:** `GET /api/gis/operational-picture/?layers=…` returning scope, AOR polygons, mission points, trip routes, vehicle last-known positions, georeferenced hazards. New `DashboardMap.jsx`.

---

## 6. Revised role-based authority

| Role (spec) | Current mapping | Status |
|---|---|---|
| Administrator | `users.authority = 'Administrator'` | `W` |
| Commander | none | `M` |
| Deputy Commander | none | `M` |
| Director for Operations | `users.authority_level` enum has the label, unused for auth | `D` |
| Safety Officer | `users.authority = 'Unit Safety Officer'` | `W` |
| Logistics Officer | none (inventory writes gated on safety/admin) | `M` |
| Operations Officer | none | `M` |
| Admin Officer | none | `M` |
| Evaluator | `users.authority = 'Evaluator'` | `W` |
| Creator | `users.authority = 'Creator'` | `W` |
| Logistic NCOIC | none | `M` |

Root cause: authorisation is a **single enum column** read directly in `apps/core/permissions.py` (`authority(user)`), so a person can hold exactly one role and no role can be unit-scoped.

**Plan (no schema change to `users`):**
1. `command_unit_appointment` table: `(unit, user, role, is_active, appointed_by, appointed_at, ended_at)`.
2. `apps/core/roles.py`: one `effective_roles(user)` that merges the legacy `authority` with active appointments and acting appointments; one `has_capability(user, capability, unit_id=None)` used by every view.
3. Permission matrix as data (`ROLE_CAPABILITIES`), documented in `docs/ROLE_PERMISSION_MATRIX.md`.
4. Backwards compatibility: a user with no appointment keeps exactly today's behaviour from `authority`, so all 485 accounts and the existing suite are unaffected.

---

## 7. Permission matrix (backend-enforced)

| # | Requirement | Status | Notes |
|---|---|---|---|
| 7.1 | Matrix over view/create/edit/delete/submit/review/evaluate/approve/reject/return/comment/manage-assignments/analytics/subordinates/config | `M` | Today: 10 boolean flags built ad hoc in `accounts/views.py::_permissions_payload`. |
| 7.2 | Combine multiple roles without one bypassing another's restriction | `M` | No combining is possible with one enum. |
| 7.3 | Frontend buttons are not the boundary | `W` | Existing pattern is correct; new code follows it. |
| 7.4 | Auditable permission changes | `M` | Appointment create/end must be audited. |

**Plan:** capabilities are **additive but never escalate a role's own restriction**: e.g. `operations_officer` grants mission approval at the unit but does not grant inventory write; `commander` grants comment/route but not record edit. Delegations (acting) add only the listed capabilities and never `system_config`.

---

## 8. Workflows (mission, MRM, inventory, instructions)

| # | Requirement | Status | Notes |
|---|---|---|---|
| 8.1 | Mission: create → Operations Center → Operations Officer → Decision Authority → MRM → execution | `M` | `approve` is a single step open to `ADMIN`/`Unit Safety Officer`; no routing record at all. |
| 8.2 | Category-specific routing not hardcoded to one chain | `M` | — |
| 8.3 | MRM: submit → evaluator → decision authority → notifications → audit | `W` (mostly) | `submit`/`review`/`approve` exist with audit + partial notifications. Missing: "cannot approve own submission", decision-authority resolution, notification on final approval. |
| 8.4 | Inventory: NCOIC request → Logistics Officer approval → commit stock | `M` | `InventoryItemViewSet.move` writes stock immediately; no request/approval stage, so "do not change stock before approval" cannot hold. |
| 8.5 | Instructions: issue → notify → acknowledge → act → verify → addressed | `M` | No model. |
| 8.6 | Approval cannot be bypassed | `M` | Must be enforced server-side once routing exists. |

**Plan:** additive `command_mission_routing` (append-only), `ops_inventory_request`, `command_instruction` + `command_instruction_recipient`.

---

## 9. Governance & security (§12 of the spec)

| # | Requirement | Status | Notes |
|---|---|---|---|
| 9.1 | Tamper-resistant audit trail | `I` | `core_audit_log` is populated and API read-only, but the model allows `save()`/`delete()` and there is no DB guard. |
| 9.2 | Failed-authorisation attempts logged without secrets | `M` | `toms_exception_handler` returns 403 without logging. |
| 9.3 | Acting authority with start/end, delegator, delegated permissions, expiry, revocation | `M` | — |
| 9.4 | Prevent overlapping command authority | `M` | — |
| 9.5 | Configurable Decision Authority (not a job title hardcoded) | `M` | — |
| 9.6 | One active Commander / Deputy / DFO per unit | `M` | — |
| 9.7 | Single unified unit-scoped authorisation service | `I` | `accessible_unit_ids` exists but permission classes bypass it for writes. |

**Plan:** append-only `AuditLog.save/delete` guards; 401/403 logging in the exception handler; `command_acting_appointment`; partial unique constraint per `(unit, role)`; `apps/core/roles.py` as the single policy layer.

---

## Data-integrity constraints observed

* `users.authority` is a PostgreSQL **enum** — new roles must not be added as values without approval.
* `users.unit` is `NOT NULL` varchar and mirrors `unit_id`; profile editing must not touch it.
* Legacy models are `managed = False`; nothing in this enhancement alters them.
* Every new table is additive, new-named, and FK-references legacy rows with `db_constraint=False`.

---

## Implementation order (safest first)

| Phase | Scope | Why this order |
|---|---|---|
| **P1** | Audit hardening + unified role/capability layer (`apps/core/roles.py`) | Everything else depends on the policy layer; security first. |
| **P2** | Appointments, acting authority, office/section, assignments (models + API + tests) | Structural; needed before permission-gated UI. |
| **P3** | Notification persistence (fields, ack/complete, summary) + instruction/action items | Depends on P2 recipients. |
| **P4** | Mission / MRM / inventory workflow enforcement + notifications | Depends on P1 capabilities and P3 notification delivery. |
| **P5** | Dashboard username + profile editing | Independent, user-facing. |
| **P6** | GIS operational picture + dashboard map | Reads data produced by P2–P4. |
| **P7** | React UI: bell, profile, sections, map, instructions | Consumes P2–P6 APIs. |
| **P8** | Docs: matrix, workflows, compatibility, CHANGES | Records what shipped. |

**Approval gate:** no production migration, no deployment. New migrations are additive and tested against `toms_staging` + in-memory SQLite only.
