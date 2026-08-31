"""Forms for composing notifications.

Most notifications are created by the system (submission confirmed,
evaluation complete, deadlines). This form covers the admin case of
sending an announcement to selected users.
"""
from django import forms
from django.contrib.auth.models import User

from .models import Notification


class NotificationForm(forms.ModelForm):
    class Meta:
        model = Notification
        fields = ["user", "type", "subject", "body", "link"]
        widgets = {"body": forms.Textarea(attrs={"rows": 4})}


class BroadcastNotificationForm(forms.Form):
    """Send one notification to many users at once."""

    recipients = forms.ModelMultipleChoiceField(
        queryset=User.objects.filter(is_active=True),
        widget=forms.CheckboxSelectMultiple,
    )
    type = forms.ChoiceField(
        choices=Notification.Type.choices, initial=Notification.Type.GENERAL
    )
    subject = forms.CharField(max_length=200)
    body = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}), required=False)

    def save(self):
        """Create one Notification per recipient."""
        data = self.cleaned_data
        return Notification.objects.bulk_create(
            [
                Notification(
                    user=user,
                    type=data["type"],
                    subject=data["subject"],
                    body=data["body"],
                )
                for user in data["recipients"]
            ]
        )
