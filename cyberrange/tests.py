"""Range hooks: exports match the infra contracts, findings respect scope,
WireGuard profiles reach only their owner."""
import datetime
import importlib
import io
import pathlib
import shutil
import tempfile

from django.apps import apps
from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Team, TeamMember, UserProfile
from audit.models import AuditLog
from competitions.models import Competition, TargetAssignment
from services import range_service

from .models import Finding, WireGuardProfile

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

        def team(name, ident, side, track, ent, cell, captain=None):
            return Team.objects.create(
                competition=self.comp, team_name=name, team_identifier=ident, team_type=side,
                track=track, enterprise=ent, cell_id=cell, status=Team.Status.APPROVED, captain=captain,
            )

        self.blue_user = User.objects.create_user("amina", "amina@strathmore.edu", PASSWORD)
        self.blue_a = team("Nyati", "JS26-B-001", "blue", "application", "a", "APP03", self.blue_user)
        self.blue_b = team("Simba", "JS26-B-002", "blue", "application", "b", "APP03")
        self.ai_a = team("Tai", "JS26-B-003", "blue", "ai", "a", "AI01")
        self.blue_a.service_ip = "10.108.0.11"
        self.blue_a.save(update_fields=["service_ip"])
        self.blue_b.service_ip = "10.108.0.12"
        self.blue_b.save(update_fields=["service_ip"])

        self.red_user = User.objects.create_user("otieno", "otieno@strathmore.edu", PASSWORD)
        self.red = team("Kifaru", "JS26-R-001", "red", "application", "a", "APPRED01", self.red_user)
        TeamMember.objects.create(team=self.red, user=self.red_user, student_name="Otieno",
                                  email="otieno@strathmore.edu", role=TeamMember.MemberRole.CAPTAIN)

        TargetAssignment.objects.create(competition=self.comp, red_team=self.red, target_team=self.blue_a, is_active=True)
        TargetAssignment.objects.create(competition=self.comp, red_team=self.red, target_team=self.blue_b, is_active=False)

        self.judge = User.objects.create_user("judy", "judy@strathmore.edu", PASSWORD)
        self.judge.profile.role = UserProfile.Role.JUDGE
        self.judge.profile.save()


class ExportTests(RangeTestBase):
    def test_namespace_uses_the_shared_infrastructure_contract(self):
        self.assertEqual(range_service.namespace_for(self.blue_a), "jengasec-ent-a-app03")
        self.assertEqual(range_service.namespace_for(self.blue_b), "jengasec-ent-b-app03")
        self.blue_a.namespace = "custom-ns"
        with self.assertRaises(ValueError):
            range_service.namespace_for(self.blue_a)

    def test_migration_upgrades_legacy_derived_namespaces(self):
        self.blue_a.namespace = "enta-app03"
        self.blue_a.save(update_fields=["namespace"])
        migration = importlib.import_module(
            "accounts.migrations.0016_canonical_team_namespaces"
        )
        migration.canonicalize_default_namespaces(apps, None)
        self.blue_a.refresh_from_db()
        self.assertEqual(self.blue_a.namespace, "jengasec-ent-a-app03")

    def test_cells_yaml_lists_blue_cells_only_with_kinds(self):
        text = range_service.cells_yaml(self.comp)
        self.assertIn(
            "name: jengasec-ent-a-app03, enterprise: enta, code: APP03, kind: app",
            text,
        )
        self.assertIn(
            "name: jengasec-ent-b-app03, enterprise: entb, code: APP03, kind: app",
            text,
        )
        self.assertIn('service_ip: "10.108.0.11"', text)
        self.assertIn(
            "name: jengasec-ent-a-ai01, enterprise: enta, code: AI01, kind: ai",
            text,
        )
        self.assertNotIn("APPRED01", text)

    def test_scope_csv_matches_the_infra_contract(self):
        lines = range_service.scope_csv(self.comp).splitlines()
        infra_header = (
            "red_team,red_cell,enterprise,target_cell,target_namespace,"
            "target_service_ip,wg_range,round_active,notes"
        )
        self.assertEqual(lines[0], infra_header)
        self.assertIn(
            "JS26-R-001,APPRED01,enta,APP03,jengasec-ent-a-app03,"
            "10.108.0.11,10.60.1.0/24,true,",
            lines,
        )
        self.assertIn(
            "JS26-R-001,APPRED01,entb,APP03,jengasec-ent-b-app03,"
            "10.108.0.12,10.60.1.0/24,false,",
            lines,
        )

    def test_duplicate_reserved_service_ip_is_rejected(self):
        self.blue_b.service_ip = self.blue_a.service_ip
        with self.assertRaises(ValidationError):
            self.blue_b.full_clean()

    def test_allocation_csv_carries_no_personal_data(self):
        text = range_service.allocation_csv(self.comp)
        self.assertNotIn("@", text)
        self.assertIn(
            "enta,APP03,jengasec-ent-a-app03,10.108.0.11,"
            "app,blue,application,JS26-B-001,Nyati",
            text,
        )

    def test_export_command_writes_three_files_and_audits(self):
        out = pathlib.Path(tempfile.mkdtemp(prefix="range-export-"))
        try:
            call_command("export_range", competition=self.comp.pk, out=str(out), fill_namespaces=True, stdout=io.StringIO())
            for name in ("cells.yaml", "scope-assignments.csv", "allocation.csv", "net-targets.csv",
                         "net-assignments.csv", "net-infrastructure-targets.csv", "net-access-policies.csv"):
                self.assertTrue((out / name).exists(), name)
        finally:
            shutil.rmtree(out, ignore_errors=True)
        self.blue_a.refresh_from_db()
        self.assertEqual(self.blue_a.namespace, "jengasec-ent-a-app03")
        self.assertTrue(AuditLog.objects.filter(action=AuditLog.Action.RANGE_EXPORTED).exists())



class NetBootstrapExportTests(RangeTestBase):
    """The CSVs NET's WireGuard bootstrap loads (scripts/bootstrap_event.py)."""

    def rows(self, name):
        import csv
        return list(csv.DictReader(io.StringIO(range_service.net_bootstrap_files(self.comp)[name])))

    def test_team_codes_are_nets_and_stable(self):
        codes = range_service.kit_team_codes(self.comp)
        self.assertEqual(codes[self.blue_a.pk], "BLUE-APP-01")    # ENT-A before ENT-B
        self.assertEqual(codes[self.blue_b.pk], "BLUE-APP-02")
        self.assertEqual(codes[self.ai_a.pk], "BLUE-AI-01")
        self.assertEqual(codes[self.red.pk], "RED-APP-01")
        self.assertEqual(range_service.kit_team_codes(self.comp), codes)

    def test_targets_are_the_apps_and_the_ai_api(self):
        rows = {r["target_code"]: r for r in self.rows("net-targets.csv")}
        self.assertEqual(set(rows), {"ENT-A/APP03", "ENT-B/APP03", "AI-API/BLUE-AI-01"})
        self.assertEqual(rows["ENT-A/APP03"]["ip"], "10.108.0.11")
        self.assertEqual(rows["ENT-A/APP03"]["owner_team"], "BLUE-APP-01")
        self.assertEqual(rows["ENT-A/APP03"]["namespace"], "jengasec-ent-a-app03")
        self.assertEqual((rows["AI-API/BLUE-AI-01"]["ip"], rows["AI-API/BLUE-AI-01"]["ports"]), ("10.109.0.10", "8443"))

    def test_only_active_assignments_open(self):
        rows = self.rows("net-assignments.csv")
        self.assertEqual([(r["red_team_code"], r["target_code"]) for r in rows], [("RED-APP-01", "ENT-A/APP03")])

    def test_ai_red_pairs_with_two_ai_blue_at_most(self):
        for n in range(2, 7):
            Team.objects.create(competition=self.comp, team_name=f"AIB{n}", team_identifier=f"JS26-B-1{n:02d}",
                                team_type="blue", track="ai", enterprise="b", cell_id=f"AI{n:02d}",
                                status=Team.Status.APPROVED)
        for n in range(1, 4):
            Team.objects.create(competition=self.comp, team_name=f"AIR{n}", team_identifier=f"JS26-R-2{n:02d}",
                                team_type="red", track="ai", status=Team.Status.APPROVED)
        ai = [r for r in self.rows("net-assignments.csv") if r["red_team_code"].startswith("RED-AI-")]
        per_red, per_blue = {}, {}
        for r in ai:
            per_red[r["red_team_code"]] = per_red.get(r["red_team_code"], 0) + 1
            per_blue[r["target_code"]] = per_blue.get(r["target_code"], 0) + 1
        self.assertEqual(per_red, {"RED-AI-01": 2, "RED-AI-02": 2, "RED-AI-03": 2})
        self.assertEqual(len(per_blue), 6)
        self.assertTrue(all(n == 1 for n in per_blue.values()))

    def test_no_names_or_identifiers_leave(self):
        files = "".join(range_service.net_bootstrap_files(self.comp).values())
        for leak in ("Nyati", "Kifaru", "JS26-", "otieno", "amina"):
            self.assertNotIn(leak, files)

    def test_a_blue_app_without_ip_stops_the_export(self):
        Team.objects.filter(pk=self.blue_b.pk).update(service_ip=None)
        with self.assertRaises(ValueError):
            range_service.net_bootstrap_files(self.comp)


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


KEY = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="    # a well-formed WireGuard public key
PROFILE = {
    "participant_id": 41, "address": "10.60.1.10/32", "endpoint": "vpn.jengasec.test:51820",
    "server_public_key": "c2VydmVyLXB1YmxpYy1rZXktMzItYnl0ZXMtbG9uZyE=",
    "allowed_ips": ["10.108.0.11/32"],
    "targets": [{"code": "ENT-A/APP03", "ip": "10.108.0.11", "protocol": "tcp", "ports": [80, 443], "namespace": ""}],
}


class FakePortal:
    """Stands in for NET's control plane; records every call."""

    def __init__(self, register=None, profile=None):
        self.calls = []
        self.register_reply = register or {"participant_id": 41, "status": "pending", "eligible": False, "job_id": None}
        self.profile_reply = profile if profile is not None else PROFILE

    def __call__(self, path, method="GET", payload=None):
        from services.vpn_portal import PortalError

        self.calls.append((method, path, payload))
        if isinstance(self.register_reply, PortalError) and path == "/v1/participants":
            raise self.register_reply
        if path == "/v1/participants":
            return self.register_reply
        if path.endswith("/approve"):
            return {"participant_id": 41, "eligible": True, "job_id": 7, "job_status": "queued"}
        if path.endswith("/revoke"):
            return {"participant_id": 41, "status": "revoked", "job_id": 8}
        if path.endswith("/vpn-profile"):
            if isinstance(self.profile_reply, PortalError):
                raise self.profile_reply
            return self.profile_reply
        raise AssertionError(path)


@override_settings(PORTAL_API_URL="http://127.0.0.1:8080", PORTAL_ADMIN_TOKEN="test-token")
class VpnPortalTests(RangeTestBase):
    def portal(self, **kw):
        from unittest import mock

        fake = FakePortal(**kw)
        patcher = mock.patch("services.vpn_portal._request", side_effect=fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake

    def register(self, key=KEY):
        self.client.login(username="otieno", password=PASSWORD)
        return self.client.post(reverse("cyberrange:wireguard_register"), {"public_key": key}, follow=True)

    def test_a_red_member_is_offered_registration(self):
        self.portal()
        self.client.login(username="otieno", password=PASSWORD)
        self.assertContains(self.client.get(reverse("cyberrange:wireguard")), "Register my laptop")

    def test_registration_sends_the_team_code_and_public_key_then_approves(self):
        from .models import VpnRegistration

        fake = self.portal()
        self.register()
        method, path, body = fake.calls[0]
        self.assertEqual((method, path), ("POST", "/v1/participants"))
        self.assertEqual(body["team_code"], "RED-APP-01")
        self.assertEqual(body["role"], "red_participant")
        self.assertEqual(body["public_key"], KEY)
        self.assertIsNone(body["email"])                       # no personal data beyond the username
        self.assertEqual(body["external_identity"], f"jengasec-web-{self.red_user.pk}-1")
        self.assertEqual(fake.calls[1][:2], ("POST", "/v1/admin/participants/41/approve"))
        reg = VpnRegistration.objects.get(user=self.red_user)
        self.assertEqual((reg.status, reg.participant_id), (VpnRegistration.Status.APPROVED, 41))
        self.assertTrue(AuditLog.objects.filter(action=AuditLog.Action.VPN_REGISTERED).exists())

    def test_auto_approved_registrations_are_not_approved_twice(self):
        fake = self.portal(register={"participant_id": 41, "status": "active", "eligible": True, "job_id": 3})
        self.register()
        self.assertFalse(any(path.endswith("/approve") for _, path, _ in fake.calls))

    def test_a_private_key_or_garbage_is_refused_before_any_call(self):
        fake = self.portal()
        response = self.register(key="not-a-key")
        self.assertContains(response, "not a WireGuard public key")
        self.assertEqual([c for c in fake.calls if c[1] == "/v1/participants"], [])

    def test_the_tunnel_settings_are_shown_without_any_private_key(self):
        self.portal()
        response = self.register()
        self.assertContains(response, "Address = 10.60.1.10/32")
        self.assertContains(response, "AllowedIPs = 10.108.0.11/32")
        self.assertContains(response, "Endpoint = vpn.jengasec.test:51820")
        self.assertNotContains(response, "PrivateKey = " + KEY)

    def test_waiting_for_the_worker_says_so(self):
        from services.vpn_portal import PortalError

        self.portal(profile=PortalError(409, "VPN provisioning is not complete"))
        self.assertContains(self.register(), "setting you up")

    def test_no_target_yet_says_so(self):
        from services.vpn_portal import PortalError

        self.portal(profile=PortalError(403, "No authorized target services are assigned"))
        self.assertContains(self.register(), "no target assigned yet")

    def test_a_refusal_is_recorded_for_organisers(self):
        from services.vpn_portal import PortalError
        from .models import VpnRegistration

        self.portal(register=PortalError(503, "Event topology has not been bootstrapped"))
        self.assertContains(self.register(), "did not accept the registration")
        reg = VpnRegistration.objects.get(user=self.red_user)
        self.assertEqual(reg.status, VpnRegistration.Status.FAILED)
        self.assertIn("bootstrapped", reg.last_error)

    def test_someone_without_an_approved_team_cannot_register(self):
        fake = self.portal()
        nobody = User.objects.create_user("wanjiru", "wanjiru@strathmore.edu", PASSWORD)
        self.client.force_login(nobody)
        self.assertContains(self.client.get(reverse("cyberrange:wireguard")), "VPN access opens once")
        response = self.client.post(reverse("cyberrange:wireguard_register"), {"public_key": KEY})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(fake.calls, [])

    def test_revoke_then_register_again_uses_a_new_identity(self):
        from django.contrib.admin.sites import site
        from .models import VpnRegistration

        fake = self.portal()
        self.register()
        staff = User.objects.create_user("ops", "ops@strathmore.edu", PASSWORD, is_staff=True, is_superuser=True)
        self.client.force_login(staff)
        self.client.post(reverse("admin:cyberrange_vpnregistration_changelist"),
                         {"action": "revoke_vpn", "_selected_action": [VpnRegistration.objects.get().pk]})
        self.assertIn(("POST", "/v1/admin/participants/41/revoke", {}), fake.calls)
        self.assertEqual(VpnRegistration.objects.get().status, VpnRegistration.Status.REVOKED)
        self.register()
        last_register = [c for c in fake.calls if c[1] == "/v1/participants"][-1]
        self.assertEqual(last_register[2]["external_identity"], f"jengasec-web-{self.red_user.pk}-2")

    @override_settings(PORTAL_API_URL="", PORTAL_ADMIN_TOKEN="")
    def test_without_the_api_configured_nothing_changes(self):
        self.client.login(username="otieno", password=PASSWORD)
        self.assertContains(self.client.get(reverse("cyberrange:wireguard")), "Configurations are issued")
        self.assertEqual(self.client.post(reverse("cyberrange:wireguard_register"), {"public_key": KEY}).status_code, 404)


@override_settings(VENUE_PARTICIPANT_CIDRS=["10.52.0.0/18"])
class WifiAttributionTests(RangeTestBase):
    """Venue Wi-Fi addresses are attributed to the signed-in person's team."""

    def visit(self, user, ip, **headers):
        from .attribution import SESSION_KEY

        self.client.force_login(user)
        session = self.client.session
        session.pop(SESSION_KEY, None)     # skip the once-a-minute throttle between steps
        session.save()
        return self.client.get(reverse("cyberrange:wireguard"), HTTP_X_REAL_IP=ip, **headers)

    def claims(self):
        from .models import WifiClaim
        return list(WifiClaim.objects.order_by("pk").values_list("ip_address", "team_identifier", "team_label", "ended_at"))

    def test_a_red_member_on_the_wifi_is_attributed_to_their_team(self):
        self.visit(self.red_user, "10.52.3.7")
        (ip, team, label, ended), = self.claims()
        self.assertEqual((ip, team, label, ended), ("10.52.3.7", "JS26-R-001", "red-app", None))

    def test_blue_claims_carry_the_cell_namespace(self):
        from .models import WifiClaim
        self.blue_a.namespace = "jengasec-ent-a-app03"
        self.blue_a.save(update_fields=["namespace"])
        self.visit(self.blue_user, "10.52.0.40")
        claim = WifiClaim.objects.get()
        self.assertEqual((claim.team_label, claim.namespace), ("blue-app", "jengasec-ent-a-app03"))

    def test_addresses_outside_the_pool_are_ignored(self):
        self.visit(self.red_user, "192.168.40.253")
        self.visit(self.red_user, "10.53.0.1")
        self.assertEqual(self.claims(), [])

    def test_a_forged_forwarded_for_header_cannot_claim_someone_elses_address(self):
        self.visit(self.red_user, "10.52.9.9", HTTP_X_FORWARDED_FOR="10.52.0.40")
        self.assertEqual([c[0] for c in self.claims()], ["10.52.9.9"])

    def test_one_address_one_person_the_earlier_claim_ends(self):
        self.visit(self.red_user, "10.52.1.1")
        self.visit(self.blue_user, "10.52.1.1")
        rows = self.claims()
        self.assertEqual([r[1] for r in rows], ["JS26-R-001", "JS26-B-001"])
        self.assertIsNotNone(rows[0][3])     # Red's claim ended when Blue took the address
        self.assertIsNone(rows[1][3])

    def test_the_same_person_refreshes_one_claim(self):
        self.visit(self.red_user, "10.52.1.1")
        self.visit(self.red_user, "10.52.1.1")
        self.assertEqual(len(self.claims()), 1)

    def test_people_without_an_approved_team_are_not_attributed(self):
        nobody = User.objects.create_user("wanjiku", "wanjiku@strathmore.edu", PASSWORD)
        self.visit(nobody, "10.52.2.2")
        self.assertEqual(self.claims(), [])

    def test_judges_are_labelled_as_judges(self):
        self.visit(self.judge, "10.52.4.4")
        self.assertEqual([(c[1], c[2]) for c in self.claims()], [("", "judge")])


class GenerateWireGuardProfilesTests(RangeTestBase):
    """generate_wireguard_profiles: addresses from NET's plan, stable keys, access rules."""

    def setUp(self):
        super().setUp()
        self.out = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.out, ignore_errors=True)
        self.staff = User.objects.create_user("stella", "stella@strathmore.edu", PASSWORD, is_staff=True)

    def generate(self):
        call_command("generate_wireguard_profiles", "--endpoint", "203.0.113.10",
                     "--out", str(self.out), "--allow-local",
                     stdout=io.StringIO(), stderr=io.StringIO())

    def profile(self, username):
        return (self.out / "profiles" / f"{username}.conf").read_text()

    def test_addresses_follow_the_plan(self):
        self.generate()
        self.assertIn("Address = 10.60.1.10/32", self.profile("otieno"))   # App Red team 1
        self.assertIn("Address = 10.61.1.10/32", self.profile("amina"))    # App Blue team 1, captain
        self.assertIn("Address = 10.63.0.10/32", self.profile("stella"))   # staff
        self.assertIn("Endpoint = 203.0.113.10:51820", self.profile("otieno"))
        self.assertIn("AllowedIPs = 10.108.0.0/24, 10.250.0.1/32", self.profile("otieno"))

    def test_every_profile_is_a_server_peer(self):
        self.generate()
        server = (self.out / "server" / "wg0.conf").read_text()
        for address in ("10.60.1.10", "10.61.1.10", "10.63.0.10"):
            self.assertIn(f"AllowedIPs = {address}/32", server)

    def test_blue_teams_reach_only_their_own_app(self):
        self.generate()
        acl = (self.out / "server" / "acl.nft").read_text()
        self.assertIn("ip saddr 10.61.1.0/24 ip daddr 10.108.0.11 tcp dport 80 accept", acl)
        self.assertIn("ip saddr 10.61.2.0/24 ip daddr 10.108.0.12 tcp dport 80 accept", acl)
        self.assertNotIn("flush ruleset", acl.replace("Never `flush ruleset`", ""))

    def test_rerun_keeps_keys(self):
        self.generate()
        before = self.profile("otieno")
        self.generate()
        self.assertEqual(before, self.profile("otieno"))

    def test_people_without_accounts_get_no_profile(self):
        TeamMember.objects.create(team=self.red, user=None, student_name="Ghost",
                                  email="ghost@strathmore.edu")
        self.generate()
        names = sorted(p.stem for p in (self.out / "profiles").glob("*.conf"))
        self.assertEqual(names, ["amina", "otieno", "stella"])

    def test_profiles_load_into_the_site(self):
        self.generate()
        call_command("load_wireguard_profiles", "--dir", str(self.out / "profiles"), stdout=io.StringIO())
        self.assertEqual(WireGuardProfile.objects.get(user=self.red_user).address, "10.60.1.10/32")

    def test_unapproved_teams_get_access_after_approved_ones(self):
        pending = Team.objects.create(
            competition=self.comp, team_name="Pending", team_identifier="JS26-R-009", team_type="red",
            track="application", enterprise="a", cell_id="APPRED09", status=Team.Status.SUBMITTED,
        )
        newbie = User.objects.create_user("newbie", "newbie@strathmore.edu", PASSWORD)
        TeamMember.objects.create(team=pending, user=newbie, student_name="Newbie", email="newbie@strathmore.edu")
        self.generate()
        self.assertIn("Address = 10.60.1.10/32", self.profile("otieno"))   # approved first: team 1
        self.assertIn("Address = 10.60.2.10/32", self.profile("newbie"))   # pending after it
        # Approving it later must not move anyone's address.
        pending.status = Team.Status.APPROVED
        pending.save(update_fields=["status"])
        self.generate()
        self.assertIn("Address = 10.60.2.10/32", self.profile("newbie"))
        self.assertIn("Address = 10.60.1.10/32", self.profile("otieno"))

    def test_withdrawn_teams_get_nothing(self):
        self.red.status = Team.Status.WITHDRAWN
        self.red.save(update_fields=["status"])
        self.generate()
        self.assertFalse((self.out / "profiles" / "otieno.conf").exists())

    def test_challenges_sit_behind_the_gate(self):
        self.generate()
        acl = (self.out / "server" / "acl.nft").read_text()
        self.assertIn("jump challenges", acl)
        self.assertIn("tcp dport 8088 accept", acl)          # check page, always open
        challenges = acl[acl.index("chain challenges"):]
        self.assertIn("tcp dport { 30000-30099 } accept", challenges)
