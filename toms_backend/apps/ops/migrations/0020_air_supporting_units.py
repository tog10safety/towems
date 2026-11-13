"""An air mission may support more than one friendly unit.

The single ``supporting_unit`` foreign key becomes ``supporting_units``. Existing
flights keep their one choice by copying it into the new join table before the
old column is dropped.
"""

from django.db import migrations, models


def copy_supporting_units(apps, schema_editor):
    AirMissionDetail = apps.get_model("ops", "AirMissionDetail")
    for detail in AirMissionDetail.objects.exclude(supporting_unit__isnull=True):
        detail.supporting_units.add(detail.supporting_unit_id)


class Migration(migrations.Migration):

    dependencies = [
        ("ops", "0019_air_supporting_unit_troop_details"),
    ]

    operations = [
        migrations.AddField(
            model_name="airmissiondetail",
            name="supporting_units",
            field=models.ManyToManyField(
                blank=True,
                help_text="Friendly units supported by this air mission.",
                related_name="supported_missions",
                to="ops.operationalmapfeature",
            ),
        ),
        migrations.RunPython(copy_supporting_units, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="airmissiondetail",
            name="supporting_unit",
        ),
    ]
