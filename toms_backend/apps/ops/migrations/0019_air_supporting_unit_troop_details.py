import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ops", "0018_airfield"),
    ]

    operations = [
        migrations.AddField(
            model_name="operationalmapfeature",
            name="service_category",
            field=models.CharField(
                blank=True,
                choices=[
                    ("army", "Philippine Army"),
                    ("navy", "Philippine Navy"),
                    ("pnp", "Philippine National Police"),
                    ("coast_guard", "Coast Guard"),
                ],
                default="",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="operationalmapfeature",
            name="unit_category",
            field=models.CharField(
                blank=True,
                choices=[
                    ("division", "Division"),
                    ("brigade", "Brigade"),
                    ("battalion", "Battalion"),
                    ("company", "Company"),
                ],
                default="",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="operationalmapfeature",
            name="acronym",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
        migrations.AddField(
            model_name="operationalmapfeature",
            name="address",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="operationalmapfeature",
            name="mgrs",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="operationalmapfeature",
            name="contact_number",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
        migrations.AddField(
            model_name="airmissiondetail",
            name="supporting_unit",
            field=models.ForeignKey(
                blank=True,
                help_text="Friendly unit supported by this air mission.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="supported_missions",
                to="ops.operationalmapfeature",
            ),
        ),
    ]
