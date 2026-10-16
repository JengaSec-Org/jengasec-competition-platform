from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='appsubmission',
            name='deployed_digest_verified_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='appsubmission',
            name='digest_verified_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='appsubmission',
            name='frozen_verified_digest',
            field=models.CharField(blank=True, max_length=71),
        ),
        migrations.AddField(
            model_name='appsubmission',
            name='resolved_digest',
            field=models.CharField(blank=True, max_length=71),
        ),
    ]
