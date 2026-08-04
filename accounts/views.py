from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.urls import reverse_lazy
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    UpdateView,
)

from .forms import TeamForm
from .models import Team


class StaffRequiredMixin(UserPassesTestMixin):
    """Only staff may create/update/delete; everyone logged in may view."""

    def test_func(self):
        return self.request.user.is_active and self.request.user.is_staff


class TeamListView(LoginRequiredMixin, ListView):
    model = Team
    template_name = "accounts/team_list.html"
    context_object_name = "teams"
    paginate_by = 20

    def get_queryset(self):
        return Team.objects.select_related("competition", "captain")


class TeamDetailView(LoginRequiredMixin, DetailView):
    model = Team
    template_name = "accounts/team_detail.html"
    context_object_name = "team"

    def get_queryset(self):
        return Team.objects.select_related("competition", "captain").prefetch_related(
            "members", "submissions__submission_type"
        )


class TeamCreateView(LoginRequiredMixin, StaffRequiredMixin, CreateView):
    model = Team
    form_class = TeamForm
    template_name = "accounts/team_form.html"
    success_url = reverse_lazy("accounts:team_list")


class TeamUpdateView(LoginRequiredMixin, StaffRequiredMixin, UpdateView):
    model = Team
    form_class = TeamForm
    template_name = "accounts/team_form.html"
    success_url = reverse_lazy("accounts:team_list")


class TeamDeleteView(LoginRequiredMixin, StaffRequiredMixin, DeleteView):
    model = Team
    template_name = "accounts/team_confirm_delete.html"
    success_url = reverse_lazy("accounts:team_list")
