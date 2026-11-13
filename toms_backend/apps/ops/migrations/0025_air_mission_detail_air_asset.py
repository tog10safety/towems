import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ops", "0024_air_asset_identity_unique"),
    ]

    operations = [
        migrations.AddField(
            model_name="airmissiondetail",
            name="air_asset",
            field=models.ForeignKey(
                blank=True,
                help_text="Air Assets register entry linked to this flight.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="air_mission_details",
                to="ops.airasset",
            ),
        ),
    ]