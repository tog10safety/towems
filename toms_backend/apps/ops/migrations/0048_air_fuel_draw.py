from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
        ("ops", "0047_air_leg_engine_times"),
    ]

    operations = [
        migrations.CreateModel(
            name="AirFuelDraw",
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
                (
                    "source",
                    models.CharField(
                        choices=[("drum", "Drums"), ("bulk", "Bulk (litres)")],
                        max_length=10,
                    ),
                ),
                (
                    "quantity",
                    models.PositiveIntegerField(
                        help_text="Whole drums, or whole litres for bulk stock."
                    ),
                ),
                (
                    "litres",
                    models.DecimalField(
                        decimal_places=1,
                        default=0,
                        help_text="Litres the draw represents, for reporting only.",
                        max_digits=10,
                    ),
                ),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("posted", "Posted to stock"),
                            ("pending", "Awaiting stock"),
                            ("void", "Void"),
                        ],
                        default="posted",
                        max_length=10,
                    ),
                ),
                ("note", models.CharField(blank=True, default="", max_length=255)),
                (
                    "aircraft",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="fuel_draws",
                        to="ops.airmissionaircraft",
                    ),
                ),
                (
                    "item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="air_fuel_draws",
                        to="ops.inventoryitem",
                    ),
                ),
                (
                    "mission",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="air_fuel_draws",
                        to="ops.mission",
                    ),
                ),
                (
                    "recorded_by",
                    models.ForeignKey(
                        blank=True,
                        db_column="recorded_by",
                        db_constraint=False,
                        null=True,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="air_fuel_draws",
                        to="accounts.user",
                    ),
                ),
                (
                    "transaction",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="air_fuel_draws",
                        to="ops.inventorytransaction",
                    ),
                ),
            ],
            options={
                "db_table": "ops_air_fuel_draw",
                "ordering": ["aircraft__sequence", "id"],
            },
        ),
        migrations.AddConstraint(
            model_name="airfueldraw",
            constraint=models.UniqueConstraint(
                fields=("aircraft",), name="ops_air_fuel_draw_aircraft_uniq"
            ),
        ),
    ]
