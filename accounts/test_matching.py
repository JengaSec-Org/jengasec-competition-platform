"""Putting unassigned competitors on teams with a seat free.

    python manage.py test accounts.test_matching

Two opt-ins have to meet: a person declares a side and asks to be
placed, a captain says the team will consider strangers. Neither side is
automatic, and the invitation itself is the existing one -- so every
rule about size, verification and locked registrations still applies.
"""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from accounts.models import Team, UserProfile
from accounts.tests import make_competition, make_team, make_user
from services import matching_service as svc
from services import team_service


class PoolTests(TestCase):
    def setUp(self):
        self.comp = make_competition()
        self.captain = make_user("skipper")
        self.team = make_team(self.comp, self.captain, team_type=Team.TeamType.BLUE)
        self.team.open_to_recruits = True
        self.team.save()
        self.drifter = make_user("drifter")
        UserProfile.objects.filter(user=self.drifter).update(role="")

    def test_declaring_a_side_puts_you_in_the_pool(self):
        svc.join_pool(self.drifter, UserProfile.Role.BLUE_TEAM)
        profile = self.drifter.profile
        profile.refresh_from_db()
        self.assertTrue(profile.seeking_team)
        self.assertEqual(profile.role, UserProfile.Role.BLUE_TEAM)
        self.assertIsNotNone(profile.seeking_since)
        self.assertIn(profile, list(svc.pool_for(self.team)))

    def test_the_other_side_does_not_see_you(self):
        svc.join_pool(self.drifter, UserProfile.Role.RED_TEAM)
        self.assertNotIn(self.drifter.profile, list(svc.pool_for(self.team)))

    def test_somebody_already_on_a_team_cannot_join_the_pool(self):
        with self.assertRaises(svc.MatchingError):
            svc.join_pool(self.captain, UserProfile.Role.BLUE_TEAM)

    def test_an_unverified_account_cannot_join_the_pool(self):
        UserProfile.objects.filter(user=self.drifter).update(email_verified_at=None)
        # Re-fetch: the setUp helper has already touched `.profile`, so the
        # instance holds a cached copy that the queryset update did not
        # touch. A real request always starts from a fresh user.
        fresh = User.objects.get(pk=self.drifter.pk)
        with self.assertRaises(svc.MatchingError):
            svc.join_pool(fresh, UserProfile.Role.BLUE_TEAM)

    def test_an_unknown_side_is_refused(self):
        with self.assertRaises(svc.MatchingError):
            svc.join_pool(self.drifter, "purple")

    def test_leaving_the_pool_keeps_the_declared_side(self):
        svc.join_pool(self.drifter, UserProfile.Role.BLUE_TEAM)
        svc.leave_pool(self.drifter)
        profile = self.drifter.profile
        profile.refresh_from_db()
        self.assertFalse(profile.seeking_team)
        self.assertEqual(profile.role, UserProfile.Role.BLUE_TEAM)

    def test_being_seated_takes_you_out_of_the_pool(self):
        svc.join_pool(self.drifter, UserProfile.Role.BLUE_TEAM)
        team_service.add_member(self.team, self.drifter)
        svc.on_joined_team(self.drifter)
        self.assertNotIn(self.drifter.profile, list(svc.pool_for(self.team)))

    def test_accepting_an_invitation_clears_the_flag(self):
        """Through the real accept path, not the helper."""
        svc.join_pool(self.drifter, UserProfile.Role.BLUE_TEAM)
        invitation = team_service.invite(self.team, self.drifter.email, self.captain)
        team_service.accept_invitation(invitation, self.drifter)
        profile = self.drifter.profile
        profile.refresh_from_db()
        self.assertFalse(profile.seeking_team)


class SeatCountingTests(TestCase):
    def setUp(self):
        self.comp = make_competition()
        self.captain = make_user("skipper")
        self.team = make_team(self.comp, self.captain, team_type=Team.TeamType.BLUE)

    def test_teams_are_exactly_four(self):
        self.assertEqual(team_service.MIN_MEMBERS, 4)
        self.assertEqual(team_service.MAX_MEMBERS, 4)

    def test_seats_free_counts_down_as_members_join(self):
        self.assertEqual(svc.seats_free(self.team), 3)
        for name in ("one", "two"):
            team_service.add_member(self.team, make_user(name))
        self.assertEqual(svc.seats_free(self.team), 1)

    def test_a_full_team_is_not_listed_as_short(self):
        self.team.open_to_recruits = True
        self.team.save()
        for name in ("one", "two", "three"):
            team_service.add_member(self.team, make_user(name))
        self.assertEqual(svc.seats_free(self.team), 0)
        self.assertNotIn(self.team, list(svc.teams_with_seats(self.comp)))

    def test_only_opted_in_teams_are_listed_by_default(self):
        self.assertNotIn(self.team, list(svc.teams_with_seats(self.comp)))
        self.assertIn(self.team, list(svc.teams_with_seats(self.comp, open_only=False)))

    def test_a_three_person_team_is_now_one_short(self):
        """Three used to be a legal roster."""
        from notifications.management.commands.send_registration_reminders import (
            outstanding_for,
        )

        for name in ("one", "two"):
            team_service.add_member(self.team, make_user(name))
        items = " ".join(outstanding_for(self.team))
        self.assertIn("exactly 4 people", items)

    def test_a_locked_team_cannot_change_its_recruiting_flag(self):
        self.team.status = Team.Status.SUBMITTED
        self.team.save()
        with self.assertRaises(team_service.TeamRuleError):
            svc.set_open_to_recruits(self.team, True)


class MatchingPagesTests(TestCase):
    def setUp(self):
        self.comp = make_competition()
        self.captain = make_user("skipper")
        self.team = make_team(self.comp, self.captain, team_type=Team.TeamType.BLUE)
        self.drifter = make_user("drifter")
        UserProfile.objects.filter(user=self.drifter).update(role="")

    def test_find_team_renders(self):
        """A POST-only test missed a NameError on the GET path."""
        self.client.force_login(self.drifter)
        response = self.client.get(reverse("accounts:find_team"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("Which side", response.content.decode())

    def test_find_team_lets_you_declare_a_side(self):
        self.client.force_login(self.drifter)
        response = self.client.post(
            reverse("accounts:find_team"), {"side": "blue_team"}, follow=True
        )
        self.assertEqual(response.status_code, 200)
        self.drifter.profile.refresh_from_db()
        self.assertTrue(self.drifter.profile.seeking_team)
        self.assertIn("You are on the list", response.content.decode())

    def test_find_team_redirects_somebody_who_has_a_team(self):
        self.client.force_login(self.captain)
        response = self.client.get(reverse("accounts:find_team"))
        self.assertRedirects(response, reverse("accounts:team_requests"))

    def test_a_captain_can_open_the_team_and_see_the_pool(self):
        svc.join_pool(self.drifter, UserProfile.Role.BLUE_TEAM)
        self.client.force_login(self.captain)
        self.client.post(reverse("accounts:recruit"), {"action": "open"})
        self.team.refresh_from_db()
        self.assertTrue(self.team.open_to_recruits)

        body = self.client.get(reverse("accounts:recruit")).content.decode()
        self.assertIn("drifter", body)

    def test_a_closed_team_is_shown_nobody(self):
        svc.join_pool(self.drifter, UserProfile.Role.BLUE_TEAM)
        self.client.force_login(self.captain)
        body = self.client.get(reverse("accounts:recruit")).content.decode()
        self.assertNotIn("drifter", body)

    def test_a_non_captain_is_turned_away(self):
        member = make_user("crew")
        team_service.add_member(self.team, member)
        self.client.force_login(member)
        response = self.client.get(reverse("accounts:recruit"))
        self.assertRedirects(response, reverse("accounts:team_requests"))

    def test_inviting_from_the_pool_uses_the_normal_invitation(self):
        svc.join_pool(self.drifter, UserProfile.Role.BLUE_TEAM)
        self.client.force_login(self.captain)
        self.client.post(reverse("accounts:recruit"), {"action": "open"})
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                reverse("accounts:recruit"),
                {"action": "invite", "email": self.drifter.email},
            )
        self.assertTrue(
            self.team.invitations.filter(email__iexact=self.drifter.email).exists()
        )

    def test_the_old_dead_end_now_offers_the_pool(self):
        """`choose_team` used to say only "wait for your captain"."""
        self.client.force_login(self.drifter)
        body = self.client.get(reverse("accounts:choose_team")).content.decode()
        self.assertIn(reverse("accounts:find_team"), body)


class ConsentTests(TestCase):
    """Consent recorded at the point of collection, and visible afterwards."""

    PASSWORD = "jengasec-test-pass-9241"

    def register(self, **overrides):
        data = {
            "username": "newcomer", "first_name": "New", "last_name": "Comer",
            "email": "newcomer@strathmore.edu",
            "password1": self.PASSWORD, "password2": self.PASSWORD,
            "privacy_accepted": "on",
        }
        data.update(overrides)
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(reverse("accounts:register"), data)

    def test_registering_records_the_consent(self):
        self.register()
        profile = User.objects.get(username="newcomer").profile
        self.assertIsNotNone(profile.privacy_accepted_at)
        self.assertIsNotNone(profile.privacy_accepted_ip)

    def test_registration_is_refused_without_consent(self):
        response = self.register(privacy_accepted="")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="newcomer").exists())
        self.assertContains(response, "agree to the data privacy notice")

    def test_the_register_is_staff_only(self):
        self.assertEqual(
            self.client.get(reverse("accounts:consent_register")).status_code, 302
        )

    def test_the_register_reports_both_consents_separately(self):
        self.register()
        staff = User.objects.create_user("op", password="x", is_staff=True)
        self.client.force_login(staff)
        body = self.client.get(reverse("accounts:consent_register")).content.decode()
        self.assertIn("newcomer", body)
        self.assertIn("Agreed", body)       # privacy, ticked at registration
        self.assertIn("Outstanding", body)  # code of conduct, not yet accepted

    def test_the_export_is_a_csv_with_both_columns(self):
        self.register()
        staff = User.objects.create_user("op", password="x", is_staff=True)
        self.client.force_login(staff)
        response = self.client.get(reverse("accounts:consent_export"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
        body = response.content.decode()
        self.assertIn("privacy notice accepted", body)
        self.assertIn("code of conduct", body)
        self.assertIn("newcomer", body)


class RosterCallTests(TestCase):
    """The two emails: a team short of four, and a person with none."""

    def setUp(self):
        self.comp = make_competition()
        self.captain = make_user("skipper")
        self.team = make_team(self.comp, self.captain, team_type=Team.TeamType.BLUE)
        self.mate = make_user("mate")
        team_service.add_member(self.team, self.mate)
        self.drifter = make_user("drifter")
        UserProfile.objects.filter(user=self.drifter).update(role="")

    def run_command(self, *args):
        from io import StringIO
        from django.core.management import call_command

        out = StringIO()
        call_command("send_roster_call", *args, stdout=out)
        return out.getvalue()

    def body_for(self, user):
        from notifications.models import Notification

        return Notification.objects.get(user=user).body

    def test_preview_sends_nothing(self):
        from notifications.models import Notification

        output = self.run_command()
        self.assertIn("Preview only", output)
        self.assertFalse(Notification.objects.exists())

    def test_everyone_on_a_short_team_is_told_not_just_the_captain(self):
        self.run_command("--confirm")
        self.assertIn("short of four", self.body_for(self.captain))
        self.assertIn("short of four", self.body_for(self.mate))

    def test_only_the_captain_is_pointed_at_the_recruit_page(self):
        self.run_command("--confirm")
        self.assertIn("/accounts/recruit/", self.body_for(self.captain))
        self.assertNotIn("/accounts/recruit/", self.body_for(self.mate))

    def test_somebody_with_no_team_gets_the_other_email(self):
        self.run_command("--confirm")
        body = self.body_for(self.drifter)
        self.assertIn("not on a team yet", body)
        self.assertIn("/accounts/find-team/", body)

    def test_a_declared_side_is_not_asked_to_declare_again(self):
        svc.join_pool(self.drifter, UserProfile.Role.BLUE_TEAM)
        self.run_command("--confirm")
        body = self.body_for(self.drifter)
        self.assertIn("You are listed to captains", body)
        self.assertNotIn("Tell us whether you want to play", body)

    def test_only_short_skips_the_teamless(self):
        from notifications.models import Notification

        self.run_command("--only", "short", "--confirm")
        self.assertFalse(Notification.objects.filter(user=self.drifter).exists())
        self.assertTrue(Notification.objects.filter(user=self.captain).exists())

    def test_only_teamless_skips_the_short_teams(self):
        from notifications.models import Notification

        self.run_command("--only", "teamless", "--confirm")
        self.assertTrue(Notification.objects.filter(user=self.drifter).exists())
        self.assertFalse(Notification.objects.filter(user=self.captain).exists())

    def test_a_full_team_is_not_chased(self):
        from notifications.models import Notification

        for name in ("three", "four"):
            team_service.add_member(self.team, make_user(name))
        self.run_command("--only", "short", "--confirm")
        self.assertFalse(Notification.objects.filter(user=self.captain).exists())

    def test_sending_twice_at_the_same_seat_count_does_not_resend(self):
        from notifications.models import Notification

        self.run_command("--confirm")
        self.run_command("--confirm")
        self.assertEqual(Notification.objects.filter(user=self.captain).count(), 1)

    def test_it_is_offered_in_the_console(self):
        from notifications import jobs

        job = jobs.get("roster_call")
        self.assertIsNotNone(job)
        self.assertEqual(job.command, "send_roster_call")
