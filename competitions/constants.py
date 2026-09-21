"""Competition-wide choice sets.

Lives outside models.py so both `accounts` and `competitions` can import
it without a circular dependency.

Reference: JengaSec Entry Guide v1.0 (4 Sep 2026), the JengaBank edition.
Two enterprises, both running the JengaBank architecture; four tracks,
which are the cross-product of two specialisations and two sides. The
Cloud track is not offered in this edition.
"""
from django.db import models

EDITION = "JS26"


class Track(models.TextChoices):
    """Team specialisation. Combined with the team's side (blue / red) this
    gives the four Guide tracks: Application Blue, AI Defence Blue,
    Application Red, AI Red."""

    APPLICATION = "application", "Application"
    AI = "ai", "AI"


class Enterprise(models.TextChoices):
    """Two independent JengaBank instances. Every brief is built twice --
    once in each -- so the comparison between them is part of what is
    judged."""

    A = "a", "ENTA — JengaBank A"
    B = "b", "ENTB — JengaBank B"


# Short forms used in cell codes and the mandatory file name
# (JS26_<ENTERPRISE>_<CELL>_<TEAMID>_<TYPE>_v<N>.pdf).
ENTERPRISE_CODES = {Enterprise.A: "ENTA", Enterprise.B: "ENTB"}


def track_name(team_type, track):
    """The Guide's name for a side + specialisation, e.g. 'AI Defence Blue'."""
    names = {
        ("blue", Track.APPLICATION): "Application Blue",
        ("blue", Track.AI): "AI Defence Blue",
        ("red", Track.APPLICATION): "Application Red",
        ("red", Track.AI): "AI Red",
    }
    return names.get((team_type, track), "")


def cell_prefix(team_type, track):
    """Cell code family for a side + specialisation: APP / AI / APPRED / AIRED."""
    base = "APP" if track == Track.APPLICATION else "AI"
    return base if team_type == "blue" else base + "RED"
