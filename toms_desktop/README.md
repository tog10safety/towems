# TOWEMS Offline Armory Management System (Desktop)

A standalone Windows desktop application for the Armory Management module of
TOWEMS. It runs fully offline on Python, PySide6 and SQLite, and synchronizes
local accountability records with the existing TOWEMS Django REST API once a
week or on demand.

The desktop does **not** replace TOWEMS and does **not** create duplicate central
armory tables. It uses the existing TOWEMS Armory models and adds only the
fields and the scoped snapshot endpoint required for offline idempotency and
reference download.

## Delivered components

| Component | Location |
| --- | --- |
| Python source | `toms_desktop/toms_armory/` |
| Automated tests | `toms_desktop/tests/` |
| Windows executable | `toms_desktop/dist/TOWEMS-Armory.exe` |
| Windows installer | `toms_desktop/dist/TOWEMS-Armory-Setup.exe` |
| Installer source | `toms_desktop/installer.py`, `toms_desktop/installer.spec` |
| Technical documentation | `toms_desktop/docs/TECHNICAL.md` |
| User manual | `toms_desktop/docs/USER_MANUAL.md` |
| Installation guide | `toms_desktop/docs/INSTALLATION.md` |
| Central backend changes | `toms_backend/apps/armory/` and migration `0002` |

## Main modules

1. Dashboard
2. Firearm inventory
3. Ammunition inventory
4. Personnel management
5. Personnel firearm assignment
6. Firearm and ammunition issuance
7. Firearm and ammunition deposit / return
8. Transaction history
9. Reports (PDF and Excel)
10. Synchronization management
11. User management
12. System settings

## Armory rules implemented

- **Firearm ID** is generated as `PAF-` + the normalized serial number, for
  example `PAF-AFP9028394892`.
- **Butt number** is unique inside each firearm type. A rifle and a pistol may
  share a butt number; two rifles may not.
- Every authorised person is required to hold exactly one designated rifle and
  one designated pistol before an issue is released.
- One firearm cannot be actively designated to two people, and one person cannot
  have two active designations of the same type.
- Reassignment closes the previous assignment row and preserves history.
- Issuance is either mission-based or regular. A Mission Order is optional.
  Regular issuance requires a purpose/reason, identity verification and the
  receiving person's acknowledgement.
- Only designated firearms can be issued unless a designation is changed through
  the authorised assignment workflow.
- All designated firearms must be physically verified and returned together.
- Returns reconcile every ammunition line; shortfalls become recorded
  discrepancies.
- Ammunition is never edited directly. Stock movements are transactions.
- Every issue, return, assignment, inspection and adjustment is audited locally
  and queued for TOWEMS.

## Quick start from source

```bash
cd "Project TOWEMS/toms_desktop"
.venv/Scripts/python.exe -m toms_armory
```

The application stores its data under:

```text
%LOCALAPPDATA%\TOWEMS Armory Management\
    armory.db
    receipts\
    exports\
    backups\
    logs\
```

`TOWEMS_ARMORY_DATA_DIR` overrides that location for testing or a portable
deployment.

On first run the application asks for a local administrator, a local armory
unit, and a password of at least 10 characters containing letters and numbers.
The first account is local only. TOWEMS credentials are configured separately
under **Synchronization → Configure**.

## Synchronization

Configure the TOWEMS API address, for example:

```text
https://towems.example.org/api
```

Use an account that can manage the armory. The first **SYNC NOW** asks for the
TOWEMS password and stores only the resulting refresh token in Windows
Credential Manager. Later manual and scheduled runs reuse that token. The
password itself is never written to the database.

Default schedule: **Sunday at 23:00**, configurable in System Settings or the
Synchronization page. If the application is not running, install the optional
Windows scheduled task from the installer, or create one that runs:

```bat
"C:\Path\TOWEMS-Armory.exe" --sync-only
```

The headless mode writes its result to `logs\sync.log`.

Upload order is controlled and idempotent:

1. Firearm registrations
2. Ammunition lot registrations
3. Personnel designation state
4. Issuances
5. Returns
6. Inspections
7. Ammunition adjustments

Each object carries the local UUID. TOWEMS returns the existing record when the
same UUID is received again, so a dropped response cannot duplicate a
transaction. Local transactions are never deleted after upload.

## Central TOWEMS preparation

Apply the additive migration before starting the new backend:

```bash
cd "Project TOWEMS/toms_backend"
../.venv-toms/Scripts/python.exe manage.py migrate armory --settings=toms.settings.dev
```

The migration adds:

- `ArmoryTransaction.purpose`
- `ArmoryTransaction.sync_uuid` (unique, nullable)
- `PersonnelFirearmAssignment.sync_uuid` (indexed)
- `FirearmInspection.sync_uuid` (unique, nullable)

The snapshot endpoint is:

```text
GET /api/armory/sync/snapshot/
```

It returns the units, active personnel, firearms, ammunition, assignments and
mission orders/rosters in the signed-in account's armory scope. It is read-only
and requires an authenticated armory manager.

## Build

The virtual environment already contains the pinned dependencies.

```bash
cd "Project TOWEMS/toms_desktop"
.venv/Scripts/pyinstaller.exe --noconfirm --clean towems_armory.spec
.venv/Scripts/python.exe build_installer.py
```

`build_installer.py` packages `dist\TOWEMS-Armory.exe` and the documentation into
`dist\TOWEMS-Armory-Setup.exe`. The installer writes to
`%LOCALAPPDATA%\Programs\TOWEMS Armory` by default and does not require
administrator rights.

## Tests and verification

```bash
cd "Project TOWEMS/toms_desktop"
.venv/Scripts/python.exe -m pytest tests -q
```

Current desktop suite: **16 passed**. It covers firearm identity and butt-number
uniqueness, assignments and history, issue/return rules, ammunition
reconciliation, adjustments, password hashing, PDF/Excel output, HTTPS policy,
backup and a full mocked synchronization cycle.

Central Armory regression suite: **49 passed**.

```bash
cd "Project TOWEMS/toms_backend"
../.venv-toms/Scripts/python.exe -m pytest apps/armory/tests/test_armory.py -q
```

The frontend production build also succeeds after the optional purpose field was
added to the standalone issue modal:

```bash
cd "Project TOWEMS/toms_frontend"
npm run build
```

## Operating constraints

- TOWEMS personnel are downloaded. Locally created personnel remain local until
  the person exists in TOWEMS; designations and transactions for them stay in
  the queue with a clear error. Identity, rank, unit and contact fields of a
  downloaded person are read-only locally; only armory authorisation and active
  state can be changed on the desktop.
- The JWT endpoint requires the TOWEMS account to have completed its first-sign-in
  profile and authenticator setup. The API token flow follows the existing TOWEMS
  token contract and does not replace TOWEMS web MFA.
- The in-app scheduler runs while the desktop application is open. For a fully
  unattended weekly run, use the optional scheduled task.
- A remote API must use HTTPS. Plain HTTP is accepted only for `localhost` /
  `127.0.0.1` development.
- Backups are local SQLite copies. They contain armory data and password hashes;
  store and transfer them under armory control.

## Support

Use **System Settings → Audit trail** for local activity, **Synchronization →
Upload queue** for failed operations, and `logs\` for application errors. The
database can be backed up and restored entirely offline from System Settings.
