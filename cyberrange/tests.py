"""Range hooks: exports match the infra contracts, findings respect scope,
WireGuard profiles reach only their owner."""
import datetime
import io
import pathlib
import shutil
import tempfile

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Team, TeamMember, UserProfile
from audit.models import AuditLog
from competitions.models import ApplicationBrief, Competition, TargetAssignment
from services import range_service

from .models import Finding, IPAllocation, RangeApplication, ScopeAssignment, WireGuardProfile

PASSWORD = "Range-Test-Pass-2026!"
TMP_MEDIA = tempfile.mkdtemp(prefix="jengasec-range-tests-")
# Never let a developer's S3_* settings send test files to the real bucket.
TEST_STORAGES = {
    **settings.STORAGES,
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage", "OPTIONS": {"location": TMP_MEDIA}},
}


def tearDownModule():
    shutil.rmtree(TMP_MEDIA, ignore_errors=True)


@override_settings(STORAGES=TEST_STORAGES, MEDIA_ROOT=TMP_MEDIA)
class RangeTestBase(TestCase):
    def setUp(self):
        self.comp = Competition.objects.create(
            name="JS26 range", start_date=datetime.date(2026, 10, 14),
            end_date=datetime.date(2026, 10, 17), status=Competition.Status.OPEN,
        )
        self.briefs = {
            code: ApplicationBrief.objects.create(
                competition=self.comp,
                code=code,
                name=f"Reference service {code}",
                is_optional=code in {"APP08", "APP09"},
            )
            for code in range_service.APPLICATION_CODES
        }

        def team(name, ident, side, track, ent, cell, captain=None):
            return Team.objects.create(
                competition=self.comp, team_name=name, team_identifier=ident, team_type=side,
                track=track, enterprise=ent, cell_id=cell, status=Team.Status.APPROVED, captain=captain,
                application_brief=self.briefs.get(cell),
            )

        self.blue_user = User.objects.create_user("amina", "amina@strathmore.edu", PASSWORD)
        self.blue_a = team("Nyati", "JS26-B-001", "blue", "application", "a", "APP03", self.blue_user)
        TeamMember.objects.create(team=self.blue_a, user=self.blue_user, student_name="Amina",
                                  email="amina@strathmore.edu", role=TeamMember.MemberRole.CAPTAIN)
        self.blue_b = team("Simba", "JS26-B-002", "blue", "application", "b", "APP03")
        self.ai_a = team("Tai", "JS26-B-003", "blue", "ai", "a", "AI01")

        self.red_user = User.objects.create_user("otieno", "otieno@strathmore.edu", PASSWORD)
        self.red = team("Kifaru", "JS26-R-001", "red", "application", "a", "APPRED01", self.red_user)
        TeamMember.objects.create(team=self.red, user=self.red_user, student_name="Otieno",
                                  email="otieno@strathmore.edu", role=TeamMember.MemberRole.CAPTAIN)

        TargetAssignment.objects.create(competition=self.comp, red_team=self.red, target_team=self.blue_a, is_active=True)
        TargetAssignment.objects.create(competition=self.comp, red_team=self.red, target_team=self.blue_b, is_active=False)

        self.judge = User.objects.create_user("judy", "judy@strathmore.edu", PASSWORD)
        self.judge.profile.role = UserProfile.Role.JUDGE
        self.judge.profile.save()
        range_service.sync_observability_data(self.comp)


class ExportTests(RangeTestBase):
    def test_namespace_is_always_canonical(self):
        self.assertEqual(range_service.namespace_for(self.blue_a), "jengasec-ent-a-app03")
        self.assertEqual(range_service.namespace_for(self.blue_b), "jengasec-ent-b-app03")
        self.blue_a.namespace = "custom-ns"
        self.assertEqual(range_service.namespace_for(self.blue_a), "jengasec-ent-a-app03")

    def test_cells_yaml_lists_blue_cells_only_with_kinds(self):
        text = range_service.cells_yaml(self.comp)
        self.assertIn("namespace: jengasec-ent-a-app03, enterprise: ENT-A, application: APP03", text)
        self.assertIn("namespace: jengasec-ent-b-app03, enterprise: ENT-B, application: APP03", text)
        self.assertEqual(text.count("  - {"), 18)
        self.assertNotIn("AI01", text)
        self.assertNotIn("APPRED01", text)

    def test_scope_csv_matches_the_infra_contract(self):
        lines = range_service.scope_csv(self.comp).splitlines()
        infra_header = "team_id,source_cidr,enterprise,application,namespace,target_ip,active,notes"
        self.assertEqual(lines[0], infra_header)
        self.assertIn("JS26-R-001,10.60.1.0/24,ENT-A,APP03,jengasec-ent-a-app03,10.108.0.13,true,", lines)
        self.assertIn("JS26-R-001,10.60.1.0/24,ENT-B,APP03,jengasec-ent-b-app03,10.108.0.23,false,", lines)

    def test_allocation_csv_carries_no_personal_data(self):
        text = range_service.allocation_csv(self.comp)
        self.assertNotIn("@", text)
        self.assertIn("ENT-A,APP03,jengasec-ent-a-app03,app,blue,application,JS26-B-001,Nyati", text)

    def test_export_command_writes_three_files_and_audits(self):
        out = pathlib.Path(tempfile.mkdtemp(prefix="range-export-"))
        try:
            call_command("export_range", competition=self.comp.pk, out=str(out), fill_namespaces=True, stdout=io.StringIO())
            for name in ("cells.yaml", "scope-assignments.csv", "allocation.csv"):
                self.assertTrue((out / name).exists(), name)
        finally:
            shutil.rmtree(out, ignore_errors=True)
        self.blue_a.refresh_from_db()
        self.assertEqual(self.blue_a.namespace, "jengasec-ent-a-app03")
        self.assertTrue(AuditLog.objects.filter(action=AuditLog.Action.RANGE_EXPORTED).exists())


class ObservabilityInterfaceTests(RangeTestBase):
    def test_reconciliation_is_idempotent_and_keeps_optional_apps_symmetric(self):
        first = range_service.sync_observability_data(self.comp)
        second = range_service.sync_observability_data(self.comp)
        self.assertEqual(first, second)
        self.assertEqual(RangeApplication.objects.filter(competition=self.comp).count(), 18)
        optional = RangeApplication.objects.filter(competition=self.comp, optional=True)
        self.assertEqual(optional.count(), 4)
        self.assertFalse(optional.filter(enabled=True).exists())
        self.assertEqual(ScopeAssignment.objects.filter(competition=self.comp).count(), 2)

    def test_wireguard_allocation_uses_opaque_identifier_and_no_personal_fields(self):
        profile = WireGuardProfile(
            user=self.red_user,
            team=self.red,
            address="10.60.1.7/32",
            downloaded_at=timezone.now(),
        )
        profile.config.save("red.conf", ContentFile(b"[Interface]\nPrivateKey = TEST\n"), save=True)
        range_service.sync_observability_data(self.comp)
        allocation = IPAllocation.objects.get(
            competition=self.comp,
            kind=IPAllocation.Kind.PARTICIPANT,
        )
        self.assertEqual(allocation.ip_address, "10.60.1.7")
        self.assertEqual(allocation.status, IPAllocation.Status.ACTIVE)
        self.assertRegex(allocation.participant.participant_identifier, r"^JS26-P-\d{4,}$")
        self.assertEqual(allocation.team_label, IPAllocation.TeamLabel.RED_APP)

    def test_postgresql_views_are_versioned_and_privacy_safe(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL views are exercised by hosted CI")
        event_id = "evt_" + "a" * 64
        Finding.objects.create(
            competition=self.comp,
            red_team=self.red,
            target_team=self.blue_a,
            round=1,
            started_at="2026-10-14T09:10Z",
            ended_at="2026-10-14T09:40Z",
            title="test",
            technique="test",
            steps="test",
            evidence="event cited separately",
            telemetry_event_ids=[event_id],
            impact="test",
        )
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM obs_access_principals_v1")
            access_columns = [column.name for column in cursor.description]
            access_rows = cursor.fetchall()
            cursor.execute("SELECT telemetry_event_ids FROM obs_findings_v1")
            finding_event_ids = cursor.fetchone()[0]
        self.assertEqual(
            access_columns,
            [
                "contract_version", "updated_at", "participant_id", "role", "team_id",
                "enterprise", "application", "namespace",
            ],
        )
        self.assertNotIn("name", access_columns)
        self.assertNotIn("email", access_columns)
        self.assertTrue(any(row[3] == "blue_team" and row[5] == "ENT-A" for row in access_rows))
        self.assertTrue(any(row[3] == "judge" and row[5] is None for row in access_rows))
        self.assertEqual(finding_event_ids, [event_id])


class FindingTests(RangeTestBase):
    def post_finding(self, target):
        return self.client.post(reverse("cyberrange:finding_new"), {
            "target_team": target.pk, "round": 1,
            "started_at": "2026-10-14T09:10", "ended_at": "2026-10-14T09:40",
            "title": "IDOR on /accounts/<id>", "technique": "IDOR", "attack_id": "T1190",
            "steps": "1. Log in\n2. Change the id", "evidence": "GET /accounts/2 returned 200",
            "impact": "Read any customer's balance",
        })

    def test_red_member_reports_against_active_scope(self):
        self.client.login(username="otieno", password=PASSWORD)
        self.assertEqual(self.client.get(reverse("cyberrange:finding_new")).status_code, 200)
        response = self.post_finding(self.blue_a)
        finding = Finding.objects.get()
        self.assertRedirects(response, reverse("cyberrange:finding_detail", args=[finding.pk]))
        self.assertEqual((finding.red_team, finding.submitted_by), (self.red, self.red_user))
        self.assertTrue(AuditLog.objects.filter(action=AuditLog.Action.FINDING_SUBMITTED).exists())

    def test_inactive_or_unassigned_target_is_refused(self):
        self.client.login(username="otieno", password=PASSWORD)
        for target in (self.blue_b, self.ai_a):  # assignment inactive / no assignment at all
            response = self.post_finding(target)
            self.assertEqual(response.status_code, 200)
            self.assertIn("target_team", response.context["form"].errors)
        self.assertFalse(Finding.objects.exists())

    def test_non_red_users_cannot_report(self):
        self.client.login(username="amina", password=PASSWORD)
        self.assertEqual(self.client.get(reverse("cyberrange:finding_new")).status_code, 404)

    def test_visibility_owner_team_and_reviewers_only(self):
        finding = Finding.objects.create(
            competition=self.comp, red_team=self.red, target_team=self.blue_a, round=1,
            started_at="2026-10-14T09:10Z", ended_at="2026-10-14T09:40Z", title="t", technique="x",
            steps="s", evidence="e", impact="i", submitted_by=self.red_user,
        )
        url = reverse("cyberrange:finding_detail", args=[finding.pk])
        self.client.login(username="amina", password=PASSWORD)  # the Blue team being attacked
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.login(username="otieno", password=PASSWORD)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.login(username="judy", password=PASSWORD)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.context["review_form"])

    def test_duplicate_needs_an_earlier_finding(self):
        first = Finding.objects.create(
            competition=self.comp, red_team=self.red, target_team=self.blue_a, round=1,
            started_at="2026-10-14T09:10Z", ended_at="2026-10-14T09:40Z", title="a", technique="x",
            steps="s", evidence="e", impact="i",
        )
        second = Finding.objects.create(
            competition=self.comp, red_team=self.red, target_team=self.blue_a, round=1,
            started_at="2026-10-14T09:50Z", ended_at="2026-10-14T09:55Z", title="b", technique="x",
            steps="s", evidence="e", impact="i",
        )
        self.client.login(username="judy", password=PASSWORD)
        url = reverse("cyberrange:finding_detail", args=[second.pk])
        response = self.client.post(url, {"status": "duplicate", "severity": "", "reviewer_notes": ""})
        self.assertIn("duplicate_of", response.context["review_form"].errors)
        self.client.post(url, {"status": "duplicate", "severity": "", "duplicate_of": first.pk, "reviewer_notes": ""})
        second.refresh_from_db()
        self.assertEqual((second.status, second.duplicate_of), (Finding.Status.DUPLICATE, first))


class WireGuardTests(RangeTestBase):
    def give_profile(self, user):
        profile = WireGuardProfile(user=user, address="10.60.1.2/32")
        profile.config.save(f"{user.username}.conf", ContentFile(b"[Interface]\nPrivateKey = TEST\n"), save=True)
        return profile

    def test_owner_downloads_and_it_is_recorded(self):
        profile = self.give_profile(self.red_user)
        self.client.login(username="otieno", password=PASSWORD)
        response = self.client.get(reverse("cyberrange:wireguard_download"))
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"PrivateKey", b"".join(response.streaming_content))
        profile.refresh_from_db()
        self.assertIsNotNone(profile.downloaded_at)
        self.assertTrue(AuditLog.objects.filter(action=AuditLog.Action.WIREGUARD_DOWNLOADED).exists())

    def test_nobody_else_gets_it_and_revoked_is_gone(self):
        profile = self.give_profile(self.red_user)
        staff = User.objects.create_superuser("admin", "admin@strathmore.edu", PASSWORD)
        self.client.force_login(staff)  # not even staff: the URL only ever serves your own file
        self.assertEqual(self.client.get(reverse("cyberrange:wireguard_download")).status_code, 404)
        profile.revoked = True
        profile.save()
        self.client.login(username="otieno", password=PASSWORD)
        self.assertEqual(self.client.get(reverse("cyberrange:wireguard_download")).status_code, 404)

    def test_load_command_matches_files_to_usernames(self):
        src = pathlib.Path(tempfile.mkdtemp(prefix="wg-"))
        try:
            (src / "otieno.conf").write_text("[Interface]\nAddress = 10.60.1.7/32\n", encoding="utf-8")
            (src / "nobody.conf").write_text("[Interface]\n", encoding="utf-8")
            (src / "gateway-peers.conf").write_text("# peers\n", encoding="utf-8")
            call_command("load_wireguard_profiles", dir=str(src), stdout=io.StringIO(), stderr=io.StringIO())
        finally:
            shutil.rmtree(src, ignore_errors=True)
        profile = WireGuardProfile.objects.get()
        self.assertEqual((profile.user, profile.address, profile.team), (self.red_user, "10.60.1.7/32", self.red))
