import datetime as dt
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection, models, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from accounts.models import Team
from competitions.models import Competition, TargetAssignment
from cyberrange.models import (
    ActionScope, Finding, IPAllocation, RangeApplication, ScopeAssignment,
    ScoringIdentityState, TargetIdentityInterval, TeamIdentityInterval, WireGuardProfile,
)
from cyberrange.scoring_views import identity_snapshot as identity_view, ingest_receipt, scenario_snapshot as scenario_view
from services.scoring_identity import (
    IdentityError, enroll_identity, identity_snapshot, refresh_identity,
    resolve_team, validate_scoring_assignment,
)


class IdentityFixture(TestCase):
    def setUp(self):
        self.now = timezone.now()
        clock_patch = patch("django.utils.timezone.now", return_value=self.now)
        self.clock = clock_patch.start()
        self.addCleanup(clock_patch.stop)
        self.competition = Competition.objects.create(name="Scoring identity", start_date=self.now.date(), end_date=self.now.date())
        self.window = patch("services.scoring_identity.competition_window", return_value=(self.now, self.now + dt.timedelta(hours=1)))
        self.window.start()
        self.addCleanup(self.window.stop)
        User = get_user_model()
        self.staff = User.objects.create_user("organiser", is_staff=True)
        self.red_user = User.objects.create_user("attacker")
        self.blue = Team.objects.create(
            competition=self.competition, team_name="Blue", team_identifier="JS26-B-001",
            team_type="blue", track="ai", enterprise="a", cell_id="AI01",
            namespace="jengasec-ent-a-ai01", status="approved",
        )
        self.red = Team.objects.create(
            competition=self.competition, team_name="Red", team_identifier="JS26-R-001",
            team_type="red", track="ai", enterprise="a", cell_id="AIRED01", status="approved",
        )
        self.target = RangeApplication.objects.create(
            competition=self.competition, team=self.blue, application_instance_id="ENT-A/AI01",
            enterprise="ENT-A", application="AI01", namespace=self.blue.namespace,
            service_name="Defender", ip_address="10.40.0.2", port=8080, status="live",
            image_digest="sha256:" + "a" * 64, verified_at=self.now,
        )
        self.profile = WireGuardProfile.objects.create(user=self.red_user, team=self.red, address="10.60.1.2/32")
        self.assignment = TargetAssignment.objects.create(competition=self.competition, red_team=self.red, target_team=self.blue, is_active=True)
        self.state = enroll_identity(self.competition, now=self.now)

    def advance(self, seconds):
        self.clock.return_value = self.now + dt.timedelta(seconds=seconds)
        return self.clock.return_value

    def application_target(self):
        from deployments.models import AppSubmission
        from services import deployment_service
        team = Team.objects.create(competition=self.competition, team_name="Application owner",
            team_identifier="JS26-B-002", team_type="blue", track="application", enterprise="a",
            cell_id="APP01", namespace="jengasec-ent-a-app01", status="approved")
        target = RangeApplication.objects.create(competition=self.competition, team=team,
            application_instance_id="ENT-A/APP01", enterprise="ENT-A", application="APP01", namespace=team.namespace,
            service_name="Application", ip_address="10.108.0.10", port=8080, status="live")
        submission = AppSubmission.objects.create(competition=self.competition, team=team, attempt=1,
            image_reference="registry.invalid/application:v1", registry_username="builder", port=8080)
        deployment_service.record_status(submission, "healthy", resolved_digest="sha256:" + "b" * 64)
        deployment_service.freeze(self.competition)
        return team, target, submission


class ScoringIdentityTests(IdentityFixture):
    def test_snapshot_is_canonical_and_contains_no_personal_fields(self):
        snapshot = identity_snapshot(self.competition, now=self.now)
        self.assertEqual(snapshot["schema_version"], "scoring-identity/2.0")
        self.assertEqual(snapshot["targets"][0]["team"], self.blue.team_identifier)
        self.assertEqual(snapshot["scopes"][0]["source_cidr"], "10.60.1.2/32")
        self.assertEqual(snapshot["targets"][0]["frozen_digest"], self.target.image_digest)
        serialized = json.dumps(snapshot)
        for private in ("organiser", "attacker", "Blue", "Red", "config", "access_notes", "email"):
            self.assertNotIn(private, serialized)

    def test_unknown_native_and_case_ambiguous_team_is_rejected(self):
        for identifier in ("1", "native-1", "js26-b-001", "JS26-B-999", self.red.team_name):
            with self.subTest(identifier=identifier), self.assertRaises(IdentityError):
                resolve_team(self.competition, identifier)
        other = Team.objects.create(competition=self.competition, team_name="Other", team_type="blue", track="ai")
        Team.objects.filter(pk=other.pk).update(team_identifier="js26-b-001")
        with self.assertRaises(IdentityError):
            resolve_team(self.competition, "JS26-B-001")

    def test_interval_tracks_ownership_change_without_rewriting_history(self):
        first = TeamIdentityInterval.objects.get(team=self.blue)
        at = self.advance(30)
        self.blue.status = "withdrawn"
        self.blue.save(update_fields=["status"])
        first.refresh_from_db()
        self.assertEqual(first.valid_until, at)
        before = identity_snapshot(self.competition, start=self.now, end=at, now=at)
        self.assertEqual(before["targets"][0]["team"], self.blue.team_identifier)
        current = identity_snapshot(self.competition, now=at)
        self.assertEqual(current["targets"], [])
        self.assertEqual(current["scopes"], [])

    def test_enrolled_team_cannot_change_scoring_role_or_competition(self):
        other = Competition.objects.create(name="Other scoring", start_date=self.now.date(), end_date=self.now.date())
        for field, value in (("track", "application"), ("team_type", "red"), ("competition_id", other.pk)):
            self.blue.refresh_from_db()
            setattr(self.blue, field, value)
            with self.subTest(field=field), self.assertRaises(IdentityError):
                self.blue.save(update_fields=[field])
        Team.objects.filter(pk=self.blue.pk).update(track="application")
        with self.assertRaises(IdentityError):
            refresh_identity(self.competition)

    def test_freshness_gap_is_not_filled_retroactively(self):
        at = self.advance(130)
        self.target.verified_at = at
        self.target.save(update_fields=["verified_at", "updated_at"])
        intervals = list(TargetIdentityInterval.objects.order_by("valid_from"))
        self.assertEqual(len(intervals), 2)
        self.assertEqual(intervals[0].valid_until, self.now + dt.timedelta(seconds=120))
        self.assertEqual(intervals[1].valid_from, at)
        with self.assertRaises(IdentityError):
            validate_scoring_assignment(self.competition, self.blue, self.now, at, [1, 2])

    def test_artifact_heartbeat_extends_only_contiguous_verified_interval(self):
        self.advance(60)
        self.target.verified_at = self.clock.return_value
        self.target.save(update_fields=["verified_at", "updated_at"])
        self.assertEqual(TargetIdentityInterval.objects.count(), 1)
        row = TargetIdentityInterval.objects.get()
        self.assertEqual(row.valid_from, self.now)
        self.assertEqual(row.valid_until, self.now + dt.timedelta(seconds=180))

    def test_application_failure_does_not_remove_known_artifact(self):
        self.advance(10)
        self.target.status = "down"
        self.target.save(update_fields=["status", "updated_at"])
        snapshot = identity_snapshot(self.competition)
        self.assertEqual(len(snapshot["targets"]), 1)

    def test_ip_reassignment_ends_old_scope_at_change(self):
        at = self.advance(30)
        self.profile.address = "10.60.1.3/32"
        self.profile.save(update_fields=["address"])
        allocation = IPAllocation.objects.get(ip_address="10.60.1.2")
        self.assertEqual(allocation.valid_until, at)
        old_scope = ScopeAssignment.objects.get(source_cidr="10.60.1.2/32")
        self.assertEqual(old_scope.valid_until, at)
        self.assertEqual(identity_snapshot(self.competition)["scopes"][0]["source_cidr"], "10.60.1.3/32")

    def test_non_host_or_scoped_source_addresses_are_not_invented(self):
        for address in ("10.60.1.2/24", "fe80::1%eth0/128", "bad"):
            self.advance(10)
            self.profile.address = address
            self.profile.save(update_fields=["address"])
            self.assertEqual(identity_snapshot(self.competition)["scopes"], [])

    def test_manual_scope_is_separate_from_attacker_scope(self):
        self.assertFalse(any(s["team"] == self.blue.team_identifier for s in identity_snapshot(self.competition)["scopes"]))
        ActionScope.objects.create(competition=self.competition, team=self.blue, target=self.target,
            allowed_actions=["block-ip", "revert"], active=True, approved_by=self.staff)
        own = [s for s in identity_snapshot(self.competition)["scopes"] if s["team"] == self.blue.team_identifier]
        self.assertEqual(len(own), 1)
        self.assertEqual(own[0]["source_cidr"], "10.60.1.2/32")
        with self.assertRaises(ValidationError):
            ActionScope.objects.create(competition=self.competition, team=self.red, target=self.target,
                allowed_actions=["block-ip"], active=True, approved_by=self.red_user)

    def test_window_attribution_requires_coverage_and_known_revisions(self):
        self.advance(60)
        for team in (self.blue, self.red):
            rows = validate_scoring_assignment(self.competition, team, self.now, self.clock.return_value, [1])
            self.assertTrue(rows)
            for revisions in ([], [0], [True], [1, 1], [999]):
                with self.subTest(revisions=revisions), self.assertRaises(IdentityError):
                    validate_scoring_assignment(self.competition, team, self.now, self.clock.return_value, revisions)
        with self.assertRaises(IdentityError):
            validate_scoring_assignment(self.competition, self.blue, self.now - dt.timedelta(seconds=1), self.clock.return_value, [1])

    def test_stale_snapshot_and_overlapping_owner_fail_closed(self):
        self.advance(121)
        with self.assertRaises(IdentityError):
            identity_snapshot(self.competition)
        self.advance(60)
        row = TargetIdentityInterval.objects.get()
        row.pk = None
        row._state.adding = True
        row.save()
        with self.assertRaises(IdentityError):
            validate_scoring_assignment(self.competition, self.blue, self.now, self.clock.return_value, [1])

    def test_interval_immutable_and_failed_source_write_rolls_back(self):
        row = TeamIdentityInterval.objects.get(team=self.blue)
        with self.assertRaises(ValidationError):
            row.save()
        with self.assertRaises(ValidationError):
            TeamIdentityInterval.objects.filter(pk=row.pk).update(identifier="other")
        with self.assertRaises(IdentityError):
            with transaction.atomic():
                self.blue.team_identifier = "JS26-B-002"
                self.blue.save()
        self.blue.refresh_from_db()
        self.assertEqual(self.blue.team_identifier, "JS26-B-001")

    @override_settings(FEED_TOKEN="feed-secret")
    def test_identity_endpoint_requires_feed_credential(self):
        factory = RequestFactory()
        missing = identity_view(factory.get("/api/scoring/identity/", {"competition": self.competition.pk}))
        self.assertEqual(missing.status_code, 403)
        allowed = identity_view(factory.get("/api/scoring/identity/", {"competition": self.competition.pk}, HTTP_X_FEED_TOKEN="feed-secret"))
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed["Cache-Control"], "no-store")

    @override_settings(FEED_TOKEN="publisher-secret", SCORING_IDENTITY_TOKEN="identity-secret")
    def test_identity_credential_cannot_read_receipts_or_write_observations(self):
        factory = RequestFactory()
        headers = {"HTTP_X_SCORING_IDENTITY_TOKEN": "identity-secret"}
        allowed = identity_view(factory.get("/api/scoring/identity/", {"competition": self.competition.pk}, **headers))
        self.assertEqual(allowed.status_code, 200)
        for view, path in ((ingest_receipt, "/api/scoring/receipts/"), (scenario_view, "/api/scoring/scenarios/")):
            denied = view(factory.get(path, {"competition": self.competition.pk}, **headers))
            self.assertEqual(denied.status_code, 403)
        denied = ingest_receipt(factory.post("/api/scoring/receipts/", data="{}", content_type="application/json", **headers))
        self.assertEqual(denied.status_code, 403)
        wrong = identity_view(factory.get("/api/scoring/identity/", {"competition": self.competition.pk}, HTTP_X_SCORING_IDENTITY_TOKEN="wrong"))
        self.assertEqual(wrong.status_code, 403)
        with override_settings(FEED_TOKEN="", SCORING_IDENTITY_TOKEN=""):
            empty = identity_view(factory.get("/api/scoring/identity/", {"competition": self.competition.pk}))
            self.assertEqual(empty.status_code, 404)

    def test_v1_view_names_and_finding_aliases_are_exact(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM obs_findings_v1 WHERE 1 = 0")
            self.assertEqual([c[0] for c in cursor.description], [
                "contract_version", "updated_at", "finding_id", "enterprise", "application", "namespace",
                "start_ts", "end_ts", "telemetry_event_ids", "correlation_ids",
            ])
            cursor.execute("SELECT * FROM obs_ip_allocations_v1")
            columns = [c[0] for c in cursor.description]
            self.assertNotIn("user_id", columns)
            self.assertIn("participant_id", columns)

    def test_finding_references_reject_wrong_family_and_duplicate_values(self):
        finding = Finding(telemetry_event_ids=["infra_" + "a" * 64])
        with self.assertRaises(ValidationError):
            finding.clean()
        finding.telemetry_event_ids = ["evt_" + "a" * 64] * 2
        with self.assertRaises(ValidationError):
            finding.clean()
        finding.telemetry_event_ids = []
        finding.correlation_ids = {"private": "body"}
        with self.assertRaises(ValidationError):
            finding.clean()

    def test_v1_finding_export_requires_unique_full_event_time_ownership(self):
        owner, target, _ = self.application_target()
        self.advance(20)
        finding = Finding.objects.create(competition=self.competition, red_team=self.red, target_team=owner,
            round=1, started_at=self.now, ended_at=self.clock.return_value, title="Private finding",
            technique="private technique", steps="private steps", evidence="private evidence", impact="private impact",
            telemetry_event_ids=["evt_" + "a" * 64], correlation_ids=["trace:one"])
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM obs_findings_v1 WHERE finding_id = %s", [str(finding.pk)])
            values = cursor.fetchall()
            self.assertEqual(len(values), 1)
            self.assertNotIn("private", str(values).lower())
            self.assertEqual(values[0][3:6], ("ENT-A", "APP01", owner.namespace))
            self.assertIn("evt_" + "a" * 64, values[0][-2])
            self.assertIn("trace:one", values[0][-1])
        original = TargetIdentityInterval.objects.get(range_target=target)
        original.pk = None
        original._state.adding = True
        original.valid_from = self.now + dt.timedelta(seconds=10)
        original.revision += 1
        original.save()
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM obs_findings_v1 WHERE finding_id = %s", [str(finding.pk)])
            self.assertEqual(cursor.fetchone()[0], 0)

    def test_pinned_application_stays_eligible_during_failure_and_digest_drift_holds(self):
        from services import deployment_service
        team, target, submission = self.application_target()
        self.advance(20)
        deployment_service.record_status(submission, "failed")
        deployment_service.record_artifact(submission, "sha256:" + "b" * 64)
        rows = identity_snapshot(self.competition)["targets"]
        self.assertIn(team.team_identifier, {r["team"] for r in rows})
        self.advance(30)
        deployment_service.record_artifact(submission, "sha256:" + "c" * 64)
        rows = identity_snapshot(self.competition)["targets"]
        self.assertNotIn(team.team_identifier, {r["team"] for r in rows})
        historic = TargetIdentityInterval.objects.get(team=team)
        self.assertEqual(historic.valid_until, self.clock.return_value)

    def test_bulk_source_changes_hold_until_observed_refresh(self):
        Team.objects.filter(pk=self.blue.pk).update(status="withdrawn")
        with self.assertRaises(IdentityError):
            identity_snapshot(self.competition)
        self.advance(20)
        refresh_identity(self.competition)
        self.assertEqual(identity_snapshot(self.competition)["targets"], [])

    def test_final_window_history_works_after_actions_close(self):
        end = self.now + dt.timedelta(seconds=100)
        with patch("services.scoring_identity.competition_window", return_value=(self.now, end)):
            self.advance(220)
            historical = identity_snapshot(self.competition, start=self.now, end=end)
            self.assertTrue(historical["historical"])
            self.assertGreater(historical["expires_at"], historical["captured_at"])
            self.assertTrue(validate_scoring_assignment(self.competition, self.blue, self.now, end, [1]))
            with self.assertRaises(IdentityError):
                identity_snapshot(self.competition)


class ScoringIdentityMigrationTests(TransactionTestCase):
    def test_upgrade_preserves_sources_without_fabricating_history_and_views_reverse(self):
        executor = MigrationExecutor(connection)
        original_targets = executor.loader.graph.leaf_nodes()
        legacy_targets = [("accounts", "0016_participant_consent"),
                          ("competitions", "0017_competitionsettings_red_roster_deadline"),
                          ("cyberrange", "0001_initial")]
        try:
            executor.migrate(legacy_targets)
            old = executor.loader.project_state(legacy_targets).apps
            User = old.get_model("auth", "User")
            Profile = old.get_model("accounts", "UserProfile")
            user = User.objects.create(username="migration-source")
            profile = Profile.objects.create(user=user)
            executor = MigrationExecutor(connection)
            executor.migrate(original_targets)
            new = executor.loader.project_state(original_targets).apps
            migrated = new.get_model("accounts", "UserProfile").objects.get(pk=profile.pk)
            self.assertRegex(migrated.participant_identifier, r"JS[0-9]{2}-P-[0-9]{4,}")
            for name in ("ScoringIdentityState", "TeamIdentityInterval", "TargetIdentityInterval", "IPAllocation", "ScopeAssignment"):
                self.assertEqual(new.get_model("cyberrange", name).objects.count(), 0)
            MigrationExecutor(connection).migrate([("cyberrange", "0002_finding_correlation_ids_finding_scoring_objective_and_more")])
            with connection.cursor() as cursor:
                tables = {item.name for item in connection.introspection.get_table_list(cursor)}
                self.assertNotIn("obs_findings_v1", tables)
                self.assertIn("cyberrange_targetidentityinterval", tables)
            MigrationExecutor(connection).migrate(original_targets)
            with connection.cursor() as cursor:
                cursor.execute("SELECT start_ts, end_ts, correlation_ids FROM obs_findings_v1")
                self.assertEqual(cursor.fetchall(), [])
        finally:
            MigrationExecutor(connection).migrate(original_targets)
