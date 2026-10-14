"""Content identity stays distinct from an application's measured availability."""

import json

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Team
from accounts.tests import make_competition
from deployments.tests import FIELDS, blue_app_team, open_the_window
from services import deployment_service as service


@override_settings(DEPLOY_TOKEN="artifact-test-credential")
class ArtifactTests(TestCase):
    def setUp(self):
        self.competition = make_competition()
        open_the_window(self.competition)
        self.team = blue_app_team(self.competition)
        self.team.status = Team.Status.APPROVED
        self.team.save(update_fields=["status"])
        self.row = service.submit(self.team, self.team.captain, **FIELDS)
        self.headers = {"X-Deploy-Token": "artifact-test-credential"}
        self.digest = "sha256:" + "a" * 64
        self.pinned = "registry.internal/apps/application@" + self.digest

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
        service.record_freeze(self.row, self.pinned)
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
        service.record_freeze(self.row, self.pinned)
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

    def test_registry_copy_does_not_refresh_a_stale_runtime_observation(self):
        service.record_status(self.row, "healthy", resolved_digest=self.digest)
        observed_at = self.row.deployed_digest_verified_at
        frozen_at = observed_at + timezone.timedelta(seconds=121)
        service.record_freeze(self.row, self.pinned, now=frozen_at)
        self.row.refresh_from_db()
        self.assertEqual(self.row.frozen_verified_digest, self.digest)
        self.assertEqual(self.row.frozen_at, frozen_at)
        self.assertEqual(self.row.deployed_digest_verified_at, observed_at)

    def test_active_export_contains_no_registry_credentials(self):
        service.record_status(self.row, "healthy", resolved_digest=self.digest)
        service.record_freeze(self.row, self.pinned)
        response = self.client.get(reverse("deployments:api_active"), headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()["active"][0]), {
            "id", "enterprise", "cell", "namespace", "frozen_verified_digest",
            "frozen_digest", "frozen_extra_images",
        })
        for after in ("-1", "invalid"):
            response = self.client.get(reverse("deployments:api_active"), {"after": after}, headers=self.headers)
            self.assertEqual(response.status_code, 400)

    def test_existing_registry_freeze_gets_current_proof_without_backdating(self):
        service.record_status(self.row, "healthy")
        frozen_at = timezone.now() - timezone.timedelta(hours=1)
        self.row.frozen_digest = self.pinned
        self.row.frozen_at = frozen_at
        self.row.save(update_fields=["frozen_digest", "frozen_at"])
        response = self.client.get(reverse("deployments:api_active"), headers=self.headers)
        self.assertEqual(response.json()["active"][0]["frozen_verified_digest"], self.digest)
        response = self.post("api_range_state", {"cells": [{
            "team_id": self.team.pk, "status": "down", "healthy": False,
            "image_digest": self.digest,
        }]})
        self.assertEqual(response.status_code, 200)
        self.row.refresh_from_db()
        self.assertEqual(self.row.frozen_at, frozen_at)
        self.assertEqual(self.row.frozen_verified_digest, self.digest)
        self.assertGreater(self.row.deployed_digest_verified_at, frozen_at)

    def test_freeze_receipt_pins_every_image_and_retries_without_rewriting_identity(self):
        self.row.extra_images = ["registry.internal/worker:v1"]
        self.row.save(update_fields=["extra_images"])
        service.record_status(self.row, "healthy")
        receipt = {"id": self.row.pk, "digest": self.pinned, "extra_digests": []}
        self.assertEqual(self.post("api_freeze_submission", receipt).status_code, 400)
        receipt["extra_digests"] = ["registry.internal/worker@sha256:" + "b" * 64]
        self.assertEqual(self.post("api_freeze_submission", receipt).status_code, 200)
        self.row.refresh_from_db()
        first_frozen = self.row.frozen_at
        self.assertEqual(self.row.frozen_digest, self.pinned)
        self.assertEqual(self.row.frozen_verified_digest, self.digest)
        self.assertEqual(self.row.frozen_extra_images, receipt["extra_digests"])
        self.assertIsNone(self.row.deployed_digest_verified_at)
        service.record_status(self.row, "failed", detail="Unavailable")
        self.assertEqual(self.post("api_freeze_submission", receipt).status_code, 200)
        self.row.refresh_from_db()
        self.assertEqual(self.row.frozen_at, first_frozen)
        self.assertEqual(self.row.status, "failed")
        receipt["extra_digests"] = ["registry.internal/worker@sha256:" + "c" * 64]
        self.assertEqual(self.post("api_freeze_submission", receipt).status_code, 409)

    def test_extra_container_drift_or_missing_proof_cannot_refresh_a_matching_primary(self):
        self.row.extra_images = ["registry.internal/worker:v1"]
        self.row.save(update_fields=["extra_images"])
        service.record_status(self.row, "healthy", resolved_digest=self.digest)
        extra = "sha256:" + "b" * 64
        service.record_freeze(self.row, self.pinned, ["registry.internal/worker@" + extra])
        self.row.refresh_from_db()
        self.assertIsNone(self.row.deployed_digest_verified_at)
        for extras in (None, [], ["sha256:" + "c" * 64]):
            with self.subTest(extras=extras):
                receipt = {"id": self.row.pk, "resolved_digest": self.digest}
                cell = {"team_id": self.team.pk, "status": "down", "healthy": False,
                        "image_digest": self.digest}
                if extras is not None:
                    receipt["extra_digests"] = extras
                    cell["extra_digests"] = extras
                self.assertEqual(self.post("api_artifact", receipt).status_code, 400)
                self.assertEqual(self.post("api_status", {**receipt, "status": "healthy"}).status_code, 400)
                self.assertEqual(self.post("api_range_state", {"cells": [cell]}).status_code, 200)
                self.row.refresh_from_db()
                self.assertIsNone(self.row.deployed_digest_verified_at)
        response = self.post("api_artifact", {"id": self.row.pk, "resolved_digest": self.digest,
                                               "extra_digests": [extra]})
        self.assertEqual(response.status_code, 200)
        self.row.refresh_from_db()
        confirmed_at = self.row.deployed_digest_verified_at
        self.assertIsNotNone(confirmed_at)
        response = self.post("api_range_state", {"cells": [{
            "team_id": self.team.pk, "status": "down", "healthy": False,
            "image_digest": self.digest, "extra_digests": [extra],
        }]})
        self.assertEqual(response.status_code, 200)
        self.row.refresh_from_db()
        self.assertGreaterEqual(self.row.deployed_digest_verified_at, confirmed_at)
        confirmed_at = self.row.deployed_digest_verified_at
        self.assertEqual(self.post("api_range_state", {"cells": [{
            "team_id": self.team.pk, "status": "down", "healthy": False,
            "image_digest": self.digest, "extra_digests": ["sha256:" + "c" * 64],
        }]}).status_code, 200)
        self.row.refresh_from_db()
        self.assertEqual(self.row.deployed_digest_verified_at, confirmed_at)

    def test_range_observation_attests_content_during_an_outage_and_records_drift(self):
        service.record_status(self.row, "healthy")
        service.record_freeze(self.row, self.pinned)
        service.record_status(self.row, "failed", detail="Unavailable")
        for observed in (self.digest, "sha256:" + "b" * 64):
            with self.subTest(observed=observed):
                response = self.post("api_range_state", {"cells": [{
                    "team_id": self.team.pk, "status": "down", "healthy": False,
                    "image_digest": observed,
                }]})
                self.assertEqual(response.status_code, 200)
                self.row.refresh_from_db()
                self.assertEqual(self.row.resolved_digest, observed)
                self.assertEqual(self.row.frozen_verified_digest, self.digest)
                self.assertEqual(self.row.status, "failed")
                self.assertIsNotNone(self.row.deployed_digest_verified_at)
        response = self.client.get(reverse("deployments:api_range_state"), headers=self.headers)
        self.assertEqual(response.json()["cells"][0]["image_digest"], self.pinned)

    def test_missing_runtime_digest_cannot_refresh_frozen_artifact(self):
        service.record_status(self.row, "healthy")
        service.record_freeze(self.row, self.pinned)
        response = self.post("api_range_state", {"cells": [{
            "team_id": self.team.pk, "status": "down", "healthy": False, "image_digest": "",
        }]})
        self.assertEqual(response.status_code, 200)
        self.row.refresh_from_db()
        self.assertIsNone(self.row.deployed_digest_verified_at)

    def test_old_selection_tag_can_acquire_real_proof_without_backdating(self):
        service.record_status(self.row, "healthy")
        old_time = timezone.now() - timezone.timedelta(hours=1)
        self.row.frozen_digest = self.row.image_reference
        self.row.frozen_at = old_time
        self.row.save(update_fields=["frozen_digest", "frozen_at"])
        candidates = self.client.get(reverse("deployments:api_freeze_candidates"),
                                     {"competition": self.competition.pk}, headers=self.headers)
        self.assertEqual([row["id"] for row in candidates.json()["candidates"]], [self.row.pk])
        copied_at = timezone.now()
        service.record_freeze(self.row, self.pinned, now=copied_at)
        self.row.refresh_from_db()
        self.assertEqual(self.row.frozen_at, copied_at)
        self.assertEqual(self.row.frozen_verified_digest, self.digest)
        self.assertIsNone(self.row.deployed_digest_verified_at)
