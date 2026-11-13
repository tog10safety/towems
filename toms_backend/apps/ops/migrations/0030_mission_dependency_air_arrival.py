from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("ops", "0029_air_mission_asset_crew"),
    ]

    operations = [
        migrations.AddField(
            model_name="airmissiondetail",
            name="actual_arrival",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="airmissiondetail",
            name="rendezvous_ready_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mission",
            name="dependency_buffer_minutes",
            field=models.PositiveSmallIntegerField(default=30),
        ),
        migrations.AddField(
            model_name="mission",
            name="dependency_release",
            field=models.CharField(
                choices=[
                    ("completion", "Predecessor completed"),
                    ("rendezvous_ready", "Predecessor rendezvous ready"),
                ],
                default="completion",
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="mission",
            name="depends_on",
            field=models.ForeignKey(
                blank=True,
                help_text="An earlier mission that must finish before this one executes.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="dependent_missions",
                to="ops.mission",
            ),
        ),
    ]
