from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ops", "0023_air_asset_assignment"),
    ]

    operations = [
        migrations.AlterField(
            model_name="airasset",
            name="tail_number",
            field=models.CharField(max_length=60),
        ),
        migrations.AddConstraint(
            model_name="airasset",
            constraint=models.UniqueConstraint(
                fields=["aircraft_type", "model", "tail_number"],
                name="ops_air_asset_identity_uniq",
            ),
        ),
    ]
