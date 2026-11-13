"""Introduce the standard office catalogue for every unit.

Additive only:

* ``command_office_section.is_custom`` is a new nullable-safe boolean with a
  default, so no existing row needs rewriting.
* The data step creates the twelve standard entries per unit (matched on
  ``(unit, code)`` so it is idempotent) and *deactivates* - never deletes - any
  entry that predates the catalogue. Active personnel assignments to those
  entries are ended with a timestamp, preserving history. The unit's Operations
  Center office is left untouched because it carries the unit's routing
  authority and is not part of the staff catalogue.

The catalogue is copied verbatim rather than imported from
``apps.command.offices`` so this migration keeps behaving the same way even if
the catalogue changes later.
"""

from django.db import migrations, models

CATALOGUE = (
    ("A1", "Personnel / Administration", "office"),
    ("A2", "Intelligence", "office"),
    ("A3", "Operations", "office"),
    ("A4", "Logistics", "office"),
    ("A5", "Plans", "office"),
    ("A6", "Communications, Electronics and Information Systems (CEIS)", "office"),
    ("A7", "Civil-Military Operations (CMO)", "office"),
    ("A8", "Training", "office"),
    ("A9", "Reservist Affairs", "office"),
    ("A10", "Finance", "office"),
    ("SAFETY", "Safety Office", "office"),
    ("OTHER", "Specify – Other Office/Section", "section"),
)
CODES = tuple(code for code, _, _ in CATALOGUE)


def seed(apps, schema_editor):
    OfficeSection = apps.get_model("command", "OfficeSection")
    SectionAssignment = apps.get_model("command", "SectionAssignment")
    Unit = apps.get_model("legacy", "Unit")
    from django.utils import timezone

    # The legacy tables are unmanaged and, in a fresh test database, are created
    # after ``migrate`` runs. Nothing to seed there: the API seeds per unit.
    if "units" not in schema_editor.connection.introspection.table_names():
        return

    now = timezone.now()
    for unit in Unit.objects.all().only("id"):
        for code, name, kind in CATALOGUE:
            OfficeSection.objects.get_or_create(
                unit_id=unit.id,
                code=code,
                defaults={
                    "name": name,
                    "kind": kind,
                    "is_custom": False,
                    "description": "",
                },
            )

        stale = (
            OfficeSection.objects.filter(unit_id=unit.id, is_active=True)
            .exclude(code__in=CODES)
            .exclude(function="operations_center")
        )
        stale_ids = list(stale.values_list("id", flat=True))
        if not stale_ids:
            continue
        SectionAssignment.objects.filter(section_id__in=stale_ids, is_active=True).update(
            is_active=False, ended_at=now
        )
        stale.update(is_active=False)


def unseed(apps, schema_editor):
    """No-op: the previous office rows were deactivated, not removed."""


class Migration(migrations.Migration):

    dependencies = [
        ("command", "0002_officesection_function_and_more"),
        ("legacy", "__first__"),
    ]

    operations = [
        migrations.AddField(
            model_name="officesection",
            name="is_custom",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(seed, unseed),
    ]
