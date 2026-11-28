# TOWEMS Armory Desktop — Technical Documentation

Version 1.0.0
Application: TOWEMS Armory Management (offline desktop)
Platform: Windows 10 / 11, Python 3.11, PySide6 6.12, SQLite, SQLAlchemy 2.1

## 1. Purpose and boundaries

The desktop is an offline accountability ledger for the existing TOWEMS Armory
Management module. It is not a second TOWEMS server and does not own central
personnel, mission or inventory master data. The central Django application
remains authoritative for:

- units and personnel;
- mission orders, approvals and rosters;
- firearm and ammunition registers;
- personnel firearm designations.

The desktop is authoritative for the accountability events it records while
disconnected: issuances, returns, inspections, stock adjustments and designation
changes made by an authorised local armorer. Those events are uploaded with
stable UUIDs and then reconciled against a fresh scoped snapshot.

## 2. Runtime architecture

```text
PySide6 screens
   │
   ├── services.py        local business rules, RBAC, audit, queue
   │     └── db.py        SQLAlchemy engine/session, backup, queue helpers
   │           └── SQLite armory.db
   │
   ├── reports.py         ReportLab PDF and openpyxl Excel output
   │
   └── sync.py            upload/download orchestration
         └── api_client.py    HTTPS JWT client, refresh-token vault
```

The GUI never calls HTTP directly. The synchronization page starts a `QThread`
worker; SQLite uses WAL and a 30-second busy timeout, so reads and local
transactions remain available while the worker is running.

### Application paths

`toms_armory/config.py` resolves writable state under
`%LOCALAPPDATA%\TOWEMS Armory Management` unless `TOWEMS_ARMORY_DATA_DIR` is set.
This keeps the installed executable read-only-safe and supports per-user data.

```text
armory.db        SQLite ledger
receipts/        generated transaction PDFs
exports/         generated report PDF/XLSX
backups/         database backups
logs/            application errors and headless sync log
```

## 3. Local database

SQLite schema created by `Base.metadata.create_all` in `toms_armory/models.py`.
Additive columns are applied by `db._ensure_lightweight_columns` for upgrades of
the first release.

| Table | Purpose | Key relationships / constraints |
| --- | --- | --- |
| `app_setting` | key/value settings | `key` primary key |
| `database_meta` | schema/version metadata | singleton |
| `local_user` | local authentication | unique username; PBKDF2 hash; role; active flag |
| `unit` | downloaded/local armory units | `remote_id` unique; acronym/name |
| `personnel` | downloaded/local people | `remote_id` unique; username and service number unique |
| `mission` | downloaded orders | `remote_id` unique; mission number unique; `order_ready` |
| `mission_personnel` | downloaded rosters | unique `(mission_id, personnel_id)` |
| `firearm` | local register | `remote_id`; `firearm_id` unique; serial unique case-insensitive; butt unique per type; custody/condition checks |
| `ammunition` | local lots | `remote_id`; ammunition ID unique; non-negative stock checks |
| `assignment` | designation history | active unique indexes on `(personnel_id, assignment_type)` and `firearm_id`; type check |
| `armory_transaction` | issue/return/adjustment header | unique local reference; UUID; remote ID; personnel/unit/mission snapshots; sync state |
| `armory_transaction_line` | frozen item lines | firearm and ammunition snapshots; original-line link for returns |
| `firearm_inspection` | condition history | UUID, remote ID, firearm, transaction link |
| `audit_log` | local accountability trail | user, action, entity, before/after JSON, note |
| `sync_queue` | idempotent upload queue | UUID, action, payload, state, attempts, next attempt, last error |
| `sync_history` | synchronization runs | start/finish, status, counts, message and errors |

### Transaction snapshots

Transaction lines freeze:

- Firearm ID, butt number, serial number, firearm type
- manufacturer, model, caliber
- ammunition name, caliber, type, lot, unit of measure, quantity
- condition and inspection remarks

This is deliberate: later registry edits cannot rewrite what was physically
issued or returned.

## 4. Business rules

### 4.1 Firearm identity

`firearm_identifier(serial)` normalizes trim/case and returns `PAF-<SERIAL>`.
The database rejects duplicate serials case-insensitively. Butt numbers are
compared after normalization inside `firearm_type`; the same value is allowed
for a rifle and a pistol.

### 4.2 Designations

`assign_designated_firearms`:

1. Requires an active, authorised person and a firearm from the same armory unit.
2. Rejects retired, unserviceable, condemned or currently issued weapons.
3. Rejects a weapon actively designated to somebody else.
4. Closes the previous active assignment of the same type and clears the old
   firearm's `assigned_personnel_id`.
5. Creates a new active assignment and queues one `assignment.set` state
   operation for the person.
6. After the changes, requires both a rifle and a pistol designation.

The operation is transactional. If the second designation fails, the first is
rolled back by the caller's session scope.

### 4.3 Issuance

`issue_firearms` requires:

- active/authorised person; identity verification; acknowledgement name matching
  the person's full name or username;
- mission roster membership and a ready Mission Order when a mission is used;
- a non-empty purpose when no mission is used;
- one active rifle and one active pistol;
- every designated firearm supplied with matching Firearm ID and butt number;
- serviceable/stored custody for every firearm;
- serviceable, unexpired ammunition with sufficient stock;
- no existing open issuance for the person.

It creates an open `issuance` transaction, freezes lines, creates inspection rows,
sets firearms to `issued`, decrements ammunition stock, increments
`quantity_issued`, audits each firearm and queues `transaction.issue`.

### 4.4 Return

`return_firearms` requires every firearm from the original issuance, matching
butt numbers, identity verification, acknowledgement, and a valid condition for
each firearm. It:

- creates a closed `return` transaction linked to the original;
- returns firearms to `stored` with the inspected condition;
- creates inspection rows;
- adds only physically returned ammunition to stock;
- removes the full original issued quantity from `quantity_issued`;
- records each shortfall in `discrepancy_notes`;
- closes the original issuance.

### 4.5 Ammunition

A lot has `quantity_received`, `quantity_issued` and `stock_balance`. Direct
balance edits are not exposed. `adjust_ammunition` records an adjustment
transaction with a reason, changes the target balance, and adds the positive
delta to `quantity_received`. This mirrors the central rule that a received
quantity is not rewritten as a correction.

### 4.6 Inspections

Standalone inspections update the firearm condition and last inspection date,
and queue `inspection.create`. Inspections created as part of issue/return are
included in that transaction in TOWEMS; the desktop marks the corresponding local
inspection rows as synchronized when the transaction is accepted.

## 5. Synchronization protocol

### 5.1 Authentication

`api_client.TowemsClient` uses the existing TOWEMS simple-JWT endpoints:

```text
POST /api/auth/token/          {"username": "...", "password": "..."}
POST /api/auth/token/refresh/  {"refresh": "..."}
```

The access token is kept in memory. The refresh token is stored through
`keyring` in Windows Credential Manager under service
`TOWEMS Armory Management`; the key includes the API base URL and username. If
the credential vault is unavailable, the session works only until the process
ends. The password is never persisted.

Remote API URLs must be HTTPS. Loopback HTTP is allowed for development.

### 5.2 Cycle

`SyncService.run` performs:

1. Load API base/username from settings, authenticate or restore a session.
2. Fetch the scoped snapshot.
3. Bootstrap units and personnel only, so local firearm/ammo registrations can
   resolve remote unit IDs without overwriting local custody.
4. Process pending queue rows in ascending ID order, respecting
   `next_attempt_at`.
5. Fetch the full snapshot again.
6. Merge units, personnel, firearms, ammunition, missions/rosters and
   assignments; preserve local custody/stock when a related local transaction or
   queue row is still unsynced.
7. Write `SyncHistory` and a local audit event.

### 5.3 Queue operations

| Action | Central endpoint | Idempotency key |
| --- | --- | --- |
| `firearm.create` | `POST /api/armory/firearms/` | local serial/register reconciliation |
| `ammunition.create` | `POST /api/armory/ammunition/` | local lot/register reconciliation |
| `assignment.set` | `POST /api/armory/assignments/assign/` + release actions | queue UUID (`client_uuid`) |
| `transaction.issue` | `POST /api/armory/issuances/` | transaction UUID (`sync_uuid`) |
| `transaction.return` | `POST /api/armory/returns/` | transaction UUID (`sync_uuid`) |
| `inspection.create` | `POST /api/armory/firearms/{id}/inspect/` | inspection UUID |
| `ammunition.adjust` | `POST /api/armory/ammunition/{id}/adjust/` | transaction UUID |

`assignment.set` sends the current full designation state. The worker:

- loads central active assignments for the person;
- calls `assign` for desired rifle/pistol types;
- calls `release` for central types no longer held;
- maps returned remote assignment IDs back to local active rows.

If a person is local-only, the operation fails with an explicit message and the
person stays usable offline.

### 5.4 Error handling

| Error | Queue behaviour |
| --- | --- |
| 400/403/404/409 (permanent) | state `failed`, error retained; operator can fix and Retry Failed |
| 401 after refresh | authentication failure; remaining rows stay pending |
| 408/425/429/5xx | state `pending`, exponential backoff (2 min to 120 min) |
| Network failure | current row pending with backoff; cycle stops to avoid a retry storm |

`SyncQueue` rows are never deleted on success. They remain as an audit trail with
state `synced`. Local transactions are never deleted after upload.

### 5.5 Snapshot merge and conflict rules

- Units link by remote ID, then exact acronym/name. In a single-unit scoped
  deployment, the one local unit can link to the one remote unit.
- People link by remote ID, then username, then service number. Remote people not
  present in the snapshot are marked inactive locally; local-only people are
  preserved.
- Firearms link by remote ID, then serial. Registry fields and custody are
  updated unless a local transaction for the firearm is still unsynced.
- Ammunition links by remote ID, ammunition ID or natural lot tuple. Stock is
  preserved while a local issue/return/adjustment is unsynced.
- Missions link by remote ID/number; rosters are replaced from the snapshot.
- Assignments link by remote ID or matching local designation. Pending/failed
  assignment-state operations protect the local designation from being
  overwritten.
- A transaction already carrying a remote ID is not uploaded again.
- The central transaction UUID is unique; the API returns the existing row for a
  repeated issue/return/assignment/inspection/adjustment request.

## 6. Security

### Local authentication and RBAC

Passwords use PBKDF2-HMAC-SHA256 with 600,000 iterations, a per-password 16-byte
salt, and constant-time verification. Minimum length is 10 characters with at
least one letter and one digit. `local_user.must_change_password` forces a change
for accounts created by an administrator.

Roles:

| Role | Permissions |
| --- | --- |
| Administrator | all permissions, users, settings, audit, backup, sync |
| Armorer | inventory, personnel, assignments, issue, return, reports, sync |
| Supervisor | view and reports only |
| Viewer | view only |

Every service mutation calls `_check(actor, permission)`; the UI disabling is a
second layer, not the enforcement.

### Sessions and audit

- Idle timeout is configurable (default 15 minutes, minimum 1). Activity resets
  the timer; a lock dialog requires the same local account.
- Sign-in success/failure, CRUD, issue, return, inspection, adjustment, sync,
  backup and password changes write `audit_log` entries with before/after JSON.
- The audit table is append-only from the application's perspective; it is
  retained in backups and can be exported through Reports.

### Backup and recovery

`db.backup_database` uses the SQLite online backup API. It is safe while the
application is running. `db.restore_database` copies the selected backup over the
live database and reinitializes the engine; the UI requires a restart after a
restore. Backups are unencrypted SQLite files and must be protected as sensitive
armory data.

## 7. Reporting and receipts

`reports.write_receipt` creates an A4 PDF with:

- local and TOWEMS references;
- mission/standalone labelling and purpose;
- person, rank, unit, releasing armorer, acknowledgement;
- every firearm's ID, butt, type and serial;
- ammunition quantities and condition;
- discrepancy and inspection notes;
- signature blocks.

`write_report` produces landscape PDFs; `write_excel` creates an autofiltered,
frozen-header workbook. Reports are stored under `exports/` unless the user picks
another path.

## 8. Packaging

`towems_armory.spec` builds `dist/TOWEMS-Armory.exe` as a one-file, windowed
PyInstaller application. Resources (`logo.png`, `logo.ico`) are bundled under
`resources/` and resolved by `config.resource_path` for both source and frozen
runs.

`installer.py` / `installer.spec` builds `dist/TOWEMS-Armory-Setup.exe`. It is a
per-user installer: it copies the application and documentation under
`%LOCALAPPDATA%\Programs\TOWEMS Armory`, optionally creates Start Menu/Desktop
shortcuts and an optional Sunday 23:00 scheduled task, and writes a user
Add/Remove Programs entry and uninstaller. It does not require elevation.

## 9. Verification evidence

Last run in this workspace:

```text
Desktop: 16 passed in 5.71s
Central Armory: 49 passed
Frontend: vite build completed successfully
PyInstaller: dist/TOWEMS-Armory.exe built successfully
Headless smoke: --version exit 0; --sync-only initializes the DB and logs a
missing-session failure as expected.
```

The desktop suite mocks the central API for queue/merge testing. The central
suite exercises the real Django endpoints, serializers, idempotency and snapshot.
A live end-to-end test against a production TOWEMS instance requires real
credentials and approved test data and is therefore a deployment acceptance
step, not a developer unit test.

## 10. Known limitations and operational notes

- The TOWEMS JWT endpoint enforces the account's first-sign-in setup gate, not an
  interactive MFA challenge. Use a dedicated armory API account that has
  completed TOWEMS MFA enrolment and profile setup.
- Central personnel creation is not implemented from the desktop. Create people
  in TOWEMS, then synchronize. Identity, rank, unit and contact fields of a
  downloaded person are read-only on the desktop; armory authorisation and active
  state can be changed locally.
- If the local setup unit name does not match any central unit and the account
  sees multiple central units, the armorer must register/assign using a
  downloaded unit or align the local unit acronym.
- The in-process scheduler requires the desktop application to be running. The
  installer's optional scheduled task (or a manually created task) provides
  unattended weekly execution.
- There is no automatic conflict-resolution UI for permanent 4xx upload errors;
  the error is retained in the queue for correction and **Retry Failed**.
- ReportLab uses standard PDF fonts; the interface itself uses Segoe UI.
