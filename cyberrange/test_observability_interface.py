"""Reconciliation and native view boundaries, using only disposable databases."""
import datetime
import importlib
import json
from unittest import skipUnless

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from accounts.models import Team, TeamMember, UserProfile
from competitions.models import TargetAssignment
from services import range_service

from .models import Finding, IPAllocation, RangeApplication, ScopeAssignment, WireGuardProfile
from .tests import RangeTestBase


class ReconciliationTests(RangeTestBase):
    def setUp(self):
        super().setUp()
        self.wireguard = WireGuardProfile.objects.create(
            user=self.red_user, team=self.red, address="10.60.7.10/32", downloaded_at=timezone.now(),
        )
        range_service.sync_observability_data(self.comp)

    def snapshot(self):
        return [list(model.objects.order_by("pk").values()) for model in
                (RangeApplication, IPAllocation, ScopeAssignment, UserProfile)]

    def test_identical_run_keeps_ids_validity_timestamps_and_roles(self):
        before = self.snapshot()
        groups = list(User.groups.through.objects.order_by("pk").values())
        range_service.sync_observability_data(self.comp)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(list(User.groups.through.objects.order_by("pk").values()), groups)

    def test_enabled_optional_apps_stay_enabled_on_default_rerun(self):
        range_service.sync_observability_data(self.comp, enable_optional=True)
        before = self.snapshot()
        range_service.sync_observability_data(self.comp)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(RangeApplication.objects.filter(optional=True, enabled=True).count(), 4)

    def test_asymmetric_optional_settings_fail_without_partial_updates(self):
        RangeApplication.objects.filter(enterprise="ENT-A", application="APP08").update(enabled=True)
        before = self.snapshot()
        with self.assertRaisesRegex(range_service.ObservabilitySyncError, "inconsistent activation"):
            range_service.sync_observability_data(self.comp)
        self.assertEqual(self.snapshot(), before)
        range_service.sync_observability_data(self.comp, enable_optional=True)
        self.assertEqual(RangeApplication.objects.filter(optional=True, enabled=True).count(), 4)

    def test_assigned_service_address_wins_and_old_allocation_is_retired(self):
        self.blue_a.service_ip = "10.108.0.50"
        self.blue_a.save(update_fields=["service_ip"])
        range_service.sync_observability_data(self.comp)
        app = RangeApplication.objects.get(competition=self.comp, enterprise="ENT-A", application="APP03")
        self.assertEqual((app.ip_address, app.namespace), ("10.108.0.50", "jengasec-ent-a-app03"))
        self.assertEqual(IPAllocation.objects.get(ip_address="10.108.0.13").status, "revoked")
        self.blue_a.refresh_from_db()
        self.assertEqual(self.blue_a.service_ip, "10.108.0.50")
        self.assertIn("10.108.0.50", range_service.scope_csv(self.comp))
        before = self.snapshot()
        range_service.sync_observability_data(self.comp)
        self.assertEqual(self.snapshot(), before)

    def test_invalid_namespace_or_reservation_rolls_back(self):
        for field, value in (("namespace", "custom-owned-namespace"), ("service_ip", "10.109.0.10")):
            with self.subTest(field=field):
                original = getattr(self.blue_a, field)
                Team.objects.filter(pk=self.blue_a.pk).update(**{field: value})
                before = self.snapshot()
                with self.assertRaises(range_service.ObservabilitySyncError):
                    range_service.sync_observability_data(self.comp)
                self.assertEqual(self.snapshot(), before)
                Team.objects.filter(pk=self.blue_a.pk).update(**{field: original})

    def test_unassigned_namespace_does_not_create_read_scope_or_change_source(self):
        Team.objects.filter(pk=self.blue_a.pk).update(namespace="")
        result = range_service.sync_observability_data(self.comp)
        self.assertGreater(result["unresolved_scopes"], 0)
        self.assertFalse(ScopeAssignment.objects.get(application__enterprise="ENT-A").active)
        self.blue_a.refresh_from_db()
        self.assertEqual(self.blue_a.namespace, "")

    def test_adding_an_unassigned_team_never_renumbers_networks(self):
        before = range_service.red_ranges(self.comp)
        Team.objects.create(competition=self.comp, team_name="Earlier team", team_identifier="JS26-R-000",
                            team_type="red", track="application", status=Team.Status.APPROVED)
        range_service.sync_observability_data(self.comp)
        self.assertEqual(range_service.red_ranges(self.comp), before)
        self.assertEqual(before[self.red.pk], "10.60.7.0/24")

    def test_deployment_exports_keep_legacy_shape_without_reconciliation(self):
        before = self.snapshot()
        self.assertIn("name: jengasec-ent-a-app03", range_service.deployment_cells_yaml(self.comp))
        self.assertIn("10.60.7.0/24,true", range_service.deployment_scope_csv(self.comp))
        self.assertEqual(self.snapshot(), before)
        WireGuardProfile.objects.filter(pk=self.wireguard.pk).update(revoked=True)
        export = range_service.deployment_scope_csv(self.comp)
        self.assertNotIn("10.60.7.0/24", export)
        self.assertIn("source network unavailable", export)
        self.assertNotIn(",true,", export)

    def test_missing_invalid_and_revoked_profile_retire_prior_binding(self):
        for value in ("", "not-an-address", "10.108.0.13/32", "10.60.7.1/32", "10.60.7.10/24"):
            with self.subTest(value=value):
                WireGuardProfile.objects.filter(pk=self.wireguard.pk).update(address=value)
                result = range_service.sync_observability_data(self.comp)
                self.assertEqual(result["invalid_profiles"], 1)
                allocation = IPAllocation.objects.get(kind="participant")
                self.assertEqual(allocation.status, "revoked")
                self.assertFalse(ScopeAssignment.objects.filter(active=True).exists())
                before = self.snapshot()
                range_service.sync_observability_data(self.comp)
                self.assertEqual(self.snapshot(), before)
                WireGuardProfile.objects.filter(pk=self.wireguard.pk).update(address="10.60.7.10/32")
                range_service.sync_observability_data(self.comp)
        WireGuardProfile.objects.filter(pk=self.wireguard.pk).update(revoked=True)
        range_service.sync_observability_data(self.comp)
        before = self.snapshot()
        range_service.sync_observability_data(self.comp)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(IPAllocation.objects.get(kind="participant").status, "revoked")

    def test_inactive_user_and_removed_membership_do_not_keep_allocations(self):
        User.objects.filter(pk=self.red_user.pk).update(is_active=False)
        range_service.sync_observability_data(self.comp)
        self.assertEqual(IPAllocation.objects.get(kind="participant").status, "revoked")
        User.objects.filter(pk=self.red_user.pk).update(is_active=True)
        Team.objects.filter(pk=self.red.pk).update(captain=None)
        TeamMember.objects.filter(team=self.red, user=self.red_user).delete()
        range_service.sync_observability_data(self.comp)
        self.assertFalse(ScopeAssignment.objects.filter(active=True).exists())

    def test_duplicate_source_ip_is_rejected_atomically(self):
        user = User.objects.create_user("other-participant")
        TeamMember.objects.create(team=self.red, user=user, student_name="fixture", email="fixture@example.invalid")
        WireGuardProfile.objects.create(user=user, team=self.red, address=self.wireguard.address)
        before = self.snapshot()
        with self.assertRaisesRegex(range_service.ObservabilitySyncError, "More than one participant"):
            range_service.sync_observability_data(self.comp)
        self.assertEqual(self.snapshot(), before)

    def test_missing_catalogue_is_rejected_without_changes(self):
        self.briefs["APP09"].delete()
        before = self.snapshot()
        with self.assertRaisesRegex(range_service.ObservabilitySyncError, "catalogue is incomplete"):
            range_service.sync_observability_data(self.comp)
        self.assertEqual(self.snapshot(), before)

    def test_invalid_identifier_failure_rolls_back_earlier_updates(self):
        self.briefs["APP01"].name = "Updated service"
        self.briefs["APP01"].save(update_fields=["name"])
        UserProfile.objects.filter(user=self.red_user).update(participant_identifier="person@example.test")
        before = self.snapshot()
        with self.assertRaisesRegex(range_service.ObservabilitySyncError, "invalid participant identifier"):
            range_service.sync_observability_data(self.comp)
        self.assertEqual(self.snapshot(), before)

    def test_opaque_reference_issuance_never_changes_role_groups(self):
        UserProfile.objects.filter(user=self.judge).update(participant_identifier="", role="admin")
        before = list(User.groups.through.objects.order_by("pk").values())
        range_service.sync_observability_data(self.comp)
        self.assertEqual(list(User.groups.through.objects.order_by("pk").values()), before)
        self.judge.refresh_from_db()
        self.assertFalse(self.judge.is_staff)


@skipUnless(connection.vendor == "postgresql", "native PostgreSQL views")
class PostgreSQLViewTests(RangeTestBase):
    def setUp(self):
        super().setUp()
        self.wireguard = WireGuardProfile.objects.create(
            user=self.red_user, team=self.red, address="10.60.7.10/32", downloaded_at=timezone.now(),
        )
        range_service.sync_observability_data(self.comp)

    def rows(self, view):
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM " + connection.ops.quote_name(view))
            rows = [dict(zip([column.name for column in cursor.description], row)) for row in cursor.fetchall()]
        for row in rows:
            for name in ("telemetry_event_ids", "correlation_ids"):
                if name in row and isinstance(row[name], str):
                    row[name] = json.loads(row[name])
        return rows

    def finding(self, **extra):
        values = dict(competition=self.comp, red_team=self.red, target_team=self.blue_a, round=1,
                      started_at=timezone.now() - datetime.timedelta(minutes=5), ended_at=timezone.now(),
                      title="Private report title", technique="private", steps="private", evidence="private",
                      impact="private", telemetry_event_ids=["evt_" + "a" * 64], correlation_ids=["request-1"])
        values.update(extra)
        return Finding.objects.create(**values)

    def test_all_five_views_have_only_contract_columns(self):
        expected = {
            "obs_ip_allocations_v1": "contract_version updated_at allocation_id ip_address kind participant_id team_id team_label namespace status valid_from valid_until",
            "obs_applications_v1": "contract_version updated_at application_instance_id enterprise application namespace service_name ip_address status optional enabled image_digest",
            "obs_scope_assignments_v1": "contract_version updated_at team_id source_cidr enterprise application namespace target_ip active valid_from valid_until",
            "obs_access_principals_v1": "contract_version updated_at participant_id role team_id enterprise application namespace",
            "obs_findings_v1": "contract_version updated_at finding_id enterprise application namespace start_ts end_ts telemetry_event_ids correlation_ids",
        }
        self.finding()
        for name, columns in expected.items():
            with self.subTest(view=name), connection.cursor() as cursor:
                cursor.execute("SELECT * FROM " + connection.ops.quote_name(name))
                self.assertEqual([column.name for column in cursor.description], columns.split())
                self.assertTrue(cursor.fetchall())

    def test_inactive_assignment_account_or_team_loses_access_without_sync(self):
        def roles():
            return {row["role"] for row in self.rows("obs_access_principals_v1")}
        self.assertIn("red_team", roles())
        TargetAssignment.objects.filter(red_team=self.red).update(is_active=False)
        self.assertNotIn("red_team", roles())
        User.objects.filter(pk=self.judge.pk).update(is_active=False)
        self.assertNotIn("judge", roles())
        Team.objects.filter(pk=self.blue_a.pk).update(status=Team.Status.WITHDRAWN)
        self.assertNotIn("blue_team", roles())

    def test_global_principals_never_also_export_team_roles(self):
        TeamMember.objects.create(team=self.red, user=self.judge,
                                  student_name="fixture", email="fixture@example.invalid")
        Team.objects.filter(pk=self.blue_b.pk).update(captain=self.judge)
        self.judge.profile.refresh_from_db()
        participant_id = self.judge.profile.participant_identifier
        for source_role, exported_role in (("judge", "judge"), ("admin", "staff")):
            with self.subTest(role=source_role):
                UserProfile.objects.filter(user=self.judge).update(role=source_role)
                rows = [row for row in self.rows("obs_access_principals_v1")
                        if row["participant_id"] == participant_id]
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["role"], exported_role)
                self.assertIsNone(rows[0]["team_id"])
                self.assertIsNone(rows[0]["namespace"])

    def test_revoked_profile_expired_scope_and_changed_target_fail_closed(self):
        for change in ("revoked", "expired", "namespace", "address", "enterprise"):
            with self.subTest(change=change), transaction.atomic():
                if change == "revoked":
                    WireGuardProfile.objects.filter(pk=self.wireguard.pk).update(revoked=True)
                elif change == "expired":
                    ScopeAssignment.objects.update(valid_until=timezone.now() - datetime.timedelta(seconds=1))
                elif change == "namespace":
                    Team.objects.filter(pk=self.blue_a.pk).update(namespace="not-the-assigned-namespace")
                elif change == "enterprise":
                    Team.objects.filter(pk=self.blue_a.pk).update(enterprise="")
                else:
                    Team.objects.filter(pk=self.blue_a.pk).update(service_ip="10.108.0.50")
                self.assertTrue(all(row["active"] is False for row in self.rows("obs_scope_assignments_v1")))
                self.assertFalse(any(row["role"] == "red_team" for row in self.rows("obs_access_principals_v1")))
                transaction.set_rollback(True)

    def test_findings_have_exact_windows_and_deduplicated_opaque_references(self):
        event = "evt_" + "a" * 64
        finding = self.finding(telemetry_event_ids=[event, event], correlation_ids=["request-1", "request-1"])
        row = self.rows("obs_findings_v1")[0]
        self.assertEqual(row["finding_id"], str(finding.pk))
        self.assertEqual((row["start_ts"], row["end_ts"]), (finding.started_at, finding.ended_at))
        self.assertEqual(row["telemetry_event_ids"], [event])
        self.assertEqual(row["correlation_ids"], ["request-1"])
        self.assertNotIn("Private", str(row))

    def test_unstructured_or_malformed_finding_ids_never_leave_the_view(self):
        for refs in ([], {}, ["person@example.invalid"], [None], [1]):
            finding = self.finding(telemetry_event_ids=refs, correlation_ids=[])
            self.assertFalse(self.rows("obs_findings_v1"))
            finding.delete()
        finding = self.finding(correlation_ids="not-a-list")
        with self.assertRaises(ValidationError):
            finding.full_clean()
        self.assertFalse(self.rows("obs_findings_v1"))

    def test_reader_can_select_views_but_not_source_tables_or_write(self):
        with connection.cursor() as cursor:
            cursor.execute("CREATE ROLE obs_contract_test_reader NOLOGIN")
            cursor.execute("GRANT USAGE ON SCHEMA public TO obs_contract_test_reader")
            for name in ("obs_ip_allocations_v1", "obs_applications_v1", "obs_scope_assignments_v1",
                         "obs_access_principals_v1", "obs_findings_v1"):
                cursor.execute(f"GRANT SELECT ON {name} TO obs_contract_test_reader")
            cursor.execute("SET LOCAL ROLE obs_contract_test_reader")
            try:
                for name in ("obs_ip_allocations_v1", "obs_applications_v1", "obs_scope_assignments_v1",
                             "obs_access_principals_v1", "obs_findings_v1"):
                    self.rows(name)
                for statement in ("SELECT * FROM auth_user", "SELECT * FROM cyberrange_wireguardprofile",
                                  "UPDATE applications SET enabled=false", "DELETE FROM obs_ip_allocations_v1"):
                    with self.subTest(statement=statement), self.assertRaises(DatabaseError):
                        with transaction.atomic():
                            cursor.execute(statement)
            finally:
                cursor.execute("RESET ROLE")

    def test_view_rollback_and_reapply_preserve_reader_grants(self):
        migration = importlib.import_module("cyberrange.migrations.0004_observability_contract")
        with connection.cursor() as cursor:
            cursor.execute("CREATE ROLE obs_contract_rollback_reader NOLOGIN")
            cursor.execute("GRANT SELECT ON obs_findings_v1 TO obs_contract_rollback_reader")
        with connection.schema_editor(atomic=False) as editor:
            migration.backwards(None, editor)
            migration.forwards(None, editor)
        with connection.cursor() as cursor:
            cursor.execute("SELECT has_table_privilege('obs_contract_rollback_reader', 'obs_findings_v1', 'SELECT')")
            self.assertTrue(cursor.fetchone()[0])
        self.finding()
        self.assertIn("correlation_ids", self.rows("obs_findings_v1")[0])
