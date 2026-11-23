"""A store's own map point, for stock moved off its original site.

Fuel is pinned from the airfield its location names, or from the owning unit's
marker. Once drums are transferred to another operational-mapping location -
a friendly troop position, an air strip or another unit - neither of those
describes where the drums now stand, so the store keeps its own point.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ops", "0045_pol_da_number"),
    ]

    operations = [
        migrations.AddField(
            model_name="inventoryitem",
            name="location_latitude",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="inventoryitem",
            name="location_longitude",
            field=models.FloatField(blank=True, null=True),
        ),
    ]
