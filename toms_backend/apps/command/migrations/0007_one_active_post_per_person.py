from django.db import migrations, models
from django.utils import timezone


def keep_one_post_per_person(apps, schema_editor):
    """Reconcile existing rows before the constraint goes on.

    A person may already hold the same post in two units (the exact case the
    rule now forbids). The appointment in the person's own unit is kept; the
    others are ended, never deleted, so the history survives and the migration
    cannot fail on live data.
    """
    UnitAppointment = apps.get_model("command", "UnitAppointment")
    User = apps.get_model("accounts", "User")

    duplicates = (
        UnitAppointment.objects.filter(is_active=True)
        .values("user_id", "role")
        .annotate(total=models.Count("id"))
        .filter(total__gt=1)
    )
    for duplicate in duplicates:
        rows = list(
            UnitAppointment.objects.filter(
                is_active=True, user_id=duplicate["user_id"], role=duplicate["role"]
            ).order_by("id")
        )
        home_unit_id = (
            User.objects.filter(id=duplicate["user_id"])
            .values_list("unit_id", flat=True)
            .first()
        )
        keep = next((row for row in rows if row.unit_id == home_unit_id), rows[0])
        for row in rows:
            if row.id == keep.id:
                continue
            row.is_active = False
            row.ended_at = timezone.now()
            row.notes = (
                row.notes + "\nEnded: one person may hold a post once across all units."
            ).strip()
            row.save(update_fields=["is_active", "ended_at", "notes"])


class Migration(migrations.Migration):
    dependencies = [("command", "0006_instruction_attachment")]

    operations = [
        migrations.RunPython(keep_one_post_per_person, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="unitappointment",
            constraint=models.UniqueConstraint(
                condition=models.Q(is_active=True),
                fields=("user", "role"),
                name="command_one_active_post_per_person",
            ),
        ),
    ]
