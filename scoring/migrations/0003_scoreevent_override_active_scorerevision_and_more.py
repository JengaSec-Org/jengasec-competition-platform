import django.db.models.deletion
import django.utils.timezone
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0016_participant_consent'),
        ('competitions', '0017_competitionsettings_red_roster_deadline'),
        ('cyberrange', '0001_initial'),
        ('scoring', '0002_teamscore_bonus_alter_scoreevent_source'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='scoreevent',
            name='override_active',
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name='ScoreRevision',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('sequence', models.PositiveIntegerField()),
                ('producer_revision', models.PositiveBigIntegerField(blank=True, null=True)),
                ('accepted_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('stage', models.CharField(max_length=20)),
                ('criterion', models.CharField(blank=True, max_length=40)),
                ('marks', models.DecimalField(decimal_places=2, max_digits=6)),
                ('day', models.PositiveSmallIntegerField(blank=True, choices=[(1, 'Day 1'), (2, 'Day 2'), (3, 'Day 3')], null=True)),
                ('source', models.CharField(choices=[('finding', 'From a finding'), ('telemetry', 'From telemetry'), ('judge', 'Judge'), ('entry', 'Entry score'), ('bonus', 'Bonus')], max_length=20)),
                ('note', models.TextField(blank=True)),
                ('override_active', models.BooleanField(default=False)),
                ('policy_version', models.CharField(blank=True, max_length=40)),
                ('policy_sha256', models.CharField(blank=True, max_length=64)),
                ('window_start', models.DateTimeField(blank=True, null=True)),
                ('window_end', models.DateTimeField(blank=True, null=True)),
                ('facts', models.JSONField(default=dict)),
                ('quality', models.JSONField(default=dict)),
                ('assignment_revisions', models.JSONField(default=list)),
                ('evidence', models.JSONField(default=list)),
                ('input_sha256', models.CharField(blank=True, max_length=64)),
                ('payload_sha256', models.CharField(blank=True, max_length=64)),
                ('baseline', models.BooleanField(default=False)),
                ('event', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='revisions', to='scoring.scoreevent')),
                ('finding', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='score_revisions', to='cyberrange.finding')),
                ('recorded_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='score_revisions', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-accepted_at', '-pk'],
                'indexes': [models.Index(fields=['accepted_at', 'event'], name='ix_score_revision_time')],
                'constraints': [models.UniqueConstraint(fields=('event', 'sequence'), name='scoring_revision_order'), models.UniqueConstraint(condition=models.Q(('producer_revision__isnull', False)), fields=('event', 'producer_revision'), name='scoring_producer_revision_once')],
            },
        ),
        migrations.CreateModel(
            name='ScoringEvidence',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('family', models.CharField(max_length=20)),
                ('archive', models.FileField(upload_to='scoring/evidence/')),
                ('sha256', models.CharField(max_length=64)),
                ('input_sha256', models.CharField(max_length=64)),
                ('snapshot', models.JSONField()),
                ('byte_size', models.PositiveIntegerField()),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('verified_at', models.DateTimeField()),
                ('retain_until', models.DateTimeField(blank=True, null=True)),
                ('appeal_hold', models.BooleanField(default=False)),
                ('competition', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='competitions.competition')),
                ('team', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='accounts.team')),
            ],
            options={
                'constraints': [models.UniqueConstraint(fields=('competition', 'team', 'sha256'), name='scoring_evidence_once')],
            },
        ),
        migrations.CreateModel(
            name='ScoringPolicyEnrollment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('policy_version', models.CharField(max_length=40)),
                ('policy_sha256', models.CharField(max_length=64)),
                ('window_start', models.DateTimeField()),
                ('window_end', models.DateTimeField()),
                ('archive_budget_bytes', models.PositiveBigIntegerField()),
                ('enrolled_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('reconciled_at', models.DateTimeField()),
                ('finalized_at', models.DateTimeField(blank=True, null=True)),
                ('competition', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='scoring_policy', to='competitions.competition')),
                ('recorded_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='scoring_enrollments', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'constraints': [models.CheckConstraint(condition=models.Q(('window_end__gt', models.F('window_start'))), name='scoring_window_positive'), models.CheckConstraint(condition=models.Q(('archive_budget_bytes__gt', 0)), name='scoring_archive_budget_positive')],
            },
        ),
    ]
