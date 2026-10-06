"""Competition settings -- the deadline timeline.

    python manage.py test competitions

The four deadlines have to be saved in the order the event actually runs,
and the form's pairwise check is the only thing enforcing it.
"""
import datetime

from django.test import TestCase

from competitions.forms import CompetitionSettingsForm
from competitions.models import Competition, CompetitionSettings


def make_competition():
    return Competition.objects.create(
        name="JS26 test",
        start_date=datetime.date(2026, 10, 14),
        end_date=datetime.date(2026, 10, 17),
        status=Competition.Status.OPEN,
    )


# The real JS26 timeline: registration shuts, proposals land the next day,
# the build runs to the event, and the documents are due at the end.
TIMELINE = {
    "registration_deadline": "2026-10-07T13:00",
    "proposal_deadline": "2026-10-08T13:00",
    "build_deadline": "2026-10-14T08:00",
    "submission_deadline": "2026-10-17T23:59",
}

OTHER_FIELDS = {
    "max_proposals_per_team": 1,
    "default_proposal_cap": 2,
    "appeal_window_days": 3,
    "enforcement_level": CompetitionSettings.Enforcement.STRICT,
}


class DeadlineOrderingTests(TestCase):
    """Proposals come before the build, not after.

    The check used to compare the deadlines in the order
    registration -> build -> proposal, which forced `build <= proposal`
    and rejected the correct timeline with "The Proposal deadline cannot
    fall before the Build deadline."
    """

    def setUp(self):
        self.settings_row = make_competition().settings

    def form(self, **overrides):
        data = {**TIMELINE, **OTHER_FIELDS, **overrides}
        return CompetitionSettingsForm(data, instance=self.settings_row)

    def test_the_real_timeline_is_accepted(self):
        form = self.form()
        self.assertTrue(form.is_valid(), form.errors)

    def test_a_proposal_deadline_after_the_build_is_rejected(self):
        form = self.form(proposal_deadline="2026-10-15T13:00")
        self.assertFalse(form.is_valid())
        self.assertIn(
            "The Build deadline cannot fall before the Proposal deadline.",
            form.errors["__all__"],
        )

    def test_a_proposal_deadline_before_registration_closes_is_rejected(self):
        form = self.form(proposal_deadline="2026-10-06T13:00")
        self.assertFalse(form.is_valid())
        self.assertIn(
            "The Proposal deadline cannot fall before the Registration deadline.",
            form.errors["__all__"],
        )

    def test_a_blank_proposal_deadline_is_skipped(self):
        """It is optional: proposals then answer to the submission deadline."""
        form = self.form(proposal_deadline="")
        self.assertTrue(form.is_valid(), form.errors)

    def test_the_saved_values_land_in_east_africa_time(self):
        from django.utils import timezone

        form = self.form()
        self.assertTrue(form.is_valid(), form.errors)
        row = form.save()
        local = timezone.localtime(row.proposal_deadline)
        self.assertEqual((local.hour, local.minute), (13, 0))
        self.assertEqual(local.date(), datetime.date(2026, 10, 8))


class KeyDatesTests(TestCase):
    """The one timeline all three surfaces read from.

    Added because the team dashboards were rendering
    `competition.end_date` under the heading "registration closes" --
    three places each reaching for their own field is how that happened.
    """

    def setUp(self):
        from django.utils import timezone

        self.comp = make_competition()
        row = self.comp.settings
        row.registration_deadline = timezone.now() - datetime.timedelta(days=1)
        row.proposal_deadline = timezone.now() + datetime.timedelta(days=1)
        row.build_deadline = timezone.now() + datetime.timedelta(days=7)
        row.submission_deadline = timezone.now() + datetime.timedelta(days=10)
        row.save()
        self.comp.refresh_from_db()

    def dates(self, team=None):
        from services import competition_service

        return competition_service.key_dates(self.comp, team)

    def test_every_set_deadline_appears_in_order(self):
        kinds = [d["kind"] for d in self.dates()]
        self.assertEqual(kinds, ["registration", "proposal", "build", "submission"])

    def test_a_past_deadline_is_marked_closed(self):
        rows = {d["kind"]: d for d in self.dates()}
        self.assertTrue(rows["registration"]["passed"])
        self.assertFalse(rows["proposal"]["passed"])

    def test_exactly_one_row_is_flagged_as_next(self):
        flagged = [d["kind"] for d in self.dates() if d["next"]]
        self.assertEqual(flagged, ["proposal"])

    def test_unset_deadlines_are_omitted(self):
        row = self.comp.settings
        row.build_deadline = None
        row.save()
        self.assertNotIn("build", [d["kind"] for d in self.dates()])

    def test_application_red_is_not_told_about_proposals(self):
        """It registers only and files no proposal (Guide section 3)."""
        from accounts.models import Team
        from competitions.constants import Track
        from django.contrib.auth.models import User

        captain = User.objects.create_user("redcap", password="x")
        team = Team.objects.create(
            competition=self.comp, team_name="Nyati", team_type=Team.TeamType.RED,
            track=Track.APPLICATION, captain=captain, institution="Strathmore",
        )
        self.assertFalse(team.submits_proposal)
        self.assertNotIn("proposal", [d["kind"] for d in self.dates(team)])

    def test_a_proposal_filing_team_is_told(self):
        from accounts.models import Team
        from competitions.constants import Track
        from django.contrib.auth.models import User

        captain = User.objects.create_user("aicap", password="x")
        team = Team.objects.create(
            competition=self.comp, team_name="Chui", team_type=Team.TeamType.RED,
            track=Track.AI, captain=captain, institution="Strathmore",
        )
        self.assertTrue(team.submits_proposal)
        self.assertIn("proposal", [d["kind"] for d in self.dates(team)])

    def test_no_deadlines_set_gives_an_empty_list(self):
        row = self.comp.settings
        row.registration_deadline = None
        row.proposal_deadline = None
        row.build_deadline = None
        row.submission_deadline = None
        row.save()
        self.assertEqual(self.dates(), [])


class LandingPageTests(TestCase):
    """The public page must show the dates, and must never 500."""

    def test_the_dates_reach_the_page(self):
        from django.utils import timezone
        from django.urls import reverse

        comp = make_competition()
        row = comp.settings
        row.registration_deadline = timezone.now() + datetime.timedelta(days=2)
        row.save()

        response = self.client.get(reverse("landing"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["key_dates"])
        self.assertIn("Registration closes", response.content.decode())

    def test_it_still_renders_with_no_competition(self):
        from django.urls import reverse

        response = self.client.get(reverse("landing"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["key_dates"], [])


class RedQualifierTests(TestCase):
    """Application Red earns its place through the HTB qualifier.

    The date used to be hardcoded in three files ("1 October 2026") and
    went stale without anyone noticing, so it now lives on
    CompetitionSettings and reaches the page from there.
    """

    def setUp(self):
        from django.utils import timezone

        self.comp = make_competition()
        row = self.comp.settings
        row.registration_deadline = timezone.now() + datetime.timedelta(days=1)
        row.proposal_deadline = timezone.now() + datetime.timedelta(days=2)
        row.red_qualifier_opens_at = timezone.now() + datetime.timedelta(days=2)
        row.build_deadline = timezone.now() + datetime.timedelta(days=8)
        row.save()
        self.comp.refresh_from_db()

    def team(self, team_type, track, name):
        from accounts.models import Team
        from django.contrib.auth.models import User

        captain = User.objects.create_user(name, password="x")
        return Team.objects.create(
            competition=self.comp, team_name=name.title(), team_type=team_type,
            track=track, captain=captain, institution="Strathmore",
        )

    def kinds(self, team):
        from services import competition_service

        return [d["kind"] for d in competition_service.key_dates(self.comp, team)]

    def test_application_red_sees_the_qualifier_and_no_proposal(self):
        from accounts.models import Team
        from competitions.constants import Track

        team = self.team(Team.TeamType.RED, Track.APPLICATION, "appred")
        kinds = self.kinds(team)
        self.assertIn("qualifier", kinds)
        self.assertNotIn("proposal", kinds)

    def test_ai_red_sees_the_proposal_and_no_qualifier(self):
        from accounts.models import Team
        from competitions.constants import Track

        team = self.team(Team.TeamType.RED, Track.AI, "aired")
        kinds = self.kinds(team)
        self.assertIn("proposal", kinds)
        self.assertNotIn("qualifier", kinds)

    def test_blue_teams_never_see_the_qualifier(self):
        from accounts.models import Team
        from competitions.constants import Track

        for track in (Track.APPLICATION, Track.AI):
            team = self.team(Team.TeamType.BLUE, track, f"blue{track}")
            self.assertNotIn("qualifier", self.kinds(team))

    def test_the_organiser_form_can_set_it(self):
        form = CompetitionSettingsForm(
            {**TIMELINE, **OTHER_FIELDS, "red_qualifier_opens_at": "2026-10-08T09:00"},
            instance=self.comp.settings,
        )
        self.assertTrue(form.is_valid(), form.errors)
        row = form.save()
        from django.utils import timezone

        local = timezone.localtime(row.red_qualifier_opens_at)
        self.assertEqual(local.date(), datetime.date(2026, 10, 8))
        self.assertEqual((local.hour, local.minute), (9, 0))

    def test_it_is_optional(self):
        """Blank is honest when the date is not fixed; the page says TBA."""
        form = CompetitionSettingsForm(
            {**TIMELINE, **OTHER_FIELDS, "red_qualifier_opens_at": ""},
            instance=self.comp.settings,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertNotIn("qualifier", [d["kind"] for d in self.kinds_for_blank()])

    def kinds_for_blank(self):
        from services import competition_service

        row = self.comp.settings
        row.red_qualifier_opens_at = None
        row.save()
        self.comp.refresh_from_db()
        return competition_service.key_dates(self.comp)

    def test_no_stale_hardcoded_date_remains(self):
        """The old copy named a date in three files; none may name one."""
        from pathlib import Path

        for path in (
            "templates/dashboard/red/registration.html",
            "accounts/forms.py",
            "services/submission_service.py",
        ):
            text = Path(path).read_text(encoding="utf-8")
            self.assertNotIn("1 October", text, f"{path} still hardcodes a date")
