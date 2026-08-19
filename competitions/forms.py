"""Forms for competitions, categories, and phases."""
from django import forms

from .models import (
    Competition,
    CompetitionCategory,
    CompetitionPhase,
    CompetitionSettings,
)


class CompetitionForm(forms.ModelForm):
    class Meta:
        model = Competition
        fields = ["name", "theme", "description", "start_date", "end_date", "status"]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_date"), cleaned.get("end_date")
        if start and end and end < start:
            raise forms.ValidationError("End date must be on or after the start date.")
        return cleaned


class CompetitionCategoryForm(forms.ModelForm):
    class Meta:
        model = CompetitionCategory
        fields = ["name", "description"]


class CompetitionPhaseForm(forms.ModelForm):
    class Meta:
        model = CompetitionPhase
        fields = ["name", "description", "start_date", "end_date", "order"]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_date"), cleaned.get("end_date")
        if start and end and end < start:
            raise forms.ValidationError("Phase end date must be on or after its start.")
        return cleaned


CompetitionCategoryFormSet = forms.inlineformset_factory(
    Competition, CompetitionCategory, form=CompetitionCategoryForm, extra=2, can_delete=True
)

CompetitionPhaseFormSet = forms.inlineformset_factory(
    Competition, CompetitionPhase, form=CompetitionPhaseForm, extra=2, can_delete=True
)


class CompetitionSettingsForm(forms.ModelForm):
    """Timelines and caps for one competition (Module 2: define timelines)."""

    class Meta:
        model = CompetitionSettings
        fields = [
            "registration_deadline",
            "build_deadline",
            "submission_deadline",
            "max_proposals_per_team",
            "default_proposal_cap",
            "appeal_window_days",
        ]
        widgets = {
            "registration_deadline": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
            "build_deadline": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
            "submission_deadline": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Browsers only prefill datetime-local inputs in this exact format.
        for name in ("registration_deadline", "build_deadline", "submission_deadline"):
            self.fields[name].input_formats = ["%Y-%m-%dT%H:%M"]

    def clean(self):
        cleaned = super().clean()
        order = [
            ("registration_deadline", "Registration"),
            ("build_deadline", "Build"),
            ("submission_deadline", "Submission"),
        ]
        seen = [(cleaned.get(f), label) for f, label in order if cleaned.get(f)]
        for (earlier, first), (later, second) in zip(seen, seen[1:]):
            if later < earlier:
                raise forms.ValidationError(
                    f"The {second} deadline cannot fall before the {first} deadline."
                )
        return cleaned
