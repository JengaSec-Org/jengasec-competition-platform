"""Forms for building rubrics and their weighted criteria."""
from decimal import Decimal

from django import forms

from .models import Rubric, RubricCriterion


class RubricForm(forms.ModelForm):
    class Meta:
        model = Rubric
        fields = [
            "competition",
            "submission_type",
            "title",
            "description",
            "is_active",
        ]


class RubricCriterionForm(forms.ModelForm):
    class Meta:
        model = RubricCriterion
        fields = ["criterion", "description", "weight", "max_score", "display_order"]

# just added for check 
        widgets = {
            "criterion": forms.TextInput(attrs={"class": "input"}),
            "description": forms.TextInput(attrs={"class": "input"}),
            "weight": forms.NumberInput(attrs={"class": "input", "step": "0.01"}),
            "max_score": forms.NumberInput(attrs={"class": "input", "step": "0.01"}),
            "display_order": forms.NumberInput(attrs={"class": "input"}),
        }



class BaseRubricCriterionFormSet(forms.BaseInlineFormSet):
    """Enforces the rule the schema can't: weights must sum to 100."""

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        total = Decimal("0")
        rows = 0
        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get("DELETE"):
                continue
            weight = form.cleaned_data.get("weight")
            if weight is not None:
                total += weight
                rows += 1
        if rows and total != Decimal("100.00"):
            raise forms.ValidationError(
                f"Criterion weights must sum to 100% (currently {total}%)."
            )


RubricCriterionFormSet = forms.inlineformset_factory(
    Rubric,
    RubricCriterion,
    form=RubricCriterionForm,
    formset=BaseRubricCriterionFormSet,
    extra=4,
    can_delete=True,
)
