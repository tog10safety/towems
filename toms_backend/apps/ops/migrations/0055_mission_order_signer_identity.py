"""Freeze the signer's name, rank and branch on a signed Mission Order.

The post was already recorded at signature, but the name, rank and branch were
still read from the personnel record, so a promotion or a corrected name
rewrote a document that had already been signed. Store them with the rest of
the block, and fill them in for orders signed before this change - the
personnel record is the only source for what they read at the time.
"""

from django.db import migrations, models


def record_signed_signer_identity(apps, schema_editor):
    Mission = apps.get_model("ops", "Mission")
    User = apps.get_model("accounts", "User")

    signed = Mission.objects.filter(mission_order_signed_at__isnull=False)
    for mission in signed.iterator():
        signer_id = (
            mission.mission_order_signed_by_id
            or mission.mission_order_signatory_id
        )
        if signer_id is None:
            continue
        signer = User.objects.filter(pk=signer_id).first()
        if signer is None:
            continue
        name = " ".join(
            part
            for part in (
                signer.first_name,
                signer.middle_name,
                signer.last_name,
            )
            if part
        ).strip()
        Mission.objects.filter(pk=mission.pk).update(
            mission_order_signer_name=name[:200],
            mission_order_signer_rank=(signer.rank or "").strip()[:50],
            mission_order_signer_branch=(
                signer.branch_of_service or ""
            ).strip()[:100],
        )


class Migration(migrations.Migration):
    dependencies = [
        ("ops", "0054_mission_order_signature_block"),
    ]

    operations = [
        migrations.AddField(
            model_name="mission",
            name="mission_order_signer_name",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
        migrations.AddField(
            model_name="mission",
            name="mission_order_signer_rank",
            field=models.CharField(blank=True, default="", max_length=50),
        ),
        migrations.AddField(
            model_name="mission",
            name="mission_order_signer_branch",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
        migrations.RunPython(record_signed_signer_identity, migrations.RunPython.noop),
    ]
