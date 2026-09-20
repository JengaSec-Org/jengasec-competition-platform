import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0004_create_team_roles'),
        ('competitions', '0001_initial'),
    ]

    operations = [
        # NB: an earlier version of this migration removed `team_type`.
        # That field is what routes a captain to the blue or red dashboard
        # and what TargetAssignment / cell allocation key on, so it stays.
        # Anyone who applied the earlier version on a dev database should
        # delete db.sqlite3 and migrate again.
        migrations.AddField(
            model_name='team',
            name='track',
            field=models.CharField(
                choices=[('cloud', 'Cloud'), ('application', 'Application'), ('ai', 'AI')],
                default='cloud',
                max_length=20,
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='team',
            name='application_choice',
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.CreateModel(
            name='TeamJoinRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected')], default='pending', max_length=10)),
                ('requested_at', models.DateTimeField(auto_now_add=True)),
                ('decided_at', models.DateTimeField(blank=True, null=True)),
                ('team', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='join_requests', to='accounts.team')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='team_join_requests', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-requested_at'],
                'constraints': [models.UniqueConstraint(condition=models.Q(('status', 'pending')), fields=('user',), name='one_pending_join_request_per_user')],
            },
        ),
    ]