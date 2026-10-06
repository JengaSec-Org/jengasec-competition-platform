"""Announcements, the job registry and the scheduled runner.

    python manage.py test notifications

The runner is the piece with teeth: it sends email to everyone and is
reachable without a login, so its guard is tested from the outside.
"""
import datetime

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Team, TeamMember
from competitions.models import Competition
from notifications import jobs
from notifications.models import Announcement, Notification
from services import announcement_service as svc


def make_competition():
    return Competition.objects.create(
        name="JS26 test",
        start_date=datetime.date(2026, 10, 14),
        end_date=datetime.date(2026, 10, 17),
        status=Competition.Status.OPEN,
    )


def make_team(comp, name, team_type, captain_name):
    captain = User.objects.create_user(
        captain_name, email=f"{captain_name}@example.test", password="x"
    )
    team = Team.objects.create(
        competition=comp, team_name=name, team_type=team_type,
        track=Team.Track.AI, captain=captain, institution="Strathmore",
    )
    TeamMember.objects.create(
        team=team, user=captain, student_name=captain_name,
        email=captain.email, role=TeamMember.MemberRole.CAPTAIN,
    )
    return team


def staff(username="op"):
    return User.objects.create_user(username, password="x", is_staff=True)


class AnnouncementAudienceTests(TestCase):
    def setUp(self):
        self.comp = make_competition()
        self.red = make_team(self.comp, "Nyati", Team.TeamType.RED, "redcap")
        self.blue = make_team(self.comp, "Simba", Team.TeamType.BLUE, "bluecap")

    def test_red_reaches_only_red(self):
        svc.announce("red", "Challenges delayed", "Waiting on partners.")
        self.assertTrue(Notification.objects.filter(user=self.red.captain).exists())
        self.assertFalse(Notification.objects.filter(user=self.blue.captain).exists())

    def test_all_reaches_both(self):
        svc.announce("all", "Venue change", "We have moved hall.")
        self.assertTrue(Notification.objects.filter(user=self.red.captain).exists())
        self.assertTrue(Notification.objects.filter(user=self.blue.captain).exists())

    def test_announcements_are_emailed_not_just_shown(self):
        """The form this replaced created notifications with no email."""
        svc.announce("all", "Venue change", "We have moved hall.")
        self.assertTrue(Notification.objects.filter(send_email=True).exists())

    def test_withdrawn_teams_are_left_out(self):
        self.red.status = Team.Status.WITHDRAWN
        self.red.save()
        with self.assertRaises(svc.AnnouncementError):
            svc.announce("red", "Challenges delayed", "Waiting on partners.")

    def test_sending_twice_does_not_double_up(self):
        svc.announce("red", "Challenges delayed", "Waiting on partners.")
        svc.announce("red", "Challenges delayed", "Waiting on partners.")
        self.assertEqual(Notification.objects.filter(user=self.red.captain).count(), 1)

    def test_an_empty_subject_or_body_is_refused(self):
        for subject, body in (("", "text"), ("subject", "   ")):
            with self.assertRaises(svc.AnnouncementError):
                svc.announce("all", subject, body)

    def test_an_unknown_audience_is_refused(self):
        with self.assertRaises(svc.AnnouncementError):
            svc.announce("purple", "Subject", "Body")

    def test_preview_sends_nothing(self):
        plan = svc.preview("red", "Challenges delayed", "Waiting on partners.")
        self.assertEqual(plan["team_count"], 1)
        self.assertEqual(plan["recipient_count"], 1)
        self.assertFalse(Notification.objects.exists())


class PinnedAnnouncementTests(TestCase):
    def setUp(self):
        self.comp = make_competition()
        self.red = make_team(self.comp, "Nyati", Team.TeamType.RED, "redcap")
        self.blue = make_team(self.comp, "Simba", Team.TeamType.BLUE, "bluecap")

    def pin(self, **kwargs):
        return svc.announce(
            "red", "Challenges delayed", "Waiting on partners.", pin=True, **kwargs
        )

    def test_pinning_shows_it_to_that_side_only(self):
        self.pin()
        self.assertEqual(Announcement.active_for(self.red).count(), 1)
        self.assertEqual(Announcement.active_for(self.blue).count(), 0)

    def test_deactivating_takes_it_down(self):
        self.pin()
        Announcement.objects.update(is_active=False)
        self.assertEqual(Announcement.active_for(self.red).count(), 0)

    def test_an_expiry_in_the_past_takes_it_down(self):
        self.pin()
        Announcement.objects.update(
            expires_at=timezone.now() - datetime.timedelta(minutes=1)
        )
        self.assertEqual(Announcement.active_for(self.red).count(), 0)

    def test_an_expiry_in_the_future_leaves_it_up(self):
        self.pin(expires_at=timezone.now() + datetime.timedelta(days=1))
        self.assertEqual(Announcement.active_for(self.red).count(), 1)

    def test_an_unscoped_announcement_shows_in_every_competition(self):
        Announcement.objects.create(
            competition=None, audience=Announcement.Audience.ALL,
            title="Site notice", body="Hello.",
        )
        self.assertEqual(Announcement.active_for(self.blue).count(), 1)

    def test_no_team_means_no_announcements(self):
        self.assertEqual(Announcement.active_for(None).count(), 0)

    def test_paragraphs_split_on_blank_lines(self):
        item = Announcement.objects.create(
            title="T", body="First para.\n\nSecond para.\n\n\nThird.",
        )
        self.assertEqual(item.paragraphs, ["First para.", "Second para.", "Third."])


class JobRegistryTests(TestCase):
    def test_every_job_names_a_real_command(self):
        from django.core.management import get_commands

        known = get_commands()
        for job in jobs.JOBS:
            self.assertIn(job.command, known, f"{job.key} points at a missing command")

    def test_keys_are_unique(self):
        keys = [job.key for job in jobs.JOBS]
        self.assertEqual(len(keys), len(set(keys)))

    def test_the_scheduled_set_is_a_subset(self):
        self.assertTrue(set(jobs.SCHEDULED).issubset(set(jobs.JOBS)))

    def test_one_failing_job_does_not_stop_the_rest(self):
        from unittest.mock import patch

        def explode(self_, args):
            raise RuntimeError("boom")

        with patch.object(jobs.Job, "_run", explode):
            results = jobs.run_scheduled()
        self.assertEqual(len(results), len(jobs.SCHEDULED))
        self.assertTrue(all(not row["ok"] for row in results.values()))
        self.assertIn("boom", next(iter(results.values()))["output"])


class ScheduledRunnerTests(TestCase):
    """The endpoint an external cron posts to."""

    def setUp(self):
        self.url = reverse("tasks_run")

    @override_settings(CRON_TOKEN="")
    def test_unconfigured_is_a_404_not_an_open_runner(self):
        self.assertEqual(self.client.post(self.url).status_code, 404)

    @override_settings(CRON_TOKEN="right-token")
    def test_a_missing_token_is_refused(self):
        self.assertEqual(self.client.post(self.url).status_code, 403)

    @override_settings(CRON_TOKEN="right-token")
    def test_a_wrong_token_is_refused(self):
        response = self.client.post(self.url, headers={"X-Cron-Token": "wrong"})
        self.assertEqual(response.status_code, 403)

    @override_settings(CRON_TOKEN="right-token")
    def test_get_is_not_allowed(self):
        """These jobs send email; a prefetch must not fire them."""
        response = self.client.get(self.url, headers={"X-Cron-Token": "right-token"})
        self.assertEqual(response.status_code, 405)

    @override_settings(CRON_TOKEN="right-token")
    def test_the_right_token_runs_the_scheduled_jobs(self):
        response = self.client.post(self.url, headers={"X-Cron-Token": "right-token"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(sorted(payload["ran"]), sorted(j.key for j in jobs.SCHEDULED))
        self.assertEqual(payload["failed"], [])


class ConsoleTests(TestCase):
    def setUp(self):
        self.comp = make_competition()
        make_team(self.comp, "Nyati", Team.TeamType.RED, "redcap")
        self.url = reverse("notifications:console")

    def test_anonymous_is_sent_to_login(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_a_signed_in_competitor_cannot_see_it(self):
        self.client.force_login(User.objects.create_user("nosy", password="x"))
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_staff_see_every_job(self):
        self.client.force_login(staff())
        body = self.client.get(self.url).content.decode()
        for job in jobs.JOBS:
            self.assertIn(job.label, body)

    def test_previewing_a_job_sends_nothing(self):
        self.client.force_login(staff())
        response = self.client.post(
            self.url, {"job": "data_completion", "action": "preview"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Notification.objects.exists())

    def test_an_unknown_job_is_a_404(self):
        self.client.force_login(staff())
        response = self.client.post(self.url, {"job": "rm-rf", "action": "preview"})
        self.assertEqual(response.status_code, 404)

    def test_announcing_from_the_console_reaches_the_side_and_pins(self):
        self.client.force_login(staff())
        response = self.client.post(self.url, {
            "action": "announce_send",
            "side": "red",
            "subject": "Challenges delayed",
            "body": "Waiting on partners.",
            "pin": "on",
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Notification.objects.filter(subject="Challenges delayed").exists())
        self.assertTrue(Announcement.objects.filter(title="Challenges delayed").exists())

    def test_announcing_to_a_side_with_no_teams_is_refused(self):
        self.client.force_login(staff())
        response = self.client.post(self.url, {
            "action": "announce_send",
            "side": "blue",          # only a red team exists
            "subject": "Nobody home",
            "body": "Text.",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Notification.objects.exists())

    def test_an_expiry_already_past_is_refused(self):
        self.client.force_login(staff())
        past = (timezone.now() - datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
        response = self.client.post(self.url, {
            "action": "announce_send",
            "side": "red",
            "subject": "Too late",
            "body": "Text.",
            "pin": "on",
            "expires_at": past,
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Announcement.objects.exists())
