from django import forms

from accounts.models import Team

from .models import Finding

EVIDENCE_MAX_BYTES = 20 * 1024 * 1024


class VpnKeyForm(forms.Form):
    """The public key the person's WireGuard app shows for a new empty tunnel."""

    public_key = forms.CharField(
        max_length=60, strip=True, label="Your WireGuard public key",
        help_text="44 characters, ending in =. Never paste the private key.",
    )

    def clean_public_key(self):
        from services.vpn_portal import valid_public_key

        key = self.cleaned_data["public_key"]
        if not valid_public_key(key):
            raise forms.ValidationError("That is not a WireGuard public key. Copy the line labelled Public key.")
        return key


class FindingForm(forms.ModelForm):
    """A Red team's finding. Only cells in the team's active scope can be chosen."""

    class Meta:
        model = Finding
        fields = [
            "target_team", "round", "started_at", "ended_at", "title", "technique", "attack_id",
            "steps", "evidence", "evidence_file", "impact", "suggested_fix",
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

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("started_at"), cleaned.get("ended_at")
        if start and end and end < start:
            self.add_error("ended_at", "The end time is before the start time.")
        return cleaned
