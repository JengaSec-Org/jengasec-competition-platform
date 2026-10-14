"""Content identity stays distinct from an application's measured availability."""

import json

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.tests import make_competition
from deployments.tests import FIELDS, blue_app_team, open_the_window
from services import deployment_service as service


@override_settings(DEPLOY_TOKEN="artifact-test-credential")
class ArtifactTests(TestCase):
    def setUp(self):
        self.competition = make_competition()
        open_the_window(self.competition)
        self.team = blue_app_team(self.competition)
        self.row = service.submit(self.team, self.team.captain, **FIELDS)
        self.headers = {"X-Deploy-Token": "artifact-test-credential"}
        self.digest = "sha256:" + "a" * 64

    def post(self, route, data, headers=None):
        return self.client.post(reverse("deployments:" + route), json.dumps(data),
                                "application/json", headers=headers or self.headers)

    def test_tag_only_freeze_never_claims_verified_content(self):
        service.record_status(self.row, "healthy")
        service.freeze(self.competition)
        self.row.refresh_from_db()
        self.assertEqual(self.row.frozen_verified_digest, "")

    def test_verified_freeze_and_artifact_heartbeat_do_not_turn_outage_into_health(self):
        response = self.post("api_status", {"id": self.row.pk, "status": "healthy", "resolved_digest": self.digest})
        self.assertEqual(response.status_code, 200)
        service.freeze(self.competition)
        self.row.refresh_from_db()
        self.assertEqual(self.row.frozen_verified_digest, self.digest)
        service.record_status(self.row, "failed", detail="Application unavailable")
        response = self.post("api_artifact", {"id": self.row.pk, "resolved_digest": self.digest})
        self.assertEqual(response.status_code, 200)
        self.row.refresh_from_db()
        self.assertEqual(self.row.status, "failed")
        self.assertIsNotNone(self.row.deployed_digest_verified_at)

    def test_changed_artifact_does_not_rewrite_frozen_identity(self):
        service.record_status(self.row, "healthy", resolved_digest=self.digest)
        service.freeze(self.competition)
        response = self.post("api_artifact", {"id": self.row.pk, "resolved_digest": "sha256:" + "b" * 64})
        self.assertFalse(response.json()["matches_frozen"])
        self.row.refresh_from_db()
        self.assertEqual(self.row.frozen_verified_digest, self.digest)
        self.assertNotEqual(self.row.resolved_digest, self.digest)

    def test_invalid_digest_and_bad_auth_cannot_refresh_identity(self):
        for value in [FIELDS["image_reference"], "sha256:" + "g" * 64, 42, {}]:
            response = self.post("api_status", {"id": self.row.pk, "status": "healthy", "resolved_digest": value})
            self.assertEqual(response.status_code, 400)
        response = self.post("api_status", {"id": self.row.pk, "status": "healthy", "resolved_digest": self.digest}, {"X-Deploy-Token": "wrong"})
        self.assertEqual(response.status_code, 403)
        self.row.refresh_from_db()
        self.assertIsNone(self.row.digest_verified_at)

    def test_stale_artifact_cannot_be_claimed_at_freeze(self):
        service.record_status(self.row, "healthy", resolved_digest=self.digest)
        service.freeze(self.competition, now=timezone.now() + timezone.timedelta(seconds=121))
        self.row.refresh_from_db()
        self.assertEqual(self.row.frozen_verified_digest, "")

    def test_active_export_contains_no_registry_credentials(self):
        service.record_status(self.row, "healthy", resolved_digest=self.digest)
        service.freeze(self.competition)
        response = self.client.get(reverse("deployments:api_active"), headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()["active"][0]), {"id", "enterprise", "cell", "frozen_verified_digest"})
