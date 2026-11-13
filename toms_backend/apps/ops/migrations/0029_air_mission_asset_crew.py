from django.db import migrations, models


def mark_existing_leads(apps, schema_editor):
    AirMissionAsset = apps.get_model("ops", "AirMissionAsset")
    AirMissionAsset.objects.filter(is_lead=True).update(element_role="element_lead")


class Migration(migrations.Migration):

    dependencies = [
        ("ops", "0028_airasset_current_location_details"),
    ]

    operations = [
        migrations.AddField(
            model_name="airmissionasset",
            name="co_pilot",
            field=models.CharField(blank=True, default="", max_length=140),
        ),
        migrations.AddField(
            model_name="airmissionasset",
            name="element_role",
            field=models.CharField(
                choices=[
                    ("element_lead", "Element Lead"),
                    ("wing_man", "Wing Man"),
                ],
                default="wing_man",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="airmissionasset",
            name="pilot_in_command",
            field=models.CharField(blank=True, default="", max_length=140),
        ),
        migrations.RunPython(mark_existing_leads, migrations.RunPython.noop),
    ]
