"""Refresh attribution when an authenticated deployment receipt advances."""
from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender="deployments.AppSubmission")
def artifact_receipt_changed(sender, instance, raw=False, **kwargs):
    if raw:
        return
    from cyberrange.models import ScoringIdentityState
    from services.scoring_identity import refresh_identity
    if ScoringIdentityState.objects.filter(competition_id=instance.competition_id).exists():
        refresh_identity(instance.competition)
