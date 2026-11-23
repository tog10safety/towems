# TOWEMS Role and Permission Matrix

**Source of truth:** `toms_backend/apps/core/roles.py`
**Enforcement layer:** `apps/core/permissions.py` + unit-scoped querysets (`apps/core/scoping.py`,
`apps/command/views.py::scoped_units`).
**Verified by:** `apps/command/tests/test_appointments.py`, `apps/ops/tests/test_missions.py`,
`apps/ops/tests/test_inventory.py`, `apps/ops/tests/test_operational_picture.py`.

The frontend only hides controls. Every rule below is checked on the server; a direct API
request with a manipulated body, id or query parameter is rejected.

---

## 1. How a user's authority is resolved

Three layers are merged into one capability set:

1. **Base account role** — the legacy `users.authority` enum. It is *not* altered:
   `Administrator`, `Evaluator`, `Creator`, `Unit Safety Officer`.
2. **Unit appointments** — rows in `command_unit_appointment` naming a TOWEMS post
   (Commander, Deputy Commander, Director for Operations, Safety Officer, Logistics Officer,
   Operations Officer, Admin Officer, Logistic NCOIC) for one unit.
3. **Office membership** — an active `command_section_assignment` (primary or secondary) to an
   active `command_office_section`. This is how the **Operations Center** works: it is an office of
   a unit, not a personal post, so its authority belongs to the office and is held by every active
   member for that unit only.
   Office membership also grants the capabilities attached to the office's *code*
   (`roles.OFFICE_CODE_CAPABILITIES`). **A3 – Operations** is the only such office today: its
   members hold `view_missions` and `manage_missions` (Mission Planning) for their own unit. The
   grant is functional only — no office code can confer `manage_users`, `manage_sections`,
   `manage_aor` or any other administrative capability, and it never becomes a unit appointment.
4. **Acting appointments** — time-boxed delegation in `command_acting_appointment`, with the
   delegating authority, reason, start/end, delegated capabilities and revocation record.

```python
roles.effective_roles(user, unit_id)      # names in force
roles.capabilities_for(user, unit_id)     # capability set
roles.has_capability(user, cap, unit_id)  # single check used by views
```

An appointment covers **its own unit and every subordinate unit** (same rule as
`accessible_unit_ids`). It never reaches a sibling or parent unit.

**Combination rule (explicit):** capabilities are a union. An appointment *adds* authority; it
never removes what the person's base account already allows. Command posts therefore receive
only command capabilities, and mission safety is additionally protected by workflow gates
(no self-approval; routing before decision). This preserves the 485 legacy accounts exactly.
A user who holds a `Creator` account *and* a Commander appointment can still create MRMs —
because of the Creator account, not because of the command post.

**Hard limits:** `system_config`, `manage_appointments`, `manage_users` and `manage_aor` can never
be granted by an acting delegation (`NON_DELEGABLE`), and acting authority only ever narrows the
acting role's own capability set.

**AOR management:** only the Administrator role holds `manage_aor`. Until this enhancement a
Unit Safety Officer could create AORs; that is now Administrator-only (spec §1), and every other
role keeps read access within its own unit scope. Command posts may view the unit's AOR and its
subordinates' AORs but cannot create, edit, transfer or remove coverage.

**AOR upkeep (`maintain_aor`)** is the narrower grant added later: rename an AOR, change its
colour or notes, archive it out of active coverage and restore it. It is held by the Administrator
and by the Operations Center office **for its own unit only** (never its subordinates), and it
does *not* allow moving coverage to another unit, redrawing a boundary, or deleting the record —
those stay with `manage_aor`. `maintain_aor` can never be delegated by an acting appointment.

**Account administration:** only the Administrator role holds `manage_users`. A Unit Safety
Officer or Admin Officer may maintain personnel records through the legacy form, but may no
longer change a role, username or serial number there; those are Administrator-only actions
and are audited. No role can read a password hash, and no endpoint returns one.

**Three kinds of role, one catalogue.** `GET /api/roles/` returns every assignable role with its
label, whether it is an *account* role, a *unit appointment* or an *office* (`kind`), its data
scope, whether it is capped at one holder per unit, and the capabilities it grants. The
Administrator interface builds every picker from that payload, so no role can exist in the backend
while being absent from the interface. `Safety Officer` is deliberately both an account role and a
post and is listed once (`kind: "both"`). `Operations Center` is `kind: "office"` and is never
appointable.

---

## 2. Capability matrix

`X` = granted by this role. Blank = not granted by this role.

| Capability | Administrator | Commander | Deputy Cdr | Director Ops | Operations Center | Safety Officer | Logistics Officer | Logistic NCOIC | Operations Officer | Admin Officer | Evaluator | Creator |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| view | X | X | X | X | X | X | X | X | X | X | X | X |
| create | X | | | | | X | | | | | X | X |
| edit | X | | | | | X | | | | | X | X |
| delete | X | | | | | | | | | | | |
| submit | X | | | | | X | | | | | X | X |
| review | X | | | | | X | | | X | | X | |
| review at Operations Officer stage (`review_missions`) | X | | | | | X | | | X | | X | |
| view missions in scope (`view_missions`) | X | X | X | X | X | X | X | | X | X | | |
| evaluate | X | | | | | X | | | | | X | |
| approve | X | X | X | | | X | | | X | | | |
| reject | X | X | X | | | X | X | | X | | | |
| return for correction | X | X | X | | | X | X | | X | | | |
| comment / instruct | X | X | X | X | X | X | X | X | X | X | X | X |
| route | X | X | X | X | X | | | | X | | | |
| manage sections | X | | | | | | | | | X | | |
| manage personnel | X | | | | | | | | | X | | |
| plan missions — create / edit / remove drafts (`manage_missions`) | X | | | | X | | | | | | | |
| approve missions | X | X | X | | | X | | | | | | |
| manage inventory — add, edit, delete, record stock movements | X | | | | | | X | | | | | |
| record aircraft fuel consumption (`record_air_fuel`) | X | | | | X | | X | X | | | | |
| manage the vehicle register (`manage_vehicles`) | X | | | | | | X | | | | | |
| request inventory | X | | | | | | X | X | | | | |
| approve inventory | X | | | | | X | | | | | | |
| view sensitive inventory | X | | | | | X | X | | | | | |
| manage appointments | X | | | | | | | | | | | |
| manage acting authority | X | X | | | | | | | | | | |
| view analytics | X | X | X | X | X | X | X | | X | | | |
| view subordinate units | X | X | X | X | | X | X | X | | X | | |
| view audit log | X | | | | | | | | | | | |
| system configuration | X | | | | | | | | | | | |
| manage user accounts (`manage_users`) | X | | | | | | | | | | | |
| manage AOR coverage (`manage_aor`) | X | | | | | | | | | | | |
| maintain AOR records in own unit (`maintain_aor`) | X | | | | X | | | | | | | |
| view the hazard library (`view_hazards`) | X | X | X | X | | X | | | X | | | |
| maintain the hazard library (`manage_hazards`) | X | | | | | X | | | | | | |
| audit post-mission feedback (`audit_post_mission_feedback`) | X | | | | | X | | | | | | |

**A3 – Operations** is the unit's staff planning office: every active member (primary or
secondary assignment) holds `view_missions` and `manage_missions` for that unit only, which is what
shows and permits Mission Planning. The grant comes from the office code, not from a role or an
appointment; moving the person out of A3 removes it on the next request, and it adds no user, role,
AOR or office-administration capability.

The **Operations Center** is the unit's watch and also its planning authority: it creates and plans
Air and Ground operations (`manage_missions`), screens, comments and routes them. It holds
neither `review_missions` (the Operations Officer stage) nor `approve_missions` (the Decision
Authority stage); those are separate capabilities precisely so the watch cannot inherit a
reviewer's or approver's authority. Commanders and the staff officers keep `view_missions` but no
`manage_missions`: their oversight of a mission is read-only, and the only write paths they keep
are the workflow ones (comments/instructions, routing, the final decision).

**Mission visibility.** A mission record is never visible to everyone:

| Caller | Sees |
|---|---|
| Administrator | every mission in every active unit |
| A3 – Operations office member, Operations Center office member, Commander, Deputy Commander, Director for Operations, Operations Officer, Admin Officer, Logistics Officer, Safety Officer | missions of their unit and its authorised subordinates |
| Anyone else | only the missions they created, are assigned to as personnel, or are on the MRM team for |

The scope comes from one place (`apps/ops/services.visible_missions`) used by the mission list, the
mission detail, the dashboard counters and the operational map, so a tampered request parameter
cannot widen it.

**MRM register visibility and deletion.** The risk register (`/risk`, `RiskRegisterViewSet`) uses its
own rule (`apps/ops/mrm_access.py::visible_mrms`), because an assessment is not a mission: the
oversight posts read their unit's assessments, everyone else only the ones they are attached to.

| Caller | Sees | May delete |
|---|---|---|
| Administrator | every MRM in every active unit | any MRM |
| Unit Safety Officer | own unit and subordinates, plus assigned units (`unit_assignments`) | only an MRM that is not approved |
| Commander, Deputy Commander, Director for Operations, Operations Officer, Operations Center office member | own unit and subordinates | no |
| Anyone else (Creator, Evaluator, ...) | only the MRMs they raised, were assigned (evaluator or routed decision authority) or were tagged on | no |

The register also filters by `unit` (the choices come from `GET /api/risk-register/units/`, itself
scoped the same way) and by `date_from` / `date_to` over the dates the MRM covers - the linked
mission's `start_at`/`end_at`, falling back to `date_created` for legacy rows that never had a TOWEMS
mission. Deleting a row is a real delete (`DELETE /api/risk-register/{id}/`, or
`POST /api/risk-register/bulk-delete/` with `ids`): hazards, tagged personnel, feedback, the mission
link and the stored approved PDF are removed with it, and the full snapshot is written to both audit
trails first. `can_delete` on the serializer tells the interface whether to offer the button; the
server re-checks it for every id in a bulk request and reports the refused ids with their reason.

Unit scope by post:

| Post | Scope |
|---|---|
| Administrator | all active units |
| Commander / Deputy Commander / Director for Operations | assigned unit + all subordinates |
| Safety Officer (base or appointment) | base: assigned unit(s) + subordinates; appointment: post unit + subordinates |
| Logistics / Operations / Admin Officer, Logistic NCOIC | assigned unit + subordinates (Operations Officer is unit-only in policy; the API does not let them act on a sibling unit) |
| Operations Center (members of the unit's Operations Center office) | assigned unit only (no `view_subordinate_units`) |
| Evaluator / Creator | own unit + subordinates |

**Hazard library.** The legacy `hazard_registry` table is the unit's institutional hazard
library, and every MRM selects its hazards from it. `manage_hazards` (create, edit, delete,
import, export) is held by the Administrator and by the Safety Officer **for their own unit only**;
`view_hazards` (read) is additionally held by the Commander, Deputy Commander, Director for
Operations and Operations Officer for their unit and its subordinates. Export is a maintainer's
tool because the workbook exists to be edited and imported again, so those four posts read the
library on screen only. No other account can read it — the module is absent from the menu and the
API answers 403. The import template's Unit column is a dropdown of the units the caller may
import into, so no row can name a unit TOWEMS does not record. A hazard
that an assessment still selects cannot be deleted: the API refuses with the number of assessment
rows that use it, because removing it would break the record the assessment and its PDF were built
from. Import and export use the downloadable `.xlsx` template; an import is all-or-nothing and
reports every invalid row.

**Post-mission feedback.** The people tagged on an MRM (`mrm_personnel`) file feedback once the
assessment is approved and its mission is completed or cancelled. Nobody needs a capability to
file their own feedback; the record is scoped to the tagged person. `audit_post_mission_feedback`
is held by the Administrator and the Safety Officer, and the audit queue is scoped to the units
that officer covers (own unit and subordinates, plus assigned units for the base Safety Officer
account). The auditor can mark a submission reviewed, actioned or dismissed (with a reason), and
can adopt a recommended hazard straight into the unit's library - which requires
`manage_hazards` for that unit.

Two different questions are answered here. **Authority scope** (`role_scope` in the catalogue) is
what the role may *act* on, and is narrower - the Operations Center acts on its own unit only.
**Record visibility** (`role_read_scope`) is the queryset rule in
`apps/core/scoping.py::accessible_unit_ids`, which gives every account its own unit and that
unit's subordinates, and the Administrator every active unit. `GET /api/roles/` returns both
(`scope`/`scope_label` and `read_scope`/`read_scope_label`) so the interface does not imply one
while enforcing the other.

---

## 3. Organisational constraints

| Rule | Enforcement |
|---|---|
| One active Commander per unit | `create_appointment` pre-check + partial unique index `command_one_active_holder_per_post` |
| One active Deputy Commander per unit | same |
| One active Director for Operations per unit | same |
| Commander, Deputy and DFO are distinct posts | separate role values; test `test_commander_deputy_and_director_are_distinct_posts` |
| Other posts may be shared | no constraint; test `test_sharing_a_post_is_allowed_for_non_exclusive_roles` |
| No overlapping acting command for the same post | overlap query in `create_acting_appointment`; 400 with "Conflicting command authority" |
| Acting authority expires automatically | every lookup filters `starts_at <= now < ends_at`, `revoked_at IS NULL` |
| Acting authority is auditable | creation and revocation write `core_audit_log` rows |
| Acting appointment cannot grant admin | `NON_DELEGABLE` subtraction; test `test_acting_authority_never_grants_system_config` |
| Admin Officer never sees credentials | personnel serializer never includes `password_hash`; test `test_admin_officer_never_receives_credentials` |

---

## 4. Decision Authority (configurable)

`ApprovalRule` (`command_approval_rule`) selects the approver roles for a mission or MRM,
optionally per unit and mission category. The most specific active row wins; with no rows the
defaults apply:

* **Mission:** Commander, Deputy Commander, Administrator
* **MRM:** Commander, Deputy Commander, Administrator, Safety Officer

`approver_roles_for(entity, unit_id, category)` is the only lookup used by the approval views,
so the final approver is configuration, not a hard-coded job title. Changing a rule is an
audited administrator action.

---

## 5. Workflow gates that do not depend on the matrix

* A mission cannot reach final approval until an `operations_officer` **approved** routing row
  exists; the Operations Center stage must precede it.
* The Operations Center must first record `routed` before the Operations Officer stage accepts
  a decision, and the Officer stage is gated on `review_missions`, which the Operations Center
  does not hold (test `test_operations_center_cannot_review_at_the_officer_stage`).
* A creator cannot approve their own mission (`approve` returns 403).
* A pending inventory request never changes stock; `decide` commits the ledger entry in the same
  transaction as the decision.
* Workflow-backed notifications cannot be closed from the bell; they close when the workflow
  records completion.

---

## 6. Enforcement points (audit map)

| Area | File |
|---|---|
| Capability definitions / matrix | `apps/core/roles.py` |
| Permission classes | `apps/core/permissions.py` |
| Unit scope | `apps/core/scoping.py`, `apps/command/views.py::scoped_units` |
| Missions / approval chain | `apps/ops/views.py::MissionViewSet` |
| MRM workflow | `apps/ops/views.py::RiskRegisterViewSet` |
| Inventory requests | `apps/ops/views.py::InventoryRequestViewSet` |
| Inventory register + vehicle register writes | `apps/ops/views.py::InventoryItemViewSet`, `VehicleViewSet` (`InventoryPermission`, `CanManageVehicles`) |
| Appointments / acting / sections / instructions | `apps/command/views.py`, `apps/command/services.py` |
| Role catalogue (`GET /api/roles/`) | `apps/core/roles.py::role_catalogue`, `apps/accounts/views.py::RoleCatalogueView` |
| Office membership → role | `apps/core/roles.py::active_office_roles` (`OFFICE_FUNCTION_ROLE_MAP`) |
| Account role assignment | `apps/accounts/views.py::UserAdminViewSet`, `apps/accounts/serializers.py` |
| Audit + failed-authorisation logging | `apps/core/audit.py`, `apps/core/errors.py` |
| Dashboard + map scoping | `apps/ops/views.py::DashboardView`, `OperationalPictureView` |

---

## 7. Policy decisions requiring confirmation

1. **Additive combination.** As described in §1, an appointment never removes base-account
   permissions. If policy requires a Commander's Creator account to be suspended while they
   hold command, that is a new (subtractive) rule and needs an explicit decision.
2. **Operations Officer scope.** The specification says "current unit only"; the appointment
   lookup reaches subordinates like every other post. No endpoint currently lets an Operations
   Officer act on a subordinate unit's mission without the routing chain, but the capability
   itself is not unit-limited in the same way the wording implies.
3. **Operations Center scope.** Implemented as the narrower reading: unit-only, no
   `view_subordinate_units`. Say so if the watch is meant to see the whole subordinate tree.

---

## 8. Assigning roles (administrator workflow)

The Administrator interface (`/admin/users` → **Roles & appointments**) assigns authority in two
stages, both backend-enforced:

| Step | Endpoint | Validation |
|---|---|---|
| Account role | `PATCH /admin/users/{id}/` → `authority` | Only the four legacy enum values; changing your own role is refused; audited as `update_user_account` |
| Operations Center | `POST /offices/` → `{unit, name, code, kind: "office", function: "operations_center"}`, then `POST /section-assignments/` | One active Operations Center office per unit (partial unique index + serializer check); assignments must stay inside the member's own unit; authority is unit-only |
| Unit appointment | `POST /appointments/` → `{unit_id, user_id, role, notes}` | `APPOINTMENT_ROLE_VALUES` only (account roles are refused here); inactive accounts refused; duplicate active post refused; one holder per exclusive post; unit must be inside the caller's scope; audited as `create_appointment` |
| End appointment | `DELETE /appointments/{id}/` | Cannot re-end an ended post; audited as `end_appointment` with the previous unit and post |

Roles are never assigned by the person themselves: `manage_appointments` is held by the
Administrator alone and is `NON_DELEGABLE`, so neither command nor acting authority can grant it.
A role change is visible on the holder's **next request** because appointments are read from the
database per request - nothing is cached on the session or the user object
(`apps/core/roles.py::active_appointments`, test
`test_an_appointment_applies_to_an_already_signed_in_session`).

Verification: `apps/accounts/tests/test_role_management.py` (catalogue, every role assignable,
revocation, audit, refusals), `apps/ops/tests/test_operations_center_role.py` (workflow
separation), `apps/command/tests/test_appointments.py` (cardinality, acting authority).

---

## 9. Instructions and action items

Instructions (`/api/instructions/`) are open to every signed-in account, because an instruction is
addressed to a person who then has to acknowledge and complete it. What is restricted is the
**"All visible"** view - the whole set of instructions in scope, rather than the caller's own -
and it is held by exactly four posts:

| Post | Where it comes from |
|---|---|
| Administrator | `users.authority` |
| Commander | `command_unit_appointment` |
| Deputy Commander | `command_unit_appointment` |
| Operations Center | membership of the unit's Operations Center office (`function = operations_center`) |

The capability is `view_all_instructions` (`apps/core/roles.py::CAP_VIEW_ALL_INSTRUCTIONS`), exposed to
the SPA as `permissions.can_view_all_instructions`. In the page it removes the tab; on the server it
refuses a list request with **no `scope` parameter**, which is exactly what that tab sends:

| Request | Caller | Result |
|---|---|---|
| `GET /api/instructions/?scope=outstanding` | any account | their own open action items |
| `GET /api/instructions/?scope=mine` / `?scope=issued` | any account | items addressed to / issued by them |
| `GET /api/instructions/` (no scope, "All visible") | Administrator, Commander, Deputy Commander, Operations Center | everything in the caller's unit scope |
| `GET /api/instructions/` (no scope, "All visible") | anyone else | `403` |

Individual records (`/instructions/{id}/`) and the action endpoints (`acknowledge`, `complete`,
`verify`, `attachments`) stay available to whoever the record is already visible to - the unit
scope, the sender, or a named recipient - so delivery keeps working. Issuing additionally requires
`comment`, as before.
