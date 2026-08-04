"""Forms for competitions, categories, and phases."""
from django import forms

from .models import Competition, CompetitionCategory, CompetitionPhase


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
