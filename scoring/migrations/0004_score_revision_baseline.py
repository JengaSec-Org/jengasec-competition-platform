from django.db import migrations
from django.utils import timezone


def baseline(apps, schema_editor):
    Event = apps.get_model("scoring", "ScoreEvent")
    Revision = apps.get_model("scoring", "ScoreRevision")
    now = timezone.now()
    batch = []
    for event in Event.objects.using(schema_editor.connection.alias).order_by("pk").iterator():
        override = event.source == "judge" and bool(event.dedupe_key)
        if override:
            Event.objects.using(schema_editor.connection.alias).filter(pk=event.pk).update(override_active=True)
        fields = {name: getattr(event, name) for name in (
            "stage", "criterion", "marks", "day", "source", "note", "finding_id", "recorded_by_id")}
        batch.append(Revision(event_id=event.pk, sequence=1, accepted_at=now, baseline=True,
                              override_active=override,
                              quality={"history_before_baseline": "unavailable"}, **fields))
        if len(batch) >= 500:
            Revision.objects.using(schema_editor.connection.alias).bulk_create(batch)
            batch = []
    if batch:
        Revision.objects.using(schema_editor.connection.alias).bulk_create(batch)


class Migration(migrations.Migration):
    dependencies = [("scoring", "0003_scoreevent_override_active_scorerevision_and_more")]
    operations = [migrations.RunPython(baseline, migrations.RunPython.noop)]
