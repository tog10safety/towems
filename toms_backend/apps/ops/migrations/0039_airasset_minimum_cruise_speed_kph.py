from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ops", "0038_airasset_ground_speed_kph"),
    ]

    operations = [
        migrations.AddField(
            model_name="airasset",
            name="minimum_cruise_speed_kph",
            field=models.FloatField(
                blank=True,
                help_text="Conservative cruise speed used when calculating flight ETAs.",
                null=True,
                validators=[
                    MinValueValidator(1),
                    MaxValueValidator(5000),
                ],
            ),
        ),
    ]