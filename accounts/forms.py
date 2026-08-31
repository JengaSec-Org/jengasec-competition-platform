"""Forms for user profiles, teams, and team members."""
from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from .models import Team, TeamMember, UserProfile


class UserProfileForm(forms.ModelForm):
    """Assign a platform role. Saving syncs the matching auth Group."""

    class Meta:
        model = UserProfile
        fields = ["role", "institution"]


class UserRegistrationForm(UserCreationForm):
    """Self-registration / admin user creation.

    Role is assigned separately by an admin, so new accounts land on the
    "role not assigned" page until approved.
    """

    first_name = forms.CharField(max_length=100)
    last_name = forms.CharField(max_length=100)
    email = forms.EmailField(max_length=254)

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email"]

    def clean_email(self):
        email = self.cleaned_data["email"]
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email


class TeamForm(forms.ModelForm):
    class Meta:
        model = Team
        fields = [
            "competition",
            "team_name",
            "team_type",
            "captain",
            "institution",
            "status",
        ]


class TeamMemberForm(forms.ModelForm):
    class Meta:
        model = TeamMember
        fields = ["student_name", "email", "role", "user"]


# Inline formset: edit a team's roster alongside the team itself.
TeamMemberFormSet = forms.inlineformset_factory(
    Team, TeamMember, form=TeamMemberForm, extra=3, can_delete=True
)
