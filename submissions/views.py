from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.views.generic import DetailView, ListView

from .models import Submission


class SubmissionListView(LoginRequiredMixin, ListView):
    """Staff see every submission; team users see their teams' only."""

    model = Submission
    template_name = "submissions/submission_list.html"
    context_object_name = "submissions"
    paginate_by = 20

    def get_queryset(self):
        qs = Submission.objects.select_related(
            "team", "competition", "submission_type"
        )
        user = self.request.user
        if user.is_staff:
            return qs
        return qs.filter(
            Q(team__captain=user)
            | Q(team__members__user=user)
            | Q(team__members__email__iexact=user.email)
        ).distinct()


class SubmissionDetailView(LoginRequiredMixin, DetailView):
    model = Submission
    template_name = "submissions/submission_detail.html"
    context_object_name = "submission"

    def get_queryset(self):
        # Same visibility rule as the list.
        view = SubmissionListView()
        view.request = self.request
        return view.get_queryset().prefetch_related("files")
