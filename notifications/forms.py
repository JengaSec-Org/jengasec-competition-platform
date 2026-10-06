"""Forms for composing notifications.

Most notifications are created by the system (submission confirmed,
evaluation complete, deadlines). These cover the admin cases: one
notification to one person, and an announcement to a whole side of the
competition.
"""
from django import forms
from django.utils import timezone

from services import announcement_service as announce_svc

from .models import Notification


class NotificationForm(forms.ModelForm):
    class Meta:
        model = Notification
        fields = ["user", "type", "subject", "body"]
        widgets = {"body": forms.Textarea(attrs={"rows": 4})}


class AnnouncementForm(forms.Form):
    """An organiser's message to red teams, blue teams or everybody.

    Replaces the old `BroadcastNotificationForm`, which picked recipients
    one checkbox at a time, was wired to no URL, and created
    notifications without `send_email`, so it never actually reached
    anyone. Audiences here are sides, because that is how organisers
    think about who needs telling.
    """

    SIDE_CHOICES = (
        ("all", "Everybody"),
        ("red", "Red teams only"),
        ("blue", "Blue teams only"),
    )

    side = forms.ChoiceField(choices=SIDE_CHOICES, initial="all")
    subject = forms.CharField(
        max_length=200,
        help_text="Also the email subject line, so make it mean something on its own.",
    )
    body = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 10}),
        help_text="Plain text. Blank lines separate paragraphs.",
    )
    pin = forms.BooleanField(
        required=False,
        initial=True,
        label="Also pin to their dashboards",
        help_text="An email is read once. Pin anything teams need to see every visit.",
    )
    expires_at = forms.DateTimeField(
        required=False,
        label="Un-pin automatically after",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        help_text="Optional. Leave blank to take it down by hand.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["expires_at"].input_formats = ["%Y-%m-%dT%H:%M"]

    def clean_expires_at(self):
        when = self.cleaned_data.get("expires_at")
        if when and when <= timezone.now():
            raise forms.ValidationError(
                "That moment has already passed, so the notice would never show."
            )
        return when

    def clean(self):
        data = super().clean()
        side = data.get("side")
        if side:
            try:
                if not announce_svc.audience(side).exists():
                    self.add_error(
                        "side", f"There are no {side} teams to announce to."
                    )
            except announce_svc.AnnouncementError as exc:
                self.add_error("side", str(exc))
        return data
