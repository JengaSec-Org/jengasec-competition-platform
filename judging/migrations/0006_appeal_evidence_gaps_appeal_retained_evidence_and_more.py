import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0016_participant_consent'),
        ('judging', '0005_phase_c_submission_rules'),
        ('scoring', '0005_scoringreadiness'),
        ('submissions', '0011_seed_penalty_schedule'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='appeal',
            name='evidence_gaps',
            field=models.JSONField(blank=True, default=list, editable=False),
        ),
        migrations.AddField(
            model_name='appeal',
            name='retained_evidence',
            field=models.ManyToManyField(blank=True, related_name='score_appeals', to='scoring.scoringevidence'),
        ),
        migrations.AddField(
            model_name='appeal',
            name='score_event',
            field=models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='appeals', to='scoring.scoreevent'),
        ),
        migrations.AddField(
            model_name='appeal',
            name='score_revision',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='appeals', to='scoring.scorerevision'),
        ),
        migrations.AlterField(
            model_name='appeal',
            name='submission',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='appeals', to='submissions.submission'),
        ),
        migrations.AddConstraint(
            model_name='appeal',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('score_event__isnull', True), ('score_revision__isnull', True), ('submission__isnull', False)), models.Q(('score_event__isnull', False), ('score_revision__isnull', False), ('submission__isnull', True)), _connector='OR'), name='appeal_exactly_one_result'),
        ),
        migrations.AddConstraint(
            model_name='appeal',
            constraint=models.UniqueConstraint(condition=models.Q(('score_event__isnull', False), ('status__in', ['open', 'under_review'])), fields=('score_event',), name='one_active_score_component_appeal'),
        ),
    ]
