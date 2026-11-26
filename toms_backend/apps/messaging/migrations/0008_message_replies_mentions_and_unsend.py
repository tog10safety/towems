import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0013_devicesession"),
        ("messaging", "0007_backfill_group_chat_routing_threads"),
    ]

    operations = [
        migrations.AddField(
            model_name="message",
            name="mentions",
            field=models.ManyToManyField(
                blank=True, related_name="messaging_mentions", to="accounts.user"
            ),
        ),
        migrations.AddField(
            model_name="message",
            name="reply_to",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="replies",
                to="messaging.message",
            ),
        ),
        migrations.AddField(
            model_name="message",
            name="unsent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="message",
            name="unsent_by",
            field=models.ForeignKey(
                blank=True,
                db_column="unsent_by_id",
                db_constraint=False,
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="+",
                to="accounts.user",
            ),
        ),
    ]
