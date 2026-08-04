from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.urls import reverse_lazy
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from .forms import CompetitionForm
from .models import Competition


class StaffRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_active and self.request.user.is_staff


class CompetitionListView(LoginRequiredMixin, ListView):
    model = Competition
    template_name = "competitions/competition_list.html"
    context_object_name = "competitions"


class CompetitionDetailView(LoginRequiredMixin, DetailView):
    model = Competition
    template_name = "competitions/competition_detail.html"
    context_object_name = "competition"

    def get_queryset(self):
        return Competition.objects.prefetch_related("phases", "categories", "teams")


class CompetitionCreateView(LoginRequiredMixin, StaffRequiredMixin, CreateView):
    model = Competition
    form_class = CompetitionForm
    template_name = "competitions/competition_form.html"
    success_url = reverse_lazy("competitions:list")


class CompetitionUpdateView(LoginRequiredMixin, StaffRequiredMixin, UpdateView):
    model = Competition
    form_class = CompetitionForm
    template_name = "competitions/competition_form.html"
    success_url = reverse_lazy("competitions:list")
