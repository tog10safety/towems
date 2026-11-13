# TOWEMS — Database Compatibility Report

**Companion to:** `TECHNICAL_AUDIT.md`
**Generated inventory:** `docs/DATABASE_SCHEMA_INVENTORY.md` (read-only introspection)
**Machine-readable:** `docs/database_schema.json`
**Reproduce with:** `python tools/introspect_schema.py --json docs/database_schema.json`

> **Source of truth = the database, not `models.py`.** Introspection already proved the ORM and the
> database disagree in at least two places (§7). Every claim in this report is taken from
> `information_schema`/`pg_catalog`, not from the SQLAlchemy models.
>
> **Path note (2026-10-28).** The `models.py` / `migrations/` references in §7 and §8 point at
> the legacy Flask ORM, which was archived into `Flask MRM OLD/`. The Django mappings this
> report validates live in `toms_backend/apps/legacy/models.py` and were unaffected by the move
> (`verify_legacy_mapping`: 0 mismatches, 9,823 rows).

**Database inspected:** `localhost:5432/mrm_system` (PostgreSQL 18.4, extensions: `plpgsql` only)
**Production (DigitalOcean):** not reachable from this environment; **not** inspected and **not** modified.
See §9 for the required pre-cutover re-verification.

---

## 1. Preservation contract (non-negotiable)

| Rule | How it is enforced in this port |
| --- | --- |
| Never drop/rename an existing table or column | All 11 legacy tables are Django models with `managed = False`; Django emits **no DDL** for them |
| Never change an existing column type, PK, FK, constraint, index | Same mechanism; no `ALTER` is generated. Migrations touch only `ops_*` / `core_*` tables |
| Never delete or replace records | No data migrations, no `TRUNCATE`, no re-seed on legacy tables |
| Preserve identifiers/relationships/timestamps | Existing `id` integer PKs and sequences are reused verbatim; Django is told not to create them |
| Never use production as a test database | Test suite uses an isolated SQLite/`toms_staging` database; production credentials are absent from this machine |
| No credentials in source/logs | Django settings read `DATABASE_URL` from env; no fallback password; `.env.example` only |

---

## 2. Ownership model (the `managed` decision, justified)

The instruction warned against using `managed = False` indiscriminately. Ownership is assigned table by table:

| Ownership | Meaning | `managed` | Tables |
| --- | --- | --- | --- |
| **Legacy-owned** | Schema created and evolved by the old Flask app. Django reads/writes rows but must never alter structure. | `False` | All 11 existing tables |
| **TOWEMS-owned** | New tables created by Django migrations in this engagement. Django fully owns them. | `True` | `ops_*`, `core_*` (new) |

Why not `managed = True` with "fake initial migrations" for legacy tables? Because a fake initial
migration would make Django *believe* it owns the schema. Any future `makemigrations` (an innocuous
`blank=True`, a `choices` refresh, a related-name tweak) could then generate `ALTER`/`DROP` against
production tables holding 484 users and 4,461 mission hazards. `managed = False` makes that structurally
impossible rather than merely discouraged.

Why not hand the legacy tables to Django ownership anyway "because we are migrating"? Because the
instruction is explicit that existing tables must not be renamed, retyped, or re-indexed without approval,
and the ORM currently **disagrees** with the live schema (§7). Legacy ownership keeps that drift inert.

---

## 3. Table-by-table mapping

Legend: **RW** = Django reads and writes rows; **RO** = read-only in this phase.

| # | Live table | Rows | Django model | App | Access | `managed` | Notes |
| ---: | --- | ---: | --- | --- | --- | --- | --- |
| 1 | `users` | 484 | `accounts.User` | accounts | RW | `False` | Custom `AUTH_USER_MODEL`; `password_hash` holds **werkzeug scrypt**; 22 columns incl. orphaned legacy `unit` |
| 2 | `units` | 6 | `legacy.Unit` | legacy | RW | `False` | Self-FK `parent_id` hierarchy; PG enum `unit_category_enum` |
| 3 | `unit_assignments` | 0 | `legacy.UnitAssignment` | legacy | RW | `False` | Unique `(user_id, unit_id)` |
| 4 | `unit_logos` | 6 | `legacy.UnitLogo` | legacy | RW | `False` | Dual identity: `unit_id` (unique, FK) **and** legacy `unit_name` |
| 5 | `hazard_registry` | 1,923 | `legacy.HazardRegistry` | legacy | RW | `False` | Risk ratings stored as `"4B"` strings; PG enums `severity_enum` |
| 6 | `mrm_forms` | 292 | `legacy.MRMForm` | legacy | RW | `False` | 50 columns; `edit_history` JSONB (GIN); 4 legacy + 4 FK unit snapshot columns |
| 7 | `mission_hazards` | 4,461 | `legacy.MissionHazards` | legacy | RW | `False` | Unique `(mrm_id, hazard_id)`; override columns |
| 8 | `mrm_personnel` | 9 | `legacy.MRMPersonnel` | legacy | RW | `False` | Unique `(mrm_id, user_id)` |
| 9 | `mrm_feedback` | 2 | `legacy.MRMFeedback` | legacy | RW | `False` | 23 columns, 4 user FKs |
| 10 | `feedback_notifications` | 6 | `legacy.FeedbackNotification` | legacy | RW | `False` | Notification mirror; TOWEMS adds a general `ops_notification` |
| 11 | `user_activities` | 2,612 | `legacy.UserActivity` | legacy | RW | `False` | Existing audit trail; `activity_data` JSONB (GIN), BRIN on `timestamp` |

New TOWEMS tables (all `managed = True`, created only by reviewed migrations): see §8.

---

## 4. Type mapping (verified against live columns)

| PostgreSQL (live) | Django field | Verified on | Risk / note |
| --- | --- | --- | --- |
| `integer` + `nextval(...)` | `AutoField` / `BigAutoField` (PK) | all 11 tables | Django must **not** create the sequence; `managed=False` prevents it |
| `character varying(n)` | `CharField(max_length=n)` | `users.username(80)`, `mrm_forms.mrm_number(20)` | Lengths copied exactly from `information_schema` |
| `text` | `TextField` | `hazard_description`, `safety_objectives`, `description` | — |
| `boolean` | `BooleanField(null=True)` | `creator_signed`, `is_active` | Nullable booleans exist; keep `null=True` so `None ≠ False` is preserved |
| `timestamp without time zone` | `DateTimeField(null=True)` | all date columns | **`USE_TZ = False`** — see §5 |
| `double precision` | `FloatField` | `mrm_forms.total_percent` | — |
| `jsonb` | `JSONField` | `mrm_forms.edit_history`, `user_activities.activity_data` | Django's `JSONField` maps to `jsonb` on PostgreSQL; GIN indexes untouched |
| PG `ENUM` (9 types) | `CharField(choices=…)` | `authority`, `category`, `severity`, `status`, `review_status`, `authority_level`, `activity_type`, `module`, unit `category` | Reads fine (psycopg returns the label). Writes send a string, PostgreSQL casts to the enum. **No `CREATE TYPE` is emitted** because no migration targets these tables |
| FK `integer` + constraint | `ForeignKey(..., db_constraint=False)` **for new→legacy links** | `ops_*` → `users`/`units`/`mrm_forms` | New tables may not assume legacy tables exist in a fresh dev DB; constraints are documented as a later, optional, reviewed addition |
| FK `integer` + constraint (legacy→legacy) | `ForeignKey(..., db_constraint=False)` | `mission_hazards.mrm_id` → `mrm_forms.id` | Existing constraints stay in place; Django simply doesn't re-declare them |

---

## 5. Timestamp / timezone strategy

The legacy code mixes two clocks:

* `datetime.utcnow` as a SQLAlchemy default (`users.date_created`, `units.date_created`, `mrm_forms.date_created`, `hazard_registry.date_updated`).
* `get_ph_time()` = `Asia/Manila` **timezone-aware** values (`MRMPersonnel.tagged_at`, `MRMFeedback.submitted_at`, `UserActivity.timestamp`, `FeedbackNotification.created_at`).

Both land in `timestamp without time zone` columns. Rows therefore contain a mix of UTC-naive and
UTC+8-naive values, and the database cannot tell them apart.

**Decision:** set `USE_TZ = False` in Django. With `USE_TZ = True` Django would assume stored values are UTC
and re-interpret them on read/write, silently shifting historical timestamps by 8 hours and changing what
users see on 292 MRM forms. With `USE_TZ = False`, datetimes round-trip exactly as stored — the only choice
that preserves historical values. All new TOWEMS tables store naive local (Asia/Manila) time for consistency
with the majority of recent data, and the convention is documented in the model docstrings.

**Follow-up (requires approval, not done):** a reviewed data migration that normalises legacy timestamps to a
single convention. It rewrites historical values, so it must not be run without explicit sign-off.

---

## 6. Authentication hash compatibility

`users.password_hash` was written by `werkzeug.security.generate_password_hash`. Under Werkzeug 3.0 the default
method is **scrypt**, formatted like `scrypt:32768:8:1$<salt>$<hex digest>`. Django's `check_password()` cannot
read this format.

**Two options were evaluated:**

| Option | Consequence | Verdict |
| --- | --- | --- |
| Force a password reset for all 484 users | Total lockout; breaks operational continuity | **Rejected** |
| Custom auth backend that verifies Werkzeug hashes | Existing credentials keep working unchanged; no writes to `users` | **Adopted** |

Implementation: `apps/accounts/backends.py::WerkzeugPasswordBackend`. It verifies with
`werkzeug.security.check_password_hash`, supports the `scrypt`/`pbkdf2` families, returns `None` (not an
exception) on malformed hashes so a corrupt row cannot 500 the login endpoint, and exposes
`user.authority` for permission mapping. Optional `TOMS_REHASH_ON_LOGIN` (default **off**) would upgrade
hashes to Django PBKDF2 — deliberately disabled because it writes to the legacy `users` table and needs your
approval.

`is_active` is preserved: inactive users cannot authenticate, matching the old `is_active` checks.

---

## 7. Schema-drift register (ORM ↔ live database)

| # | Drift | Evidence | Consequence today | Port handling |
| ---: | --- | --- | --- | --- |
| D1 | `models.py` defines `module_enum` with an extra value `unit_management`; the live enum has only `auth,hazards,mrm,admin,users,unit_safety` | `models.py:85-89` vs `DATABASE_SCHEMA_INVENTORY.md` enum table | Any `UserActivity` insert with `module='unit_management'` raises `InvalidTextRepresentation` → unit-management actions are not audited (or 500 the request) | Django maps the enum to `CharField` with the **live** label set; no schema change. Fixing the enum is an additive `ALTER TYPE … ADD VALUE`, deferred pending approval |
| D2 | `users.unit` **exists as a real `character varying` column**, but `models.py` defines `unit` as a Python `property` that shadows it | live column list vs `models.py:294-303` | The ORM never reads or writes that column. Legacy string unit values sitting in it are invisible to the app; any code path that "sets" `user.unit` only logs a warning and writes nothing | Django maps it as `unit_legacy` with `db_column='unit'` so the data is **preserved and visible**; `unit_id` remains authoritative |
| D3 | `unit_logos` carries both `unit_name` (legacy) and `unit_id` (unique FK) | live column list | Logo lookups fall back to `unit_name`; two units cannot share a logo row | Both columns mapped; uniqueness on `unit_id` respected |
| D4 | `mrm_forms` carries both `original_*` strings and `original_*_id` FKs | 50-column list | Historical unit attribution is duplicated | Both sets mapped read/write, never recomputed |
| D5 | Alembic has 1 stub revision; schema actually created by `db.create_all()` | `migrations/versions/` | No migration history; schema cannot be rebuilt from code | Django treats the DB as truth; new tables get real migrations |

---

## 8. New database objects introduced by TOWEMS (additive only)

Nothing below existed before. No legacy table is altered. All are created by
`toms_backend/apps/ops/migrations/` and were applied **only** to the `toms_staging` database.

| Table | Purpose | Key relationships |
| --- | --- | --- |
| `ops_mission` | Shared mission header for Air **and** Ground operations | `unit_id`→`units.id`, `created_by`→`users.id`, `risk_form_id`→`mrm_forms.id` |
| `ops_ground_mission_detail` | Ground-only fields (est. start/end, UPR, O/SPR) | 1:1 `mission` |
| `ops_air_mission_detail` | Air-only fields (aircraft, flight id, route, crew, departure/destination) | 1:1 `mission` |
| `ops_mission_personnel` | Personnel assigned to a mission with operational role | `mission`, `user_id`→`users.id` |
| `ops_inventory_category` | Materiel/equipment/firearms/ammunition/vehicles/comms | self-FK parent |
| `ops_inventory_item` | Asset master (serial/asset tag, custodian, unit, location, serviceability) | `category`, `unit_id`, `custodian_id` |
| `ops_inventory_transaction` | Immutable issue/return/transfer/maintenance/inspection ledger | `item`, `actor_id` |
| `ops_vehicle` | Vehicle register | `unit_id` |
| `ops_vehicle_trip` | Trip linked to a ground mission | `mission`, `created_by` |
| `ops_trip_itinerary` | Ordered stops with coordinates/MGRS and planned times | `trip` |
| `ops_vehicle_trip_vehicle` | Trip ↔ vehicle (multiple vehicles per trip) | `trip`, `vehicle` |
| `ops_area_of_responsibility` | AOR polygon as **GeoJSON**, colour, owning unit | `unit_id` |
| `ops_risk_assessment` | Links an MRM form to a mission, records evaluator + risk snapshot | `mission`, `mrm_form_id`→`mrm_forms.id`, `evaluator_id`→`users.id` |
| `ops_notification` | General notification/approval inbox | `recipient_id`→`users.id` |
| `core_audit_log` | TOWEMS audit trail (actor, action, entity, before/after, IP) | `actor_id`→`users.id` |

Plus the Django framework tables required for sessions/auth plumbing in the **new** database only:
`django_session`, `django_content_type`, `auth_permission`, `auth_group`, `auth_group_permissions`.

### Why no PostGIS

The inspected database has **no PostGIS extension** (`pg_extension = {plpgsql}`), and adding an extension to a
production database is an infrastructure change requiring DBA approval and superuser rights.

Therefore AOR geometry is stored as **GeoJSON in a `jsonb` column plus numeric centroid/bounding-box columns**,
which:

* works on plain PostgreSQL (proven: migrations applied to `toms_staging`),
* round-trips to Leaflet without a server-side geometry library,
* keeps spatial queries available via `jsonb` containment/`ST_`-free numeric filters,
* leaves the door open: if you later approve `CREATE EXTENSION postgis`, `postgis` can be enabled and a
  generated `geometry` column added **without touching** the GeoJSON source of truth.

Spatial validation (ring closure, ≥3 distinct vertices, lat/lon bounds, self-intersection rejection at the
bbox level) is enforced in `apps/ops/geo.py` so invalid polygons never reach the database.

---

## 9. Required pre-cutover verification of the **production** database

The local database may not match DigitalOcean. Before any production migration, run — against a **read replica
or a restored dump**, never the live primary:

```bash
# 1. Introspect production (read-only) and diff
python tools/introspect_schema.py --json docs/production_schema.json \
    --out docs/PRODUCTION_SCHEMA_INVENTORY.md

# 2. Diff against the audited local schema
python tools/diff_schema.py docs/database_schema.json docs/production_schema.json
```

`tools/diff_schema.py` reports added/removed tables, added/removed/renamed columns, type changes, enum label
differences and row-count deltas. **Cutover is blocked** if it reports any of:

* a legacy table that is missing the mapping in §3,
* a column type that differs from §4,
* an enum label absent from the Django `choices`,
* a foreign key referencing a table not in §3.

---

## 10. Data-integrity verification queries (run before and after, compare outputs)

```sql
-- Row counts (must be identical before/after; new tables excluded)
SELECT 'users' t, count(*) FROM users UNION ALL
SELECT 'units', count(*) FROM units UNION ALL
SELECT 'mrm_forms', count(*) FROM mrm_forms UNION ALL
SELECT 'mission_hazards', count(*) FROM mission_hazards UNION ALL
SELECT 'hazard_registry', count(*) FROM hazard_registry UNION ALL
SELECT 'user_activities', count(*) FROM user_activities;

-- Identity preservation: max ids must not move
SELECT max(id) FROM users; SELECT max(id) FROM mrm_forms;

-- Risk semantics must survive: no rating may become NULL or numeric
SELECT count(*) FROM hazard_registry WHERE before_risk_rating IS NULL;
SELECT count(DISTINCT before_risk_rating) FROM hazard_registry;  -- expected: string tokens like '4B'

-- Evaluator linkage must not be lost
SELECT count(*) FROM mrm_forms WHERE reviewed_by IS NOT NULL;

-- Approval chain integrity
SELECT count(*) FROM mrm_forms WHERE authority_approved AND authority_approval_date IS NULL;
```

Expected values recorded at audit time: `users=484`, `units=6`, `mrm_forms=292`, `mission_hazards=4461`,
`hazard_registry=1923`, `user_activities=2612`.

---

## 11. Compatibility matrix (summary)

| Capability | Legacy (Flask) | TOWEMS (Django/DRF) | Compatibility |
| --- | --- | --- | --- |
| Read `users` incl. legacy `unit` column | partial (property hides column) | full | **Improved**, data preserved |
| Login with existing passwords | werkzeug scrypt | same algorithm, custom backend | **Yes** |
| Roles/permissions | decorators per route | DRF permission classes | **Yes**, centralised |
| MRM create/edit/review/approve | Jinja + session | REST + session/JWT | **Yes** (API parity, see `TOWEMS_MODULES.md` §7) |
| Risk rating strings `"4B"` | string concat | identical algorithm | **Yes** (golden tests) |
| Mission hazards with overrides | yes | yes | **Yes** |
| Unit snapshots on MRM | both column sets | both mapped | **Yes** |
| Audit trail | `user_activities` | `user_activities` (kept) + `core_audit_log` | **Yes**, additive |
| Air vs Ground operations | single `activity_mission` field only | distinct categories + detail tables | **Extended** (no data loss) |
| Inventory / vehicles / trips | absent | new tables | **New** |
| GIS / AOR | absent | GeoJSON polygons, Leaflet | **New** (no PostGIS needed) |
| PostGIS | not installed | not required | **Unchanged** |

---

## 12. Rollback of database changes

Because every TOWEMS object is **new**, rollback is purely additive-reversal and never touches legacy data:

```sql
-- Only the new tables; safe because they contain only TOWEMS data
DROP TABLE IF EXISTS ops_trip_itinerary, ops_vehicle_trip_vehicle, ops_vehicle_trip,
                     ops_vehicle, ops_inventory_transaction, ops_inventory_item,
                     ops_inventory_category, ops_mission_personnel,
                     ops_ground_mission_detail, ops_air_mission_detail,
                     ops_risk_assessment, ops_area_of_responsibility,
                     ops_notification, ops_mission CASCADE;
```

Take a `pg_dump` of the database **before** creating the TOWEMS tables so this is never necessary as a
recovery step. The exact commands are in `DEPLOYMENT_AND_ROLLBACK.md`.
