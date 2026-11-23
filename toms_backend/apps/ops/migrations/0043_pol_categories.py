"""The POL catalogue: nine coded categories, and the grades two of them carry.

The single ``JET-A1`` category seeded by 0042 becomes ``POL-01 Aviation Fuel``,
so stock already filed under it stays where it is; the other eight categories
are added keyed on their unique code, following the non-destructive seeding of
0011 - an officer may rename one, and re-running the migration never duplicates
it.

``fuel_grade`` gains AVGas, diesel and gasoline alongside Jet A1. Which grade a
POL row may carry is decided by its category code in ``apps.ops.fuel``: POL-01
is AVGas or Jet A1, POL-02 is diesel or gasoline, and the remaining categories
carry no grade at all.
"""

from django.db import migrations, models

FUEL_GRADE_CHOICES = [
    ("", "Not graded"),
    ("avgas", "AVGas"),
    ("jet_a1", "Jet A1"),
    ("diesel", "Diesel"),
    ("gasoline", "Gasoline"),
]

#: Code and name, in the order the register should list them.
POL_CATEGORIES = [
    ("POL-01", "Aviation Fuel"),
    ("POL-02", "Automotive Fuel"),
    ("POL-03", "Engine Lubricants"),
    ("POL-04", "Hydraulic Fluids"),
    ("POL-05", "Greases"),
    ("POL-06", "Transmission and Gear Oils"),
    ("POL-07", "Coolants"),
    ("POL-08", "Special-Purpose Fluids"),
    ("POL-09", "Cleaning and Solvents"),
]


def seed(apps, schema_editor):
    InventoryCategory = apps.get_model("ops", "InventoryCategory")
    # The Jet A1 category becomes the aviation fuel category, keeping the items
    # that are already filed under it.
    legacy = InventoryCategory.objects.filter(code="JET-A1").first()
    if legacy is not None and not InventoryCategory.objects.filter(code="POL-01").exists():
        legacy.code = "POL-01"
        legacy.name = "Aviation Fuel"
        legacy.kind = "fuel"
        legacy.save(update_fields=["code", "name", "kind"])

    for code, name in POL_CATEGORIES:
        InventoryCategory.objects.get_or_create(
            code=code,
            defaults={"name": name, "kind": "fuel", "is_sensitive": False},
        )


def unseed(apps, schema_editor):
    """Remove the seeded categories only while nothing is filed under them."""
    InventoryCategory = apps.get_model("ops", "InventoryCategory")
    InventoryItem = apps.get_model("ops", "InventoryItem")
    for code, _name in POL_CATEGORIES:
        category = InventoryCategory.objects.filter(code=code).first()
        if category is None:
            continue
        if InventoryItem.objects.filter(category_id=category.id).exists():
            continue
        category.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("ops", "0042_inventory_fuel_monitoring"),
    ]

    operations = [
        migrations.AlterField(
            model_name="inventoryitem",
            name="fuel_grade",
            field=models.CharField(
                blank=True,
                choices=FUEL_GRADE_CHOICES,
                default="",
                max_length=40,
            ),
        ),
        migrations.RunPython(seed, unseed),
    ]
