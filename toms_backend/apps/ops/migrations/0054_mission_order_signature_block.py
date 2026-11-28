"""Record the signature block a Mission Order was signed with.

A signed order is a record of the day it was signed. Until now the PDF rebuilt
the signer's post from the live roster, so moving a commander to another post
or unit rewrote orders they had already signed. The block is stored on the
mission at signature time from now on; this migration reconstructs it for the
orders already signed, using the appointment history - an appointment's
creation and end dates say which post was held on the signature date.
"""

from django.db import migrations, models

COMMANDER_ROLE = "commander"
COMMANDER_DESIGNATIONS = {
    "Command": "Commander",
    "Wing": "Wing Commander",
    "Group": "Group Commander",
    "Center": "Center Commander",
    "Squadron": "Squadron Commander",
}
AUTHORITY_LABELS = {
    "commander": "Commander",
    "deputy_commander": "Deputy Commander",
    "director_operations": "Director for Operations",
    "operations_officer": "Operations Officer",
}


def record_signed_blocks(apps, schema_editor):
    Mission = apps.get_model("ops", "Mission")
    UnitAppointment = apps.get_model("command", "UnitAppointment")
    User = apps.get_model("accounts", "User")
    Unit = apps.get_model("legacy", "Unit")

    # The legacy units table is unmanaged, so a fresh test database creates it
    # after migrations run. Never join it when it is not there.
    has_units = "units" in set(
        schema_editor.connection.introspection.table_names()
    )

    signed = Mission.objects.filter(mission_order_signed_at__isnull=False)
    for mission in signed.iterator():
        signer_id = (
            mission.mission_order_signed_by_id
            or mission.mission_order_signatory_id
        )
        if signer_id is None:
            continue
        signed_at = mission.mission_order_signed_at
        # A commander appointment that was live on the signature date is the
        # record of the post held then, whether or not it is still active.
        as_commander = (
            UnitAppointment.objects.filter(
                unit_id=mission.unit_id,
                user_id=signer_id,
                role=COMMANDER_ROLE,
                created_at__lte=signed_at,
            )
            .filter(
                models.Q(ended_at__isnull=True) | models.Q(ended_at__gt=signed_at)
            )
            .exists()
        )
        signer = User.objects.filter(pk=signer_id).first()
        if (
            not as_commander
            and signer is not None
            and mission.approving_authority_id == signer_id
            and mission.approving_authority_role == "commander"
            and signer.unit_id == mission.unit_id
        ):
            as_commander = True

        if as_commander:
            category = ""
            if has_units and mission.unit_id is not None:
                category = (
                    Unit.objects.filter(pk=mission.unit_id)
                    .values_list("category", flat=True)
                    .first()
                    or ""
                )
            designation = COMMANDER_DESIGNATIONS.get(category, "Commander")
        elif mission.approving_authority_id == signer_id:
            designation = AUTHORITY_LABELS.get(
                mission.approving_authority_role, ""
            )
        else:
            designation = (getattr(signer, "designation", "") or "").strip()

        Mission.objects.filter(pk=mission.pk).update(
            mission_order_signer_designation=designation[:120],
            mission_order_signer_as_commander=as_commander,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("ops", "0053_pov_plate_as_identifier"),
        # This migration reads command.UnitAppointment. Without an explicit
        # edge the executor is free to apply it before the command app exists
        # on a fresh database (the armory app exposes that ordering).
        ("command", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="mission",
            name="mission_order_signer_designation",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AddField(
            model_name="mission",
            name="mission_order_signer_as_commander",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(record_signed_blocks, migrations.RunPython.noop),
    ]
