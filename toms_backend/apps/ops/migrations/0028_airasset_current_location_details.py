from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ops", "0027_airasset_current_location"),
    ]

    operations = [
        migrations.AlterField(
            model_name="airasset",
            name="current_location",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Operational-mapping location: name, place and MGRS grid.",
                max_length=255,
            ),
        ),
    ]
