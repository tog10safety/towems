from django.db import migrations, models


def copy_existing_selections(apps, schema_editor):
    """Carry any single selected value into the new list fields."""
    AirAsset = apps.get_model("ops", "AirAsset")
    for asset in AirAsset.objects.all().iterator():
        updates = {}
        if asset.firepower_capability:
            updates["firepower_capabilities"] = [asset.firepower_capability]
        if asset.munitions_category:
            updates["munitions_categories"] = [asset.munitions_category]
        if updates:
            AirAsset.objects.filter(pk=asset.pk).update(**updates)


class Migration(migrations.Migration):

    dependencies = [
        ("ops", "0021_air_assets"),
    ]

    operations = [
        migrations.AddField(
            model_name="airasset",
            name="firepower_capabilities",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Zero or more FIREPOWER_CAPABILITY_CHOICES keys.",
            ),
        ),
        migrations.AddField(
            model_name="airasset",
            name="munitions_categories",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Zero or more MUNITIONS_CATEGORY_CHOICES keys.",
            ),
        ),
        migrations.RunPython(copy_existing_selections, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="airasset",
            name="classification",
        ),
        migrations.RemoveField(
            model_name="airasset",
            name="firepower_capability",
        ),
        migrations.RemoveField(
            model_name="airasset",
            name="munitions_category",
        ),
    ]
