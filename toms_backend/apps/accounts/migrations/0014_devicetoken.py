from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0013_devicesession'),
    ]

    operations = [
        migrations.CreateModel(
            name='DeviceToken',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('device_id', models.CharField(max_length=64)),
                ('session_key', models.CharField(blank=True, db_index=True, default='', max_length=64)),
                ('token_hash', models.CharField(max_length=64, unique=True)),
                ('label', models.CharField(blank=True, default='', max_length=80)),
                ('user_agent', models.CharField(blank=True, default='', max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('last_used_at', models.DateTimeField(blank=True, null=True)),
                ('expires_at', models.DateTimeField()),
                ('revoked_at', models.DateTimeField(blank=True, null=True)),
                ('user', models.ForeignKey(db_column='user_id', db_constraint=False, on_delete=django.db.models.deletion.CASCADE, related_name='device_tokens', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'accounts_device_token',
                'ordering': ['-created_at', '-id'],
                'constraints': [models.UniqueConstraint(fields=('user', 'device_id'), name='acct_device_token_per_install')],
            },
        ),
    ]
