from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0007_rename_other_designation"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="is_system_owner",
            field=models.BooleanField(default=False),
        ),
        migrations.AddConstraint(
            model_name="userprofile",
            constraint=models.UniqueConstraint(
                condition=models.Q(is_system_owner=True),
                fields=("is_system_owner",),
                name="acct_profile_single_owner",
            ),
        ),
    ]
