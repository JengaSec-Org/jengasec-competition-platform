"""A captain correcting their own team's details.

    python manage.py test accounts.test_team_edit

Reported by a participant: "I wrote to you last week raising concern on
my difficulty in finding the button or option to set the lead
institution... I am not even able to fill in the tracks." There was no
button, because there was no view and no route -- `institution` and
`track` were set at creation and then frozen with nothing to change
them.
"""
from django.test import TestCase
from django.urls import reverse

from accounts.models import Team
from accounts.tests import make_competition, make_team, make_user
from competitions.constants import Track
from competitions.models import ApplicationBrief
from services import team_service


class TeamEditTests(TestCase):
    def setUp(self):
        self.comp = make_competition()
        self.captain = make_user("skipper")
        self.team = make_team(self.comp, self.captain, team_type=Team.TeamType.BLUE)
        self.url = reverse("accounts:team_edit")
        # make_competition seeds no briefs, and an Application team cannot
        # be saved without one -- the form is right to refuse.
        self.brief = ApplicationBrief.objects.create(
            competition=self.comp, code="APP01", name="Internet Banking Portal",
        )

    def payload(self, **overrides):
        data = {
            "team_name": self.team.team_name,
            "team_type": self.team.team_type,
            "track": Track.APPLICATION,
            "application_brief": self.brief.pk if self.brief else "",
            "institution": "Strathmore University",
        }
        data.update(overrides)
        return data

    def test_the_page_exists_and_the_captain_can_open_it(self):
        self.client.force_login(self.captain)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Lead institution")
        self.assertContains(response, "Specialisation")

    def test_the_institution_can_be_set(self):
        self.client.force_login(self.captain)
        self.client.post(self.url, self.payload(institution="Kenyatta University"))
        self.team.refresh_from_db()
        self.assertEqual(self.team.institution, "Kenyatta University")

    def test_the_track_can_be_changed(self):
        self.client.force_login(self.captain)
        self.client.post(self.url, self.payload(track=Track.AI, application_brief=""))
        self.team.refresh_from_db()
        self.assertEqual(self.team.track, Track.AI)

    def test_an_application_team_must_name_a_brief(self):
        self.client.force_login(self.captain)
        response = self.client.post(
            self.url, self.payload(track=Track.APPLICATION, application_brief="")
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "pick the JengaBank application")

    def test_switching_to_ai_clears_the_brief(self):
        self.client.force_login(self.captain)
        self.client.post(self.url, self.payload(track=Track.AI, application_brief=""))
        self.team.refresh_from_db()
        self.assertIsNone(self.team.application_brief)

    def test_a_member_who_is_not_captain_is_turned_away(self):
        member = make_user("crew")
        team_service.add_member(self.team, member)
        self.client.force_login(member)
        self.assertRedirects(
            self.client.get(self.url), reverse("accounts:team_requests")
        )

    def test_a_locked_registration_cannot_be_edited(self):
        self.team.status = Team.Status.SUBMITTED
        self.team.save()
        self.client.force_login(self.captain)
        self.assertRedirects(
            self.client.get(self.url), reverse("accounts:team_requests")
        )
        self.client.post(self.url, self.payload(institution="Sneaky Edit"))
        self.team.refresh_from_db()
        self.assertNotEqual(self.team.institution, "Sneaky Edit")

    def test_my_team_links_to_it(self):
        """The complaint was that no button existed."""
        self.client.force_login(self.captain)
        body = self.client.get(reverse("accounts:team_requests")).content.decode()
        self.assertIn(self.url, body)
