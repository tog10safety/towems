"""Jet A1 fuel monitoring: drums and bulk inside the inventory register.

Fuel stays part of the same register as every other accountable asset, so a
receipt or an issue already writes a ledger entry and the existing unit scoping
and capabilities apply unchanged. Three optional columns say how the fuel is
held:

* ``fuel_grade`` - blank for everything that is not fuel, so materiel rows
  behave exactly as before;
* ``fuel_form`` - drums or bulk;
* ``drum_capacity_litres`` - litres in one drum, for drum stock only. Bulk
  stock is counted in litres and leaves it empty.

The ``fuel`` asset class is added and one ``JET-A1`` category is seeded, keyed
on the unique code, following the non-destructive seeding of 0011: an officer
may rename it, and re-running the migration never duplicates it.
"""

from django.db import migrations, models

KIND_CHOICES = [
    ("materiel", "Materiel"),
    ("equipment", "Equipment"),
    ("age", "Aerospace Ground Equipment (AGE)"),
    ("firearms", "Firearms"),
    ("ammunition", "Ammunition"),
    ("fuel", "Fuel & Lubricants"),
    ("vehicle", "Vehicles"),
    ("communications", "Communications Equipment"),
    ("other", "Other Authorised Assets"),
]

FUEL_GRADE_CHOICES = [
    ("", "Not fuel"),
    ("jet_a1", "Jet A1"),
]

FUEL_FORM_CHOICES = [
    ("", "Not fuel"),
    ("drum", "Drums"),
    ("bulk", "Bulk tank (litres)"),
]


def seed(apps, schema_editor):
    InventoryCategory = apps.get_model("ops", "InventoryCategory")
    InventoryCategory.objects.get_or_create(
        code="JET-A1",
        defaults={"name": "Jet A1 Fuel", "kind": "fuel", "is_sensitive": False},
    )


def unseed(apps, schema_editor):
    """Remove the seeded category only while nothing is filed under it."""
    InventoryCategory = apps.get_model("ops", "InventoryCategory")
    InventoryItem = apps.get_model("ops", "InventoryItem")
    category = InventoryCategory.objects.filter(code="JET-A1").first()
    if category is None:
        return
    if InventoryItem.objects.filter(category_id=category.id).exists():
        return
    category.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("ops", "0041_airasset_cruising_speed_knots"),
    ]

    operations = [
        migrations.AlterField(
            model_name="inventorycategory",
            name="kind",
            field=models.CharField(
                choices=KIND_CHOICES, default="materiel", max_length=20
            ),
        ),
        migrations.AddField(
            model_name="inventoryitem",
            name="fuel_grade",
            field=models.CharField(
                blank=True,
                choices=FUEL_GRADE_CHOICES,
                default="",
                max_length=40,
            ),
        ),
        migrations.AddField(
            model_name="inventoryitem",
            name="fuel_form",
            field=models.CharField(
                blank=True,
                choices=FUEL_FORM_CHOICES,
                default="",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="inventoryitem",
            name="drum_capacity_litres",
            field=models.PositiveIntegerField(
                blank=True,
                help_text=(
                    "Litres in one drum. Bulk stock is counted in litres and "
                    "leaves this empty."
                ),
                null=True,
            ),
        ),
        migrations.RunPython(seed, unseed),
    ]
