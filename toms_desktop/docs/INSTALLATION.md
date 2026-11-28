# Installation Guide — TOWEMS Armory Desktop

## Requirements

- Windows 10 or Windows 11 (64-bit).
- No Python installation is required for the packaged installer.
- Approximately 250 MB free for the installed application and local data.
- Network access is needed only for synchronization.
- TOWEMS backend with the Armory sync migration applied for synchronization.

## Install from the Setup executable

1. Copy `TOWEMS-Armory-Setup.exe` to the workstation.
2. Double-click it. Windows may show a SmartScreen warning because the installer
   is not code-signed. Use **More info → Run anyway** only after verifying the
   file came from the TOWEMS release package.
3. Choose whether to create a desktop shortcut and Start Menu shortcut.
4. Optionally enable **Install the Sunday 23:00 synchronization task**. This
   creates a per-user Windows task that runs `TOWEMS-Armory.exe --sync-only`.
   It uses the saved TOWEMS refresh token and is useful when the desktop window
   is normally closed on Sunday night.
5. Choose the installation folder. The default is:

```text
%LOCALAPPDATA%\Programs\TOWEMS Armory
```

6. Select **Install**. The installer copies the executable and documentation,
   creates the shortcuts, writes a per-user Add/Remove Programs entry, and
   creates an uninstall script.
7. Start TOWEMS Armory from the shortcut.

Administrator rights are not required for the default per-user installation.

## First run

1. Create the local administrator and local armory unit.
2. Sign in.
3. Open **Synchronization → Configure** and set:
   - API address, for example `https://towems.example.org/api`;
   - TOWEMS armory-manager account username;
   - automatic sync and weekly schedule (default Sunday 23:00).
4. Open **SYNC NOW**, enter the TOWEMS password once, and confirm the queue and
   history show success.

The password is not stored. A refresh token is placed in Windows Credential
Manager after a successful login. If the token expires after seven days of
inactivity, the next manual sync asks for the password again.

## Upgrade

Run the newer setup executable and install to the same directory. Local data is
stored under:

```text
%LOCALAPPDATA%\TOWEMS Armory Management
```

and is not overwritten by application upgrades. Back up before upgrading using
**System Settings → BACKUP DATABASE NOW**.

## Scheduled synchronization

The installer can create:

```text
Task name: TOWEMS Armory Sync
Trigger:   Weekly, Sunday, 23:00
Action:    "<install folder>\TOWEMS-Armory.exe" --sync-only
```

The task runs as the signed-in user, in the user's context, so it can read the
refresh token from Credential Manager. The result is written to:

```text
%LOCALAPPDATA%\TOWEMS Armory Management\logs\sync.log
```

You can also create or change the task manually in Task Scheduler. The desktop's
in-process scheduler still runs when the application is open; both paths use the
same queue and are idempotent.

## Silent or scripted installation

The custom setup supports these optional switches:

```text
/S /DIR="D:\Apps\TOWEMS Armory" /NODESKTOP /NOSCHEDULE
```

`/S` installs without the wizard. The default shortcut and schedule choices are
used unless the switches disable them. The installer is per-user; run it as the
armory user, not as an administrator.

## Uninstall

Use **Settings → Apps → Installed apps → TOWEMS Armory → Uninstall**, or the
Start Menu **Uninstall TOWEMS Armory** shortcut. The uninstaller removes the
program files and shortcuts. It deliberately does not delete the local data
folder, so armory records and backups remain available for lawful retention or
recovery. Delete that folder separately only when the records have been
archived/approved for destruction.

## Firewall and proxy

The desktop makes outbound HTTPS requests to the configured TOWEMS API. No
inbound port is opened. If a proxy is required, configure it at the operating
system level; the application uses the standard Windows proxy settings through
`requests`.

## Code signing

The development build is not code-signed. Production distribution should sign
the executable and installer with the organisation's Windows code-signing
certificate and verify the signature before deployment.

## Rollback

1. Close the application.
2. Restore the previous executable or reinstall the previous setup package.
3. If the database schema changed, restore the matching backup from
   **System Settings** (or the backup file made before the upgrade).
4. Start the application and verify the Dashboard and Audit trail.

Keep a copy of the release notes and database backup with each deployment.
