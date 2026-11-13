# Role management enhancement — report

**Scope:** restore the full TOWEMS role set, make every role assignable by an Administrator, and
keep role, unit and assignment separate. Enhancement only: no rebuild, no production change.

---

## 1. What was actually wrong

TOWEMS already modelled twelve roles, but in three different places, and only one of them had an
interface:

| Layer | Storage | Roles | Interface before |
|---|---|---|---|
| Account role | `users.authority` (legacy PostgreSQL enum) | Administrator, Evaluator, Creator, Unit Safety Officer | User Management + Add Personnel |
| Unit appointment | `command_unit_appointment` | Commander, Deputy Commander, Director for Operations, Safety Officer, Logistics Officer, Operations Officer, Admin Officer, Logistic NCOIC | **none** |
| Office | `command_office_section` + `command_section_assignment` | (no role mapping existed) | Offices & Sections, but it granted nothing |

The Administrator screen read the enum, so it could only ever offer those four values. The command
posts were real in the backend — `POST /api/appointments/` existed and was audited — but nothing in
the UI assigned them, which is why the system appeared to have four roles.

**Operations Center was genuinely missing as an authority.** `operations_center` existed only as a
*routing stage* (`command_mission_routing.stage`). No role, no capability, and the stage check fell
back to `route`, which the Commander and Director for Operations also hold. The office concept
already existed for personnel organisation, but an office conferred no authority at all.

Findings that were **not** defects: one-active-Commander/Deputy/Director enforcement, acting
authority with expiry and revocation, unit scoping, and audit logging of appointments all already
worked and are covered by `apps/command/tests/test_appointments.py`.

---

## 2. The Operations Center is an office

The Operations Center oversees the TOWEMS of its unit. It is therefore modelled as an **office**,
not a personal post:

* `command_office_section` gained `function` (`general` | `operations_center`), with a partial
  unique index enforcing **one active Operations Center per unit**.
* `apps.core.roles.OFFICE_FUNCTION_ROLE_MAP` maps that function to the `operations_center` role.
  Every person with an active primary or secondary assignment to the office holds the role for
  **that unit only** — nothing is cached, so ending the assignment or deactivating the office
  removes the authority on the next request.
* The role is never appointable: `POST /appointments/` refuses it with
  *"Operations Center is an office, not a post. Create that office for the unit and assign
  personnel to it under Office/Section management."*
* Capabilities: view, comment/instruction, route, analytics. It does **not** get
  `review_missions` (the Operations Officer stage), `approve_missions` (Decision Authority),
  `evaluate` (MRM), subordinate-unit oversight, personnel, inventory, account or AOR management.
* Mission submission now notifies the office's members. Operations Officer and Admin Officer remain
  recipients, so nothing regresses when a unit has no Operations Center yet.

---

## 3. What changed

### Backend

| File | Change |
|---|---|
| `apps/core/roles.py` | `ROLE_OPERATIONS_CENTER` (label, capabilities, description); `OFFICE_FUNCTION_ROLE_MAP` / `OFFICE_ROLE_VALUES`; `CAP_REVIEW_MISSIONS`; `active_office_roles()`, `role_scope()`, `role_read_scope()`, `role_catalogue()`, `CAPABILITY_LABELS`; `can_review_missions` in the permissions payload; **removed the per-instance appointment cache** |
| `apps/accounts/views.py` | `RoleCatalogueView` (`GET /api/roles/`); admin user queryset prefetches appointments, acting delegations and section assignments |
| `apps/accounts/serializers.py` | `AdminUserSerializer` gains `roles`, `role_labels`, `account_role`, `appointments[]`, `sections[]` |
| `apps/accounts/urls.py` | `roles/` route |
| `apps/command/models.py` | `OfficeSection.function` + one-active-Operations-Center constraint |
| `apps/command/serializers.py` | `function`/`function_label` exposed; second Operations Center refused with a named holder; appointment write serializer explains account/office refusals |
| `apps/command/services.py` | `users_holding_post` includes Operations Center office members (unit only); `create_appointment` refuses office and account roles with explicit messages; `end_appointment` audit records the previous post, unit and user |
| `apps/command/views.py` | Appointment creation validates the unit explicitly, logs post/unit/user/note |
| `apps/ops/views.py` | Officer-stage routing gated on `CAP_REVIEW_MISSIONS`; mission submission also notifies the Operations Center |
| `apps/command/migrations/0002_officesection_function_and_more.py` | Additive: `function` column with default + partial unique constraint. No new table, no legacy change |

### Frontend

| File | Change |
|---|---|
| `pages/UserManagementPage.jsx` | Role pickers built from `/api/roles/`; **Roles & appointments** dialog with effective roles, **office memberships**, active posts, post assignment (scope/capability preview, exclusive-holder warning) and end-post; account-role select uses `account_value`; dialog re-reads the account after every change |
| `pages/OfficeSectionsPage.jsx` | Function selector when creating an office/section; Operations Center badge on the card |
| `components/Layout.jsx` | Sidebar badge shows the roles actually in force |
| `styles.css` | `.badge-stack` for multiple role badges in a table cell |

---

## 4. Role and permission outcomes

| Role | Kind | Authority scope | Notes |
|---|---|---|---|
| Administrator | account | system-wide | Only holder of `manage_users`, `manage_appointments`, `manage_aor`, `system_config` |
| Safety Officer | **both** | unit + subordinates | Account role in the enum, also a unit post; listed once |
| Evaluator / Creator | account | unit + subordinates | MRM workflow; separation of duties unchanged |
| Commander / Deputy Commander / Director for Operations | appointment | unit + subordinates | One active holder per unit (unique index) |
| **Operations Center** | **office** | **unit only** | Membership of the unit's Operations Center office; view, comment, route, analytics |
| Operations Officer | appointment | unit + subordinates | Holds `review_missions` (the Officer stage) |
| Admin Officer | appointment | unit + subordinates | Personnel/admin; no `manage_users`, no credentials |
| Logistics Officer / Logistic NCOIC | appointment | unit + subordinates | Existing approval/request split preserved |

Full matrix: `docs/ROLE_PERMISSION_MATRIX.md` §2. Authority scope (what a role may act on) and
record visibility (unit + its subordinates, per `accessible_unit_ids`) are returned separately by
the catalogue as `scope` and `read_scope`.

---

## 5. Tests actually run

```
cd toms_backend && ../.venv-toms/Scripts/python.exe -m pytest -q
→ 309 passed, 0 failed, 0 skipped        (exit 0; SQLite test database only)

python manage.py makemigrations --check --dry-run
→ No changes detected

cd toms_frontend && npm run build
→ built in 1.50s, dist/assets/index-DFhZSejA.js 488.16 kB / 142.27 kB gzip
```

New tests — `apps/accounts/tests/test_role_management.py`:

* catalogue lists each role once, all 12 present, account/post/office metadata complete;
* `manage_aor` / `manage_users` never appear on a non-Administrator role;
* Operations Center is `kind: office`, not appointable, and lacks `review_missions`,
  `approve_missions` and `evaluate`;
* Administrator can assign **every** appointment role and the holder sees it in `/auth/me/`;
* account-role change takes effect on the next request;
* revocation removes authority immediately, including on an already signed-in session;
* **office membership** grants the Operations Center role; ending the assignment or deactivating
  the office removes it; the grant is unit-only;
* the admin list row carries effective roles, appointments and office memberships;
* account roles and `operations_center` are refused as unit appointments (with the reason);
  inactive accounts refused; one holder per exclusive post; non-Administrators 403; a Commander
  cannot promote themselves; an Administrator cannot change their own role.

New tests — `apps/ops/tests/test_operations_center_role.py`:

* office members screen and route, and the routing rows are written in the enforced order;
* they are refused at the Officer stage (403 "mission review authority");
* they cannot give the final decision;
* an office member of another unit cannot touch the mission (unit-scoped queryset → 404);
* ending the assignment removes the routing authority;
* an Officer cannot skip submission;
* submission creates an open action notification for the office members;
* a second Operations Center office for a unit is refused.

Existing `test_appointments.py` (cardinality, acting authority, expiry, scoping) passes unchanged.

**Browser walkthrough (staging):** `/offices` shows the function selector and the Operations Center
badge; flipping an existing office to `operations_center` made its member's user row show
`OPERATIONS CENTER · 5132ABS` and the effective roles read "Admin Officer, Administrator,
Operations Center"; a second Operations Center for the same unit was refused with the existing
office named; `POST /appointments/` with `operations_center` was refused with the office message;
both the office function and the earlier test appointments were restored afterwards.

---

## 6. Database compatibility

* No legacy table, column, row, identifier or constraint was touched.
* One migration, `command.0002_officesection_function_and_more`:
  * `command_office_section.function` — `VARCHAR(40)`, `NOT NULL`, default `'general'`; existing
    rows take the default, so no office silently becomes an Operations Center;
  * partial unique index `command_one_operations_center_per_unit` (unit where active and function
    = operations_center).
* No new table. Appointments and assignments are ended, never deleted, so history is preserved.
* Existing four-value account roles and the legacy accounts keep their assignments and identifiers.

---

## 7. Deployment (requires approval) and rollback

Not applied to production. Staging has the migration applied.

```bash
# 1. Verified backup
pg_dump -Fc -d <production_db> -f toms_pre_role_management_$(date +%Y%m%d).dump

# 2. Apply the additive migration (staging already verified)
.venv-toms/Scripts/python tools/staging_manage.py migrate command 0002

# 3. Deploy backend, then frontend build; verify
curl -s /api/health/                       # 200
curl -s /api/roles/                        # 12 roles, operations_center kind="office"
```

Rollback: `migrate command 0001` drops the `function` column and the constraint; no other table is
affected, and nothing was rewritten. Offices created with `function='general'` remain valid. No
data restore is needed for either direction.

**Before production:** create each unit's Operations Center office and assign its staff, otherwise
those units keep routing through the Operations Officer/Admin Officer recipients as before.

---

## 8. Remaining issues / decisions for you

1. **Add Personnel form still offers four roles.** Deliberate: it writes the legacy
   `users.authority` enum, which cannot store a post or an office role, and the earlier
   specification said not to change the form.
2. **Operations Center scope** is unit-only. If the office should also see subordinate units'
   missions (e.g. a wing watching its groups), say so — it is a one-line capability change plus
   tests.
3. **Converting an existing office**: the page can create an office with the Operations Center
   function and deactivate offices, but it cannot flip an existing office's function. A unit that
   already has an ordinary "Operations Center" section must deactivate it and create the real one.
4. **No UI yet for acting appointments or approval rules.** Both exist in the API
   (`/acting-appointments/`, `/approval-rules/`) and are audited.
