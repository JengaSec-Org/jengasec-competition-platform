"""Finding corrections keep the identifiers and attachment originally reviewed."""
import hashlib
import json

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.tests import make_competition
from competitions.models import CompetitionObjective
from cyberrange.models import Finding
from jengasec_scoring_policy import digest
from scoring.models import ScoreEvent, ScoreRevision
from scoring.tests import blue_team, red_team
from services import appeal_service, scoring_service


class FindingProvenanceTests(TestCase):
    def setUp(self):
        self.competition = make_competition()
        self.red = red_team(self.competition)
        self.target = blue_team(self.competition)
        self.target.team_identifier = "JS26-B-014"
        self.target.namespace = "jengasec-ent-a-app01"
        self.target.save()
        self.reviewer = User.objects.create_user("finding-reviewer", is_staff=True)
        self.objective = CompetitionObjective.objects.create(
            team=self.target, code="APP-01", name="Protect the issued workload")
        self.original = b"Sanitized retained observation pack."
        self.start = timezone.now() - timezone.timedelta(minutes=5)
        self.end = self.start + timezone.timedelta(seconds=30)
        self.finding = Finding.objects.create(
            competition=self.competition, red_team=self.red, target_team=self.target,
            round=1, started_at=self.start, ended_at=self.end,
            title="Private report prose", technique="Private technique prose",
            steps="Private reproduction steps", evidence="Private raw request body",
            impact="Private impact narrative", reviewer_notes="Private review notes",
            status=Finding.Status.VALID, severity=Finding.Severity.HIGH,
            telemetry_event_ids=["evt_" + "a" * 64], correlation_ids=["trace:original"],
            scoring_objective=self.objective,
            evidence_file=SimpleUploadedFile("observation-pack.txt", self.original),
        )

    def award(self):
        scoring_service.award_for_finding(self.finding, recorded_by=self.reviewer)
        return ScoreEvent.objects.get(team=self.red, criterion="severity").revisions.order_by("-sequence").first()

    def route(self, revision):
        return reverse("scoring:finding_evidence", args=[revision.pk, self.finding.pk])

    def test_correction_keeps_original_references_window_target_and_reviewer(self):
        revision = self.award()
        original_facts = json.loads(json.dumps(revision.facts))
        proof = original_facts["findings"][0]
        self.assertEqual(proof["target_team_identifier"], "JS26-B-014")
        self.assertEqual(proof["target_id"], "ENT-A/APP01")
        self.assertEqual(proof["target_namespace"], self.target.namespace)
        self.assertEqual(proof["started_at"], self.start.isoformat())
        self.assertEqual(proof["ended_at"], self.end.isoformat())
        self.assertEqual(proof["scoring_objective_id"], self.objective.pk)
        self.assertEqual(proof["evidence_file"], {
            "name": self.finding.evidence_file.name, "status": "verified",
            "size": len(self.original), "sha256": hashlib.sha256(self.original).hexdigest(),
        })
        self.assertEqual(revision.input_sha256, digest(original_facts))
        self.assertEqual(revision.recorded_by, self.reviewer)
        self.assertNotIn("Private", json.dumps(original_facts))

        self.finding.severity = Finding.Severity.LOW
        self.finding.telemetry_event_ids = ["evt_" + "b" * 64]
        self.finding.correlation_ids = ["trace:corrected"]
        self.finding.scoring_objective = None
        self.finding.started_at = self.end
        self.finding.ended_at = self.end + timezone.timedelta(seconds=30)
        self.finding.evidence_file = SimpleUploadedFile("observation-pack.txt", b"Corrected observation pack.")
        self.finding.save()
        correction = self.award()
        revision.refresh_from_db()
        self.assertEqual(revision.facts, original_facts)
        self.assertEqual(revision.input_sha256, digest(original_facts))
        self.assertEqual(revision.recorded_by, self.reviewer)
        self.assertLess(correction.marks, revision.marks)
        self.assertEqual(correction.facts["findings"][0]["telemetry_event_ids"], ["evt_" + "b" * 64])
        self.assertNotIn("scoring_objective_id", correction.facts["findings"][0])
        self.assertNotEqual(correction.facts["findings"][0]["evidence_file"]["name"], proof["evidence_file"]["name"])

    def test_historical_download_uses_the_retained_file_even_after_finding_deletion(self):
        revision = self.award()
        route = self.route(revision)
        self.finding.evidence_file = SimpleUploadedFile("observation-pack.txt", b"Replacement contents.")
        self.finding.save()
        self.finding.delete()
        self.client.force_login(self.reviewer)
        response = self.client.get(route)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), self.original)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertTrue(response.closed)

    def test_private_proof_route_and_review_links_do_not_expose_storage_names_to_teams(self):
        revision = self.award()
        self.client.force_login(self.red.captain)
        self.assertEqual(self.client.get(self.route(revision)).status_code, 404)
        page = self.client.get(reverse("scoring:revision", args=[revision.pk]))
        self.assertContains(page, "retained for authorized appeal review")
        self.assertNotContains(page, self.finding.evidence_file.name)
        self.assertNotContains(page, "trace:original")
        self.client.force_login(self.reviewer)
        page = self.client.get(reverse("scoring:revision", args=[revision.pk]))
        self.assertContains(page, self.route(revision))
        self.assertContains(page, "trace:original")
        self.assertContains(page, "retained OBS evidence pack")
        self.assertNotContains(page, self.finding.evidence_file.name)

    def test_changed_or_missing_retained_bytes_are_never_served_as_the_original(self):
        revision = self.award()
        self.client.force_login(self.reviewer)
        storage = self.finding.evidence_file.storage
        name = revision.facts["findings"][0]["evidence_file"]["name"]
        with storage.open(name, "wb") as file:
            file.write(b"x" * len(self.original))
        response = self.client.get(self.route(revision))
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(b"x" * len(self.original), response.content)
        storage.delete(name)
        self.assertEqual(self.client.get(self.route(revision)).status_code, 503)

    def test_missing_legacy_attachment_is_a_frozen_gap_and_does_not_invent_proof(self):
        self.finding.evidence_file.storage.delete(self.finding.evidence_file.name)
        revision = self.award()
        descriptor = revision.facts["findings"][0]["evidence_file"]
        self.assertEqual(descriptor, {"name": self.finding.evidence_file.name, "status": "unavailable"})
        self.assertGreater(revision.marks, 0)
        self.assertEqual(revision.recorded_by, self.reviewer)
        self.client.force_login(self.reviewer)
        self.assertEqual(self.client.get(self.route(revision)).status_code, 503)
        self.assertContains(self.client.get(reverse("scoring:revision", args=[revision.pk])), "contents could not be verified")

    def test_open_appeal_keeps_the_revision_attachment_after_source_replacement(self):
        revision = self.award()
        row = self.competition.settings
        row.results_published = True
        row.save()
        appeal = appeal_service.file_score_appeal(
            revision, self.red.captain, "wrong_document",
            "The retained observation pack does not match the stated review window for this finding.")
        self.finding.evidence_file = SimpleUploadedFile("observation-pack.txt", b"Later report evidence.")
        self.finding.save()
        self.assertEqual(appeal.score_revision.facts, revision.facts)
        self.client.force_login(self.reviewer)
        response = self.client.get(self.route(revision))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), self.original)
        self.assertTrue(response.closed)
        self.assertEqual(ScoreRevision.objects.filter(event=revision.event).count(), 1)
