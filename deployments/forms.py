"""The submission form, field for field as teams were told to prepare it.

The Image Submission Guide lists exactly eight fields, so this form has
exactly those eight. Two of them are free text on the page and
structured in the database -- environment variables and extra images are
typed one per line, because a team filling this in at midnight should not
have to write JSON.
"""
import re

from django import forms

from services.deployment_service import MAX_EXTRA_IMAGES

from .models import AppSubmission

# A registry reference with a tag: host/path:tag. Digests are the range's
# business, not something a team types.
IMAGE_RE = re.compile(r"^[a-z0-9][a-z0-9.\-]*(:[0-9]+)?/[a-z0-9._\-/]+:[A-Za-z0-9._\-]+$")

# Keys that must never arrive as "non-secret settings". Mirrors
# never_log_keys in the range's contracts/event-catalogue.yaml.
SECRET_KEYS = (
    "password", "passwd", "secret", "token", "access_token", "refresh_token",
    "card_number", "pan", "cvv", "id_number", "national_id", "passport",
    "api_key", "apikey", "private_key",
)


def _check_image(value, field_label="Image reference"):
    """Validate one image reference, returning it unchanged."""
    value = value.strip()
    if not IMAGE_RE.match(value):
        raise forms.ValidationError(
            f"{field_label} must be the full name including the registry and a tag, "
            f"for example ghcr.io/amina-dev/internet-banking-portal:v3."
        )
    if value.rsplit(":", 1)[1] == "latest":
        raise forms.ValidationError(
            "Use a new tag for every submission (v1, v2, v3) rather than 'latest', "
            "or we cannot tell which image was frozen."
        )
    return value


class AppSubmissionForm(forms.ModelForm):
    """What a captain fills in on the team page."""

    pull_token = forms.CharField(
        label="Read-only token",
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "off"}),
        help_text="GitHub: a classic token with only read:packages. Docker Hub: a "
                  "read-only token. Never a password or a token with write access. "
                  "Stored encrypted and shown to nobody.",
    )
    env_text = forms.CharField(
        label="Environment variables",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "APP_MODE=production"}),
        help_text="One per line, KEY=value. Non-secret settings only.",
    )
    extras_text = forms.CharField(
        label="Extra images",
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text=f"Optional, one per line, up to {MAX_EXTRA_IMAGES} — for example a "
                  f"background worker. They use the same token.",
    )

    class Meta:
        model = AppSubmission
        fields = ["image_reference", "registry_username", "port", "health_path", "needs_database"]
        labels = {
            "image_reference": "Image reference",
            "registry_username": "Registry username",
            "health_path": "Health-check path",
            "needs_database": "Needs a database",
        }

    def __init__(self, *args, previous=None, **kwargs):
        """`previous` pre-fills from the last attempt, minus the token."""
        super().__init__(*args, **kwargs)
        if previous is not None and not self.is_bound:
            self.initial.setdefault("image_reference", previous.image_reference)
            self.initial.setdefault("registry_username", previous.registry_username)
            self.initial.setdefault("port", previous.port)
            self.initial.setdefault("health_path", previous.health_path)
            self.initial.setdefault("needs_database", previous.needs_database)
            self.initial.setdefault(
                "env_text",
                "\n".join(f"{k}={v}" for k, v in (previous.env_vars or {}).items()),
            )
            self.initial.setdefault("extras_text", "\n".join(previous.extra_images or []))
        for name, field in self.fields.items():
            if isinstance(field.widget, forms.CheckboxInput):
                continue
            css = "textarea" if isinstance(field.widget, forms.Textarea) else "input"
            field.widget.attrs.setdefault("class", css)

    def clean_image_reference(self):
        return _check_image(self.cleaned_data["image_reference"])

    def clean_health_path(self):
        path = self.cleaned_data["health_path"].strip()
        if not path.startswith("/"):
            raise forms.ValidationError("The health-check path starts with a slash, e.g. /healthz.")
        return path

    def clean_pull_token(self):
        token = self.cleaned_data["pull_token"].strip()
        if " " in token:
            raise forms.ValidationError("That does not look like a token — it contains a space.")
        return token

    def clean_env_text(self):
        """KEY=value lines into a dict, refusing anything secret-shaped."""
        env = {}
        for number, line in enumerate(self.cleaned_data.get("env_text", "").splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                raise forms.ValidationError(f"Line {number} is not KEY=value: {line!r}")
            key, value = line.split("=", 1)
            key = key.strip()
            if not key:
                raise forms.ValidationError(f"Line {number} has no name before the '='.")
            if any(secret in key.lower() for secret in SECRET_KEYS):
                raise forms.ValidationError(
                    f"{key} looks like a secret. This form takes non-secret settings only — "
                    f"secrets are mounted by the range, never typed in here."
                )
            env[key] = value.strip()
        return env

    def clean_extras_text(self):
        extras = [line.strip() for line in self.cleaned_data.get("extras_text", "").splitlines() if line.strip()]
        if len(extras) > MAX_EXTRA_IMAGES:
            raise forms.ValidationError(
                f"Up to {MAX_EXTRA_IMAGES} extra images. You listed {len(extras)}."
            )
        return [_check_image(value, "Every extra image") for value in extras]
