from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_alter_userprofile_options"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="theme_preference",
            field=models.CharField(
                choices=[
                    ("system", "System default"),
                    ("light", "Light"),
                    ("dark", "Dark"),
                ],
                default="system",
                max_length=10,
            ),
        ),
    ]
