"""Organiser filtering, and the export that must match it.

    python manage.py test accounts.test_admin_filters

The one rule worth pinning: the CSV is the rows on screen. An export
that ignores the filters is how the wrong people get contacted.
"""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from accounts.models import Team, TeamMember, UserProfile
from accounts.tests import make_competition, make_team, make_user
from competitions.constants import Track
from services import roster_filters, team_service


class FilterTests(TestCase):
    def setUp(self):
        self.comp = make_competition()
        self.blue = make_team(
            self.comp, make_user("bluecap"), name="Simba", team_type=Team.TeamType.BLUE
        )
        self.red = make_team(
            self.comp, make_user("redcap"), name="Nyati", team_type=Team.TeamType.RED
        )
        self.red.track = Track.AI
        self.red.institution = "Kenyatta University"
        self.red.save()
        self.staff = User.objects.create_user("op", password="x", is_staff=True)

    def names(self, **params):
        return sorted(t.team_name for t in roster_filters.teams(params))

    def test_no_filters_returns_everything(self):
        self.assertEqual(self.names(), ["Nyati", "Simba"])

    def test_filtering_by_side(self):
        self.assertEqual(self.names(side=Team.TeamType.BLUE), ["Simba"])
        self.assertEqual(self.names(side=Team.TeamType.RED), ["Nyati"])

    def test_filtering_by_track(self):
        self.assertEqual(self.names(track=Track.AI), ["Nyati"])

    def test_filtering_by_status(self):
        self.blue.status = Team.Status.APPROVED
        self.blue.save()
        self.assertEqual(self.names(status=Team.Status.APPROVED), ["Simba"])

    def test_filtering_by_institution_is_a_partial_match(self):
        self.assertEqual(self.names(institution="kenyatta"), ["Nyati"])

    def test_filters_combine(self):
        self.assertEqual(self.names(side=Team.TeamType.RED, track=Track.AI), ["Nyati"])
        self.assertEqual(self.names(side=Team.TeamType.BLUE, track=Track.AI), [])

    def test_searching_finds_a_team_by_a_member_email(self):
        mate = make_user("mate")
        team_service.add_member(self.blue, mate)
        self.assertEqual(self.names(q=mate.email), ["Simba"])

    def test_short_rosters(self):
        self.assertEqual(self.names(state="short"), ["Nyati", "Simba"])
        for name in ("a", "b", "c"):
            team_service.add_member(self.blue, make_user(name))
        self.assertEqual(self.names(state="short"), ["Nyati"])
        self.assertEqual(self.names(state="full"), ["Simba"])

    def test_teams_with_no_institution(self):
        self.blue.institution = ""
        self.blue.save()
        self.assertEqual(self.names(state="no_institution"), ["Simba"])

    def test_querystring_keeps_the_other_filters(self):
        """Clicking one filter must not drop the ones already applied."""
        built = roster_filters.querystring(
            {"side": "blue", "institution": "Strathmore"}, status="approved"
        )
        self.assertIn("side=blue", built)
        self.assertIn("institution=Strathmore", built)
        self.assertIn("status=approved", built)


class MemberFilterTests(TestCase):
    def setUp(self):
        self.comp = make_competition()
        self.blue = make_team(
            self.comp, make_user("bluecap"), name="Simba", team_type=Team.TeamType.BLUE
        )
        self.red = make_team(
            self.comp, make_user("redcap"), name="Nyati", team_type=Team.TeamType.RED
        )
        self.staff = User.objects.create_user("op", password="x", is_staff=True)

    def emails(self, **params):
        return sorted(m.email for m in roster_filters.members(params))

    def test_filtering_people_by_side(self):
        self.assertEqual(self.emails(side=Team.TeamType.RED), ["redcap@strathmore.edu"])

    def test_filtering_people_with_no_id_document(self):
        UserProfile.objects.filter(user=self.blue.captain).update(identity_document="")
        found = self.emails(state="no_id")
        self.assertIn("bluecap@strathmore.edu", found)

    def test_filtering_by_role(self):
        mate = make_user("mate")
        team_service.add_member(self.blue, mate)
        captains = self.emails(role=TeamMember.MemberRole.CAPTAIN)
        self.assertIn("bluecap@strathmore.edu", captains)
        self.assertNotIn("mate@strathmore.edu", captains)


class AdminPageTests(TestCase):
    def setUp(self):
        self.comp = make_competition()
        self.blue = make_team(
            self.comp, make_user("bluecap"), name="Simba", team_type=Team.TeamType.BLUE
        )
        self.red = make_team(
            self.comp, make_user("redcap"), name="Nyati", team_type=Team.TeamType.RED
        )
        self.staff = User.objects.create_user("op", password="x", is_staff=True)
        self.client.force_login(self.staff)

    def test_the_roster_page_is_staff_only(self):
        self.client.force_login(make_user("nosy"))
        self.assertEqual(
            self.client.get(reverse("accounts:member_list")).status_code, 302
        )

    def test_team_list_narrows_by_side(self):
        body = self.client.get(reverse("accounts:team_list") + "?side=blue").content.decode()
        self.assertIn("Simba", body)
        self.assertNotIn("Nyati", body)

    def test_a_member_name_links_to_their_account(self):
        body = self.client.get(reverse("accounts:member_list")).content.decode()
        self.assertIn(
            reverse("accounts:user_edit", args=[self.blue.captain.pk]), body
        )

    def test_team_detail_links_members_to_their_accounts(self):
        body = self.client.get(
            reverse("accounts:team_detail", args=[self.blue.pk])
        ).content.decode()
        self.assertIn(reverse("accounts:user_edit", args=[self.blue.captain.pk]), body)

    def test_the_export_honours_the_filters(self):
        """The CSV is the rows on screen, not every row."""
        response = self.client.get(reverse("accounts:member_export") + "?side=blue")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("bluecap@strathmore.edu", body)
        self.assertNotIn("redcap@strathmore.edu", body)

    def test_the_export_honours_the_chosen_columns(self):
        response = self.client.get(
            reverse("accounts:member_export") + "?columns=name&columns=email"
        )
        header = response.content.decode().splitlines()[0]
        self.assertEqual(header, "Name,Email")

    def test_unknown_columns_are_ignored_and_defaults_used(self):
        response = self.client.get(
            reverse("accounts:member_export") + "?columns=password&columns=secret"
        )
        header = response.content.decode().splitlines()[0]
        self.assertIn("Team", header)
        self.assertNotIn("password", header.lower())
