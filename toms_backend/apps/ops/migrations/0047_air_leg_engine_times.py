from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ops", "0046_inventory_item_location_point"),
    ]

    operations = [
        migrations.AddField(
            model_name="airmissionitinerary",
            name="se",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="airmissionitinerary",
            name="sd",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
