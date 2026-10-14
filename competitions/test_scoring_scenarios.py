import copy
import datetime as dt
import json
import uuid
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import RequestFactory, override_settings

from competitions.models import AttackScenario, CompetitionObjective, ScenarioReceipt, ScenarioVersion
from accounts.models import Team
from cyberrange.models import ActionScope, Finding, RangeApplication, TargetIdentityInterval
from cyberrange.scoring_views import ingest_receipt
from cyberrange.test_scoring_identity import IdentityFixture
from services.scoring_scenarios import (
    ReceiptConflict, ScenarioError, approve_exclusion, authenticate_producer,
    freeze_scenario, issue_case, receipt_snapshot, record_receipt,
    register_producer, scenario_snapshot,
)


class ScenarioTests(IdentityFixture):
    def setUp(self):
        super().setUp()
        window_patch = patch("services.scoring_scenarios.competition_window", return_value=(self.now, self.now + dt.timedelta(hours=1)))
        window_patch.start()
        self.addCleanup(window_patch.stop)
        scenario = AttackScenario.objects.create(code="AI-TEST", track="ai", name="Controlled attack", objective="Observe the verified target outcome", success_condition="Independent verifier confirms the assertion")
        self.version = freeze_scenario(scenario, version=1, label="attack", family="injection",
            assertions=[{"key": "success", "operator": "eq", "expected": True}], issued_by=self.staff)
        self.objective = CompetitionObjective.objects.create(team=self.blue, code="AI-TEST", name="Defend the target")
        self.verifier, self.token = register_producer(self.competition, kind="verifier",
            valid_until=self.now + dt.timedelta(hours=1), registered_by=self.staff)
        self.runner, _ = register_producer(self.competition, kind="runner",
            valid_until=self.now + dt.timedelta(hours=1), registered_by=self.staff)
        self.competitor, _ = register_producer(self.competition, kind="competitor", team=self.blue,
            valid_until=self.now + dt.timedelta(hours=1), registered_by=self.staff)
        self.case = issue_case(self.competition, objective=self.objective, scenario_version=self.version,
            target_id=self.target.application_instance_id, starts_at=self.now + dt.timedelta(seconds=1),
            deadline_at=self.now + dt.timedelta(seconds=100), opportunity_key="attack-one",
            actor_team=self.red, issued_by=self.staff)
        self.advance(10)

    def payload(self, producer=None, *, kind="outcome", case=True, **measurements):
        producer = producer or self.verifier
        payload = dict(producer_id=str(producer.producer_id), receipt_id=str(uuid.uuid4()), kind=kind,
            observed_at=self.clock.return_value.isoformat(), clock_uncertainty_ms=0,
            measurements=measurements or {"success": True}, evidence_refs=[{"family": "ai", "id": "receipt:verified:1"}])
        if case:
            payload["case_id"] = str(self.case.case_id)
        return payload

    def test_only_organisers_can_issue_labels_or_register_producers(self):
        with self.assertRaises(ScenarioError):
            register_producer(self.competition, kind="verifier", registered_by=self.red_user, valid_until=self.now + dt.timedelta(minutes=1))
        with self.assertRaises(ScenarioError):
            freeze_scenario(self.version.scenario, version=2, label="attack", family="injection",
                assertions=[{"key": "success", "operator": "eq", "expected": True}], issued_by=self.red_user)
        self.assertNotEqual(self.verifier.credential_sha256, self.token)
        self.assertEqual(authenticate_producer(self.verifier.producer_id, self.token), self.verifier)
        with self.assertRaises(ScenarioError):
            authenticate_producer(self.verifier.producer_id, "wrong" * 8)

    def test_frozen_definition_and_case_identity_survive_prose_edits(self):
        digest = self.version.definition_sha256
        self.version.scenario.code = "RENAMED"
        self.version.scenario.save()
        self.objective.code = "CHANGED"
        self.objective.save()
        data = receipt_snapshot(self.competition)
        self.assertEqual(data["cases"][0]["scenario"]["code"], "AI-TEST")
        self.assertEqual(data["cases"][0]["scenario"]["sha256"], digest)
        self.assertEqual(data["cases"][0]["objective"]["code"], "AI-TEST")
        self.assertEqual(data["cases"][0]["opportunity_key"], "attack-one")
        with self.assertRaises(ValidationError):
            ScenarioVersion.objects.filter(pk=self.version.pk).update(label="legitimate")
        with self.assertRaises(ValidationError):
            self.case.delete()

    def test_issuance_rejects_wrong_actor_unknown_target_and_backdating(self):
        for overrides in ({"actor_team": self.blue}, {"target_id": "native-1"}, {"starts_at": self.now}):
            args = dict(objective=self.objective, scenario_version=self.version,
                target_id=self.target.application_instance_id, starts_at=self.clock.return_value,
                deadline_at=self.now + dt.timedelta(seconds=100), opportunity_key=str(uuid.uuid4()),
                actor_team=self.red, issued_by=self.staff)
            args.update(overrides)
            with self.subTest(overrides=overrides), self.assertRaises(ScenarioError):
                issue_case(self.competition, **args)

    def test_verified_receipt_idempotency_and_conflict(self):
        payload = self.payload()
        receipt, created = record_receipt(self.verifier, payload)
        self.assertTrue(created)
        duplicate, created = record_receipt(self.verifier, payload)
        self.assertFalse(created)
        self.assertEqual(receipt.pk, duplicate.pk)
        changed = copy.deepcopy(payload)
        changed["measurements"]["success"] = False
        with self.assertRaises(ReceiptConflict):
            record_receipt(self.verifier, changed)
        self.assertEqual(ScenarioReceipt.objects.count(), 1)

    def test_competitor_cannot_claim_ground_truth_or_validated_decision_checks(self):
        for kind, values in (("outcome", {"success": True}), ("decision", {"context_valid": True})):
            with self.subTest(kind=kind), self.assertRaises(ScenarioError):
                record_receipt(self.competitor, self.payload(self.competitor, kind=kind, **values))
        own = self.payload(self.competitor, kind="detection", classification="attack", incident_id="incident:1")
        receipt, _ = record_receipt(self.competitor, own)
        self.assertEqual(receipt.team_identifier, self.blue.team_identifier)

    def test_competitor_detection_cannot_claim_another_team(self):
        opponent, _ = register_producer(self.competition, kind="competitor", team=self.red,
            registered_by=self.staff, valid_until=self.now + dt.timedelta(hours=1))
        with self.assertRaises(ScenarioError):
            record_receipt(opponent, self.payload(opponent, kind="detection", classification="attack", incident_id="incident:1"))

    def test_extra_labels_raw_bodies_and_wrong_evidence_family_are_rejected(self):
        for modify in (
            lambda p: p.update(label="legitimate"),
            lambda p: p["measurements"].update(raw_body="secret"),
            lambda p: p.update(evidence_refs=[{"family": "application", "id": "infra_" + "a" * 64}]),
            lambda p: p.update(clock_uncertainty_ms=True),
        ):
            payload = self.payload()
            modify(payload)
            with self.assertRaises(ScenarioError):
                record_receipt(self.verifier, payload)

    def test_receipt_clock_and_artifact_context_are_preserved(self):
        payload = self.payload()
        payload["clock_uncertainty_ms"] = 3000
        receipt, _ = record_receipt(self.verifier, payload)
        self.assertEqual(receipt.clock_uncertainty_ms, 3000)
        self.assertEqual(receipt.target_identity.frozen_digest, self.target.image_digest)
        payload = self.payload()
        payload["observed_at"] = (self.clock.return_value + dt.timedelta(seconds=3)).isoformat()
        with self.assertRaises(ScenarioError):
            record_receipt(self.verifier, payload)

    def test_late_receipt_is_discovered_by_server_cursor(self):
        first, _ = record_receipt(self.verifier, self.payload())
        export = receipt_snapshot(self.competition, limit=1)
        self.assertEqual(export["next_cursor"], first.pk)
        self.advance(40)
        delayed = self.payload()
        delayed["observed_at"] = (self.now + dt.timedelta(seconds=5)).isoformat()
        second, _ = record_receipt(self.verifier, delayed)
        export = receipt_snapshot(self.competition, after=first.pk, cases_after=self.case.pk)
        self.assertEqual([r["sequence"] for r in export["receipts"]], [second.pk])
        self.assertEqual(export["cases"][0]["case_id"], str(self.case.case_id))
        self.assertEqual(export["receipts"][0]["observed_at"], delayed["observed_at"].replace("+00:00", "Z"))

    def test_revocation_rejects_later_case_observation_but_preserves_earlier_evidence(self):
        earlier = self.payload()
        self.advance(30)
        self.assignment.is_active = False
        self.assignment.save(update_fields=["is_active"])
        with self.assertRaises(ScenarioError):
            record_receipt(self.verifier, self.payload())
        receipt, _ = record_receipt(self.verifier, earlier)
        self.assertEqual(receipt.observed_at, self.now + dt.timedelta(seconds=10))

    def test_unanswered_cases_are_exported_without_receipts(self):
        export = receipt_snapshot(self.competition)
        self.assertEqual(export["receipts"], [])
        self.assertEqual(export["cases_next_cursor"], self.case.pk)
        self.assertEqual(export["cases"][0]["case_id"], str(self.case.case_id))

    def test_functional_probe_has_explicit_target_and_completed_count(self):
        payload = self.payload(kind="probe", case=False, successful=False, expected=1, completed=1, target_digest=self.target.image_digest)
        payload["target_id"] = self.target.application_instance_id
        record_receipt(self.verifier, payload)
        output = scenario_snapshot(self.competition, start=self.now, end=self.now + dt.timedelta(seconds=60))
        self.assertFalse(output["probes"][0]["measurements"]["successful"])
        changed = self.payload(kind="probe", case=False, successful=True, expected=1, completed=0, target_digest=self.target.image_digest)
        changed["target_id"] = self.target.application_instance_id
        with self.assertRaises(ScenarioError):
            record_receipt(self.verifier, changed)

    def test_health_receipts_preserve_gaps_and_reject_overlapping_counts(self):
        values = dict(start=self.now.isoformat(), end=self.clock.return_value.isoformat(), expected=10, completed=8, lost=1)
        receipt, _ = record_receipt(self.verifier, self.payload(kind="source_health", case=False, **values))
        output = scenario_snapshot(self.competition, start=self.now + dt.timedelta(seconds=5), end=self.now + dt.timedelta(seconds=15))
        self.assertEqual(output["health"][0]["completed"], 8)
        self.assertEqual(output["health"][0]["start"], values["start"])
        with self.assertRaises(ReceiptConflict):
            record_receipt(self.verifier, self.payload(kind="source_health", case=False, **values))
        self.assertEqual(receipt.kind, "source_health")

    def test_valid_finding_correction_changes_current_protection_state(self):
        self.assertTrue(receipt_snapshot(self.competition)["cases"][0]["no_valid_finding"])
        finding = Finding.objects.create(competition=self.competition, red_team=self.red, target_team=self.blue,
            scoring_objective=self.objective, round=1, started_at=self.now, ended_at=self.clock.return_value,
            title="Verified issue", technique="test", steps="redacted", evidence="reference", impact="scope", status="valid")
        self.assertFalse(receipt_snapshot(self.competition)["cases"][0]["no_valid_finding"])
        with self.assertRaises(ScenarioError):
            record_receipt(self.verifier, self.payload(no_valid_finding=True))
        finding.status = "duplicate"
        finding.save(update_fields=["status"])
        self.assertTrue(receipt_snapshot(self.competition)["cases"][0]["no_valid_finding"])

    def test_exclusion_requires_staff_and_real_interval(self):
        with self.assertRaises(ScenarioError):
            approve_exclusion(self.competition, start=self.now, end=self.clock.return_value,
                incident_reference="incident:1", approved_by=self.red_user)
        approve_exclusion(self.competition, start=self.now, end=self.clock.return_value,
            incident_reference="incident:1", approved_by=self.staff)
        output = scenario_snapshot(self.competition, start=self.now, end=self.now + dt.timedelta(seconds=60))
        self.assertEqual(output["exclusions"][0]["reason"], "shared_infrastructure")

    @override_settings(FEED_TOKEN="feed-secret")
    def test_http_authentication_and_error_shapes(self):
        factory = RequestFactory()
        payload = self.payload()
        missing = ingest_receipt(factory.post("/api/scoring/receipts/", data=json.dumps(payload), content_type="application/json", HTTP_X_FEED_TOKEN="feed-secret"))
        self.assertEqual(missing.status_code, 403)
        accepted = ingest_receipt(factory.post("/api/scoring/receipts/", data=json.dumps(payload), content_type="application/json", HTTP_AUTHORIZATION="Bearer " + self.token))
        self.assertEqual(accepted.status_code, 201)
        duplicate = ingest_receipt(factory.post("/api/scoring/receipts/", data=json.dumps(payload), content_type="application/json", HTTP_AUTHORIZATION="Bearer " + self.token))
        self.assertEqual(duplicate.status_code, 200)
        export = ingest_receipt(factory.get("/api/scoring/receipts/", {"competition": self.competition.pk}, HTTP_X_FEED_TOKEN="feed-secret"))
        self.assertEqual(export.status_code, 200)
        malformed = ingest_receipt(factory.post("/api/scoring/receipts/", data="[]", content_type="application/json"))
        self.assertEqual(malformed.status_code, 400)
        oversized = ingest_receipt(factory.post("/api/scoring/receipts/", data="x" * 65537, content_type="application/json"))
        self.assertEqual(oversized.status_code, 413)
        duplicate_key_body = json.dumps(self.payload()).replace('"success": true', '"success": false, "success": true')
        duplicate_key = ingest_receipt(factory.post("/api/scoring/receipts/", data=duplicate_key_body,
            content_type="application/json", HTTP_AUTHORIZATION="Bearer " + self.token))
        self.assertEqual(duplicate_key.status_code, 400)

    def test_case_authority_rejects_unlisted_trusted_producer(self):
        verifier, _ = register_producer(self.competition, kind="verifier",
            valid_until=self.now + dt.timedelta(hours=1), registered_by=self.staff)
        with self.assertRaises(ScenarioError):
            record_receipt(verifier, self.payload(verifier))
        self.assertEqual(receipt_snapshot(self.competition)["cases"][0]["required_producers"]["verifier"], [str(self.verifier.producer_id)])

    def test_action_consequences_require_a_specific_trusted_gateway_action(self):
        with self.assertRaises(ScenarioError):
            record_receipt(self.verifier, self.payload(action_verified=True, action_id="action:one"))
        gateway, _ = register_producer(self.competition, kind="gateway",
            valid_until=self.now + dt.timedelta(hours=1), registered_by=self.staff)
        version = freeze_scenario(self.version.scenario, version=2, label="attack", family="injection",
            assertions=[{"key": "action_verified", "operator": "eq", "expected": True}], issued_by=self.staff)
        self.case = issue_case(self.competition, objective=self.objective, scenario_version=version,
            target_id=self.target.application_instance_id, starts_at=self.clock.return_value,
            deadline_at=self.now + dt.timedelta(seconds=100), opportunity_key="gateway-action",
            actor_team=self.red, issued_by=self.staff)
        for key in ("action_verified", "induced", "harmful"):
            with self.subTest(key=key), self.assertRaises(ScenarioError):
                record_receipt(self.verifier, self.payload(**{key: True}))
        outcome, _ = record_receipt(self.verifier, self.payload(action_verified=True, action_id="action:one"))
        action, _ = record_receipt(gateway, self.payload(gateway, kind="action",
            action_id="action:one", action_type="block-ip", state="executed"))
        self.assertLess(outcome.pk, action.pk)
        self.assertEqual(outcome.measurements["action_id"], action.measurements["action_id"])

    def test_deployment_receipt_bootstraps_only_declared_artifact_without_backdating(self):
        owner = Team.objects.create(competition=self.competition, team_name="Second defender",
            team_identifier="JS26-B-003", team_type="blue", track="ai", enterprise="a",
            cell_id="AI02", namespace="jengasec-ent-a-ai02", status="approved")
        target = RangeApplication.objects.create(competition=self.competition, team=owner,
            application_instance_id="ENT-A/AI02", enterprise="ENT-A", application="AI02",
            namespace=owner.namespace, service_name="Second defender", ip_address="10.40.0.3", port=8080,
            status="live", image_digest="sha256:" + "c" * 64)
        deployment, _ = register_producer(self.competition, kind="deployment",
            valid_until=self.now + dt.timedelta(hours=1), registered_by=self.staff)
        self.assertFalse(TargetIdentityInterval.objects.filter(range_target=target).exists())
        bad = self.payload(deployment, kind="target_verification", case=False, target_digest="sha256:" + "d" * 64)
        bad["target_id"] = target.application_instance_id
        with self.assertRaises(ScenarioError):
            record_receipt(deployment, bad)
        participant = self.payload(self.competitor, kind="target_verification", case=False, target_digest=target.image_digest)
        participant["target_id"] = target.application_instance_id
        with self.assertRaises(ScenarioError):
            record_receipt(self.competitor, participant)
        self.advance(20)
        valid = self.payload(deployment, kind="target_verification", case=False, target_digest=target.image_digest)
        valid.update(target_id=target.application_instance_id, observed_at=(self.now + dt.timedelta(seconds=15)).isoformat())
        receipt, _ = record_receipt(deployment, valid)
        identity = TargetIdentityInterval.objects.get(range_target=target)
        self.assertEqual(identity.valid_from, self.clock.return_value)
        self.assertEqual(identity.verified_at, self.now + dt.timedelta(seconds=15))
        self.assertEqual(receipt.target_identity, identity)

    def test_cross_team_ai_case_keeps_defender_and_workload_owner_separate(self):
        from services.scoring_identity import identity_snapshot, validate_scoring_assignment
        owner, target, _ = self.application_target()
        args = dict(objective=self.objective, scenario_version=self.version,
            target_id=target.application_instance_id, starts_at=self.clock.return_value,
            deadline_at=self.now + dt.timedelta(seconds=100), opportunity_key="managed-application",
            actor_team=self.red, defender_team=self.blue, issued_by=self.staff)
        with self.assertRaises(ScenarioError):
            issue_case(self.competition, **args)
        ActionScope.objects.create(competition=self.competition, team=self.blue, target=target,
            allowed_actions=["block-ip", "revert"], active=True, approved_by=self.staff)
        case = issue_case(self.competition, **args)
        payload = self.payload(self.competitor, kind="detection", classification="attack", incident_id="incident:cross")
        payload["case_id"] = str(case.case_id)
        receipt, _ = record_receipt(self.competitor, payload)
        self.assertEqual(receipt.team_identifier, self.blue.team_identifier)
        self.assertEqual(receipt.target_identity.team, owner)
        export = receipt_snapshot(self.competition)
        serialized = next(row for row in export["cases"] if row["case_id"] == str(case.case_id))
        self.assertEqual(serialized["defender_team"], self.blue.team_identifier)
        self.assertEqual(serialized["target_team"], owner.team_identifier)
        self.advance(40)
        snapshot = identity_snapshot(self.competition, start=self.now + dt.timedelta(seconds=10), end=self.clock.return_value)
        revisions = sorted({row["revision"] for name in ("teams", "targets", "scopes") for row in snapshot[name]})
        rows = validate_scoring_assignment(self.competition, self.blue, self.now + dt.timedelta(seconds=10), self.clock.return_value, revisions)
        self.assertTrue(any(row.get("target_id") == target.application_instance_id for row in rows))

    def test_target_specific_health_cannot_mask_other_target(self):
        _, other_target, _ = self.application_target()
        start = self.clock.return_value
        self.advance(20)
        values = dict(start=start.isoformat(), end=self.clock.return_value.isoformat(), expected=10, completed=10, lost=0)
        first = self.payload(kind="source_health", case=False, **values)
        first["target_id"] = self.target.application_instance_id
        second = self.payload(kind="source_health", case=False, **values)
        second["target_id"] = other_target.application_instance_id
        record_receipt(self.verifier, first)
        record_receipt(self.verifier, second)
        output = scenario_snapshot(self.competition, start=self.now, end=self.now + dt.timedelta(seconds=60))
        self.assertEqual({r["target_id"] for r in output["health"]}, {self.target.application_instance_id, other_target.application_instance_id})

    def test_finding_review_appears_after_cursor_as_platform_authority(self):
        previous = receipt_snapshot(self.competition)["next_cursor"]
        finding = Finding.objects.create(competition=self.competition, red_team=self.red, target_team=self.blue,
            scoring_objective=self.objective, round=1, started_at=self.now, ended_at=self.clock.return_value,
            title="Finding", technique="test", steps="redacted", evidence="reference", impact="scope", status="valid")
        export = receipt_snapshot(self.competition, after=previous, cases_after=self.case.pk)
        self.assertEqual(export["receipts"][0]["producer_kind"], "platform")
        self.assertIsNone(export["receipts"][0]["producer_id"])
        self.assertEqual(export["receipts"][0]["kind"], "finding_review")
        self.assertFalse(export["cases"][0]["no_valid_finding"])
        finding.status = "duplicate"
        finding.save(update_fields=["status"])
        corrected = receipt_snapshot(self.competition, after=export["next_cursor"], cases_after=self.case.pk)
        self.assertTrue(corrected["cases"][0]["no_valid_finding"])
