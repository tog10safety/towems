"""POL batch dates: issued, tested (aviation only) and expiry.

A store of fuel or oil has a shelf life, so the batch it came from is dated.
The columns are optional and blank for everything that is not POL.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ops", "0043_pol_categories"),
    ]

    operations = [
        migrations.AddField(
            model_name="inventoryitem",
            name="date_issued",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="inventoryitem",
            name="testing_date",
            field=models.DateField(
                blank=True,
                help_text="When the batch was last tested - aviation fuel only.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="inventoryitem",
            name="expiry_date",
            field=models.DateField(blank=True, null=True),
        ),
    ]
