# TOWEMS Armory Desktop — User Manual

Version 1.0.0

## 1. What this application does

TOWEMS Armory Desktop is the offline armory ledger for TOWEMS. It lets the
armorer keep working when the TOWEMS server or internet connection is down:

- register firearms and ammunition;
- manage personnel and their designated rifle and pistol;
- issue firearms and ammunition against a mission or a regular purpose;
- receive returns, inspect weapons and reconcile ammunition;
- print receipts and PDF/Excel reports;
- review the local audit trail and transaction history;
- upload accountability records to TOWEMS and download updated personnel,
  missions, assignments and registers later.

The local database is the working copy. TOWEMS remains the central authority for
people, mission orders and master registers.

## 2. First run

1. Start the program from the Start Menu or desktop shortcut.
2. On the first run, create the local administrator:
   - display name and username;
   - optional rank;
   - password (at least 10 characters with letters and numbers);
   - armory unit name and acronym.
3. Sign in with that local account.

The local password protects this computer's armory database. It is separate from
the TOWEMS password used for synchronization.

Use **User Management** to create additional local accounts:

| Role | Use it for |
| --- | --- |
| System Administrator | full control, users, settings, backup, sync |
| Armorer / Logistics NCOIC | daily inventory, personnel, issue, return, sync |
| Armory Supervisor | read-only oversight and reports |
| Viewer | read-only armory screens |

The first sign-in with an administrator-created account asks for a password
change.

## 3. Screen guide

### Dashboard

Shows firearms on the register, custody counts, open issues, ammunition rounds,
inspection-due weapons, personnel, pending/failed synchronization, and the most
recent transactions. Click a card to jump to that module.

### Firearm inventory

- **REGISTER FIREARM** records a new rifle or pistol.
- The Firearm ID is generated automatically as `PAF-<serial>`.
- Butt numbers are unique within a firearm type.
- **EDIT** is available only for local, unsynchronised weapons that are not
  issued. Registry corrections for a TOWEMS-linked firearm are made in TOWEMS.
- **RECORD INSPECTION** changes condition, records remarks and sets the next
  inspection date. Inspect issued weapons when they are returned.
- Use the search, type and custody filters to find a weapon.
- The table always shows Firearm ID, Butt No., Type and Serial No.

### Ammunition inventory

- **REGISTER LOT** records nomenclature, caliber, type, lot number, quantity
  received, serviceability, storage, dates and responsible custodian.
- **ADJUST STOCK** requires a reason and creates an adjustment transaction. Use
  it after a physical count; do not rewrite `quantity received`.
- Search by ammunition ID, nomenclature, caliber, type or lot.
- Filter by serviceability.

### Personnel management

- Personnel normally arrive through synchronization. **ADD PERSON** is for
  local-only records; those records cannot be uploaded as new TOWEMS people.
- **EDIT** changes local details and the "authorised to receive firearms" flag.
  For a person downloaded from TOWEMS, identity, rank, unit and contact fields are
  read-only; only the armory authorisation and active flags can be changed here.
- **DESIGNATE FIREARMS** opens the rifle/pistol selection.
- **HISTORY** shows every designation and reassignment for the person.

Every authorised person should hold one designated rifle and one designated
pistol. A person without both is blocked from issue.

### Personnel firearm assignment

Shows active designations and historical rows. **NEW DESIGNATION** selects a
person and one rifle plus one pistol. Replacing a weapon closes the old row rather
than overwriting it. **RELEASE SELECTED** removes a designation; use it only on
authority, because the person is then incomplete until redesignated.

### Issuance

The eligibility table lists people, their designated weapons, readiness and any
blocker. **NEW ISSUANCE** opens the release workflow:

1. Choose **Regular / non-mission** or **Mission-based**.
2. For mission-based, choose an approved Mission Order. The roster limits the
   people available.
3. Select the receiving person.
4. For regular issuance, enter the purpose or reason. It is optional when a
   Mission Order is used.
5. Verify the Firearm ID and Butt Number for every designated weapon; tick that
   each passed inspection.
6. On the Ammunition tab, tick the lots being drawn and enter quantities.
7. Tick identity verification and acknowledgement, confirm the receiving
   person's name, add remarks/documents.
8. **RELEASE AND GENERATE RECEIPT**.

A person with an open issuance cannot receive another until the return is
recorded. Only designated firearms are released; change the designation first if
an exception is authorised.

### Deposit / return

The page lists open issuances. Select one and choose **RECEIVE RETURN**:

1. Verify identity and the original issuance reference.
2. Record the condition of every firearm; all firearms must be returned.
3. Enter the quantity of each ammunition lot physically returned. Missing rounds
   are written as a discrepancy automatically.
4. Acknowledge the deposit and add any notes.
5. **DEPOSIT AND GENERATE RECEIPT**.

The receipt is saved under `%LOCALAPPDATA%\TOWEMS Armory Management\receipts`.

### Transaction history

Search by reference, person, Firearm ID, butt number, serial number or lot.
Filter by transaction type and status. Double-click a row to see full detail and
open/print its receipt. A transaction stays in this list after synchronization;
the TOWEMS reference is shown when available.

### Reports

Choose a report, set the date range for assignments/transactions/audit, and use:

- **GENERATE PDF** for a landscape printable report;
- **EXPORT EXCEL** for an autofiltered workbook.

Reports include firearm identity fields, designation history, transaction
ledger, ammunition balances and the local audit trail.

### Synchronization

Shows pending/failed uploads, the last attempt, the next scheduled run, the
upload queue and history.

- **SYNC NOW** asks for the TOWEMS API address/username/password (password may be
  blank after the first successful sign-in) and runs immediately.
- **CONFIGURE** sets the HTTPS API address, API account, automatic sync and
  weekly day/time.
- **RETRY FAILED** returns failed operations to the queue after you correct the
  cause. Double-click a queue row to read its error.

Default schedule is Sunday 23:00. The scheduler runs while the application is
open. The installer can create a Windows task for `--sync-only` so the weekly
sync also runs when the desktop window is closed.

### User management

Create/edit local users, change roles, deactivate accounts, and set reset
passwords. An administrator cannot remove their own administrator access. New
accounts must change the temporary password at next sign-in.

### System settings

- organisation name and session timeout;
- automatic sync toggle and schedule summary;
- sync configuration dialog;
- change your password;
- backup database, restore database, open data/receipt folders;
- audit trail;
- application/data/database information.

Backups are consistent SQLite copies. Store them on controlled media. A restore
replaces the live database and requires an application restart.

## 4. Daily armory workflow

### Regular issue

1. Personnel page: confirm the person has an active rifle and pistol.
2. Issuance: select the person.
3. Enter the reason/purpose.
4. Verify each weapon and its condition.
5. Draw ammunition if required.
6. Verify identity, obtain acknowledgement and release.
7. Print/file the receipt.

### Mission issue

1. Synchronize so the approved Mission Order and roster are current.
2. Issuance → Mission-based.
3. Choose the Mission Order.
4. Select a person on the roster; the Mission Order reference is recorded
   automatically.
5. Proceed exactly as a regular issue.

### Return

1. Deposit / return: select the open issuance.
2. Confirm all firearms and the returning person.
3. Enter returned ammunition quantities.
4. Record condition and discrepancies.
5. Acknowledge and generate the deposit receipt.

## 5. Synchronization checklist

Before the first sync:

1. The TOWEMS account has completed first-sign-in setup and authenticator
   enrolment and can manage the armory.
2. The API base is the HTTPS TOWEMS `/api` address.
3. The local unit acronym/name matches a TOWEMS unit, or the account sees a
   single unit so it can be linked automatically.
4. You have the TOWEMS password for the first manual sync.

After a sync:

- **Upload queue** should show `Synced` rows and no failed rows.
- **History** shows uploaded/downloaded counts and status.
- New personnel, mission orders, assignments, firearms and ammunition appear in
  the local screens.
- Local transactions keep their original references and gain a TOWEMS reference.

If the queue contains a permanent error, read it, correct the local or central
data, then use **RETRY FAILED**. A network/5xx error is retried automatically with
backoff.

## 6. Security and accountability

- Do not share local accounts. Every action writes the signed-in username.
- The session locks after the configured idle period; sign in again to continue.
- The local password is hashed with PBKDF2-HMAC-SHA256; it cannot be recovered.
- The TOWEMS password is not stored. Only a refresh token is kept in Windows
  Credential Manager.
- Use the audit trail and reports when handing over or investigating a
  discrepancy.
- Back up before major data changes and before restoring.

## 7. Troubleshooting

| Symptom | Action |
| --- | --- |
| "No saved TOWEMS session" | Manual SYNC NOW → enter the TOWEMS password once |
| "The saved TOWEMS session has expired" | Enter the password again; tokens expire after 7 days of inactivity |
| "HTTPS" API error | Use `https://.../api` for a remote server; HTTP is loopback-only |
| Person has no designated rifle/pistol | Assignment page → designate both weapons |
| "not included in Mission Order" | Synchronize and check the roster in TOWEMS |
| "already has open issue" | Record the return first |
| Queue failed: local-only personnel | Create the person in TOWEMS, synchronize, then redesignate/retry |
| Queue failed: firearm unit not linked | Align the local armory unit with TOWEMS or register using the downloaded unit |
| Receipt/PDF not opening | Check the receipts folder; PDF viewer association may be missing |
| Database will not open | Close other copies, restore a backup, check the data folder permissions |
| Application error | Read the latest log in `%LOCALAPPDATA%\TOWEMS Armory Management\logs` |

## 8. Data, backup and recovery

Data folder:

```text
%LOCALAPPDATA%\TOWEMS Armory Management
```

Backups: **System Settings → Security & backup → BACKUP DATABASE NOW**. Choose a
destination outside the live data folder where possible. To recover, use
**RESTORE DATABASE**, select the file, confirm, then close and reopen the
application.

The database contains sensitive armory records and password hashes. Protect
backups accordingly.

## 9. Print and export

Every issue, return and stock adjustment can produce a PDF receipt immediately.
Historical receipts can be opened from Transaction History. Reports produce A4
landscape PDFs and XLSX workbooks. All output is generated locally and does not
require TOWEMS connectivity.

## 10. Getting help

Include the following when reporting a problem:

- local reference number of the affected transaction;
- exact message from the queue or dialog;
- username/role (not the password);
- time of the action;
- relevant line from `logs\error-YYYYMMDD.log` or `logs\sync.log`.

Do not send the database or receipts unless the receiving authority is cleared to
handle armory records.
