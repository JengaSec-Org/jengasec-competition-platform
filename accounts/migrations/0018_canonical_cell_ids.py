"""Cells are the family plus two digits: APP06, AI02, APPRED11, AIRED01.

Some Blue cells were typed by hand as APPBLUE06 / AIBLUE02. The range builds
the Kubernetes namespace from the cell (jengasec-ent-a-app06), so those teams
pointed at namespaces that do not exist. This renames them, keeps a stored
namespace in step, and skips (and reports) any rename that would collide.
"""
import re

from django.db import migrations

HAND_TYPED = re.compile(r"^(APP|AI)BLUE(\d{2})$")


def canonical_cells(apps, schema_editor):
    Team = apps.get_model("accounts", "Team")
    for team in Team.objects.filter(team_type="blue").exclude(cell_id="").iterator():
        match = HAND_TYPED.match(team.cell_id.upper())
        if not match:
            continue
        cell = f"{match[1]}{match[2]}"
        clash = Team.objects.filter(competition_id=team.competition_id, enterprise=team.enterprise,
                                    cell_id=cell).exclude(pk=team.pk).exists()
        if clash:
            print(f"\n  skipped team {team.pk}: {team.cell_id} -> {cell} is already taken in ENT-{team.enterprise.upper()}")
            continue
        update = {"cell_id": cell}
        if team.namespace:
            update["namespace"] = f"jengasec-ent-{team.enterprise}-{cell.lower()}" if team.enterprise else ""
        Team.objects.filter(pk=team.pk).update(**update)
        print(f"\n  team {team.pk}: {team.cell_id} -> {cell}")


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0017_merge_consent_and_namespaces"),
    ]

    operations = [
        migrations.RunPython(canonical_cells, migrations.RunPython.noop),
    ]
