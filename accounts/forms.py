"""Forms for user profiles, teams, and team members."""
from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from competitions.models import Competition

from .models import Team, TeamJoinRequest, TeamMember, UserProfile


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
            "track",
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


class TeamCreateForm(forms.ModelForm):
    """Self-service team creation: the logged-in user becomes captain
    automatically, unlike TeamForm (admin picks the captain by hand)."""

    class Meta:
        model = Team
        fields = [
            "competition",
            "team_name",
            "team_type",
            "track",
            "application_choice",
            "institution",
        ]
        widgets = {"team_type": forms.RadioSelect, "track": forms.RadioSelect}
        labels = {"team_type": "Which side are you competing on?"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["competition"].queryset = Competition.objects.filter(
            status__in=[
                Competition.Status.DRAFT,
                Competition.Status.OPEN,
                Competition.Status.IN_PROGRESS,
            ]
        ).order_by("-start_date")
        self.fields["application_choice"].required = False
        self.fields["institution"].required = False

    def clean(self):
        cleaned_data = super().clean()
        if (
            cleaned_data.get("track") == Team.Track.APPLICATION
            and not cleaned_data.get("application_choice")
        ):
            self.add_error(
                "application_choice",
                "Tell us which application your team wants to build.",
            )
        return cleaned_data


class TeamJoinForm(forms.Form):
    team = forms.ModelChoiceField(
        queryset=Team.objects.none(),
        required=False,
        empty_label="— Join no team for now —",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["team"].queryset = Team.objects.order_by("team_name")