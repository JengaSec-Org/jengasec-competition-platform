"""Proposing answers to the proposal deadline, not the registration one.

    python manage.py test competitions.test_proposal_window

They are separate dates in the Entry Guide and on the site: registration
closed on the Tuesday and proposals ran past it. Tying proposing to
`registration_open` meant that the moment registration shut, a team that
had already registered could no longer file the proposal it had been told
to file — and reopening proposals meant reopening registration to new
teams, which is a different decision entirely.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from accounts.models import Team
from accounts.tests import make_competition, make_team, make_user
from competitions.constants import Track
from competitions.models import ApplicationBrief
from services import competition_service


class ProposalWindowTests(TestCase):
    def setUp(self):
        self.comp = make_competition()
        self.row = self.comp.settings
        self.team = make_team(
            self.comp, make_user("cap"), name="Simba", team_type=Team.TeamType.BLUE
        )
        self.team.track = Track.APPLICATION
        self.team.save()
        self.brief = ApplicationBrief.objects.filter(
            competition=self.comp, is_open=True
        ).first() or ApplicationBrief.objects.create(
            competition=self.comp, code="APP01", name="Internet Banking Portal",
            is_open=True,
        )

    def window(self, *, registration, proposal):
        now = timezone.now()
        self.row.registration_deadline = now + timedelta(days=registration)
        self.row.proposal_deadline = now + timedelta(days=proposal)
        self.row.submissions_open = True
        self.row.save()
        self.comp.refresh_from_db()

    def can(self):
        from competitions.models import Competition

        team = Team.objects.select_related("competition").get(pk=self.team.pk)
        return competition_service.can_propose(team, self.brief)

    def test_a_team_may_propose_after_registration_has_closed(self):
        """The case that prompted this: registration shut, proposals open."""
        self.window(registration=-1, proposal=+1)
        self.assertFalse(self.comp.registration_open)
        self.assertTrue(self.can())

    def test_a_team_may_propose_while_both_are_open(self):
        self.window(registration=+1, proposal=+2)
        self.assertTrue(self.can())

    def test_the_proposal_deadline_still_closes_the_window(self):
        self.window(registration=+5, proposal=-1)
        self.assertFalse(self.can())

    def test_closing_submissions_stops_proposals(self):
        """The organiser switch must stop it, not only hide the button."""
        self.window(registration=+1, proposal=+1)
        self.row.submissions_open = False
        self.row.save()
        self.assertFalse(self.can())

    def test_a_brief_from_another_competition_is_refused(self):
        self.window(registration=+1, proposal=+1)
        # A second competition: make_competition() names them all the same,
        # and the name is unique.
        from competitions.models import Competition
        import datetime

        other = Competition.objects.create(
            name="JS26 elsewhere", start_date=datetime.date(2026, 10, 14),
            end_date=datetime.date(2026, 10, 17), status=Competition.Status.OPEN,
        )
        stray = ApplicationBrief.objects.create(
            competition=other, code="APP02", name="Elsewhere", is_open=True
        )
        team = Team.objects.get(pk=self.team.pk)
        self.assertFalse(competition_service.can_propose(team, stray))

    def test_a_closed_brief_is_refused(self):
        self.window(registration=+1, proposal=+1)
        self.brief.is_open = False
        self.brief.save()
        self.assertFalse(self.can())

    def test_no_proposal_deadline_set_leaves_the_window_open(self):
        """Falls back to the submission deadline, and open if neither is set."""
        self.window(registration=-1, proposal=+1)
        self.row.proposal_deadline = None
        self.row.submission_deadline = None
        self.row.save()
        self.assertTrue(self.can())

    def test_it_falls_back_to_the_submission_deadline(self):
        self.window(registration=-1, proposal=+1)
        self.row.proposal_deadline = None
        self.row.submission_deadline = timezone.now() - timedelta(days=1)
        self.row.save()
        self.assertFalse(self.can())
