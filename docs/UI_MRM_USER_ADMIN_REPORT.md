# TOWEMS UI, Mission–MRM Integration and User Administration — Implementation Report

Scope: the enhancement request covering (1) professional UI and per-user appearance settings,
(2) mission creation integrated with Mission Risk Management, and (3) administrator-controlled
user accounts. Work was done inside the existing TOWEMS codebase; no module was rebuilt and no
production system was contacted.

**Status:** implemented, tested on the staging database and in the browser. Production is
untouched and no deployment was performed.

---

## 1. Summary of completed changes

### 1.1 Appearance and interface

* One semantic colour-token system (`src/styles.css`) drives every surface. `data-theme="light|dark"`
  is set on `<html>`; `system` resolves against `prefers-color-scheme` and keeps following the OS.
* ThemeProvider (`src/theme/ThemeContext.jsx`) applies the preference immediately, mirrors it to
  `localStorage` for the pre-paint script in `index.html`, and persists it to the account through
  `PATCH /api/auth/preferences/`. The account value wins on a new browser/device; this was verified
  by clearing `localStorage` and reloading.
* Settings page (`/settings`) offers System default, Light and Dark with previews, plus account
  summary and links to profile, notifications and user management.
* A one-click toggle is in the top bar and on the sign-in and password-reset pages; the choice is
  applied before first paint, so there is no flash of the wrong theme.
* Navigation was reorganised into Operations / Resources / Command groups with a consistent inline
  SVG icon set instead of platform-dependent emoji. Active state, focus rings, tables, cards,
  buttons, modals and notification panel use the same tokens.
* Responsive rules cover tablet/mobile: off-canvas navigation with scrim, collapsing grids, form
  rows and detail lists, and a single-column theme picker.
* Maps follow the theme: OpenStreetMap tiles in light mode, CARTO dark tiles in dark mode, with
  correct attribution. Legends, markers and layer colours remain readable in both.

### 1.2 Mission → MRM workflow

* Additive table `ops_mission_risk_team` records who prepares a mission's assessment (lead,
  assessor or safety reviewer), who assigned them and when, and whether the assignment is active.
  Removing a person deactivates the row instead of deleting history.
* Both Air and Ground mission forms include an **MRM team** section. Candidates come from
  `/personnel/assignable/?mrm_unit=<id>`, limited to the mission unit, its HQ chain and its
  subordinate units. The server re-validates every assignment: the person must be active, inside
  that command tree and hold MRM create/evaluate capability. Inactive, unrelated, duplicate or
  unauthorised people are rejected with a field error.
* On mission creation (and when a member is added later), each team member receives a persistent
  action notification naming the mission, category, scheduled start, responsible unit, a direct
  link and the required action ("Prepare the MRM assessment"). It remains pending until the MRM
  is submitted.
* `POST /missions/{id}/start-mrm/` creates a draft legacy MRM using the original `YYYYMMDDnn`
  numbering, sets the legacy creator to the MRM lead, links the mission both ways and notifies the
  lead. It refuses when no team exists or when an assessment is already linked, so duplicate
  assessments cannot be created by repeated clicks.
* Mission detail shows the MRM state (`Not yet created`, `Pending`, `In progress`,
  `Submitted for evaluation`, `Approved`, `Returned for revision`), overdue/due-soon flags, the
  linked MRM number and a link into the MRM record. The MRM detail links back to the mission.
* Choosing an existing MRM on the mission form (`risk_form`) creates the same `RiskAssessment`
  relation that `start-mrm` creates, so the mission never points at an MRM while claiming none
  exists. Selecting MRM `2026102801` on a new mission reported `In progress` with that number
  immediately, and both records linked to each other.
* An approved assessment freezes the MRM link and the team; re-pointing it would rewrite approved
  accountability. One MRM cannot be linked to a second mission: the serializer rejects a `risk_form`
  already held by another mission (`MRM 2026102801 is already linked to mission …`), while re-saving
  the owning mission keeps its own assessment.
* Existing evaluator assignment (`POST /missions/{id}/risk/`) now also writes
  `mrm_forms.reviewed_by`/`reviewer_name`. Previously it only updated the TOWEMS link row, making
  `submit` fail with "assign an evaluator" even after an evaluator was chosen. Evaluators are
  notified when assigned.
* Submitting an MRM completes the team's "prepare" action and creates the evaluator's review
  action; returning it for revision re-opens an action for the team; approval closes the remaining
  action items and notifies the mission creator and team.

### 1.3 Administrator user management

New Administrator-only surface at `/admin/users` (`manage_users` capability) with search, role,
status and unit filters, pagination and confirmation dialogs.

* **Deactivate** requires a reason, refuses self-deactivation, preserves every record, writes a
  `deactivate_user` audit entry with before/after values and note, and blocks authentication.
  `WerkzeugPasswordBackend.get_user` now returns `None` for inactive accounts, so an already-open
  session also stops working immediately.
* **Reactivate** restores access with the existing credentials and audits the change.
* **Reset password** issues a single-use link. Only a SHA-256 digest of the token is stored
  (`accounts_password_reset_token`); the token is returned once to the administrator for hand-over
  through an approved channel, expires after 60 minutes and is invalidated when a newer link is
  issued. Completion happens anonymously at `/reset-password?token=…`, enforces the legacy password
  policy, marks the token used and changes the password digest, which invalidates existing sessions.
  No endpoint ever returns an existing password or hash.
* **Edit account** permits name, rank, designation, contact number, category, role and unit.
  Username and serial number are immutable; changing your own role is refused. Role changes through
  the legacy personnel form are now Administrator-only, so a Unit Safety Officer cannot escalate a
  role there. The Add Personnel form and its fields were not changed.
* Every administrative action is written to `core_audit_log` (actor, timestamp, record,
  before/after, reason) and to the activity feed.

---

## 2. Database changes (all additive)

| Object | Change | Notes |
| --- | --- | --- |
| `accounts_user_profile` | new column `theme_preference` (`varchar(10)`, default `system`) | TOWEMS-owned table, not a legacy table |
| `accounts_password_reset_token` | new table | user, token digest, creator, created/expiry/used timestamps, requesting IP |
| `ops_mission_risk_team` | new table | mission, user, role, assigned_by, assigned_at, is_active; unique `(mission,user)` |

Migrations: `accounts/0003_userprofile_theme_preference`, `accounts/0004_passwordresettoken`,
`ops/0004_missionriskteam`. `makemigrations --check` reports no drift; all three were applied to
`toms_staging` only.

No legacy table, column, type, constraint, identifier or row was altered. Starting an MRM from a
mission creates a normal `mrm_forms` row through the ORM, exactly as the legacy application does;
merely viewing a mission writes nothing.

---

## 3. Files changed

**Backend**

* `apps/accounts/models.py` — theme preference, reset-token model
* `apps/accounts/backends.py` — inactive accounts no longer resolve from a session
* `apps/accounts/profile.py` — preferences payload + `PreferencesView`
* `apps/accounts/serializers.py` — admin serializers, reset-completion serializer, identity/role guards
* `apps/accounts/views.py` — `UserAdminViewSet`, `PasswordResetView`, preferences in `/auth/me/`
* `apps/accounts/urls.py` — new routes
* `apps/accounts/migrations/0003_*`, `0004_*`
* `apps/core/roles.py` — `manage_users`, MRM team qualification, command-tree helper,
  `can_prepare_mrm`, non-delegable account administration
* `apps/core/permissions.py` — `IsUserAdministrator`
* `apps/ops/models.py` — `MissionRiskTeam`
* `apps/ops/serializers.py` — MRM status/team read fields, team validation and persistence,
  mission↔MRM link sync, legacy mission fields on the MRM
* `apps/ops/views.py` — team notifications, `start-mrm`, evaluator sync, MRM workflow actions
* `apps/ops/services.py` — legacy-compatible MRM numbering
* `apps/ops/migrations/0004_missionriskteam.py`
* Tests: `apps/accounts/tests/test_theme_preferences.py`,
  `apps/accounts/tests/test_user_administration.py`, `apps/ops/tests/test_mission_mrm.py`

**Frontend**

* `index.html` — pre-paint theme resolution
* `src/main.jsx`, `src/App.jsx` — providers and routes
* `src/theme/ThemeContext.jsx` — new
* `src/components/Icon.jsx` — new SVG icon set
* `src/components/Layout.jsx` — grouped navigation, top-bar theme toggle/settings, mobile scrim
* `src/components/MrmTeamPicker.jsx` — new
* `src/components/NotificationBell.jsx` — icon
* `src/components/DashboardMap.jsx`, `src/pages/AORPage.jsx` — theme-aware map tiles
* `src/pages/SettingsPage.jsx` — new
* `src/pages/UserManagementPage.jsx` — new
* `src/pages/ResetPasswordPage.jsx` — new
* `src/pages/LoginPage.jsx` — theme toggle
* `src/pages/MissionFormPage.jsx` — MRM team section and payload
* `src/pages/MissionDetailPage.jsx` — MRM status card, team management, start assessment
* `src/pages/MissionsPage.jsx` — MRM column/overdue indicator
* `src/pages/RiskDetailPage.jsx` — mission back-link
* `src/pages/DashboardPage.jsx` — MRM outstanding statistic
* `src/styles.css` — tokens, light theme, responsive and new component styles

**Documentation** — this report, `API_REFERENCE.md`, `ROLE_PERMISSION_MATRIX.md`, `CHANGES.md`,
`DEPLOYMENT_AND_ROLLBACK.md`.

---

## 4. Tests and verification (actual)

* Backend suite (`python -m pytest -o addopts="" -q`, in-memory SQLite, isolated): **251 passed,
  0 failed, 0 skipped**.
* New coverage includes theme persistence/isolation/validation; admin-only access; password never
  serialised; deactivation reason/audit/sign-in block/session invalidation/reactivation;
  self-deactivation; reset single-use, expiry,  policy-before-consumption, inactive-account refusal;
  identity/role immutability; Unit Safety Officer escalation block; MRM team validation
  (inactive/unrelated/duplicate), notification creation, start-mrm numbering/link/duplicate
  prevention, legacy back-link, team changes and freeze, approved-link protection, linking an
  existing assessment setting the mission's status, one assessment per mission (cross-mission link
  refused, owner re-save allowed), submission completing the action, and overdue detection.
* Frontend production build: success (113 modules, 464.59 kB / 135.91 kB gzip).
* Staging migrations applied; `check` and `makemigrations --check` clean.
* Browser walkthrough (Vite 5180 / Django 8010, staging DB):
  * light and dark modes render correctly; System default follows the OS; preference survived a
    reload **and** a cleared `localStorage`, proving it came from the account;
  * created a ground mission with an MRM team, saw `Pending` and the team row;
  * started the MRM from the mission, saw `In progress`, the generated number `2026102801` and the
    mission's `TOWEMS-GO-20261028-002`; followed the link to the MRM, which links back to the mission;
  * issued a reset link for a verification account, opened and validated it, then deactivated and
    reactivated the account and confirmed the status/actions updated and an audit reason was
    recorded;
  * linked an existing assessment to a new ground mission over the API: the mission came back
    `in_progress` with MRM `2026102801`, the mission page showed `IN PROGRESS`/`LINKED MRM` with
    `Open MRM record →`, and the MRM page linked back to its mission;
  * re-linking that same assessment to a second mission was refused with a field error, and the
    first mission kept its link;
  * recent activity listed `@Admin`, `@900595`, `@903125` — usernames, not full names;
  * maps used CARTO dark tiles in dark mode and OSM tiles in light mode.
* Production database: **not contacted, not migrated, not deployed.**

---

## 5. Remaining issues and limitations

1. **MRM content authoring** remains the legacy MRM workflow. TOWEMS can create/link a draft,
   assign an evaluator, submit, review and approve it, but there is no hazard-selection editor in
   the React app. The mission shows the correct status and links; editing the assessment content
   still requires the existing MRM module.
2. **Reset-link delivery** is manual by design (no email/SMS integration exists). The link is
   shown once to the Administrator; it must be handed over through an approved channel and treated
   as a secret. It is not written to the audit trail or logs.
3. **Legacy form persistence**: an MRM started from a mission is a normal draft. If an operator
   deletes that legacy row directly in the database, the link would dangle; the API never deletes
   MRMs.
4. The test mission `TOWEMS-GO-20261028-002` and its MRM `2026102801` were created in
   `toms_staging` during verification. They are not production data and can be removed from the
   staging clone if desired.
5. Theme choice for the pre-login page is local until the user signs in; after sign-in the account
   preference is authoritative. This is expected for a per-account setting.

---

## 6. Deployment, backup and rollback (requires approval)

Migrations are additive and affect only TOWEMS-owned tables. **Do not run them against production
without explicit approval.**

1. Back up the production database and verify the restore (see `DEPLOYMENT_AND_ROLLBACK.md` §2);
   also back up `signatures/`, `approved_pdfs/` and `static/unit_logos/`.
2. Row-count and checksum the legacy tables (`users`, `units`, `mrm_forms`, `mission_hazards`,
   `hazard_registry`, …).
3. Review `migrate --plan`; expect only `AddField`/`CreateModel` for the three objects above.
4. Apply with `DJANGO_SETTINGS_MODULE=toms.settings.prod` after approval.
5. Run `verify_legacy_mapping`; confirm zero mismatches and identical legacy row counts.
6. Deploy the backend, then the built frontend (`toms_frontend/dist`).
7. Smoke-test: sign in, set each theme, create a draft mission with a team (do not start an MRM on
   production test data unless intended), open User Management, issue a reset for a test account,
   deactivate/reactivate it, and confirm Audit Trail entries.
8. Keep the previous backend/frontend artifacts available. Rollback: redeploy the previous release
   and, if required, restore the pre-migration dump. The new tables are inert for the old code; no
   legacy object was modified, so a data rollback should not be necessary.

**Accessibility note:** theme tokens were chosen for contrast in both modes and controls have
visible focus rings. A formal WCAG audit has not been run.
