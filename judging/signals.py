from django.db.models.signals import post_save
from django.dispatch import receiver
from django_q.tasks import async_task

from .models import Evaluation


@receiver(post_save, sender=Evaluation)
def queue_ai_evaluation_on_creation(sender, instance, created, **kwargs):
    """
    Per team lead: AI evaluation runs automatically the moment a submission
    is assigned to a judge (i.e., an Evaluation row is created) -- not on
    a button click. Fires regardless of WHERE the Evaluation was created
    (the new assignment page, Django admin, a shell script) since it's a
    signal on the model itself.
    """
    if created:
        async_task("judging.tasks.run_ai_evaluation_task", instance.id)