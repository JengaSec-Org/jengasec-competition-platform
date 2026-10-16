import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0016_participant_consent'),
        ('competitions', '0017_competitionsettings_red_roster_deadline'),
        ('scoring', '0004_score_revision_baseline'),
    ]

    operations = [
        migrations.CreateModel(
            name='ScoringReadiness',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('criterion', models.CharField(max_length=40)),
                ('policy_version', models.CharField(max_length=40)),
                ('window_start', models.DateTimeField()),
                ('window_end', models.DateTimeField()),
                ('status', models.CharField(choices=[('collecting', 'Collecting observations'), ('insufficient_data', 'Insufficient observations'), ('attribution_unresolved', 'Assignment verification pending'), ('publication_failed', 'Publication pending'), ('published', 'Verified marks published')], max_length=30)),
                ('reason_code', models.CharField(max_length=80)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('competition', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='competitions.competition')),
                ('team', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='accounts.team')),
            ],
            options={
                'constraints': [models.UniqueConstraint(fields=('competition', 'team', 'criterion'), name='scoring_readiness_component')],
            },
        ),
    ]
