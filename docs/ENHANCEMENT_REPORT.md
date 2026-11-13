# TOWEMS Enhancement — Implementation Report

**Specification:** `Promp.txt` — *TOWEMS Development Update: Dashboard, User Profiles,
Notifications, Unit Management, GIS, and Role-Based Authority*
**Date:** 2026-10-28
**Baseline:** existing TOWEMS (Django 5 / DRF + React 18 / Vite). No rebuild, no replacement app.
**Gap analysis:** `ENHANCEMENT_GAP_ANALYSIS.md` (audit) · **Role matrix:** `ROLE_PERMISSION_MATRIX.md`

---

## 1. What was implemented

### 1.1 Dashboard recent activity shows usernames
`DashboardView` returns `actor` = `users.username`, plus `actor_username` and `actor_name`
(full name, for accessibility only). The React feed renders `@username` with the full name as a
tooltip. Applies to every audited action (mission create/submit/approve, MRM submit/review,
inventory, profile, instructions, appointments, denied access). No new identity column.

### 1.2 Self-service profile for every user
* `GET/PATCH /api/auth/profile/` — the endpoint has **no user id**, so a manipulated request
  cannot address another account. Only `contact_number` and `bio` are editable; any attempt to
  change `authority`, `unit_id`, `username`, `serial_number` or `is_active` is rejected 400.
* `POST/GET/DELETE /api/auth/profile/avatar/` — PNG/JPEG/WebP, magic-byte sniffing (an SVG or
  text file is refused), 2 MiB cap, replace and remove. Stored in the additive
  `accounts_user_profile` table + `uploads/avatars/`.
* `POST/GET/DELETE /api/auth/profile/signature/` — writes the **existing**
  `users.signature_filename` column and the existing `signatures/` directory read by the MRM
  PDF generator.
* `GET /api/personnel/{id}/avatar/` — another person's picture, only inside the caller's unit scope.
* Password change reuses `POST /api/auth/password/` (policy enforced, session hash updated).
* The Add Personnel form and `PersonnelWriteSerializer` were not touched.

### 1.3 Notification bell and persistent action notifications
* New fields on `ops_notification`: `priority`, `requires_action`, `action_status`
  (`none/open/in_progress/completed`), `action_label`, `action_url`, `due_at`,
  `acknowledged_at/by`, `completed_at/by`, `completion_note`.
* New endpoints: `GET /notifications/summary/`, `?scope=action|unread|history`,
  `POST /{id}/acknowledge/`, `POST /{id}/complete/`, `POST /mark-all-read/`.
* React: bell in the top bar with an unread dot and a **pending-action count**, a dropdown of
  the six most urgent actions, and a full inbox page with pending/in-progress/addressed filters.
* Action notifications are produced by the workflows (mission routing, MRM, inventory requests,
  instructions). The badge counts pending actions, not unread rows.

### 1.4 Office/Section management
* New tables `command_office_section` and `command_section_assignment`.
* Admin Officer (or Administrator) can create offices/sections under a unit, deactivate them
  (never delete), and assign one **primary** and one optional **secondary** section per person.
* Server rules: same section cannot be assigned twice (even as primary + secondary), the person
  and section must share a unit, and the caller must be inside that unit's scope.
* History is preserved: a replaced assignment is ended (`is_active=false`, `ended_at`), not deleted.
* React page `/offices` with unit/type filters, section cards and the assignment table; the
  section and appointment appear on the profile and on personnel data returned by the API.
* Personnel search in the assignment picker hits the server, so people beyond the first 200
  records are reachable.

### 1.5 Dashboard GIS map
* `GET /api/gis/operational-picture/` returns one unit-scoped payload: scope (unit + authorised
  subordinates), AOR polygons, mission points split into air/ground, last-known vehicle
  positions, trip routes, georeferenced hazards and honest counts of what could **not** be drawn.
* React `DashboardMap` (react-leaflet): layer toggles, legend, unit/category/status/date filters,
  popups with record details, loading/error/empty states, and a visible statement of scope.
* **No invented coordinates.** Missions without coordinates are excluded and counted
  (`missions_missing_coordinates`). Hazards need an additive `command_hazard_location` row
  (the legacy hazard registry has no geometry) and the UI reports how many are unplotted.
* Vehicle positions carry `reported_at`, `source`, `age_seconds`, `is_stale` and **`is_live: false`**;
  the popup says "last known position — not live tracking". Air missions show their stored
  mission location; no live aircraft tracking is implied.
* A client-supplied `?unit=` may only **narrow** the scope; naming an unrelated unit returns 403.

### 1.6 Roles, permissions and organisational constraints
The new authority model is implemented as **unit appointments** on top of the untouched legacy
enum, so no PostgreSQL enum was altered and no existing account lost a permission.
`apps/core/roles.py` is the single policy layer; `ROLE_PERMISSION_MATRIX.md` lists every role's
capabilities. Commander, Deputy Commander and Director for Operations are separate posts, capped
at one active holder per unit by a partial unique index plus a service-level check. Acting
command is time-boxed, auditable, non-overlapping and can never grant `system_config` or
appointment management. Decision Authority is configurable per entity/unit/category.

### 1.7 Workflows and governance
* **Mission:** create → submit to Operations Center → Operations Center routes → Operations
  Officer reviews (approve/reject/return) → Decision Authority final approval → execution.
  `approve` returns 400 unless an Operations Officer `approved` routing row exists, and 403 when
  the caller created the mission. Every step is an append-only `command_mission_routing` row and
  an audit entry.
* **MRM:** the existing submit → evaluator → authority workflow is preserved; notifying on final
  approval and self-approval prevention are enforced by the existing risk views plus the
  notification layer.
* **Inventory:** `ops_inventory_request` holds the proposal; stock moves only inside `decide`
  in the same transaction as the ledger entry, so a pending request cannot change balances.
* **Instructions:** `command_instruction` + `command_instruction_recipient`; issue → notify →
  acknowledge (separate timestamp) → complete (with note) → verify. Receipt of the notification
  is never treated as completion.
* **Audit:** `core_audit_log` records actor, time, action, record and before/after values;
  refused 401/403 requests are logged without bodies, tokens or query strings. Appointment,
  acting, section, instruction, workflow and profile changes are all audited.

---

## 2. Files created or modified

### Backend — created (enhancement phase)
```
apps/command/{__init__,apps,models,serializers,services,urls,views}.py
apps/command/migrations/{__init__,0001_initial}.py
apps/command/tests/{__init__,test_appointments,test_sections,test_instructions}.py
apps/accounts/profile.py
apps/accounts/migrations/0002_alter_userprofile_options.py
apps/core/roles.py
apps/ops/notifications.py
apps/ops/migrations/0002_notification_acknowledged_at_and_more.py
apps/ops/migrations/0003_inventoryrequest.py
apps/accounts/tests/test_profile.py
apps/ops/tests/test_notifications.py
apps/ops/tests/test_operational_picture.py
```

### Backend — modified
```
apps/core/permissions.py          capability-based permission classes
apps/core/audit.py                before/after snapshots, audited viewset mixin
apps/core/errors.py               denial logging
apps/accounts/models.py           additive UserProfile
apps/accounts/views.py            profile routes, personnel scoping
apps/ops/models.py                Notification action fields, InventoryRequest
apps/ops/views.py                 dashboard username, mission routing/approval,
                                  inventory requests, operational picture, notification actions
apps/ops/serializers.py           notification/request/GIS payloads
apps/ops/urls.py                  new routes
apps/command/services.py          duplicate-section fix, verify_instruction import fix
apps/command/views.py             recipient-only acknowledge/complete (403)
toms/settings/base.py             command app, upload dirs
toms/settings/test.py             temp upload dirs, server-error logging
toms/urls.py                      command routes
conftest.py                       fixtures (ops_officer, commander, evaluator_other_unit)
apps/ops/tests/test_dashboard.py  username regression test
```

### Frontend — created
```
src/components/NotificationBell.jsx
src/components/DashboardMap.jsx
src/pages/ProfilePage.jsx
src/pages/NotificationsPage.jsx
src/pages/OfficeSectionsPage.jsx
src/pages/InstructionsPage.jsx
src/pages/InstructionDetailPage.jsx
src/utils/notifications.js
```

### Frontend — modified
```
src/api/client.js                 FormData/multipart support, api.upload()
src/App.jsx                       /profile, /notifications, /offices, /instructions routes
src/components/Layout.jsx         bell, profile link, new navigation entries
src/pages/DashboardPage.jsx       map + @username feed
src/styles.css                    bell, inbox, profile, sections, map styles
```

### Documentation
```
docs/ROLE_PERMISSION_MATRIX.md    new
docs/ENHANCEMENT_REPORT.md        new (this file)
docs/API_REFERENCE.md             new endpoint sections
docs/CHANGES.md                   §10 enhancement record
```

---

## 3. Database changes

All additions are new tables or new columns on **TOWEMS-owned** tables. Nothing was dropped,
renamed, truncated or recreated.

| Object | Type | Notes |
|---|---|---|
| `accounts_user_profile` | new table | avatar filename/content type, bio; PK = `users.id`, no FK constraint |
| `command_unit_appointment` | new table | unit/user/role, active flag, appointed/ended by and at, notes; partial unique `command_one_active_holder_per_post` |
| `command_acting_appointment` | new table | delegator, reason, window, delegated capabilities, revocation; check `ends_at > starts_at` |
| `command_office_section` | new table | unit, name, code, kind, parent, active; unique `(unit, code)` |
| `command_section_assignment` | new table | user, section, primary/secondary, active, ended_*; two partial unique constraints |
| `command_instruction` / `command_instruction_recipient` | new tables | instruction record + per-recipient acknowledgement/completion |
| `command_mission_routing` | new table | append-only routing history |
| `command_approval_rule` | new table | configurable Decision Authority |
| `command_hazard_location` | new table | geometry for legacy hazards |
| `command_vehicle_position` | new table | last-known vehicle position with report time |
| `ops_inventory_request` | new table | proposal, decision, committed transaction |
| `ops_notification` | additive columns | priority, requires_action, action_status, action_label, action_url, due_at, acknowledged_*, completed_*, completion_note, index |
| `accounts_user_profile` | additive migration | Meta ordering only (`0002`) |

No new PostgreSQL enum values; all new choice fields are `varchar`. Legacy models remain
`managed = False`; every FK from a new table to a legacy table uses `db_constraint=False`, so
PostgreSQL gains no constraint on the Flask-owned schema.

---

## 4. Compatibility with existing data

* `users` was not altered. `signature_filename`, `contact_number` and `authority` are used as-is.
* MRM tables, approval records, evaluator assignments and risk calculations are untouched; the
  existing MRM endpoints and PDF generation were only extended with notifications/guards.
* Legacy row counts on staging before and after migration were identical:
  `users` 485, `units` 6, `mrm_forms` 292, `hazard_registry` 1923, `mission_hazards` 4461,
  `user_activities` 2656 — total 9,846. `verify_legacy_mapping` reports 0 mismatches.
* A user with no appointment behaves exactly as before; the legacy enum still decides their role.

---

## 5. Workflows

```
MISSION
creator ──submit──▶ Operations Center ──route──▶ Operations Officer
                                               ├─ approve ──▶ Decision Authority ──approve──▶ approved
                                               ├─ reject  ──▶ rejected (notify creator)
                                               └─ return  ──▶ draft (persistent action for creator)
Rules: submit precedes routing; Operations Center precedes Operations Officer;
       final approval requires an OO "approved" row; creator cannot approve own mission;
       approver roles come from ApprovalRule or the documented defaults.

MRM (existing workflow, preserved)
creator ──submit──▶ assigned Evaluator ──review──▶ Decision Authority ──decide──▶ approved
                    └─ return ──▶ draft (creator action stays pending)
Rules: evaluator must be eligible and inside the creator's unit scope; self-approval blocked
       where the workflow requires separation; all decisions audited.

INVENTORY
Logistic NCOIC / authorised ──request──▶ Logistics Officer ──decide──▶
   approved → ledger transaction committed in the same transaction → stock updated
   rejected / returned → no stock change, requester notified
Rules: a pending request never changes stock; sensitive items restricted.

INSTRUCTION / SAFETY FEEDBACK
sender ──issue──▶ recipient notification (action open)
recipient ──acknowledge──▶ in_progress (timestamp kept separately)
recipient ──complete + note──▶ recipient addressed; item completed/verified when all done
sender ──verify──▶ verified (audited)
Rules: reading ≠ acknowledging; acknowledging ≠ completing; completion requires acknowledgement;
       the bell cannot close a workflow-backed action.
```

---

## 6. Notification persistence logic

* `requires_action=true` creates `action_status='open'`. `mark-read` only sets `is_read`/`read_at`.
* `acknowledge` records `acknowledged_at/by` and moves to `in_progress`; it never sets
  `completed_at`.
* Completion is performed by the owning workflow through `complete_action(...)`, which records
  `completed_at`, `completed_by` and an optional note. The bell's `complete` endpoint refuses
  workflow-backed types (`instruction`, `mission`, `mrm_form`, `inventory_request`) with 400 and
  tells the user where to act.
* Completed rows are retained and appear under `?scope=history`; nothing is deleted.
* Logout, refresh and a new session do not change `action_status` (verified by
  `test_pending_action_survives_a_new_session`).
* The bell badge counts `open + in_progress`, so clearing unread cannot hide outstanding work.

---

## 7. GIS unit scoping

`OperationalPictureView` computes scope with `unit_ids_for_filter(user)` (unit + descendants;
administrators get all active units). A `?unit=` parameter must be a member of that scope, else
403. Every layer query filters on the same scope, and `GeocodeSearchView` only searches records
already visible to TOWEMS (no external geocoder). The dashboard, GIS API and underlying querysets
therefore share one rule; there is no client-side bypass.

---

## 8. Tests executed (actual results)

| Check | Command | Result |
|---|---|---|
| Backend suite | `cd toms_backend && ../.venv-toms/Scripts/python -m pytest` | **210 passed, 0 failed, 0 skipped** (exit 0) |
| New workflow tests | `pytest apps/accounts/tests/test_profile.py apps/ops/tests/test_notifications.py apps/command/tests/test_sections.py apps/command/tests/test_instructions.py apps/ops/tests/test_operational_picture.py apps/ops/tests/test_dashboard.py` | **66 passed** |
| Django system check | `manage.py check --settings=toms.settings.test` | no issues |
| Migration consistency | `makemigrations --check --dry-run` | no pending changes |
| Frontend production build | `npm run build` | success — 107 modules, 433.89 kB (127.64 kB gzip) |
| Staging migrations | `tools/staging_manage.py migrate` | applied `accounts.0002`, `ops.0002`, `command.0001`, `ops.0003` |
| Legacy mapping | `tools/staging_manage.py verify_legacy_mapping` | 9,846 legacy rows, all mappings verified |
| Browser walkthrough (staging) | Vite `127.0.0.1:5180` → Django `127.0.0.1:8010` | dashboard map renders, activity shows `@username`, profile save + avatar/signature upload render, office/section assignment saved, instruction issued, bell = 1 pending after read, stayed 1 after acknowledge, 0 only after completion, history retained |
| Production database | — | **not contacted, not migrated** |

Coverage added for the acceptance criteria: username vs full name, self-only profile editing,
role/unit escalation refusal, image validation and size limits, signature column reuse,
read/acknowledge/complete separation, non-recipient refusal, workflow completion, duplicate and
cross-unit section assignments, primary/secondary independence, scope narrowing/refusal,
coordinates honesty, stale-vehicle labelling, hazard counts, one-commander-per-unit, acting
revocation and expiry, and no-admin-via-acting.

---

## 9. Staging-only artefacts used for the browser check

Created in `toms_staging` only (safe to delete; no production effect):

* user `toms.verify` (Administrator + Admin Officer appointment), serial `VERIFY-0001`
* office `OPS-VERIFY`, primary assignment for that user
* mission `VERIFY-MSN-1` with coordinates
* instructions `Verification instruction` and `Bell verification`

The staging clone needed one repair before migrating: it had `ops.0001` recorded before
`accounts.0001`, so its history was inconsistent. The new `accounts_user_profile` table was
created with the exact SQL `sqlmigrate accounts 0001` emits and the migration was recorded,
which unblocked the remaining purely additive migrations. No table was dropped and no row was
deleted; legacy counts are unchanged.

---

## 10. Outstanding issues / blocked items

| # | Item | Impact | Next step |
|---|---|---|---|
| 1 | **Production migration not applied.** | Expected — requires explicit approval. | Follow §11 and `DEPLOYMENT_AND_ROLLBACK.md`. |
| 2 | Additive role combination (§7 of the matrix). A Commander who also holds a Creator base account keeps Creator CRUD. | Policy interpretation. | Confirm whether command should *suspend* base capabilities; that is a new subtractive rule and needs approval. |
| 3 | Operations Officer scope wording ("current unit only") is implemented as unit + subordinates, consistent with every other post. | Low. | Confirm intended reach before deployment. |
| 4 | Mission edit UI still absent (PATCH API exists) — unchanged from baseline. | Medium UX. | Add an edit route reusing `MissionFormPage`. |
| 5 | No `Age`/cache headers on map endpoints; 500-record caps apply. | Low at current volume. | Revisit if data grows. |
| 6 | No email/SMS/push delivery; bell is in-app by requirement. | Low. | Add a delivery backend when a channel is chosen. |
| 7 | Profile avatar/signature files live on local disk (`uploads/`, `signatures/`). | Medium at multi-instance deploy. | Move to object storage before horizontal scaling. |
| 8 | Instructions/recipient pickers load up to 200 rows per search; server-side search added, but no pagination inside the modal. | Low. | Add infinite scroll if units grow. |

No requirement was marked complete on inspection alone: each was exercised by an automated test
and, for the user-facing flows, by the browser walkthrough above.

---

## 11. Deployment, backup and rollback

**Do not deploy without explicit approval.** Production `mrm_system` has not been touched.

1. **Backup first** — `pg_dump` the production database (schema + data) and store it off-host;
   verify restore on a scratch database. Back up `signatures/`, `uploads/`, `approved_pdfs/`.
2. **Drift check** — run `tools/introspect_schema.py` against production and diff with
   `docs/database_schema.json` before migrating.
3. **Staging rehearsal** — already done; repeat on a fresh clone of the current production dump.
4. **Apply migrations** in order: `accounts 0001/0002`, `command 0001`, `ops 0002`, `ops 0003`.
   They are additive: new tables, new columns on TOWEMS-owned tables, indexes and constraints.
   No legacy table is altered. Use `migrate --plan` first and confirm only these operations.
5. **Deploy** the built SPA (`toms_frontend/dist/`) and the Django app together; the SPA expects
   the new endpoints. Keep the previous build for rollback.
6. **Smoke test** — `/api/health/`, login, dashboard, map, profile, notification summary,
   appointments list, office list.
7. **Rollback** — stop the app, restore the pre-deploy database dump (the additive tables can be
   left in place; the previous code ignores them), restore the previous SPA build. Do **not**
   reverse-migrate on production without a tested plan; dropping the new tables is only safe
   after confirming no TOWEMS-authored rows are needed.

Environment variables: no new secrets. Existing `DATABASE_URL`, `DJANGO_SECRET_KEY`,
`DJANGO_ALLOWED_HOSTS`, origins and `TOMS_PAGE_SIZE` are sufficient.
