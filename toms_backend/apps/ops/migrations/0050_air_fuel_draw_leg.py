"""Fuel is recorded per itinerary leg instead of once per sortie.

A multi-leg sortie owes a POL-01 figure at each leg's shutdown, so every draw
now points at the leg it was recorded for and the uniqueness rule moves from
the airframe to the airframe-and-leg pair.

Existing sortie-wide draws are pinned to the leg their sortie ended on: the
last live leg that carries a shutdown, else the last live leg.
"""

from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models


def pin_existing_draws_to_their_last_leg(apps, schema_editor):
    AirFuelDraw = apps.get_model("ops", "AirFuelDraw")
    for draw in AirFuelDraw.objects.select_related("aircraft"):
        legs = list(
            draw.aircraft.itineraries.exclude(status="cancelled").order_by(
                "sequence", "id"
            )
        )
        if not legs:
            # Nothing left to attach the figure to; the ledger keeps the history.
            draw.delete()
            continue
        shut_down = [leg for leg in legs if leg.sd and not leg.overflight]
        draw.leg = shut_down[-1] if shut_down else legs[-1]
        draw.save(update_fields=["leg"])


class Migration(migrations.Migration):

    dependencies = [
        ("ops", "0049_air_fuel_no_consumption"),
    ]

    operations = [
        migrations.AddField(
            model_name="airfueldraw",
            name="leg",
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="fuel_draws",
                to="ops.airmissionitinerary",
            ),
        ),
        migrations.RunPython(
            pin_existing_draws_to_their_last_leg, migrations.RunPython.noop
        ),
        migrations.AlterField(
            model_name="airfueldraw",
            name="leg",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="fuel_draws",
                help_text="The itinerary leg this figure was recorded for.",
                to="ops.airmissionitinerary",
            ),
        ),
        migrations.AlterModelOptions(
            name="airfueldraw",
            options={"ordering": ["aircraft__sequence", "leg__sequence", "id"]},
        ),
        migrations.RemoveConstraint(
            model_name="airfueldraw",
            name="ops_air_fuel_draw_aircraft_uniq",
        ),
        migrations.AddConstraint(
            model_name="airfueldraw",
            constraint=models.UniqueConstraint(
                fields=("aircraft", "leg"), name="ops_air_fuel_draw_leg_uniq"
            ),
        ),
    ]
