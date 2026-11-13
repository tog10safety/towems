"""Inventory asset classes: add Aerospace Ground Equipment and seed the catalogue.

The register was empty everywhere - no categories existed, so the "Add item" form
had nothing to select and no materiel could be recorded at all. This migration
adds the AGE class and seeds one standard category per asset class the unit
accounts for.

Seeding is deliberately non-destructive: ``get_or_create`` keyed on the unique
``code``, so a category an officer has already created (or renamed) is never
overwritten, and re-running the migration cannot duplicate a row.

Vehicles are deliberately *not* seeded as an inventory category: the vehicle
register is the record of truth for a vehicle (it is what dispatch, trip
planning and tracking read), and a second row for the same truck would drift.
The inventory interface lists the vehicle register under the Vehicles class
instead.
"""

from django.db import migrations, models

KIND_CHOICES = [
    ("materiel", "Materiel"),
    ("equipment", "Equipment"),
    ("age", "Aerospace Ground Equipment (AGE)"),
    ("firearms", "Firearms"),
    ("ammunition", "Ammunition"),
    ("vehicle", "Vehicles"),
    ("communications", "Communications Equipment"),
    ("other", "Other Authorised Assets"),
]

#: ``code``, ``name``, ``kind``, ``is_sensitive``
CATALOGUE = (
    ("MAT", "Materiel", "materiel", False),
    ("EQP", "Equipment & Tools", "equipment", False),
    ("AGE", "Aerospace Ground Equipment (AGE)", "age", False),
    ("COMMS", "Communications Equipment", "communications", False),
    ("FA", "Firearms", "firearms", True),
    ("AMMO", "Ammunition", "ammunition", True),
)


def seed(apps, schema_editor):
    InventoryCategory = apps.get_model("ops", "InventoryCategory")
    for code, name, kind, is_sensitive in CATALOGUE:
        InventoryCategory.objects.get_or_create(
            code=code,
            defaults={"name": name, "kind": kind, "is_sensitive": is_sensitive},
        )


def unseed(apps, schema_editor):
    """Remove only the rows this migration created, and only while unused."""
    InventoryCategory = apps.get_model("ops", "InventoryCategory")
    InventoryItem = apps.get_model("ops", "InventoryItem")
    for code, _name, _kind, _sensitive in CATALOGUE:
        category = InventoryCategory.objects.filter(code=code).first()
        if category is None:
            continue
        if InventoryItem.objects.filter(category_id=category.id).exists():
            continue
        category.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("ops", "0010_vehicle_movement_tracking"),
    ]

    operations = [
        migrations.AlterField(
            model_name="inventorycategory",
            name="kind",
            field=models.CharField(
                choices=KIND_CHOICES, default="materiel", max_length=20
            ),
        ),
        migrations.RunPython(seed, unseed),
    ]
