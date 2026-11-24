"""Move privately owned vehicles onto the plate the planner typed.

A POV was stored under a generated ``POV-0003`` reference with the plate kept
beside it, so every board, driver screen and order read the number instead of
the car. ``registration_number`` is meant to be the plate, so existing rows are
moved onto theirs. A plate that is blank, too long, or already registered to
another vehicle is left alone: no vehicle may take another's identifier.
"""

from django.db import migrations


def move_povs_onto_their_plate(apps, schema_editor):
    Vehicle = apps.get_model("ops", "Vehicle")

    taken = {}
    for vehicle_id, number in Vehicle.objects.values_list("id", "registration_number"):
        taken[(number or "").strip().casefold()] = vehicle_id

    for vehicle in Vehicle.objects.filter(is_pov=True).exclude(registration_no=""):
        plate = (vehicle.registration_no or "").strip()
        if not plate or len(plate) > 40:
            continue
        key = plate.casefold()
        holder = taken.get(key)
        if holder is not None and holder != vehicle.id:
            continue
        taken.pop((vehicle.registration_number or "").strip().casefold(), None)
        if plate == vehicle.registration_number:
            taken[key] = vehicle.id
            continue
        vehicle.registration_number = plate
        vehicle.save(update_fields=["registration_number"])
        taken[key] = vehicle.id


class Migration(migrations.Migration):
    dependencies = [
        ("ops", "0052_alter_mission_abort_destination"),
    ]

    operations = [
        # The old references were generated, so there is nothing to restore on
        # the way back; the plate is what the record should have said.
        migrations.RunPython(move_povs_onto_their_plate, migrations.RunPython.noop),
    ]
