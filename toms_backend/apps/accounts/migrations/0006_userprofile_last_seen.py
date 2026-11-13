"""Record each account's last authenticated request.

``UserProfile.last_seen`` is what the dashboard's "online now" rail reads. It is
nullable so existing profiles are simply "not seen yet" until their next
request, and additive so the legacy ``users`` table is untouched.

Nothing is backfilled: guessing a last-seen time from ``users.date_created`` or
the audit log would show people as online who are not.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0005_userprofile_afsc_primary_userprofile_afsc_secondary_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="last_seen",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
