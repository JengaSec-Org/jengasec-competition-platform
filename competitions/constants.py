"""Competition-wide choice sets.

Lives outside models.py so both `accounts` and `competitions` can import
it without a circular dependency.

Reference: JengaSec Final Competition Overview (Aug 2026) — two
enterprises, three tracks.
"""
from django.db import models


class Track(models.TextChoices):
    """Team specialisation. For a red team this is its attack surface."""

    CLOUD = "cloud", "Cloud"
    APPLICATION = "application", "Application"
    AI = "ai", "AI"


class Enterprise(models.TextChoices):
    A = "a", "Enterprise A — Financial"
    B = "b", "Enterprise B — Healthcare"
