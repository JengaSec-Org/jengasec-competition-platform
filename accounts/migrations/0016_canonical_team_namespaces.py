from django.db import migrations


def canonicalize_default_namespaces(apps, schema_editor):
    Team = apps.get_model("accounts", "Team")
    for team in Team.objects.filter(team_type="blue").exclude(namespace="").iterator():
        if team.enterprise not in {"a", "b"} or not team.cell_id:
            continue
        legacy = f"ent{team.enterprise}-{team.cell_id.lower()}"
        if team.namespace == legacy:
            namespace = f"jengasec-ent-{team.enterprise}-{team.cell_id.lower()}"
            Team.objects.filter(pk=team.pk).update(namespace=namespace)


def restore_default_namespaces(apps, schema_editor):
    Team = apps.get_model("accounts", "Team")
    for team in Team.objects.filter(team_type="blue").exclude(namespace="").iterator():
        if team.enterprise not in {"a", "b"} or not team.cell_id:
            continue
        canonical = f"jengasec-ent-{team.enterprise}-{team.cell_id.lower()}"
        if team.namespace == canonical:
            namespace = f"ent{team.enterprise}-{team.cell_id.lower()}"
            Team.objects.filter(pk=team.pk).update(namespace=namespace)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0015_team_service_ip"),
    ]

    operations = [
        migrations.RunPython(
            canonicalize_default_namespaces,
            restore_default_namespaces,
        ),
    ]
