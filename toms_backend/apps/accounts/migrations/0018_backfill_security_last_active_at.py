from django.db import migrations
from django.db.models import F


def backfill_security_clock(apps, schema_editor):
    """Seed the new inactivity clock from the existing presence stamp.

    The presence clock has been running since before this feature. Copying it
    once avoids either deactivating recently active accounts on the first scan
    or letting an old last_seen value hold a dormant account open forever.
    """
    UserProfile = apps.get_model("accounts", "UserProfile")
    UserProfile.objects.filter(
        security_last_active_at__isnull=True,
        last_seen__isnull=False,
    ).update(security_last_active_at=F("last_seen"))


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0017_mobiledevice_session_key"),
    ]

    operations = [
        migrations.RunPython(backfill_security_clock, migrations.RunPython.noop),
    ]