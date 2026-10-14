"""Versioned publication preserves evidence, revisions and access boundaries."""
from decimal import Decimal
import json
import tempfile
import types
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Team
from accounts.tests import make_competition
from jengasec_scoring_policy import POLICY_SHA256, POLICY_VERSION
from scoring.models import ScoreEvent, ScoreRevision, ScoringEvidence, ScoringPolicyEnrollment
from scoring.tests import blue_team
from services import scoring_service

QUALITY = {"receipts_complete": True, "source_authenticated": True, "clock_uncertainty_seconds": 0}


@override_settings(FEED_TOKEN="test-feed-credential")
class VersionedFeedTests(TestCase):
    def setUp(self):
        self.comp = make_competition()
        self.team = blue_team(self.comp)
        self.team.team_identifier = "JS26-B-014"
        self.team.save()
        self.rival = blue_team(self.comp, "Other", "APP02")
        self.rival.team_identifier = "JS26-B-015"
        self.rival.save()
        self.staff = User.objects.create_user("archive-reviewer", is_staff=True, password="x")
        self.start = timezone.now() - timezone.timedelta(hours=1)
        self.end = timezone.now() - timezone.timedelta(minutes=3)
        self.enrollment = ScoringPolicyEnrollment.objects.create(
            competition=self.comp, policy_version=POLICY_VERSION, policy_sha256=POLICY_SHA256,
            window_start=self.start, window_end=self.start + timezone.timedelta(days=1),
            archive_budget_bytes=1024 * 1024, reconciled_at=timezone.now())
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        setting = override_settings(MEDIA_ROOT=self.directory.name)
        setting.enable()
        self.addCleanup(setting.disable)
        # This suite isolates the scoring boundary. Historical identity is
        # exercised by the identity suite and the combined acceptance test.
        self.identity = types.ModuleType("services.scoring_identity")
        self.identity.IdentityError = ValueError
        self.identity.resolve_team = lambda competition, identifier: Team.objects.get(
            competition=competition, team_identifier=identifier)
        self.identity.validate_scoring_assignment = Mock(return_value=[{"revision": 1}])
        stub = patch.dict("sys.modules", {"services.scoring_identity": self.identity})
        stub.start()
        self.addCleanup(stub.stop)

    def snapshot(self):
        return {"policy_version": POLICY_VERSION, "side": "application_blue", "criterion": "availability",
                "window": {"start": self.start.isoformat(), "end": self.end.isoformat()},
                "facts": {"expected": 100, "completed": 100, "successful": 90},
                "quality": dict(QUALITY), "assignment_revisions": [1]}

    def evidence_body(self, snapshot=None):
        return {"schema_version": "scoring-evidence/1.0", "competition": self.comp.pk,
                "team": self.team.team_identifier, "family": "metrics",
                "snapshot": snapshot or self.snapshot(), "queries": ["functional_probes"],
                "records": [{"family": "metrics", "id": "metric_probes", "successful": 90, "completed": 100}]}

    def post(self, url, body, token="test-feed-credential"):
        return self.client.post(url, json.dumps(body), content_type="application/json",
                                headers={"X-Feed-Token": token})

    def row(self, snapshot=None, revision=1):
        snapshot = snapshot or self.snapshot()
        response = self.post(reverse("api_scoring_evidence"), self.evidence_body(snapshot))
        self.assertEqual(response.status_code, 201, response.content)
        evidence = response.json()
        return {"team": self.team.team_identifier, "stage": "main", "key": "main:availability",
                "criterion": "availability", "revision": revision, "policy_version": POLICY_VERSION,
                "window": snapshot["window"], "facts": snapshot["facts"], "quality": snapshot["quality"],
                "assignment_revisions": snapshot["assignment_revisions"], "marks": "10.80",
                "note": "Verified functional probes", "evidence": [{"id": evidence["id"], "sha256": evidence["sha256"]}]}

    def publish(self, rows):
        return self.post(reverse("api_feed"), {"schema_version": "scoring-feed/2.0",
                         "competition": self.comp.pk, "scores": rows})

    def test_duplicate_json_fields_are_rejected_before_processing(self):
        ambiguous = [
            '{"competition": 1, "competition": 2}',
            '{"scores": [{"facts": {"successful": 90, "successful": 100}}]}',
            '{"snapshot": {"quality": {"source_authenticated": false, "source_authenticated": true}}}',
            '{"team": "JS26-B-014", "te\\u0061m": "JS26-B-015"}',
        ]
        for endpoint in ("api_feed", "api_scoring_evidence", "api_scoring_readiness"):
            for raw in ambiguous:
                with self.subTest(endpoint=endpoint, raw=raw):
                    response = self.client.post(reverse(endpoint), raw, content_type="application/json",
                                                headers={"X-Feed-Token": "test-feed-credential"})
                    self.assertEqual(response.status_code, 400, response.content)
                    self.assertIn("unambiguous finite JSON", response.json()["error"])
        self.assertFalse(ScoreRevision.objects.exists())
        self.assertFalse(ScoringEvidence.objects.exists())

    def test_archive_publish_duplicate_and_explicit_per_row_acknowledgement(self):
        row = self.row()
        first = self.publish([row])
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(first.json()["outcomes"][0]["status"], "accepted")
        repeated = self.publish([row])
        self.assertEqual(repeated.json()["outcomes"][0]["status"], "duplicate")
        self.assertEqual(ScoreRevision.objects.count(), 1)
        self.assertEqual(scoring_service.recompute(self.team).main, Decimal("10.80"))
        self.assertEqual(self.identity.validate_scoring_assignment.call_count, 2)

    def test_committed_archive_retry_survives_outage_and_finalization_without_new_state(self):
        body = self.evidence_body()
        first = self.post(reverse("api_scoring_evidence"), body)
        self.assertEqual(first.status_code, 201)
        original = ScoringEvidence.objects.values().get()
        self.identity.validate_scoring_assignment.side_effect = ValueError("identity unavailable during correction")
        changed = {**body, "queries": ["corrected_functional_probes"]}
        repeated = self.post(reverse("api_scoring_evidence"), body)
        self.assertEqual(repeated.status_code, 201, repeated.content)
        self.assertEqual(repeated.json(), first.json())
        self.assertEqual(self.identity.validate_scoring_assignment.call_count, 1)
        rejected = self.post(reverse("api_scoring_evidence"), changed)
        self.assertEqual(rejected.json()["code"], "identity_unavailable")

        self.enrollment.finalized_at = timezone.now()
        self.enrollment.save()
        repeated = self.post(reverse("api_scoring_evidence"), body)
        self.assertEqual(repeated.json(), first.json())
        rejected = self.post(reverse("api_scoring_evidence"), changed)
        self.assertEqual(rejected.json()["code"], "finalized")
        self.team.status = Team.Status.WITHDRAWN
        Team.objects.filter(pk=self.team.pk).update(status=self.team.status)
        self.assertEqual(self.post(reverse("api_scoring_evidence"), body).json(), first.json())
        self.assertEqual(self.post(reverse("api_scoring_evidence"), changed).status_code, 400)
        self.assertEqual(self.post(reverse("api_scoring_evidence"), body, token="wrong").status_code, 403)
        rival = {**body, "team": self.rival.team_identifier}
        self.assertEqual(self.post(reverse("api_scoring_evidence"), rival).status_code, 400)
        self.assertEqual(list(ScoringEvidence.objects.values()), [original])
        self.assertFalse(ScoreRevision.objects.exists())
        self.assertFalse(ScoreEvent.objects.exists())

    def test_committed_score_retry_after_withdrawal_preserves_the_original_revision(self):
        row = self.row()
        first = self.publish([row]).json()["outcomes"][0]
        original = ScoreRevision.objects.values().get()
        event = ScoreEvent.objects.values().get()
        self.team.status = Team.Status.WITHDRAWN
        Team.objects.filter(pk=self.team.pk).update(status=self.team.status)
        self.enrollment.finalized_at = timezone.now()
        self.enrollment.save()
        self.identity.validate_scoring_assignment.side_effect = ValueError("identity unavailable during correction")
        repeated = self.publish([row])
        self.assertEqual(repeated.status_code, 200, repeated.content)
        outcome = repeated.json()["outcomes"][0]
        self.assertEqual(outcome["status"], "duplicate")
        for field in ("score_revision", "sequence", "revision", "key"):
            self.assertEqual(outcome[field], first[field])
        self.assertEqual(self.publish([{**row, "revision": 2}]).status_code, 202)
        altered = self.publish([{**row, "marks": "1.00"}])
        self.assertEqual(altered.json()["outcomes"][0]["code"], "revision_conflict")
        self.assertEqual(list(ScoreRevision.objects.values()), [original])
        self.assertEqual(list(ScoreEvent.objects.values()), [event])

    def test_committed_archive_retry_still_verifies_retained_bytes(self):
        body = self.evidence_body()
        self.assertEqual(self.post(reverse("api_scoring_evidence"), body).status_code, 201)
        evidence = ScoringEvidence.objects.get()
        with evidence.archive.open("wb") as stream:
            stream.write(b"changed")
        self.identity.validate_scoring_assignment.side_effect = ValueError("identity unavailable during correction")
        rejected = self.post(reverse("api_scoring_evidence"), body)
        self.assertEqual(rejected.status_code, 400, rejected.content)
        self.assertEqual(rejected.json()["code"], "evidence_integrity")
        self.assertEqual(ScoringEvidence.objects.count(), 1)
        self.assertFalse(ScoreRevision.objects.exists())

    def test_changed_same_revision_old_revision_and_backwards_window_are_rejected(self):
        row = self.row(revision=2)
        self.assertEqual(self.publish([row]).status_code, 200)
        for other in ({**row, "marks": "5"}, {**row, "revision": 1}):
            result = self.publish([other])
            self.assertEqual(result.json()["outcomes"][0]["code"], "revision_conflict")
        snapshot = self.snapshot()
        snapshot["window"]["end"] = (self.end - timezone.timedelta(minutes=1)).isoformat()
        result = self.publish([self.row(snapshot, revision=3)])
        self.assertEqual(result.status_code, 202)
        self.assertEqual(ScoreRevision.objects.count(), 1)

    def test_partial_response_does_not_drop_good_rows_or_leak_database_errors(self):
        row = self.row()
        result = self.publish([None, row, {**row, "revision": 2, "marks": "NaN"}])
        self.assertEqual(result.status_code, 202)
        self.assertEqual(result.json()["accepted"], 1)
        self.assertEqual([r["index"] for r in result.json()["rejected"]], [0, 2])
        self.assertEqual(ScoreRevision.objects.count(), 1)

    def test_input_and_evidence_must_match_and_raw_families_remain_typed(self):
        row = self.row()
        row["facts"]["successful"] = 50
        row["marks"] = "6.00"
        result = self.publish([row])
        self.assertIn("evidence does not match", result.json()["rejected"][0]["error"])
        body = self.evidence_body()
        body["records"][0]["id"] = "infra_cannot_be_a_metric"
        self.assertEqual(self.post(reverse("api_scoring_evidence"), body).status_code, 400)

    def test_missing_archive_tampering_and_cross_team_refs_do_not_score(self):
        row = self.row()
        evidence = ScoringEvidence.objects.get()
        with evidence.archive.open("wb") as stream:
            stream.write(b"changed")
        result = self.publish([row])
        self.assertEqual(result.json()["outcomes"][0]["code"], "evidence_integrity")
        self.assertFalse(ScoreEvent.objects.exists())

    def test_archive_capacity_is_charged_once_and_never_silently_evicts(self):
        body = self.evidence_body()
        first = self.post(reverse("api_scoring_evidence"), body)
        self.enrollment.archive_budget_bytes = ScoringEvidence.objects.get().byte_size
        self.enrollment.save()
        repeated = self.post(reverse("api_scoring_evidence"), body)
        self.assertEqual(first.json()["id"], repeated.json()["id"])
        body["records"][0]["id"] = "metric_new"
        self.assertEqual(self.post(reverse("api_scoring_evidence"), body).status_code, 507)
        self.assertEqual(ScoringEvidence.objects.count(), 1)

    def test_incomplete_receipts_or_clock_uncertainty_hold_automatic_marks(self):
        snapshot = self.snapshot()
        snapshot["quality"]["receipts_complete"] = False
        row = self.row(snapshot)
        result = self.publish([row])
        self.assertEqual(result.json()["outcomes"][0]["status"], "pending")
        self.assertFalse(ScoreEvent.objects.exists())

    def test_identity_is_required_again_when_publishing_an_archived_input(self):
        row = self.row()
        self.identity.validate_scoring_assignment.side_effect = ValueError("historical assignment is stale")
        result = self.publish([row])
        self.assertEqual(result.json()["outcomes"][0]["code"], "identity_unavailable")
        self.assertFalse(ScoreEvent.objects.exists())

    def test_manual_override_holds_updates_until_explicit_release(self):
        row = self.row()
        self.publish([row])
        event = scoring_service.award(self.team, "main", 5, source="judge", criterion="availability",
                                      dedupe_key="judge:main:availability", recorded_by=self.staff)
        held = self.publish([{**row, "revision": 2}])
        self.assertEqual(held.json()["outcomes"][0]["status"], "override_hold")
        self.assertEqual(scoring_service.recompute(self.team).main, 5)
        scoring_service.release_override(event, recorded_by=self.staff, note="Appeal evidence resolved")
        self.assertEqual(scoring_service.recompute(self.team).main, Decimal("10.80"))
        self.assertEqual(self.publish([{**row, "revision": 2}]).status_code, 200)
        self.assertEqual(ScoreEvent.objects.count(), 1)
        self.assertEqual(ScoreRevision.objects.count(), 4)

    def test_correction_history_and_five_minute_delay_use_acceptance_time(self):
        original_at = timezone.now() - timezone.timedelta(minutes=10)
        with patch("django.utils.timezone.now", return_value=original_at):
            event = scoring_service.award(self.team, "main", 4, source="judge", criterion="availability")
        cutoff = timezone.now() - timezone.timedelta(minutes=5)
        scoring_service.award(self.team, "main", 9, source="judge", criterion="availability")
        self.assertEqual(scoring_service.recompute(self.team, as_of=cutoff, save=False).main, 4)
        self.assertEqual(scoring_service.recompute(self.team, save=False).main, 9)
        self.assertEqual(scoring_service.feed(self.comp, for_staff=False)[0].marks, 4)
        first = event.revisions.order_by("sequence").first()
        with self.assertRaises(ValueError):
            first.marks = 10
            first.save()
        with self.assertRaises(ValueError):
            ScoreRevision.objects.filter(pk=first.pk).update(marks=10)

    def test_evidence_and_revision_views_respect_team_and_staff_permissions(self):
        row = self.row()
        self.publish([row])
        evidence = ScoringEvidence.objects.get()
        revision = ScoreRevision.objects.get()
        self.client.force_login(self.team.captain)
        self.assertContains(self.client.get(reverse("scoring:revision", args=[revision.pk])), "10.80")
        self.assertEqual(self.client.get(reverse("scoring:evidence", args=[evidence.pk])).status_code, 404)
        self.client.force_login(self.rival.captain)
        self.assertEqual(self.client.get(reverse("scoring:revision", args=[revision.pk])).status_code, 404)
        self.client.force_login(self.staff)
        response = self.client.get(reverse("scoring:evidence", args=[evidence.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, no-store")

    def test_enrolled_competitions_reject_legacy_and_finalized_competitions_reject_changes(self):
        row = self.row()
        self.publish([row])
        legacy = self.post(reverse("api_feed"), {"competition": self.comp.pk, "scores": [row]})
        self.assertEqual(legacy.status_code, 400)
        self.enrollment.finalized_at = timezone.now()
        self.enrollment.save()
        self.assertEqual(self.publish([row]).status_code, 200)
        result = self.publish([{**row, "revision": 2}])
        self.assertEqual(result.json()["rejected"][0]["code"], "finalized")

    def test_sensitive_or_query_only_evidence_and_bad_envelopes_are_rejected(self):
        for mutate in (
            lambda b: b["records"][0].update(token="not-retained"),
            lambda b: b.update(records=[]),
        ):
            body = self.evidence_body()
            mutate(body)
            self.assertEqual(self.post(reverse("api_scoring_evidence"), body).status_code, 400)
        for body in (None, [], {"scores": {}}, {"schema_version": "unknown", "scores": []}):
            self.assertEqual(self.post(reverse("api_feed"), body).status_code, 400)
        self.assertEqual(self.post(reverse("api_scoring_evidence"), self.evidence_body(), token="wrong").status_code, 403)
        self.assertEqual(self.post(reverse("api_feed"), {"huge": "x" * (1024 * 1024)}).status_code, 413)

    def test_role_marks_revision_numbers_and_grace_period_are_guarded(self):
        row = self.row()
        for change in ({"stage": "entry"}, {"revision": True}, {"revision": -1},
                       {"criterion": "valid", "key": "main:valid"}, {"marks": 11}):
            result = self.publish([{**row, **change}])
            self.assertEqual(result.status_code, 202)
        row["window"]["end"] = timezone.now().isoformat()
        self.assertEqual(self.publish([row]).json()["rejected"][0]["code"], "grace_period")
        self.assertFalse(ScoreEvent.objects.exists())

    def test_readiness_explains_pending_without_awarding_or_masking_an_override(self):
        body = {"competition": self.comp.pk, "team": self.team.team_identifier,
                "criterion": "availability", "policy_version": POLICY_VERSION,
                "window": self.snapshot()["window"], "status": "insufficient_data", "reason_code": "missing_probes"}
        response = self.post(reverse("api_scoring_readiness"), body)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(ScoreEvent.objects.exists())
        self.client.force_login(self.team.captain)
        self.assertContains(self.client.get(reverse("scoring:breakdown", args=[self.team.pk])), "Insufficient observations")
        self.assertEqual(self.post(reverse("api_scoring_readiness"), {**body, "status": "published"}).status_code, 400)
        row = self.row()
        self.publish([row])
        self.assertEqual(self.post(reverse("api_scoring_readiness"), {**body, "status": "published"}).status_code, 200)
        scoring_service.award(self.team, "main", 5, source="judge", criterion="availability")
        self.post(reverse("api_scoring_readiness"), body)
        event = ScoreEvent.objects.get()
        self.assertTrue(event.override_active)
        self.assertEqual(event.marks, 5)

    def test_future_policy_enrollment_preserves_totals_and_does_not_backfill_identity(self):
        from scoring.enrollment import enroll_policy, reconciliation_report

        self.enrollment.delete()
        event = scoring_service.award(self.team, "main", 8, source="telemetry", criterion="availability", dedupe_key="old:day1")
        report, report_hash = reconciliation_report(self.comp)
        self.assertFalse(report["issues"])
        self.identity.enroll_identity = Mock()
        future = timezone.now() + timezone.timedelta(minutes=1)
        enrollment = enroll_policy(self.comp, window_start=future,
                                   window_end=future + timezone.timedelta(days=1), archive_budget_bytes=10000)
        self.assertEqual(enrollment.policy_version, POLICY_VERSION)
        event.refresh_from_db()
        self.assertEqual(event.dedupe_key, "main:availability")
        self.assertEqual(scoring_service.recompute(self.team).main, 8)
        self.identity.enroll_identity.assert_called_once()

    def test_enrollment_rejects_even_a_subsecond_unrecorded_identity_gap(self):
        from scoring.enrollment import enroll_policy

        self.enrollment.delete()
        self.identity.enroll_identity = Mock()
        now = timezone.now()
        with patch("django.utils.timezone.now", return_value=now):
            for lag in (timezone.timedelta(seconds=1), timezone.timedelta(microseconds=1)):
                with self.subTest(lag=lag), self.assertRaisesRegex(ValueError, "historical identity"):
                    enroll_policy(self.comp, window_start=now - lag,
                                  window_end=now + timezone.timedelta(hours=1),
                                  archive_budget_bytes=10000)
            self.identity.enroll_identity.assert_not_called()
            enrollment = enroll_policy(self.comp, window_start=now,
                                       window_end=now + timezone.timedelta(hours=1),
                                       archive_budget_bytes=10000)
        self.assertEqual(enrollment.window_start, enrollment.enrolled_at)

    def test_finalization_requires_final_windows_or_explicit_criterion_overrides(self):
        from scoring.enrollment import finalize_policy

        later = self.enrollment.window_end + timezone.timedelta(minutes=3)
        with patch("django.utils.timezone.now", return_value=later):
            with self.assertRaises(ValueError):
                finalize_policy(self.comp)
            for team in (self.team, self.rival):
                for criterion in ("availability", "detected", "response", "unfound"):
                    scoring_service.award(team, "main", 0, source="judge", criterion=criterion,
                                          note="Explicit reviewed result", recorded_by=self.staff)
            final = finalize_policy(self.comp)
            self.assertEqual(final.finalized_at, later)

    def test_a_baseline_does_not_fabricate_an_earlier_zero_score(self):
        old = timezone.now() - timezone.timedelta(hours=1)
        with patch("django.utils.timezone.now", return_value=old):
            event = ScoreEvent.objects.create(competition=self.comp, team=self.team, stage="main",
                                             criterion="availability", marks=7, source="telemetry")
        ScoreRevision.objects.create(event=event, sequence=1, baseline=True, stage="main",
                                     criterion="availability", marks=7, source="telemetry")
        row = next(r for r in scoring_service.leaderboard(self.comp) if r["team"] == self.team)
        self.assertFalse(row["history_available"])
        self.assertIsNone(row["rank"])
        self.client.force_login(self.team.captain)
        self.assertContains(self.client.get(reverse("scoring:leaderboard")), "Earlier correction history is unavailable")

    def test_policy_window_and_archived_inputs_cannot_be_edited_in_place(self):
        self.row()
        archive = ScoringEvidence.objects.get()
        archive.snapshot = {"different": True}
        with self.assertRaises(ValueError):
            archive.save()
        self.enrollment.window_end += timezone.timedelta(days=1)
        with self.assertRaises(ValueError):
            self.enrollment.save()

    def test_retention_and_appeal_hold_can_change_without_mutating_evidence(self):
        self.row()
        archive = ScoringEvidence.objects.get()
        archive.appeal_hold = True
        archive.retain_until = timezone.now() + timezone.timedelta(days=7)
        archive.save(update_fields=["appeal_hold", "retain_until"])
        archive.refresh_from_db()
        self.assertTrue(archive.appeal_hold)

    def test_malformed_evidence_reference_is_a_terminal_row_error(self):
        row = self.row()
        row["evidence"][0]["id"] = "x" * 36
        response = self.publish([row])
        self.assertEqual(response.status_code, 202)
        rejected = response.json()["rejected"][0]
        self.assertFalse(rejected["retryable"])
        self.assertEqual(rejected["revision"], 1)
        self.assertEqual(rejected["key"], "main:availability")
        self.assertFalse(ScoreEvent.objects.exists())

    def test_releasing_without_an_automatic_baseline_preserves_latest_manual_value(self):
        event = scoring_service.award(self.team, "main", 5, source="judge", criterion="availability")
        scoring_service.release_override(event, recorded_by=self.staff, note="Wait for observations")
        event = scoring_service.award(self.team, "main", 7, source="judge", criterion="availability")
        scoring_service.release_override(event, recorded_by=self.staff, note="Wait for observations")
        event.refresh_from_db()
        self.assertEqual(event.marks, 7)
        self.assertFalse(event.override_active)
        self.assertIn("pending_reasons", event.revisions.order_by("-sequence").first().quality)
