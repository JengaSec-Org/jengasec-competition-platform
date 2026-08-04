"""Forms for narrative evaluation reports."""
from django import forms

from .models import Report


class ReportForm(forms.ModelForm):
    class Meta:
        model = Report
        fields = [
            "report_type",
            "strengths",
            "weaknesses",
            "recommendations",
            "overall_comments",
        ]
        widgets = {
            "strengths": forms.Textarea(attrs={"rows": 4}),
            "weaknesses": forms.Textarea(attrs={"rows": 4}),
            "recommendations": forms.Textarea(attrs={"rows": 4}),
            "overall_comments": forms.Textarea(attrs={"rows": 4}),
        }
