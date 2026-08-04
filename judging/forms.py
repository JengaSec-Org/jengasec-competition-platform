"""Forms for the judge review workspace and appeals."""
from django import forms

from .models import Appeal, CriterionScore, Evaluation


class EvaluationForm(forms.ModelForm):
    """Assign a judge and rubric to a submission."""

    class Meta:
        model = Evaluation
        fields = ["submission", "judge", "rubric", "status"]


class CriterionScoreForm(forms.ModelForm):
    """A judge scoring one criterion.

    `override_reason` is required whenever the judge's score differs from
    what the AI suggested — the accountability rule from the planning doc.
    The view passes the cleaned value to CriterionScore.apply_judge_score().
    """

    override_reason = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 2}),
        required=False,
        label="Reason for override",
        help_text="Required when your score differs from the AI suggestion.",
    )

    class Meta:
        model = CriterionScore
        fields = ["judge_score", "comments"]

    def clean(self):
        cleaned = super().clean()
        judge_score = cleaned.get("judge_score")
        reason = (cleaned.get("override_reason") or "").strip()

        if judge_score is not None:
            max_score = self.instance.criterion.max_score
            if judge_score < 0 or judge_score > max_score:
                self.add_error(
                    "judge_score", f"Score must be between 0 and {max_score}."
                )
            ai_score = self.instance.ai_score
            if ai_score is not None and judge_score != ai_score and not reason:
                self.add_error(
                    "override_reason",
                    "A justification is required when overriding the AI score.",
                )
        cleaned["override_reason"] = reason
        return cleaned


CriterionScoreFormSet = forms.modelformset_factory(
    CriterionScore, form=CriterionScoreForm, extra=0
)


class AppealForm(forms.ModelForm):
    """Filed by a team disputing a published result."""

    class Meta:
        model = Appeal
        fields = ["reason"]
        widgets = {"reason": forms.Textarea(attrs={"rows": 5})}


class AppealResolutionForm(forms.ModelForm):
    """Admin/judge resolving an appeal."""

    class Meta:
        model = Appeal
        fields = ["status", "resolution"]

    def clean(self):
        cleaned = super().clean()
        status = cleaned.get("status")
        resolution = (cleaned.get("resolution") or "").strip()
        closed = (
            Appeal.Status.UPHELD,
            Appeal.Status.REJECTED,
        )
        if status in closed and not resolution:
            self.add_error("resolution", "Explain the outcome before closing an appeal.")
        return cleaned
