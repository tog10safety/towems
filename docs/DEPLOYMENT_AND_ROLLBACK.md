# Deployment, Backup and Rollback — TOWEMS on DigitalOcean

> **Production cutover executed on 2026-11-23** against `mrm-system.online`
> (droplet `146.190.94.234`). Sections 1 and 1.1 record the layout that now runs there; the
> remaining steps are the operating procedure for later releases.
>
> **Deployed release: `93903bc`** (group chats now write the one-per-thread notice a private chat
> writes, so a unit or approved group message reaches the phone, 2026-11-26). Record later releases
> in the table in section 1.1.

---

## 1. Current state

| Component | State |
| --- | --- |
| Legacy Flask MRMS | Installed at `/var/www/mrm`; `mrm.service` stopped and disabled (rollback path). |
| `toms_backend` (Django/DRF) | Deployed at `/var/www/towems`, serving `127.0.0.1:8001` via `towems.service`. |
| `toms_frontend` (React/Vite) | Deployed to `/var/www/towems/toms_frontend/dist` and served by nginx. |
| Production DB `mrm_system` | 87 migrations applied; 11 legacy tables untouched and still mapped. |
| Staging DB `toms_staging` | Clone of production + the 21 additive tables. Where all Django DDL ran first. |

### 1.1 Production layout (as built on the droplet)

| Piece | Value |
| --- | --- |
| Host | Ubuntu 22.04, `mrm-system.online`, 957 MB RAM |
| Database | **Self-managed PostgreSQL 14.24 on the same droplet** (not a managed cluster), database `mrm_system`; Django connects as **`towems`** (database/schema/table owner, `CREATEDB` for the restore drill, **not** superuser), Flask still uses `postgres` |
| Application directory | `/var/www/towems` — git clone of this repository, checked out at the release SHA, **owned `root:root` (0755)** |
| Python | system `python3.10` with `/var/www/towems/venv` |
| Node | **not installed** — build the SPA elsewhere and upload `dist/` |
| Deployed release | `4805c74` — Messages conversations have a long-press (or right-click) menu: mark unread, pin, mute, select, archive and delete, with an Archived view and bulk archive/mute/delete in selection mode. The state is per person on `ConversationMember`; muted threads send no alerts (including the Messages page's own browser alert), and a new message restores a deleted thread. The unused dashboard `notifications` counts (which included message notices) are gone; the system inbox summary is the only notification counter. Messages opens a conversation on its newest message, including from a notification deep link or when the same thread is re-opened after scrolling up, instead of leaving it below the fold at the oldest message. The server's notification list and summary exclude private message notices by default; a caller must pass `include_messages=true` to see them, so a new screen cannot mix them into the system inbox. The pulse still carries messages for phones, and the bell and Notifications page ask for the exclusion explicitly. The Notifications inbox also keeps messages out of its counters and Mark all read action. Settings has a Signed-in devices panel: it lists the live sessions, marks the current one, and ends another device (revoking that session and its alert token). Every account is held to one live session at a time unless its base role is named in `TOMS_SINGLE_DEVICE_EXEMPT_ROLES` (empty by default); a second sign-in names the device already holding it and offers to end that session. Messages sits beside Online with its own unread badge, and the bell excludes message notices (`exclude_category=message`). Phone push is still the only banner source on an install with a push token (requires `FCM_PROJECT_ID`/`FCM_CREDENTIALS_FILE`, §5.4). Rollback bundle kept as `dist.prev-4805c74`. Record each release here. |
| Service | `towems.service` → gunicorn, `toms.wsgi:application`, 2 workers, `127.0.0.1:8001`, `EnvironmentFile=/var/www/towems/.env`, **`WorkingDirectory=/var/www/towems/toms_backend`** |
| Web server | nginx site `mrm`: TLS via `/etc/letsencrypt/live/mrm-system.online`, SPA root + `try_files`, `/api/` → `8001`, `/static/ /uploads/ /signatures/ /approved_pdfs/` aliased to `/var/www/mrm/*` (the app's `PROJECT_ROOT` symlinks point there, so legacy evidence files stay reachable) |
| Secrets | `/var/www/towems/.env` (0600, `www-data`); backup job environment in `/etc/towems/backup.env` (0600) with `GNUPGHOME=/var/lib/towems/gnupg`; role password copies in `/etc/towems/towems_db_password` (0640 `root:www-data`) and `/etc/towems/postgres_password` (0600 root, operator only) |
| Backups | `/var/backups/towems` (0600, `www-data`), passphrase `/etc/towems/backup.passphrase`; timers `towems-backup`, `towems-restore-test`, `towems-backup-health` installed with `/srv/toms` rewritten to `/var/www/towems` and `User=towems` to `www-data` |
| Smoke account | `toms.smoke` (Administrator, profile + Authenticator complete); password only in `/etc/towems/smoke.credentials` (0600) |

The scheduled jobs run as `www-data`, whose home is `/var/www`; `GNUPGHOME` must therefore be
set in `backup.env`, and `/etc/towems` must be traversable (`root:www-data 0750`).

**Git runs as root.** The checkout is `root:root` 0755, so every Git command on the droplet runs
as root — `git -C /var/www/towems …` in a root shell or `sudo git -C /var/www/towems …`.
`sudo -u www-data git …` fails: the tree is not writable by `www-data`, and Git's safe-directory
check rejects it. `www-data` only needs read access to the checkout and the venv, plus write
access to `uploads/`, `signatures/`, `approved_pdfs/` and `static/`. Four paths in the tree are
untracked runtime data (`approved_pdfs/`, `signatures/`, `static/static/`, `uploads/`), so
`git status` always lists them; never resolve that with `git clean` or a `--force` checkout.

**Where the service runs.** `towems.service` has `WorkingDirectory=/var/www/towems/toms_backend`
and starts gunicorn from there, so `manage.py` gets the same directory: either `cd` into it first
or call it by absolute path (`/var/www/towems/venv/bin/python /var/www/towems/toms_backend/manage.py …`).
A shell that starts somewhere else (for example `/var/www/towems`) will not find `manage.py`.

### 1.2 Database roles

| Role | Attributes | Used by |
| --- | --- | --- |
| `towems` | `LOGIN`, `CREATEDB`, owner of database `mrm_system`, schema `public` and every table, sequence, function and enum in it | Django (`/var/www/towems/.env`), backup and restore-drill timers (`/etc/towems/backup.env`) |
| `postgres` | superuser | Flask rollback (`/etc/systemd/system/mrm.service`, `/var/www/mrm/.env`), operator tasks |
| `mrm_user` | no login privileges in use | unused legacy role |

Ownership is what lets `towems` run `migrate` (DDL) without superuser rights. When a *new*
object is created outside Django by `postgres`, transfer it with
`ALTER TABLE … OWNER TO towems` before the app needs to alter it. Rotate either password with
`ALTER ROLE … PASSWORD` and update the two `.env` files plus the `mrm.service` environment,
then restart `towems.service` (and `mrm.service` if it is enabled for a rollback).

## 2. Backup and recovery (do this first, every time)

```bash
# Full logical backup of production (run from a trusted host)
pg_dump --no-owner --no-privileges -h <HOST> -p 5432 -U <USER> -d mrm_system \
  -Fc -f mrm_system_$(date +%Y%m%d_%H%M).dump

# Verify the backup is actually restorable — an untested backup is not a backup
createdb mrm_restore_test
pg_restore --no-owner -d mrm_restore_test mrm_system_YYYYMM_DDMM.dump
psql -d mrm_restore_test -c "SELECT count(*) FROM users; SELECT count(*) FROM mrm_forms;"
psql -d mrm_restore_test -c "SELECT count(*) FROM hazard_registry; SELECT count(*) FROM mission_hazards;"
dropdb mrm_restore_test
```

Retention: keep the pre-cutover dump for at least 30 days and until you have signed off on
the migrated system. Also back up `approved_pdfs/`, `signatures/` and `static/unit_logos/`
(they hold generated evidence and the images embedded in it) — they are **not** in the
database. The legacy `instance/` SQLite `.bak` files are archived under
`Flask MRM OLD/instance/` and are not needed by TOWEMS.

### 2.1 Automated encrypted backups

`tools/postgres_backup.py` wraps the same procedure with encryption, checksums, retention and
a restore drill. It reads `DATABASE_URL` from the environment (or from
`toms_backend/.env` when `python-dotenv` is installed), and it never prints the database
password.

```bash
export DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/mrm_system

# Preferred: encrypt to a GPG public key. Keep the private key off the backup host.
export TOMS_BACKUP_GPG_RECIPIENT=backup@example.com

# Alternative: symmetric GPG with a passphrase file outside the repository.
# export TOMS_BACKUP_GPG_PASSPHRASE_FILE=/etc/towems/backup.passphrase

# Optional off-host destination. Choose one or both.
export TOMS_BACKUP_OFFSITE_DIR=/mnt/offsite/towems
# export TOMS_BACKUP_UPLOAD_COMMAND='rclone copy {file} remote:towems-backups'

python tools/postgres_backup.py backup
python tools/postgres_backup.py list
python tools/postgres_backup.py restore-test
```

What `backup` does:

* opens a repeatable-read transaction and exports a PostgreSQL snapshot;
* writes `pg_dump --format=custom` from that snapshot;
* records row counts for `users`, `units`, `mrm_forms`, `hazard_registry`,
  `mission_hazards` and `django_migrations` from the same snapshot;
* verifies the archive with `pg_restore --list`;
* encrypts it with GPG (AES-256 for symmetric encryption) and removes the plaintext dump;
* writes `<backup>.dump.gpg.sha256` and `<backup>.dump.json`;
* copies the encrypted file, checksum and manifest to `TOMS_BACKUP_OFFSITE_DIR` and verifies
  the destination checksum, and/or runs `TOMS_BACKUP_UPLOAD_COMMAND` without a shell.

The upload command placeholders are `{file}`, `{checksum}` and `{manifest}`. It is split with
`shlex` and executed directly, not through a shell. Database credentials are removed from the
uploader's environment.

Retention defaults to the newest backup for each of the last 30 UTC days plus the newest for
each of the last 12 months. Override it with `TOMS_BACKUP_RETENTION_DAILY` and
`TOMS_BACKUP_RETENTION_MONTHLY`, or inspect it with:

```bash
python tools/postgres_backup.py prune --dry-run
```

Retention applies to the local backup directory. Configure a lifecycle rule or scheduled
cleanup on the off-host destination separately; `TOMS_BACKUP_OFFSITE_DIR` must point at a
mounted remote or another genuinely separate host, not a second directory on the same disk.

`restore-test` decrypts the latest backup, restores it into a new `toms_restore_*` database,
compares the key-table counts with the manifest, and drops the scratch database. It needs
`CREATEDB`; if the application role does not have it, set a separate maintenance connection:

```bash
export TOMS_BACKUP_RESTORE_ADMIN_URL=postgresql://ADMIN:PASSWORD@HOST:5432/postgres
```

Use `restore-test --keep` to retain the scratch database for inspection. Use
`TOMS_BACKUP_SNAPSHOT=0` only if a transaction pooler rejects exported snapshots; the manifest
records whether a snapshot was used. A successful drill writes `restore-drill.json` in the
backup directory; that marker is what the health check uses to prove a recent restore test
passed. Do not edit or copy it by hand.

The passphrase or private key must not be stored next to the backups. The script does not
include uploaded evidence files (`approved_pdfs/`, `signatures/`, `static/unit_logos/`); back
those up separately. For recipient rotation, follow the
[backup GPG key-rotation runbook](BACKUP_KEY_ROTATION.md); it covers overlap, restore
verification, and retention of the old private key.

### 2.2 Scheduled backups and restore drills

The repository includes systemd units for a nightly backup, a monthly restore drill and a
daily health check:

* `deploy/systemd/towems-backup.service`
* `deploy/systemd/towems-backup.timer` (02:15 daily, persistent)
* `deploy/systemd/towems-restore-test.service`
* `deploy/systemd/towems-restore-test.timer` (03:30 on the first of each month)
* `deploy/systemd/towems-backup-health.service`
* `deploy/systemd/towems-backup-health.timer` (04:30 daily)
* `deploy/systemd/backup.env.example`

Install them on the host, adjusting `/srv/toms` and the `towems` service account if the
deployment layout differs:

```bash
sudo install -d -m 0750 /etc/towems
sudo install -m 0600 deploy/systemd/backup.env.example /etc/towems/backup.env
sudoedit /etc/towems/backup.env                 # set DATABASE_URL and GPG/off-site values

sudo cp deploy/systemd/towems-backup.service \
  deploy/systemd/towems-backup.timer \
  deploy/systemd/towems-restore-test.service \
  deploy/systemd/towems-restore-test.timer \
  deploy/systemd/towems-backup-health.service \
  deploy/systemd/towems-backup-health.timer \
  /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now towems-backup.timer towems-restore-test.timer \
  towems-backup-health.timer
```

Run all three jobs once by hand before relying on the timers:

```bash
sudo systemctl start towems-backup.service
sudo journalctl -u towems-backup.service -n 50 --no-pager
sudo systemctl start towems-restore-test.service
sudo journalctl -u towems-restore-test.service -n 50 --no-pager
sudo systemctl start towems-backup-health.service
sudo journalctl -u towems-backup-health.service -n 50 --no-pager
sudo systemctl list-timers 'towems-*'
```

The units run as `towems`, so the public key or passphrase file must be readable by that
account. The restore timer creates and drops a temporary `toms_restore_*` database; set
`TOMS_BACKUP_RESTORE_ADMIN_URL` when the application role lacks `CREATEDB`.

`tools/backup_health.py` checks that the newest encrypted backup is less than 36 hours old,
that its checksum matches the manifest, that a restore drill is recorded within 35 days, and,
when `TOMS_BACKUP_OFFSITE_DIR` is set, that the newest backup, manifest and checksum are also
present off-host and match. A missing marker or failed check exits non-zero. Configure
`TOMS_BACKUP_ALERT_WEBHOOK` to receive a JSON alert (including a Slack-compatible `text`
field); without a webhook, the non-zero systemd/cron result and journal entry are the alert.

```bash
python tools/backup_health.py
python tools/backup_health.py --backup-dir /srv/toms/_backups/postgres
```

Tune the limits with `TOMS_BACKUP_MAX_AGE_HOURS` and `TOMS_RESTORE_MAX_AGE_DAYS`. After
upgrading an existing installation, run `restore-test` once to create `restore-drill.json`;
until then the health check correctly reports that no successful drill is recorded.

If the host does not use systemd, the equivalent cron entries are:

```cron
15 2 * * * cd /srv/toms && .venv-toms/bin/python tools/postgres_backup.py backup
30 3 1 * * cd /srv/toms && .venv-toms/bin/python tools/postgres_backup.py restore-test
30 4 * * * cd /srv/toms && .venv-toms/bin/python tools/backup_health.py
```

## 3. Configuration (secrets, never in the repo)

`toms_backend/.env.example` lists every variable; copy it to `toms_backend/.env` on the
server and fill in real values:

```
DJANGO_SECRET_KEY=<32+ random bytes>
DJANGO_SETTINGS_MODULE=toms.settings.prod
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/mrm_system
DJANGO_ALLOWED_HOSTS=toms.example.com
DJANGO_CORS_ALLOWED_ORIGINS=https://toms.example.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://toms.example.com
```

`prod` settings enforce `DEBUG=False`, HTTPS redirect, HSTS, secure/session cookies and
`SECURE_PROXY_SSL_HEADER` (behind nginx/DO load balancer). `USE_TZ=False` is deliberate —
legacy timestamps are naive `Asia/Manila` values (see `DATABASE_COMPATIBILITY.md` §5).

Optional mapping configuration (no secrets): `TOMS_DEFAULT_BASEMAP` (id from
`GET /api/map/config/`) and, for a self-hosted tile service, `TOMS_INTERNAL_TILE_URL`,
`TOMS_INTERNAL_TILE_ATTRIBUTION`, `TOMS_INTERNAL_TILE_MAX_ZOOM`. See
`AOR_GIS_ENHANCEMENT.md` §5 for provider licensing and data flow.

## 4. Build

```bash
# Backend (Python 3.11)
python -m venv .venv-toms
.venv-toms/Scripts/pip install -r toms_backend/requirements.txt   # or bin/pip on Linux

# Frontend (Node 18+/npm)
cd toms_frontend && npm ci && npm run build    # → toms_frontend/dist/
```

Serving the SPA: the project has **no `STATICFILES`/`whitenoise` configuration yet**, so the
intended production layout is nginx serving `toms_frontend/dist/` as the site root and
reverse-proxying `/api/` to gunicorn. (Alternative — not implemented: add
`STATIC_ROOT`/`STATICFILES_DIRS` + whitenoise and let Django serve the assets.)

```nginx
server {
    listen 443 ssl;
    server_name toms.example.com;
    root /srv/toms/toms_frontend/dist;
    index index.html;

    location / { try_files $uri /index.html; }          # React Router

    location /api/ {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_set_header   X-Real-IP         $remote_addr;
    }
}
```

```bash
gunicorn toms.wsgi:application --bind 127.0.0.1:8000 --workers 3 --timeout 60
```

## 5. Production migration procedure (REQUIRES YOUR APPROVAL)

This is the only step that writes DDL to production. The migration is **purely additive to the
legacy schema**: 15 domain tables (`ops_*`, `core_audit_log`) plus Django's own
`django_*`/`auth_*` bookkeeping tables. The UI/MRM/user-administration phase adds one column on
the TOWEMS-owned `accounts_user_profile` table (`theme_preference`) and two TOWEMS tables
(`accounts_password_reset_token`, `ops_mission_risk_team`). The AOR/GIS phase adds one TOWEMS
table (`ops_administrative_area`), one nullable column (`ops_area_of_responsibility.admin_area`)
and one partial unique index. The MFA phase adds `accounts_user_mfa` (with its `account` column)
and `accounts_mfa_recovery_code`, and `command/0011` drops two TOWEMS-owned tables that no model
references any more (`command_unit_echelon`, `command_role_assignment_request`). There is no `ALTER`, `DROP` or
`RENAME` on any **legacy** object and no legacy row is rewritten.

Run the steps **in this order**; each one is a gate for the next.

```bash
# 0. Drift check — production schema must still match docs/database_schema.json
python tools/introspect_schema.py            # then diff with docs/database_schema.json

# 1. Fresh, verified backup (section 2)

# 2. Plan — read it. Expect CreateModel/AddField for TOWEMS tables, plus the reviewed
#    command/0011 drops of two TOWEMS-owned tables. No legacy object appears.
DATABASE_URL=postgresql://…/mrm_system DJANGO_SETTINGS_MODULE=toms.settings.prod \
  python toms_backend/manage.py migrate --plan

# 3. Apply (your explicit approval required here)
DATABASE_URL=postgresql://…/mrm_system DJANGO_SETTINGS_MODULE=toms.settings.prod \
  python toms_backend/manage.py migrate

# 4. Prove nothing legacy changed
DATABASE_URL=postgresql://…/mrm_system DJANGO_SETTINGS_MODULE=toms.settings.prod \
  python toms_backend/manage.py verify_legacy_mapping
```

**Release 6929c7e gate — group-chat routing threads.** Migration
`messaging/0007_backfill_group_chat_routing_threads` backfills every request whose
`thread_id` is null. After step 3, and after the release's service restart (so an old
worker cannot still create a request without a thread), count the requests and the
requests that now have a `group_request` routing thread:

```bash
DATABASE_URL=postgresql://…/mrm_system DJANGO_SETTINGS_MODULE=toms.settings.prod \
  venv/bin/python toms_backend/manage.py shell -c "
from apps.messaging.models import GroupChatRequest

requests = GroupChatRequest.objects.count()
routed = GroupChatRequest.objects.filter(
    thread__isnull=False, thread__kind='group_request'
).count()
missing = GroupChatRequest.objects.filter(thread__isnull=True).count()
wrong_kind = (
    GroupChatRequest.objects.filter(thread__isnull=False)
    .exclude(thread__kind='group_request')
    .count()
)
print(
    f'group chat requests={requests} routed={routed} '
    f'missing_thread={missing} wrong_thread_kind={wrong_kind}'
)
if requests != routed or missing or wrong_kind:
    raise SystemExit(
        'STOP: migration 0007 did not create exactly one group_request routing '
        'thread per group chat request; do not announce the release.'
    )
"
```

`requests` is the full request count when the check runs: the pre-existing requests that
0007 had to backfill plus any created during the rollout. The counts must satisfy
`requests == routed` with `missing_thread=0` and `wrong_thread_kind=0`; on a first
messaging deployment both counts are `0`. **Stop condition:** any other result, or a
non-zero exit from the command, means 0007 did not cover every request. Stop the release,
preserve the output, and investigate or restore the verified backup from section 2 — do
not restart, upload, or announce the release until the check passes.

Post-conditions to confirm: row counts of `users`, `units`, `hazard_registry`, `mrm_forms`,
`mission_hazards`, `mrm_personnel`, `mrm_feedback`, `feedback_notifications`,
`user_activities`, `unit_assignments`, `unit_logos` are **identical before and after**;
`verify_legacy_mapping` reports zero mismatches.

```bash
# 5. Load the official province/city boundaries (idempotent; ~1,730 rows)
DATABASE_URL=postgresql://…/mrm_system DJANGO_SETTINGS_MODULE=toms.settings.prod \
  python toms_backend/manage.py import_admin_boundaries --dry-run   # then without --dry-run
```

Expect `1730 created, 0 updated, 0 unchanged, 0 skipped`. A non-zero `skipped` count means the
bundled dataset is damaged — stop and re-run `tools/build_admin_boundaries.py` before deploying.

```bash
# 6. First-login rollout: force every active account through the profile refresh
DATABASE_URL=postgresql://…/mrm_system DJANGO_SETTINGS_MODULE=toms.settings.prod \
  python toms_backend/manage.py require_profile_refresh --dry-run   # counts only
DATABASE_URL=postgresql://…/mrm_system DJANGO_SETTINGS_MODULE=toms.settings.prod \
  python toms_backend/manage.py require_profile_refresh
```

Expect `Profile refresh required for N account profile(s), N password change(s) forced of N
active account(s).`, where N is the active account count (484 at the audit baseline). This is a
**data** step, not DDL: it sets `accounts_user_profile.onboarding_required` and the existing
`users.force_password_change`, and never touches an unmanaged table's schema. Run it **before**
nginx starts serving TOWEMS, so the very first request already meets the gate.
Do not run it earlier against production while Flask is still serving users: Flask would ignore
the flags, and people would be greeted by the profile refresh whenever the switch happens.

### 5.1 What every user meets on their first sign-in (Gmail + Google Authenticator)

The rollout is two server-enforced gates, in this order; neither can be skipped by a hand-made
request.

**Gate 1 — profile refresh.** Until all four items are present the session can only reach
`/auth/profile/`, `/auth/password/`, `/auth/onboarding/`, `/auth/me/`, `/auth/mfa/` and sign-out:

1. a **new password** (the old one is still valid for signing in; `force_password_change` is set);
2. a **profile picture**;
3. a **signature** (the same column the MRM PDF generator reads);
4. the **Gmail account** they will use — it must end with `@gmail.com`
   (`PROFILE_EMAIL_SUFFIX` in `toms/settings/base.py`).

`POST /auth/onboarding/complete/` refuses to clear the prompt while any item is missing.

**Gate 2 — Google Authenticator.** The instant the profile is complete the session is held to
`/auth/mfa/` until a TOTP code is confirmed. Deployment policy
`MFA_REQUIRED_FOR_ALL_ACCOUNTS = True` in `toms/settings/base.py` applies this to **every active
account**, not only Administrators and Commanders. Enrolment is keyed to the profile's Gmail address
(the authenticator entry reads `TOWEMS (name@gmail.com)`), and setup is refused outright if that
address is missing, so the enforcement is on the account, not on whatever the browser sends.
A confirmed enrolment returns **10 one-time recovery codes**; the person must store them.

Accounts that were already enrolled keep their existing TOTP secret and continue to be
challenged normally; only newly created entries carry the Gmail label.

**API tokens.** `/auth/token/` refuses to issue a JWT while either gate is open
(`403` with `profile_setup_required` or `mfa_setup_required`), because token clients never pass
through the session middleware. The smoke test therefore has to run as a **fully set-up
account** — profile complete and authenticator enrolled — not as a freshly flagged one.

**Chasing people afterwards.** User Management (`/admin/users`) has a **Setup** column
(`Profile refresh` / `Authenticator`) and a filter: *Setup outstanding*, *Profile refresh due*,
*Authenticator not enrolled*. The **Gmail** column shows the profile address and prints
`Enrolled as …` when the address actually enrolled differs — the typo / wrong-account case.
`Reset MFA` on a row clears the secret, its account label and its recovery codes; the person
re-enrols with their Gmail on next sign-in.

**Releasing the gate (exception path).** `require_profile_refresh --clear` stops asking accounts
that have not signed in yet; it does **not** restore old passwords, so anyone who already changed
theirs keeps the new one. Use it only for a deliberate deferral, not to unblock one person — for
an individual, reset their password through User Management instead.

### 5.2 Deploying an application release onto the droplet

Every command below runs as root; the application itself still runs as `www-data` under
`towems.service`.

```bash
# 1. Back up and restore-test first (section 2) — production is self-managed PostgreSQL
sudo systemctl start towems-backup.service
sudo systemctl start towems-restore-test.service
sudo journalctl -u towems-backup.service -u towems-restore-test.service -n 30 --no-pager

# 2. Check out the release as root (the tree is root-owned — see section 1.1)
sudo git -C /var/www/towems fetch --all --prune
sudo git -C /var/www/towems checkout --detach <SHA>
sudo git -C /var/www/towems rev-parse HEAD     # must print <SHA>
sudo git -C /var/www/towems status --short      # only the runtime dirs, no modified files
```

A detached checkout pins production to a reviewed SHA and leaves `main` free to move ahead with
documentation-only commits. Record the SHA in the table in section 1.1 as part of the release.

```bash
# 3. Dependencies — only when requirements.txt changed
/var/www/towems/venv/bin/pip install -r /var/www/towems/toms_backend/requirements.txt
/var/www/towems/venv/bin/python -c 'import pywebpush'    # messaging push

# 4. Migrate, from the service WorkingDirectory
cd /var/www/towems/toms_backend
DATABASE_URL=postgresql://…/mrm_system DJANGO_SETTINGS_MODULE=toms.settings.prod \
  /var/www/towems/venv/bin/python manage.py migrate --plan
DATABASE_URL=postgresql://…/mrm_system DJANGO_SETTINGS_MODULE=toms.settings.prod \
  /var/www/towems/venv/bin/python manage.py migrate

# 5. Run the release's post-migration gate (the 6929c7e block in section 5)

# 6. Restart and confirm the listeners
sudo systemctl restart towems.service
systemctl is-active towems.service
journalctl -u towems.service -n 20 --no-pager
ss -ltnp | grep 8001       # two gunicorn workers on 127.0.0.1:8001
```

```bash
# 7. Upload the SPA — build it on the workstation, there is no Node on the droplet
rsync -a --delete toms_frontend/dist/ \
  root@mrm-system.online:/var/www/towems/toms_frontend/dist.new/
```

Then on the droplet, swap it into place atomically and keep the previous build as the rollback
copy:

```bash
mv /var/www/towems/toms_frontend/dist /var/www/towems/toms_frontend/dist.prev-<SHA>
mv /var/www/towems/toms_frontend/dist.new /var/www/towems/toms_frontend/dist
```

```bash
# 8. Smoke — the served bundle, the service worker, and the signed-out API gate
curl -fsS https://mrm-system.online/ | grep -o 'assets/index-[A-Za-z0-9_-]*\.js'
curl -fsSI https://mrm-system.online/sw.js | grep -i content-type   # application/javascript
curl -s -o /dev/null -w '%{http_code}\n' \
  https://mrm-system.online/api/messaging/conversations/           # 403 while signed out
```

Rollback for an application release is the previous SHA plus the retained `dist.prev-<SHA>`
(section 7); the additive migrations do not need undoing.

### 5.3 VAPID keys for closed-tab browser push

Release `6929c7e` added browser push. `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY` and
`VAPID_SUBJECT` are read from `/var/www/towems/.env`
(`VAPID_SUBJECT` defaults to `mailto:no-reply@mrm-system.online`, which is valid here). Without
the pair the app still delivers in-app messages; only closed-tab notifications stop.

Generate and install the pair once per deployment:

```bash
# 1. Generate into a root-only file instead of the terminal — the private key is secret
umask 077 && /var/www/towems/venv/bin/python \
  /var/www/towems/toms_backend/manage.py vapid_keys > /root/vapid.env

# 2. Append it to the service environment without printing the values
cat /root/vapid.env >> /var/www/towems/.env

# 3. Confirm names only, and that ownership and mode survived the append
cut -d= -f1 /var/www/towems/.env | grep '^VAPID_'
stat -c '%U:%G %a' /var/www/towems/.env      # www-data:www-data 600
rm -f /root/vapid.env

# 4. Restart so gunicorn picks up the new environment
sudo systemctl restart towems.service
```

`VAPID_PRIVATE_KEY` is stored base64-wrapped PEM so it fits on one env line; the public key is
the base64url point the browser subscribes with. The generated key pair is the only copy — put
it in the password manager or secret store before deleting the temporary file.

**Never rotate the pair casually.** Every browser subscription is bound to the public key that
was live when it was created, so a new pair silently stops delivery to every existing device and
people have to re-enable notifications. Rotate only if the private key leaks, and tell users to
turn notifications off and on again afterwards.

### 5.4 Phone push for the Android app (Firebase Cloud Messaging)

The watcher's foreground service is a fallback, not a guarantee: Android 15 caps a long-running
service and OEM battery managers end one anyway, which is why alerts used to arrive only when
the app was opened. FCM is the transport WhatsApp and Viber use - Google holds one connection to
the phone and wakes the app - and it is free for unlimited messages. No Google Cloud billing
account is needed; the Firebase **Spark** plan is enough.

With no Firebase configuration the server behaves exactly as before (the watcher polls) and the
app builds and runs. Configure both halves to turn push on.

**1. Create the Firebase project (once).** In <https://console.firebase.google.com> create a
project, then *Add app -> Android* with package name `online.mrmsystem.towems` (the download is
`google-services.json`). Keep it in `toms_android/android/app/google-services.json`. That file is
not a secret - it ships inside every APK - but it belongs to this one Firebase project, so keep it
out of a shared build machine's other projects.

**2. Service account for the server.** *Project settings -> Service accounts -> Generate new
private key*. Save the JSON on the droplet outside the repository:

```bash
install -m 600 -o www-data -g www-data /root/fcm-service-account.json \
  /var/www/towems/fcm-service-account.json
```

**3. Environment.** Append these names (values are not secrets except the file itself) to
`/var/www/towems/.env`, then restart:

```
FCM_PROJECT_ID=<the Firebase project id, e.g. towems-prod>
FCM_CREDENTIALS_FILE=/var/www/towems/fcm-service-account.json
```

```bash
sudo systemctl restart towems.service
cut -d= -f1 /var/www/towems/.env | grep '^FCM_'   # names only
```

**4. Rebuild the APK** after `google-services.json` exists (section 4); the `google-services`
Gradle plugin is applied only when that file is present, and the app registers its FCM token
with the watcher's own device token - no new sign-in.

**5. Verify.** Pick an account that has installed the new APK:

```bash
cd /var/www/towems/toms_backend && set -a && . /var/www/towems/.env && set +a
/var/www/towems/venv/bin/python manage.py shell -c \
  "from apps.accounts.models import DeviceToken; from apps.ops import push; \
   row = DeviceToken.objects.exclude(fcm_token='').first(); \
   print(push.configured(), row and row.user.username, push.send_to_user(row.user_id, title='TOWEMS push test', body='If this buzzes with the app closed, push works.', path='/notifications'))"
```

It must print `True <username> 1`. Then close the app (swipe it away) and have someone send a
direct message: the phone must buzz with TOWEMS nowhere on screen.

**What still limits delivery.** A phone with no Google Play services (some China-market ROMs)
cannot receive FCM at all; the watcher remains its only transport. Swiping the app out of
recents is covered: every message carries a notification block, so Play Services draws the alert
on the same channel without starting TOWEMS. An app the user has *force-stopped* in Android
settings receives nothing until it is opened again - that is Android's rule for every app,
WhatsApp included. Push tokens are stored on the install's `DeviceToken` row, so signing out,
changing the password or an expired alert token stops push with the same rows.

## 6. Cutover checklist

1. [ ] Backup taken **and restore-tested**.
2. [ ] Schema drift diff is empty.
3. [ ] `migrate --plan` reviewed and approved by you.
4. [ ] `migrate` applied; `verify_legacy_mapping` reports zero mismatches and the 11 legacy row
       counts are identical to the pre-migration snapshot.
5. [ ] `import_admin_boundaries` run (`1730 created, 0 skipped`).
6. [ ] `require_profile_refresh --dry-run` count matches the active account count, then the real
       run executed — **before** nginx starts serving TOWEMS.
7. [ ] Django + gunicorn up on the internal port; `curl /api/health/` returns 200 (users still on
       Flask).
8. [ ] First-login gate rehearsed on the real host with a throw-away account: profile form blocks
       the rest of the app, the four steps clear it, Google Authenticator enrolment follows
       immediately, the entry shows the Gmail address, and recovery codes are returned.
9. [ ] nginx switched: SPA served, `/api/` proxied to gunicorn, deep links (`/risk/1`) resolve to
       `index.html`.
10.[ ] Smoke checks on the real host as a fully set-up account (the token route is gated):
       login with an existing account (Werkzeug hash path),
       dashboard counts match legacy numbers (2026-11-23: 489 active personnel of 491 accounts,
       1,653 MRM forms, 2,599 hazards, 8 units), risk list shows the same MRM total,
       create/edit one draft, MGRS conversion, CSV export.
10a.[ ] GIS: `/api/map/config/` lists the configured basemaps, AOR / GIS page renders the
       province layer, and `POST /api/aor/assign/` as a non-Administrator returns 403.
10b.[ ] User Management → *Setup outstanding* lists the accounts still to chase; the Gmail column
       matches the address each person enrolled.
11.[ ] Old Flask app still installed; on the 2026-11-23 cutover `mrm.service` was **stopped and
       disabled** to free memory (957 MB host). Re-enable and start it for a rollback.
12.[ ] Monitor logs for 24–48 h before decommissioning anything.

## 7. Rollback

**Level 1 — application rollback (minutes, no data loss).** Point nginx back at the Flask
app (or revert the proxy config). Django's additive tables are inert and harmless to Flask;
no schema change needs undoing. Flask still runs against the same database unchanged.

**First-login flags after a rollback.** Flask ignores `onboarding_required`,
`force_password_change` and the MFA tables, so everyone can keep using the old app with the
passwords they already set. If you return to Flask for more than a few days, release the queue
with `manage.py require_profile_refresh --clear`; it stops asking accounts that have not signed
in yet and does not restore old passwords. Users who already enrolled keep their authenticator
entry — nothing has to be undone for them.

**AOR data note.** AOR rows created from administrative boundaries store their own geometry, so
reverting the code leaves them fully usable as ordinary AOR polygons; `ops_administrative_area`
is only needed for search, preview and boundary layers.

**Level 2 — undo the additive migration (only if you decide the new tables must go).**

```bash
python manage.py migrate ops zero      # drops ONLY ops_* tables Django created
python manage.py migrate               # bookkeeping tables; legacy untouched
```

Run this only after confirming no production traffic depends on them.

**Level 3 — database restore (last resort).** Drop the target DB and
`pg_restore` from the verified dump (section 2). This loses everything written since the
backup — take a fresh dump immediately before cutover so the window is minutes, not days.

Rollback triggers: login failures, wrong dashboard/risk counts, any
`verify_legacy_mapping` mismatch, or performance regression versus the Flask baseline.

## 8. Ongoing operations

* **Backups:** rely on the managed database's automated backups *and* run
  `python tools/postgres_backup.py backup` nightly (or enable the supplied systemd timer).
  Run `python tools/postgres_backup.py restore-test` at least monthly and keep the passphrase
  or private key separate from the backup destination.
* **Logs:** Django logging level via `TOMS_LOG_LEVEL`; legacy `logs/` remains in place for
  the Flask app.
* **Secrets:** rotate `DJANGO_SECRET_KEY` and DB credentials through the DO secrets store;
  never commit `.env`.
* **Monitoring:** `GET /api/health/` is unauthenticated and safe as a probe.

---

## Offices & Sections catalogue migration (pending approval)

`command/migrations/0003_default_offices.py` adds `command_office_section.is_custom` and seeds the
twelve standard offices for every unit. It is **additive and non-destructive**: pre-catalogue office
rows are deactivated (not deleted) and their active personnel assignments are ended with a
timestamp, so assignment and mission history stays intact. The Operations Center office of each unit
is never touched.

Before running it:

1. Back up the database (`pg_dump -Fc`), and verify the dump restores into a scratch database.
2. Apply to a development/staging copy first:
   `python manage.py migrate command 0003` and then
   `python manage.py migrate --plan` to confirm nothing else is pending.
3. Check the result with `SELECT unit_id, code, name, is_active FROM command_office_section
   ORDER BY unit_id, code;` — twelve active catalogue rows per unit plus any active Operations
   Center, and the previous entries inactive.
4. Only then apply to production, during a maintenance window, and keep the dump until the
   acceptance checks pass.

Rollback: restore the dump. The migration's reverse step is intentionally a no-op (it cannot know
which deactivated rows to revive), so the database backup is the rollback path for the data step.
Reverting the code alone leaves the extra offices in place, which is harmless: the previous code
ignored `is_custom` and still lists offices by unit.
