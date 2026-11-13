from django.db import migrations


def backfill(apps, schema_editor):
    """Fill the new make/model columns from the older combined ones.

    Rows written before the register was split stored "KIA" in ``make_model``
    and "KM450" in ``vehicle_type``. The display already falls back to those,
    but a row that is edited in the register must start with them filled.
    """
    Vehicle = apps.get_model("ops", "Vehicle")
    for vehicle in Vehicle.objects.all().only(
        "id", "make", "model", "make_model", "vehicle_type"
    ):
        changed = []
        if not vehicle.make and vehicle.make_model:
            vehicle.make = vehicle.make_model[:80]
            changed.append("make")
        if not vehicle.model and vehicle.vehicle_type:
            vehicle.model = vehicle.vehicle_type[:120]
            changed.append("model")
        if changed:
            vehicle.save(update_fields=changed)


class Migration(migrations.Migration):
    dependencies = [("ops", "0013_vehicle_make_vehicle_model_and_more")]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
