"""Reset the office/section table to the standard catalogue.

The unit requested a clean slate: every existing office/section row is removed
and the twelve standard entries are seeded in their place.

What this touches, and what it does not:

* ``command_office_section`` rows are deleted, together with their
  ``command_section_assignment`` membership rows - those are the records being
  replaced. Nothing else is deleted.
* Missions, MRM forms, audit entries and every other table are untouched.
  ``MRMForm.office_section`` / ``ops_mission.office_section`` are plain text
  columns, so a deleted office leaves their stored label intact rather than
  dangling.
* The unit's Operations Center office is part of the removed set. A unit that
  needs one recreates it through the interface (Specify - Other plus the
  Operations Center function); until then that unit has no routing authority.

The catalogue is copied verbatim rather than imported so the migration keeps
behaving the same way if ``apps.command.offices`` changes later.
"""

from django.db import migrations

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


def reset(apps, schema_editor):
    OfficeSection = apps.get_model("command", "OfficeSection")
    SectionAssignment = apps.get_model("command", "SectionAssignment")
    Unit = apps.get_model("legacy", "Unit")

    # Legacy tables are unmanaged and, in a fresh test database, are created
    # after ``migrate`` runs. Nothing to seed there: the API seeds per unit.
    if "units" not in schema_editor.connection.introspection.table_names():
        return

    SectionAssignment.objects.all().delete()
    OfficeSection.objects.all().delete()

    for unit in Unit.objects.all().only("id"):
        for code, name, kind in CATALOGUE:
            OfficeSection.objects.create(
                unit_id=unit.id,
                code=code,
                name=name,
                kind=kind,
                is_custom=False,
                description="",
            )


def noop(apps, schema_editor):
    """Forward-only: the previous rows were removed, not archived."""


class Migration(migrations.Migration):

    dependencies = [
        ("command", "0003_default_offices"),
        ("legacy", "__first__"),
    ]

    operations = [
        migrations.RunPython(reset, noop),
    ]
