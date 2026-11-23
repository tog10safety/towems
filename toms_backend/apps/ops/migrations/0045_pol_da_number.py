"""The delivery advice behind a bulk aviation receipt.

POL-01 bulk stock is one tank per airport, topped up by deliveries. Each
delivery is written to the ledger under its DA number; the tank keeps the
latest one so the register can name it too. Blank for everything else.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ops", "0044_pol_batch_dates"),
    ]

    operations = [
        migrations.AddField(
            model_name="inventoryitem",
            name="da_number",
            field=models.CharField(blank=True, default="", max_length=60),
        ),
    ]
