from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ops", "0026_air_mission_configuration"),
    ]

    operations = [
        migrations.AddField(
            model_name="airasset",
            name="current_location",
            field=models.CharField(
                blank=True,
                default="",
                help_text="MGRS grid where the aircraft is currently located.",
                max_length=40,
            ),
        ),
    ]
