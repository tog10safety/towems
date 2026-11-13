from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


def kph_to_knots(apps, schema_editor):
    AirAsset = apps.get_model("ops", "AirAsset")
    for asset in AirAsset.objects.exclude(cruising_speed_knots=None):
        asset.cruising_speed_knots = round(asset.cruising_speed_knots / 1.852, 2)
        asset.save(update_fields=["cruising_speed_knots"])


def knots_to_kph(apps, schema_editor):
    AirAsset = apps.get_model("ops", "AirAsset")
    for asset in AirAsset.objects.exclude(cruising_speed_knots=None):
        asset.cruising_speed_knots = round(asset.cruising_speed_knots * 1.852, 2)
        asset.save(update_fields=["cruising_speed_knots"])


class Migration(migrations.Migration):
    dependencies = [
        ("ops", "0040_alter_airasset_ground_speed_help"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="airasset",
            name="ground_speed_kph",
        ),
        migrations.RenameField(
            model_name="airasset",
            old_name="minimum_cruise_speed_kph",
            new_name="cruising_speed_knots",
        ),
        migrations.AlterField(
            model_name="airasset",
            name="cruising_speed_knots",
            field=models.FloatField(
                blank=True,
                help_text="Cruising speed used when calculating flight ETAs.",
                null=True,
                validators=[
                    MinValueValidator(1),
                    MaxValueValidator(2000),
                ],
            ),
        ),
        migrations.RunPython(kph_to_knots, knots_to_kph),
    ]