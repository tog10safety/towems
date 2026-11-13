from django.db import migrations

OLD = "Other"
NEW = "Personnel"


def rename_other_designation(apps, schema_editor):
    # ``users`` is an unmanaged legacy table. A fresh test database creates it
    # after migrations run, so this data migration has nothing to rename there.
    if "users" not in schema_editor.connection.introspection.table_names():
        return
    User = apps.get_model("accounts", "User")
    User.objects.filter(designation__iexact=OLD).update(designation=NEW)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0006_userprofile_last_seen"),
    ]

    operations = [
        migrations.RunPython(rename_other_designation, migrations.RunPython.noop),
    ]
