from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ops", "0037_airmissionitinerary_point_role"),
    ]

    operations = [
        migrations.AddField(
            model_name="airasset",
            name="ground_speed_kph",
            field=models.FloatField(
                blank=True,
                help_text="Planned ground speed used to calculate flight ETAs.",
                null=True,
                validators=[
                    MinValueValidator(1),
                    MaxValueValidator(5000),
                ],
            ),
        ),
    ]