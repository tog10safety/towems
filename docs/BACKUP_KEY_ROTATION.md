# Backup GPG key rotation runbook

Applies to the public-key recipient configured in `TOMS_BACKUP_GPG_RECIPIENT` for
`tools/postgres_backup.py`. The backup host needs only the public key; the private key
belongs on offline media or a dedicated restore host.

This runbook rotates the recipient without making existing backups unreadable. Rotate at
least 90 days before the key expires, annually, or immediately after a suspected compromise.
It does not cover the symmetric-passphrase mode (`TOMS_BACKUP_GPG_PASSPHRASE_FILE`); rotate
that passphrase separately.

## 1. Safety rules

* Never delete the old private key during the rotation. Old backups still need it.
* Keep the old public key on the backup host until the rotation is verified, in case you
  need to roll back.
* Use the full GPG fingerprint, not an email address, in `TOMS_BACKUP_GPG_RECIPIENT`.
* Do not put a private key, passphrase, or `backup.env` in the repository.
* Do not edit backup manifests or checksum files by hand.
* Do not destroy the old private key until every backup encrypted to it has left both local
  and off-site retention.

## 2. Record the current state

On the backup host, read the configured recipient without printing the rest of the secret
environment file:

```bash
sudo grep '^TOMS_BACKUP_GPG_RECIPIENT=' /etc/towems/backup.env
```

Resolve it to a fingerprint and record it in the change record:

```bash
OLD_FPR="<old fingerprint from the line above>"
gpg --list-keys --with-fingerprint "$OLD_FPR"
```

Record:

* old fingerprint and key ID;
* old key expiration date;
* date and name of the newest backup made with the old key;
* current local and off-site retention settings;
* the planned **retire-after** date (see section 7).

## 3. Generate the replacement key offline

Generate the new key on an offline administration workstation, not on the backup host. Use a
dedicated temporary GPG home so the production keyring is not changed:

```bash
umask 077
export GNUPGHOME="$(mktemp -d /secure/towems-keygen.XXXXXX)"
chmod 700 "$GNUPGHOME"

NEW_UID="TOWEMS Production Backup 2027 <towems-backup@example.org>"
gpg --quick-generate-key "$NEW_UID" rsa4096 cert,sign 2y
NEW_FPR="$(gpg --list-keys --with-colons "$NEW_UID" | awk -F: '$1=="fpr" {print $10; exit}')"
gpg --quick-add-key "$NEW_FPR" rsa4096 encr 2y
```

Use a strong passphrase when GPG prompts. Do not pass the passphrase on the command line or
store it beside the key.

Export the public and private keys, and verify the fingerprint and encryption subkey:

```bash
gpg --armor --export "$NEW_FPR" > "towems-backup-2027-public.asc"
gpg --armor --export-secret-keys "$NEW_FPR" > "towems-backup-2027-private.asc"
chmod 600 towems-backup-2027-public.asc towems-backup-2027-private.asc

gpg --show-keys --with-fingerprint --with-subkey-fingerprint \
  towems-backup-2027-public.asc
gpg --list-secret-keys --with-fingerprint "$NEW_FPR"
```

The public listing must contain the same full fingerprint and an encryption-capable subkey
(`[E]`). Store the private-key file on offline media in the key escrow location, and record
the fingerprint in the key inventory. Remove the temporary GPG home after the exports have
been copied to their secure destinations.

## 4. Distribute only the public key

Copy the public key to the backup host, then import it as the service account:

```bash
sudo -u towems gpg --import /secure/transfer/towems-backup-2027-public.asc
sudo -u towems gpg --list-keys --with-fingerprint "$NEW_FPR"
sudo -u towems gpg --list-secret-keys "$NEW_FPR"   # must show no secret key
```

Confirm the backup host can encrypt to the new recipient before changing production
configuration:

```bash
printf 'TOWEMS backup key rotation test\n' |
  sudo -u towems gpg --batch --yes --trust-model always \
    --encrypt --recipient "$NEW_FPR" > /tmp/towems-key-test.gpg
sudo -u towems gpg --list-packets /tmp/towems-key-test.gpg
rm -f /tmp/towems-key-test.gpg
```

The packet listing should name the new key ID. The backup script uses
`--trust-model always`, so no ownertrust edit is required.

## 5. Switch the production recipient

Record the old fingerprint in the change record first. Then edit the root-owned environment
file:

```bash
sudoedit /etc/towems/backup.env
```

Set the full fingerprint:

```dotenv
TOMS_BACKUP_GPG_RECIPIENT=<NEW_FINGERPRINT>
```

Run a manual backup and verify it completed:

```bash
sudo systemctl start towems-backup.service
sudo systemctl show -p Result --value towems-backup.service
sudo journalctl -u towems-backup.service -n 100 --no-pager
sudo -u towems /srv/toms/.venv-toms/bin/python \
  /srv/toms/tools/postgres_backup.py list
```

The newest encrypted backup should have a manifest and checksum sidecar. If
`TOMS_BACKUP_OFFSITE_DIR` is configured, confirm the new backup also reached the off-site
copy; the health check verifies that copy.

## 6. Prove both keys restore

Do this on the isolated restore host or in a temporary GPG home with the private key. Keep
the private key out of the backup service account's long-lived keyring.

### 6.1 New key

Import the new private key into a temporary GPG home and run the drill against the newest
backup:

```bash
export GNUPGHOME="$(mktemp -d /secure/towems-restore-new.XXXXXX)"
chmod 700 "$GNUPGHOME"
gpg --import /secure/offline/towems-backup-2027-private.asc
gpg --list-secret-keys "$NEW_FPR"

export TOMS_BACKUP_GPG_RECIPIENT="$NEW_FPR"
export TOMS_BACKUP_DIR=/srv/toms/_backups/postgres
python tools/postgres_backup.py restore-test
```

The drill must decrypt, restore, compare the key-table counts, and drop the scratch database.
It writes `restore-drill.json` only after those checks pass. Use the same database and
restore-admin environment as the normal monthly drill; do not put those credentials in this
runbook or in a shell history file.

### 6.2 Old key

Copy the newest old-key backup plus its `.json` and `.sha256` sidecars to a temporary
directory, then run a second drill with an old-key-only GPG home:

```bash
export GNUPGHOME="$(mktemp -d /secure/towems-restore-old.XXXXXX)"
chmod 700 "$GNUPGHOME"
gpg --import /secure/offline/towems-backup-<old>-private.asc

export TOMS_BACKUP_GPG_RECIPIENT="$NEW_FPR"   # only satisfies config validation
python tools/postgres_backup.py restore-test \
  --backup-dir /secure/restore-old \
  --file /secure/restore-old/<old-backup>.dump.gpg
```

The old key must still decrypt and restore the old backup. Remove both temporary GPG homes
when finished.

## 7. Retire the old key

The manifest does not record which recipient encrypted a backup, so use the rotation
timestamp as the cutoff. Every `.dump.gpg` created before that cutoff was encrypted to the
old key.

Do not remove the old private key until:

* no backup older than the cutoff remains in the local backup directory;
* no such backup remains at `TOMS_BACKUP_OFFSITE_DIR` or in the upload destination;
* the off-site lifecycle/retention policy has also removed those copies; and
* the new key has passed a real restore drill.

With the defaults (`TOMS_BACKUP_RETENTION_DAILY=30`,
`TOMS_BACKUP_RETENTION_MONTHLY=12`), a monthly backup can remain for almost 12 months after
the last old-key backup. Plan for **at least 13 months** after the rotation, and longer if
the off-site destination has a longer retention period. Set a calendar reminder for the
retire-after date.

After that date, with change approval:

```bash
# Backup host: remove the old public key only after rollback is no longer needed.
sudo -u towems gpg --delete-keys "$OLD_FPR"

# Offline escrow: destroy the old private key and update the key inventory.
```

Do not delete the old private key while any old-key backup still exists; it is the only way
to restore that backup.

## 8. Emergency rotation after compromise

If the old private key is lost, stolen, or suspected compromised:

1. Treat every backup encrypted to the old key as potentially exposed.
2. Generate and distribute a new key as above, but do not wait for the normal overlap.
3. Take a new backup with the new recipient and verify a restore immediately.
4. Preserve the old private key and relevant logs for the incident investigation; do not
   destroy evidence.
5. Revoke the old public key if a revocation certificate is available, and notify the
   security owner.
6. Do not hand-edit old backup manifests or checksums. If retained old backups must be
   re-encrypted, use a tested copy-and-verify procedure and re-run restore drills; the
   current backup tool does not re-encrypt files in place.

## 9. Rollback

If the new key cannot create or restore a backup:

1. Stop relying on the new key.
2. Set `TOMS_BACKUP_GPG_RECIPIENT` back to the old fingerprint.
3. Confirm the old public key is still on the backup host.
4. Run a manual backup and confirm its manifest, checksum and off-site copy.
5. Investigate the new key before retrying the rotation.

Keep any backup created with the new key until the incident is understood. Do not delete it
merely because the rotation was rolled back.

## 10. Sign-off checklist

- [ ] New key is RSA 4096 with an encryption-capable subkey and a recorded expiry date.
- [ ] Public key fingerprint matches the private-key fingerprint.
- [ ] Private key is stored offline; the backup host has no secret key.
- [ ] New public key is imported for the `towems` account.
- [ ] `TOMS_BACKUP_GPG_RECIPIENT` contains the full new fingerprint.
- [ ] Manual backup succeeded and the off-site copy exists.
- [ ] New-key restore drill passed.
- [ ] Old-key restore drill passed.
- [ ] `tools/backup_health.py` reports OK after the new drill.
- [ ] Old fingerprint, new fingerprint, rotation timestamp, and retire-after date are in the
      change record.
- [ ] Old public key remains available until rollback is no longer needed.
- [ ] Old private key remains in escrow until all old-key backups have expired.
- [ ] Old key was destroyed only after the retain-until date and with approval.
