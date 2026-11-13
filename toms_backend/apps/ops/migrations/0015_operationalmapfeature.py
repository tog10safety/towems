import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ops", "0014_backfill_vehicle_make_model"),
    ]

    operations = [
        migrations.CreateModel(
            name="OperationalMapFeature",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=160)),
                (
                    "feature_type",
                    models.CharField(
                        choices=[
                            ("friendly_troop", "Friendly troops"),
                            ("frequent_location", "Frequently searched location"),
                            ("operational_area", "Operational area"),
                        ],
                        default="friendly_troop",
                        max_length=30,
                    ),
                ),
                ("description", models.TextField(blank=True, default="")),
                (
                    "geometry",
                    models.JSONField(
                        help_text=(
                            "GeoJSON Point or Polygon (WGS84). Point for troops and "
                            "places, polygon for areas."
                        )
                    ),
                ),
                ("color", models.CharField(default="#4f9cf9", max_length=20)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        db_column="created_by",
                        db_constraint=False,
                        null=True,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="created_operational_map_features",
                        to="accounts.user",
                    ),
                ),
                (
                    "unit",
                    models.ForeignKey(
                        blank=True,
                        db_column="unit_id",
                        db_constraint=False,
                        null=True,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="operational_map_features",
                        to="legacy.unit",
                    ),
                ),
            ],
            options={
                "db_table": "ops_operational_map_feature",
                "ordering": ["feature_type", "name"],
                "indexes": [
                    models.Index(fields=["feature_type"], name="ops_opmap_type_idx")
                ],
            },
        ),
    ]
