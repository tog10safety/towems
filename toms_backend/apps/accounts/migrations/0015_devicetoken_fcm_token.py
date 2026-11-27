from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0014_devicetoken'),
    ]

    operations = [
        migrations.AddField(
            model_name='devicetoken',
            name='fcm_token',
            field=models.CharField(blank=True, default='', max_length=512),
        ),
    ]
