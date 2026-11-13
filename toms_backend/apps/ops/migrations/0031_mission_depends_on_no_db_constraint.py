from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("ops", "0030_mission_dependency_air_arrival"),
    ]

    operations = [
        migrations.AlterField(
            model_name="mission",
            name="depends_on",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                help_text="An earlier mission that must finish before this one executes.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="dependent_missions",
                to="ops.mission",
            ),
        ),
    ]
