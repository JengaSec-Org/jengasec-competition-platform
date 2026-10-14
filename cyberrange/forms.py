import re

from django import forms

from accounts.models import Team

from .models import Finding

EVIDENCE_MAX_BYTES = 20 * 1024 * 1024


class FindingForm(forms.ModelForm):
    """A Red team's finding. Only cells in the team's active scope can be chosen."""

    telemetry_event_ids = forms.CharField(
        required=False,
        label="Telemetry event IDs",
        help_text="One stable evt_ identifier per line. Do not paste request or response bodies.",
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    correlation_ids = forms.CharField(
        required=False,
        label="Request or trace IDs",
        help_text="One opaque identifier per line. Do not paste personal data or request contents.",
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    class Meta:
        model = Finding
        fields = [
            "target_team", "round", "started_at", "ended_at", "title", "technique", "attack_id",
            "steps", "evidence", "telemetry_event_ids", "correlation_ids", "evidence_file", "impact", "suggested_fix",
        ]
        labels = {"target_team": "Target cell"}
        widgets = {
            "started_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "ended_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "steps": forms.Textarea(attrs={"rows": 5}),
            "evidence": forms.Textarea(attrs={"rows": 4}),
            "impact": forms.Textarea(attrs={"rows": 3}),
            "suggested_fix": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, red_team, in_scope, **kwargs):
        super().__init__(*args, **kwargs)
        self.red_team = red_team
        # The scope check lives in the queryset: a target outside it is not a valid choice,
        # so a forged POST fails validation instead of reaching the database.
        self.fields["target_team"].queryset = Team.objects.filter(pk__in=in_scope)
        self.fields["target_team"].label_from_instance = lambda t: t.cell_label or t.team_name
        for field in self.fields.values():
            css = "textarea" if isinstance(field.widget, forms.Textarea) else "input"
            field.widget.attrs.setdefault("class", css)

    def clean_evidence_file(self):
        f = self.cleaned_data.get("evidence_file")
        if f and f.size > EVIDENCE_MAX_BYTES:
            raise forms.ValidationError("Evidence files are limited to 20 MB.")
        return f

    def clean_telemetry_event_ids(self):
        values = [
            value.strip()
            for value in self.cleaned_data.get("telemetry_event_ids", "").splitlines()
            if value.strip()
        ]
        invalid = [
            value
            for value in values
            if len(value) != 68
            or not value.startswith("evt_")
            or any(char not in "0123456789abcdef" for char in value[4:])
        ]
        if invalid:
            raise forms.ValidationError("Use one evt_<64 lowercase hex> identifier per line.")
        if len(values) != len(set(values)):
            raise forms.ValidationError("Each telemetry event ID may appear only once.")
        return values

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("started_at"), cleaned.get("ended_at")
        if start and end and end < start:
            self.add_error("ended_at", "The end time is before the start time.")
        return cleaned

    def clean_correlation_ids(self):
        values = [value.strip() for value in self.cleaned_data.get("correlation_ids", "").splitlines()
                  if value.strip()]
        if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", value) for value in values):
            raise forms.ValidationError("Use opaque request or trace identifiers only.")
        if len(values) != len(set(values)):
            raise forms.ValidationError("Each request or trace ID may appear only once.")
        return values
