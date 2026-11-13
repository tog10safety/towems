import django.db.models.deletion
from django.db import migrations, models


def copy_opcon_from_unit(apps, schema_editor):
    """Preserve an existing unit assignment as the operational-control unit."""
    AirAsset = apps.get_model("ops", "AirAsset")
    for asset in AirAsset.objects.exclude(unit_id__isnull=True).iterator():
        AirAsset.objects.filter(pk=asset.pk).update(opcon_unit_id=asset.unit_id)


class Migration(migrations.Migration):

    dependencies = [
        ("legacy", "__first__"),
        ("ops", "0022_air_asset_capability_lists"),
    ]

    operations = [
        migrations.AddField(
            model_name="airasset",
            name="assigned_unit",
            field=models.CharField(
                choices=[
                    ("5fw", "5th Fighter Wing"),
                    ("205th_thw", "205th Tactical Helicopter Wing"),
                    ("15sw", "15th Strike Wing"),
                    ("220aw", "220th Airlift Wing"),
                    ("250paw", "250th Presidential Airlift Wing"),
                    ("300aisw", "300th Air Intelligence Surveillance Wing"),
                    ("505sarg", "505th Search and Rescue Group"),
                ],
                default="5fw",
                max_length=50,
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="airasset",
            name="opcon_unit",
            field=models.ForeignKey(
                blank=True,
                db_column="opcon_unit_id",
                db_constraint=False,
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="+",
                to="legacy.unit",
            ),
        ),
        migrations.RunPython(copy_opcon_from_unit, migrations.RunPython.noop),
        migrations.RemoveIndex(
            model_name="airasset",
            name="ops_air_asset_unit_idx",
        ),
        migrations.RemoveField(
            model_name="airasset",
            name="unit",
        ),
        migrations.AlterField(
            model_name="airasset",
            name="opcon_unit",
            field=models.ForeignKey(
                blank=True,
                db_column="opcon_unit_id",
                db_constraint=False,
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="air_assets",
                to="legacy.unit",
            ),
        ),
        migrations.AddIndex(
            model_name="airasset",
            index=models.Index(fields=["opcon_unit"], name="ops_air_asset_opcon_idx"),
        ),
    ]
