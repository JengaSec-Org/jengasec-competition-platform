"""Handing an application to the range.

    python manage.py test deployments

The rules worth pinning, in the order they can hurt somebody:

* the registry token is a team's own credential, so it must not come back
  out of any page, export or log;
* the freeze is a hard close, because the briefing told teams it was;
* only Application Blue submits, because the other three tracks were
  never told to;
* the machine endpoints answer 404 when unconfigured, the same way the
  scheduled runner does.
"""
import json

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Team
from accounts.tests import make_competition, make_team, make_user
from competitions.constants import Track
from services import deployment_service as deploy
from services import secret_box, team_service

from .models import AppSubmission

TOKEN = "ghp_exampleonly_not_a_real_token"
FIELDS = {
    "image_reference": "ghcr.io/amina-dev/internet-banking-portal:v3",
    "registry_username": "amina-dev",
    "port": 8080,
    "health_path": "/healthz",
    "needs_database": True,
}


def blue_app_team(competition, name="Simba", cell="APP01"):
    """An Application Blue team, which is the only kind that submits.

    `cell` is a parameter because one cell per enterprise is a database
    constraint, so two teams in one test need two cells.
    """
    team = make_team(
        competition, make_user(name.lower()), name=name, team_type=Team.TeamType.BLUE
    )
    team.track = Track.APPLICATION
    team.enterprise = "a"
    team.cell_id = cell
    team.service_ip = f"10.108.0.{int(cell[-2:]) + 10}"
    team.save()
    return team


def open_the_window(competition, hours=6):
    row = competition.settings
    row.build_deadline = timezone.now() + timezone.timedelta(hours=hours)
    row.save()
    return row


class SecretBoxTests(TestCase):
    def test_a_token_survives_the_round_trip(self):
        self.assertEqual(secret_box.decrypt(secret_box.encrypt(TOKEN)), TOKEN)

    def test_nothing_in_means_nothing_out(self):
        self.assertEqual(secret_box.encrypt(""), "")
        self.assertIsNone(secret_box.decrypt(""))

    def test_the_ciphertext_is_not_the_token(self):
        self.assertNotIn(TOKEN, secret_box.encrypt(TOKEN))

    def test_an_unreadable_value_is_not_fatal(self):
        """A rotated key must read as 'ask again', never as a 500."""
        self.assertIsNone(secret_box.decrypt("not-a-fernet-token"))


class EligibilityTests(TestCase):
    def setUp(self):
        self.comp = make_competition()
        open_the_window(self.comp)

    def test_application_blue_may_submit(self):
        allowed, _ = deploy.can_submit(blue_app_team(self.comp))
        self.assertTrue(allowed)

    def test_ai_blue_does_not_submit_an_image(self):
        """AI teams run their agents on their own machines (architecture 7)."""
        team = blue_app_team(self.comp, "Chui")
        team.track = Track.AI
        team.save()
        allowed, reason = deploy.can_submit(team)
        self.assertFalse(allowed)
        self.assertIn("Application Blue", reason)

    def test_red_does_not_submit_an_image(self):
        team = make_team(
            self.comp, make_user("redcap"), name="Nyati", team_type=Team.TeamType.RED
        )
        self.assertFalse(deploy.submits_application(team))

    def test_the_freeze_is_a_hard_close(self):
        team = blue_app_team(self.comp)
        open_the_window(self.comp, hours=-1)
        allowed, reason = deploy.can_submit(team)
        self.assertFalse(allowed)
        self.assertIn("build deadline passed", reason)
        with self.assertRaises(deploy.FreezePassed):
            deploy.submit(team, team.captain, pull_token=TOKEN, **FIELDS)


class SubmitTests(TestCase):
    def setUp(self):
        self.comp = make_competition()
        open_the_window(self.comp)
        self.team = blue_app_team(self.comp)

    def submit(self, **overrides):
        fields = {**FIELDS, **overrides}
        return deploy.submit(self.team, self.team.captain, pull_token=TOKEN, **fields)

    def test_attempts_count_up_and_are_all_kept(self):
        first = self.submit()
        second = self.submit(image_reference="ghcr.io/amina-dev/internet-banking-portal:v4")
        self.assertEqual((first.attempt, second.attempt), (1, 2))
        self.assertEqual(deploy.for_team(self.team).count(), 2)
        # Newest first, so the team page shows the current state.
        self.assertEqual(deploy.latest_for_team(self.team), second)

    def test_the_token_is_stored_encrypted_and_readable_back(self):
        submission = self.submit()
        self.assertNotIn(TOKEN, submission.pull_token_encrypted)
        self.assertTrue(submission.has_pull_token)
        self.assertEqual(submission.reveal_pull_token(), TOKEN)

    def test_only_a_healthy_attempt_counts_as_passing(self):
        first = self.submit()
        self.assertIsNone(deploy.last_passing(self.team))
        deploy.record_status(first, AppSubmission.Status.DEPLOYED)
        self.assertIsNone(deploy.last_passing(self.team))
        deploy.record_status(first, AppSubmission.Status.HEALTHY)
        self.assertEqual(deploy.last_passing(self.team), first)

    def test_the_progress_strip_fills_in_order(self):
        submission = self.submit()
        self.assertEqual(submission.reached, [])
        deploy.record_status(submission, AppSubmission.Status.SCANNED)
        self.assertEqual(
            [check["done"] for check in submission.progress], [True, True, False, False]
        )

    def test_an_unknown_status_is_refused(self):
        with self.assertRaises(ValueError):
            deploy.record_status(self.submit(), "nearly")


class FreezeTests(TestCase):
    def setUp(self):
        self.comp = make_competition()
        open_the_window(self.comp)
        self.team = blue_app_team(self.comp)
        self.other = blue_app_team(self.comp, "Chui", cell="APP02")

    def test_the_last_passing_image_is_selected_and_the_gap_is_reported(self):
        passing = deploy.submit(self.team, self.team.captain, pull_token=TOKEN, **FIELDS)
        deploy.record_status(passing, AppSubmission.Status.HEALTHY)
        # A later attempt that never passed must not displace it.
        broken = deploy.submit(
            self.team, self.team.captain, pull_token=TOKEN,
            **{**FIELDS, "image_reference": "ghcr.io/amina-dev/internet-banking-portal:v9"},
        )
        deploy.record_status(broken, AppSubmission.Status.FAILED, detail="exec format error")

        result = deploy.freeze(self.comp)
        passing.refresh_from_db()
        self.assertIn(passing, result["pinned"])
        self.assertFalse(passing.frozen_at)
        self.assertFalse(passing.frozen_digest)
        # The team with nothing passing is the one somebody has to ring.
        self.assertIn(self.other, result["missing"])

    def test_selecting_freeze_candidates_does_not_falsely_mark_them_frozen(self):
        passing = deploy.submit(self.team, self.team.captain, pull_token=TOKEN, **FIELDS)
        deploy.record_status(passing, AppSubmission.Status.HEALTHY)
        deploy.freeze(self.comp)
        passing.refresh_from_db()
        self.assertIsNone(passing.frozen_at)
        deploy.freeze(self.comp)
        passing.refresh_from_db()
        self.assertIsNone(passing.frozen_at)


class FormTests(TestCase):
    def setUp(self):
        self.comp = make_competition()
        open_the_window(self.comp)
        self.team = blue_app_team(self.comp)
        self.client.force_login(self.team.captain)

    def post(self, **overrides):
        data = {
            "image_reference": FIELDS["image_reference"],
            "registry_username": "amina-dev",
            "pull_token": TOKEN,
            "port": "8080",
            "health_path": "/healthz",
            "env_text": "",
            "extras_text": "",
        }
        data.update(overrides)
        return self.client.post(reverse("deployments:submit"), data)

    def test_a_good_submission_is_recorded(self):
        response = self.post()
        self.assertRedirects(response, reverse("deployments:submit"))
        self.assertEqual(AppSubmission.objects.count(), 1)

    def test_latest_is_refused_because_the_freeze_could_not_name_it(self):
        self.post(image_reference="ghcr.io/amina-dev/portal:latest")
        self.assertEqual(AppSubmission.objects.count(), 0)

    def test_a_reference_with_no_registry_or_tag_is_refused(self):
        self.post(image_reference="my-app")
        self.assertEqual(AppSubmission.objects.count(), 0)

    def test_environment_variables_become_a_dict(self):
        self.post(env_text="APP_MODE=production\nTZ=Africa/Nairobi")
        submission = AppSubmission.objects.get()
        self.assertEqual(
            submission.env_vars, {"APP_MODE": "production", "TZ": "Africa/Nairobi"}
        )

    def test_a_secret_shaped_variable_is_refused(self):
        """The form says non-secret settings only, so it must mean it."""
        self.post(env_text="DB_PASSWORD=hunter2")
        self.assertEqual(AppSubmission.objects.count(), 0)

    def test_at_most_two_extra_images(self):
        self.post(extras_text="\n".join([
            "ghcr.io/amina-dev/worker:v1",
            "ghcr.io/amina-dev/migrations:v1",
            "ghcr.io/amina-dev/extra:v1",
        ]))
        self.assertEqual(AppSubmission.objects.count(), 0)

    def test_a_health_path_must_be_a_path(self):
        self.post(health_path="healthz")
        self.assertEqual(AppSubmission.objects.count(), 0)


class AccessTests(TestCase):
    def setUp(self):
        self.comp = make_competition()
        open_the_window(self.comp)
        self.team = blue_app_team(self.comp)
        self.submission = deploy.submit(
            self.team, self.team.captain, pull_token=TOKEN, **FIELDS
        )

    def test_a_team_on_no_application_track_cannot_reach_the_form(self):
        outsider = make_team(
            self.comp, make_user("redcap"), name="Nyati", team_type=Team.TeamType.RED
        )
        self.client.force_login(outsider.captain)
        self.assertEqual(self.client.get(reverse("deployments:submit")).status_code, 404)

    def test_a_member_sees_the_page_but_is_offered_no_form(self):
        mate = make_user("mate")
        team_service.add_member(self.team, mate)
        self.client.force_login(mate)
        response = self.client.get(reverse("deployments:submit"))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["form"])
        self.assertContains(response, "Only your captain")

    def test_another_team_cannot_read_this_attempt(self):
        other = blue_app_team(self.comp, "Chui", cell="APP02")
        self.client.force_login(other.captain)
        url = reverse("deployments:detail", args=[self.submission.pk])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_the_token_never_appears_on_a_page(self):
        """The Guide promises it is shown to no one."""
        self.client.force_login(self.team.captain)
        for url in (
            reverse("deployments:submit"),
            reverse("deployments:detail", args=[self.submission.pk]),
        ):
            self.assertNotContains(self.client.get(url), TOKEN)

    def test_the_deploy_desk_is_staff_only(self):
        self.client.force_login(self.team.captain)
        self.assertNotEqual(self.client.get(reverse("deployments:queue")).status_code, 200)
        self.assertNotEqual(
            self.client.get(reverse("deployments:review", args=[self.submission.pk])).status_code,
            200,
        )

    def test_a_competitor_cannot_reveal_the_token(self):
        self.client.force_login(self.team.captain)
        response = self.client.post(
            reverse("deployments:reveal_token", args=[self.submission.pk])
        )
        self.assertNotEqual(response.status_code, 200)

    def test_the_organiser_screens_render(self):
        """The deploy desk is used under time pressure; it must not 500."""
        self.client.force_login(
            User.objects.create_user("desk", password="x", is_staff=True, is_superuser=True)
        )
        for url in (
            reverse("deployments:queue"),
            reverse("deployments:review", args=[self.submission.pk]),
            reverse("deployments:detail", args=[self.submission.pk]),
        ):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_an_organiser_recording_a_check_is_shown_to_the_team(self):
        self.client.force_login(
            User.objects.create_user("desk2", password="x", is_staff=True, is_superuser=True)
        )
        self.client.post(
            reverse("deployments:review", args=[self.submission.pk]),
            {"status": "failed", "detail": "Rebuild with --platform linux/amd64."},
        )
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, AppSubmission.Status.FAILED)
        self.client.force_login(self.team.captain)
        self.assertContains(
            self.client.get(reverse("deployments:submit")),
            "Rebuild with --platform linux/amd64.",
        )

    def test_an_organiser_can_reveal_it(self):
        self.client.force_login(
            User.objects.create_user("op", password="x", is_staff=True, is_superuser=True)
        )
        response = self.client.post(
            reverse("deployments:reveal_token", args=[self.submission.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), TOKEN)
        self.assertIn("no-store", response["Cache-Control"])


class IngestTests(TestCase):
    def setUp(self):
        self.comp = make_competition()
        open_the_window(self.comp)
        self.team = blue_app_team(self.comp)
        self.team.status = Team.Status.APPROVED
        self.team.save(update_fields=["status"])
        self.submission = deploy.submit(
            self.team, self.team.captain, pull_token=TOKEN, **FIELDS
        )
        self.status_url = reverse("deployments:api_status")
        self.pending_url = reverse("deployments:api_pending")
        self.freeze_candidates_url = reverse("deployments:api_freeze_candidates")
        self.freeze_submission_url = reverse("deployments:api_freeze_submission")
        self.range_state_url = reverse("deployments:api_range_state")
        self.range_exports_url = reverse("deployments:api_range_exports")

    def test_unconfigured_endpoints_do_not_exist(self):
        """Same fail-closed rule as the scheduled runner."""
        with override_settings(DEPLOY_TOKEN=""):
            self.assertEqual(self.client.get(self.pending_url).status_code, 404)
            self.assertEqual(self.client.get(self.range_exports_url).status_code, 404)
            self.assertEqual(
                self.client.post(self.status_url, "{}", "application/json").status_code, 404
            )

    @override_settings(DEPLOY_TOKEN="a-long-enough-deploy-token")
    def test_a_wrong_token_is_refused(self):
        self.assertEqual(
            self.client.get(self.pending_url, headers={"x-deploy-token": "wrong"}).status_code,
            403,
        )

    @override_settings(DEPLOY_TOKEN="a-long-enough-deploy-token")
    def test_the_range_collects_what_it_needs_to_pull(self):
        response = self.client.get(
            self.pending_url, headers={"x-deploy-token": "a-long-enough-deploy-token"}
        )
        self.assertEqual(response.status_code, 200)
        row = response.json()["pending"][0]
        self.assertEqual(row["image_reference"], FIELDS["image_reference"])
        self.assertEqual(row["pull_token"], TOKEN)
        self.assertEqual(row["cell"], "APP01")
        self.assertEqual(row["namespace"], "jengasec-ent-a-app01")
        self.assertEqual(row["service_ip"], "10.108.0.11")
        self.assertIn("no-store", response["Cache-Control"])

    @override_settings(DEPLOY_TOKEN="a-long-enough-deploy-token")
    def test_the_range_records_a_check(self):
        response = self.client.post(
            self.status_url,
            json.dumps({
                "id": self.submission.pk,
                "status": "healthy",
                "logs_excerpt": "listening on 8080",
            }),
            "application/json",
            headers={"x-deploy-token": "a-long-enough-deploy-token"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["is_passing"])
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, AppSubmission.Status.HEALTHY)
        self.assertEqual(self.submission.logs_excerpt, "listening on 8080")

    @override_settings(DEPLOY_TOKEN="a-long-enough-deploy-token")
    def test_an_unknown_status_is_refused_rather_than_stored(self):
        response = self.client.post(
            self.status_url,
            json.dumps({"id": self.submission.pk, "status": "nearly"}),
            "application/json",
            headers={"x-deploy-token": "a-long-enough-deploy-token"},
        )
        self.assertEqual(response.status_code, 400)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, AppSubmission.Status.SUBMITTED)

    @override_settings(DEPLOY_TOKEN="a-long-enough-deploy-token")
    def test_a_pending_collection_is_only_what_is_waiting(self):
        deploy.record_status(self.submission, AppSubmission.Status.HEALTHY)
        response = self.client.get(
            self.pending_url, headers={"x-deploy-token": "a-long-enough-deploy-token"}
        )
        self.assertEqual(response.json()["pending"], [])

    @override_settings(DEPLOY_TOKEN="a-long-enough-deploy-token")
    def test_freeze_candidates_return_last_passing_attempt_without_falsely_freezing(self):
        deploy.record_status(self.submission, AppSubmission.Status.HEALTHY)
        response = self.client.get(
            self.freeze_candidates_url,
            {"competition": self.comp.pk},
            headers={"x-deploy-token": "a-long-enough-deploy-token"},
        )
        self.assertEqual(response.status_code, 200)
        candidate = response.json()["candidates"][0]
        self.assertEqual(candidate["id"], self.submission.pk)
        self.assertEqual(candidate["pull_token"], TOKEN)
        self.assertEqual(candidate["namespace"], "jengasec-ent-a-app01")
        self.assertEqual(candidate["service_ip"], "10.108.0.11")
        self.assertIn("no-store", response["Cache-Control"])
        self.submission.refresh_from_db()
        self.assertFalse(self.submission.frozen_digest)

    @override_settings(DEPLOY_TOKEN="a-long-enough-deploy-token")
    def test_freeze_digest_is_validated_and_returned_in_range_state(self):
        deploy.record_status(self.submission, AppSubmission.Status.HEALTHY)
        digest = "registry.internal/apps/app@sha256:" + "a" * 64
        bad = self.client.post(
            self.freeze_submission_url,
            json.dumps({"id": self.submission.pk, "digest": "latest"}),
            "application/json",
            headers={"x-deploy-token": "a-long-enough-deploy-token"},
        )
        self.assertEqual(bad.status_code, 400)
        frozen = self.client.post(
            self.freeze_submission_url,
            json.dumps({"id": self.submission.pk, "digest": digest}),
            "application/json",
            headers={"x-deploy-token": "a-long-enough-deploy-token"},
        )
        self.assertEqual(frozen.status_code, 200)
        self.assertEqual(frozen.json()["digest"], digest)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.frozen_digest, digest)
        self.assertIsNotNone(self.submission.frozen_at)
        state = self.client.get(
            self.range_state_url,
            {"competition": self.comp.pk},
            headers={"x-deploy-token": "a-long-enough-deploy-token"},
        )
        self.assertEqual(state.status_code, 200)
        self.assertEqual(state.json()["cells"][0]["image_digest"], digest)
        self.assertEqual(state.json()["cells"][0]["service_ip"], "10.108.0.11")

    @override_settings(DEPLOY_TOKEN="a-long-enough-deploy-token")
    def test_range_state_without_a_competition_is_reconcilable(self):
        response = self.client.get(
            self.range_state_url,
            headers={"x-deploy-token": "a-long-enough-deploy-token"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["cells"][0]["namespace"], "jengasec-ent-a-app01"
        )
        self.assertEqual(response.json()["cells"][0]["service_ip"], "10.108.0.11")

    @override_settings(DEPLOY_TOKEN="a-long-enough-deploy-token")
    def test_scope_exports_are_authenticated_and_competition_scoped(self):
        self.team.team_identifier = "JS26-B-001"
        self.team.save(update_fields=["team_identifier"])
        denied = self.client.get(
            self.range_exports_url,
            {"competition": self.comp.pk},
            headers={"x-deploy-token": "wrong"},
        )
        self.assertEqual(denied.status_code, 403)
        response = self.client.get(
            self.range_exports_url,
            {"competition": self.comp.pk},
            headers={"x-deploy-token": "a-long-enough-deploy-token"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("name: jengasec-ent-a-app01", payload["cells_yaml"])
        self.assertIn('team: "JS26-B-001"', payload["cells_yaml"])
        self.assertEqual(
            payload["scope_csv"].splitlines()[0],
            "red_team,red_cell,enterprise,target_cell,target_namespace,"
            "target_service_ip,wg_range,round_active,notes",
        )
        self.assertIn("no-store", response["Cache-Control"])

    @override_settings(DEPLOY_TOKEN="a-long-enough-deploy-token")
    def test_scope_exports_require_a_known_competition(self):
        headers = {"x-deploy-token": "a-long-enough-deploy-token"}
        missing = self.client.get(self.range_exports_url, headers=headers)
        unknown = self.client.get(
            self.range_exports_url, {"competition": 999999}, headers=headers
        )
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(unknown.status_code, 404)

    @override_settings(DEPLOY_TOKEN="a-long-enough-deploy-token")
    def test_an_invalid_assigned_namespace_is_rejected(self):
        self.team.namespace = "../unsafe"
        self.team.save(update_fields=["namespace"])
        response = self.client.get(
            self.range_state_url,
            headers={"x-deploy-token": "a-long-enough-deploy-token"},
        )
        self.assertEqual(response.status_code, 409)

    @override_settings(DEPLOY_TOKEN="a-long-enough-deploy-token")
    def test_missing_application_service_ip_blocks_provisioning_payload(self):
        self.team.service_ip = ""
        self.team.save(update_fields=["service_ip"])
        response = self.client.get(
            self.range_state_url,
            {"competition": self.comp.pk},
            headers={"x-deploy-token": "a-long-enough-deploy-token"},
        )
        self.assertEqual(response.status_code, 409)

    @override_settings(DEPLOY_TOKEN="a-long-enough-deploy-token")
    def test_range_state_post_updates_cell_runtime_observation(self):
        payload = {
            "cells": [{
                "team_id": self.team.pk,
                "status": "live",
                "healthy": True,
                "image_digest": "sha256:" + "b" * 64,
                "detail": "pods ready",
            }],
            "peers": [],
        }
        response = self.client.post(
            self.range_state_url,
            json.dumps(payload),
            "application/json",
            headers={"x-deploy-token": "a-long-enough-deploy-token"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["updated_cells"], 1)
        self.team.refresh_from_db()
        self.assertEqual(self.team.app_runtime_status.status, "live")
        self.assertTrue(self.team.app_runtime_status.healthy)
