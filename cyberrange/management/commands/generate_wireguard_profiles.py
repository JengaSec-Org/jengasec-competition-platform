"""Generate every participant's WireGuard profile for the cloud range.

    python manage.py generate_wireguard_profiles --endpoint 3.120.1.2:51820 --out wg-out/
    python manage.py load_wireguard_profiles --dir wg-out/profiles/

Reads approved teams and their members from this site's database and writes:

    wg-out/profiles/<username>.conf   one per person, for load_wireguard_profiles
    wg-out/server/wg0.conf            the WireGuard server, every peer included
    wg-out/server/acl.nft             who may reach which application
    wg-out/mapping.csv                username, team, range, address (no secrets)
    wg-out/state.json                 every private key — keeps reruns stable

Addresses follow NET's plan (JengaSec_VLAN_Ranges.pdf): App Red 10.60.n.x,
App Blue 10.61.n.x, AI Blue 10.62.n.x, staff 10.63.0.x, AI Red 10.64.n.x;
team n is the n-th approved team of its range by id, members start at .10.
The applications are 10.108.0.0/24 (each blue team's own `service_ip`).

Rerunning keeps everyone's existing key and address (from state.json), so
profiles already downloaded keep working; only new people are added.

wg-out/ holds private keys for everyone. Keep it off git and off shared
drives, and delete it once the server is built and the profiles are loaded.
"""
import base64
import csv
import ipaddress
import json
import pathlib

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from accounts.models import Team, TeamMember

APPS = "10.108.0.0/24"
SERVER_ADDRESS = "10.250.0.1"          # NET's WireGuard tunnel network, 10.250.0.0/22
SERVER_NET = "10.250.0.0/22"
STAFF_NET = "10.63.0.0/24"
CHECK_PORT = 8088                      # the "you are connected" page on the server

# (team_type, track) -> (range name, second octet)
RANGES = {
    ("red", "application"): ("app-red", 60),
    ("blue", "application"): ("app-blue", 61),
    ("blue", "ai"): ("ai-blue", 62),
    ("red", "ai"): ("ai-red", 64),
}


def new_key():
    private = X25519PrivateKey.generate()
    raw = private.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    return base64.b64encode(raw).decode()


def public_of(private_b64):
    private = X25519PrivateKey.from_private_bytes(base64.b64decode(private_b64))
    raw = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


class Command(BaseCommand):
    help = "Generate WireGuard profiles, the server config and the access rules for the cloud range."

    def add_arguments(self, parser):
        parser.add_argument("--endpoint", required=True,
                            help="Public address of the WireGuard server, e.g. 3.120.1.2:51820 (use an Elastic IP).")
        parser.add_argument("--out", required=True, help="Output directory (holds private keys).")
        parser.add_argument("--ranges", default="app-red,app-blue,ai-blue,ai-red,staff",
                            help="Which ranges get profiles (comma-separated).")
        parser.add_argument("--port", type=int, default=51820)
        parser.add_argument("--challenge-ports", default="30000-30099",
                            help="Ports on the server's VPN address (10.250.0.1) where the challenges "
                                 "are published (Kubernetes NodePorts). Every participant range may "
                                 "reach them. Empty to disable.")
        parser.add_argument("--approved-only", action="store_true",
                            help="Only approved teams (default: every team not rejected or withdrawn).")
        parser.add_argument("--allow-local", action="store_true",
                            help="Allow the local SQLite database (tests only).")

    def handle(self, *args, **opts):
        # Profiles generated from the local development database are profiles
        # for the wrong people. Refuse unless asked explicitly (the tests do).
        if connection.vendor == "sqlite" and not opts["allow_local"]:
            raise CommandError(
                "This is the LOCAL development database (SQLite), not the live one. "
                "Put Render's JENGASEC_DB_URL and S3_* values in this folder's .env "
                "and run it from a network where port 5432 is open (home, hotspot)."
            )
        out = pathlib.Path(opts["out"])
        wanted = {r.strip() for r in opts["ranges"].split(",") if r.strip()}
        endpoint = opts["endpoint"]
        if ":" not in endpoint:
            endpoint = f"{endpoint}:{opts['port']}"

        state_path = out / "state.json"
        state = json.loads(state_path.read_text()) if state_path.exists() else {"server": None, "people": {}}
        if not state["server"]:
            state["server"] = new_key()
        server_public = public_of(state["server"])

        people = []          # dicts: username, team, range, address, key
        blue_targets = []    # (team network, service_ip) for the ACL
        skipped = []

        # ── Teams ────────────────────────────────────────────────────────────
        # Every team that is still in the competition gets access — approved
        # or not; only Rejected and Withdrawn are left out (--approved-only
        # narrows it back). Approved teams go FIRST: they are numbered first,
        # and someone on two teams gets the approved team's address.
        #
        # A team keeps its number for good (state.json "teams"), so a team
        # approved later keeps the address its members already downloaded.
        state.setdefault("teams", {})

        def team_number(range_name, team):
            numbers = state["teams"].setdefault(range_name, {})
            key = str(team.pk)
            if key not in numbers:
                used = set(numbers.values())
                free = [i for i in range(1, 255) if i not in used]
                if not free:
                    raise CommandError(f"more than 254 {range_name} teams")
                numbers[key] = free[0]
            return numbers[key]

        if opts["approved_only"]:
            passes = [Team.objects.filter(status=Team.Status.APPROVED)]
        else:
            passes = [
                Team.objects.filter(status=Team.Status.APPROVED),
                Team.objects.exclude(status__in=[Team.Status.APPROVED, Team.Status.REJECTED,
                                                 Team.Status.WITHDRAWN]),
            ]
        ordered = []
        for teams_in_pass in passes:
            for (team_type, track), (range_name, octet) in RANGES.items():
                for team in teams_in_pass.filter(team_type=team_type, track=track).order_by("id"):
                    ordered.append((range_name, octet, team))

        for range_name, octet, team in ordered:
            n = team_number(range_name, team)
            team_net = f"10.{octet}.{n}.0/24"
            if range_name == "app-blue":
                if team.service_ip:
                    blue_targets.append((team_net, team.service_ip, team.team_name))
                else:
                    skipped.append(f"{team.team_name}: App Blue team with no service_ip — no app access rule")
            if range_name not in wanted:
                continue
            # The captain lives on the team and need not also be a
            # TeamMember row; first, then members, each person once.
            users = [team.captain] if team.captain_id else []
            users += [m.user for m in TeamMember.objects.filter(team=team, user__isnull=False)
                      .select_related("user").order_by("id")]
            unique = list({u.pk: u for u in users}.values())
            for i, user in enumerate(unique):
                if i > 244:
                    raise CommandError(f"{team.team_name} has more than 245 members")
                people.append({"username": user.username, "team": team.team_name,
                               "range": range_name, "address": f"10.{octet}.{n}.{10 + i}",
                               "status": team.status})
            unlinked = TeamMember.objects.filter(team=team, user__isnull=True).count()
            if unlinked:
                skipped.append(f"{team.team_name}: {unlinked} member(s) without an account — no profile")

        # ── Staff and judges ─────────────────────────────────────────────────
        if "staff" in wanted:
            staff = get_user_model().objects.filter(is_staff=True, is_active=True).order_by("id")
            for i, user in enumerate(staff):
                if i > 244:
                    raise CommandError("more than 245 staff accounts")
                people.append({"username": user.username, "team": "staff",
                               "range": "staff", "address": f"10.63.0.{10 + i}",
                               "status": "staff"})

        # One profile per person even if they appear twice (staff on a team).
        seen = {}
        for person in people:
            seen.setdefault(person["username"], person)
        people = list(seen.values())

        # ── Keys, stable across reruns ───────────────────────────────────────
        for person in people:
            known = state["people"].get(person["username"])
            if known and known["address"] == person["address"]:
                person["key"] = known["key"]
            else:
                if known:
                    skipped.append(f"{person['username']}: address changed "
                                   f"{known['address']} -> {person['address']}, new key issued")
                person["key"] = new_key()
            state["people"][person["username"]] = {"key": person["key"], "address": person["address"]}

        # ── Write ────────────────────────────────────────────────────────────
        (out / "profiles").mkdir(parents=True, exist_ok=True)
        (out / "server").mkdir(parents=True, exist_ok=True)

        for person in people:
            (out / "profiles" / f"{person['username']}.conf").write_text(
                "# JengaSec cloud range — personal profile. Do not share it.\n"
                "[Interface]\n"
                f"PrivateKey = {person['key']}\n"
                f"Address = {person['address']}/32\n"
                "\n[Peer]\n"
                f"PublicKey = {server_public}\n"
                f"Endpoint = {endpoint}\n"
                # Only the range goes through the tunnel; the website and the
                # rest of the internet stay on the participant's own connection.
                f"AllowedIPs = {APPS}, {SERVER_ADDRESS}/32\n"
                "PersistentKeepalive = 25\n",
                # newline="\n": on Windows Python would otherwise write \r\n, and
                # Linux's wg-quick and nft reject the stray \r on every line.
                encoding="utf-8", newline="\n",
            )

        lines = [
            "# Generated by manage.py generate_wireguard_profiles — do not edit by hand.",
            "[Interface]",
            f"Address = {SERVER_ADDRESS}/22",
            f"ListenPort = {opts['port']}",
            f"PrivateKey = {state['server']}",
        ]
        for person in people:
            lines += ["", f"# {person['username']} ({person['team']}, {person['range']})",
                      "[Peer]", f"PublicKey = {public_of(person['key'])}",
                      f"AllowedIPs = {person['address']}/32"]
        (out / "server" / "wg0.conf").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

        (out / "server" / "acl.nft").write_text(
            self._acl(blue_targets, opts["challenge_ports"].strip()), encoding="utf-8", newline="\n"
        )

        with open(out / "mapping.csv", "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["username", "team", "range", "address", "team_status"])
            for person in sorted(people, key=lambda p: ipaddress.ip_address(p["address"])):
                writer.writerow([person["username"], person["team"], person["range"], person["address"],
                                 person.get("status", "")])

        state_path.write_text(json.dumps(state, indent=1), encoding="utf-8")

        for note in skipped:
            self.stderr.write(f"note: {note}")
        counts = {}
        for person in people:
            counts[person["range"]] = counts.get(person["range"], 0) + 1
        self.stdout.write(self.style.SUCCESS(
            f"{len(people)} profiles ({', '.join(f'{k} {v}' for k, v in sorted(counts.items()))}), "
            f"{len(blue_targets)} App Blue app rules. Server public key: {server_public}"
        ))
        self.stdout.write(f"Next: load_wireguard_profiles --dir {out / 'profiles'}  ·  copy {out / 'server'} to the cloud VM")

    @staticmethod
    def _acl(blue_targets, challenge_ports=""):
        """nftables rules for the WireGuard server.

        Evaluated BEFORE Kubernetes rewrites the address (priority -150, ahead of
        kube-proxy's DNAT at -100), so the rules see the application address the
        participant asked for, 10.108.0.x, not the pod behind it.
        """
        blue = "\n".join(
            f"        ip saddr {net} ip daddr {ip} tcp dport 80 accept   # {name}"
            for net, ip, name in blue_targets
        ) or "        # (no App Blue team has a service_ip yet)"
        challenges = (
            "        # Challenges published on the server's VPN address (NodePorts): every\n"
            "        # participant range and staff.\n"
            f"        ip saddr {{ 10.60.0.0/14, 10.64.0.0/16 }} ip daddr {SERVER_ADDRESS} "
            f"tcp dport {{ {challenge_ports} }} accept\n"
            "\n"
            "        # AI Blue defends by watching: a READ-ONLY Kubernetes token (logs,\n"
            "        # pods, events of the challenges) — reachable for AI Blue and staff only.\n"
            f"        ip saddr {{ 10.62.0.0/16, {STAFF_NET} }} ip daddr {SERVER_ADDRESS} tcp dport 6443 accept"
        ) if challenge_ports else "        # (no challenge ports)"
        return f"""# Generated by manage.py generate_wireguard_profiles — do not edit by hand.
# Load with:  nft delete table inet jengasec_vpn 2>/dev/null; nft -f acl.nft
# Never `flush ruleset`: k3s keeps its own rules in the same nftables.
table inet jengasec_vpn {{
    chain pre {{
        type filter hook prerouting priority -150; policy accept;
        iifname "wg0" jump from_vpn
    }}

    chain from_vpn {{
        ct state established,related accept
        ip daddr {SERVER_ADDRESS} icmp type echo-request accept

        # Staff and judges: every application.
        ip saddr {STAFF_NET} ip daddr {APPS} accept

        # App Red and AI Red attack every application, on the app port.
        ip saddr {{ 10.60.0.0/16, 10.64.0.0/16 }} ip daddr {APPS} tcp dport 80 accept
        ip saddr {{ 10.60.0.0/16, 10.64.0.0/16 }} ip daddr {APPS} icmp type echo-request accept

        # Each App Blue team reaches only its own application.
{blue}

        # Connection check page (vpn-check on the server) — always open, so
        # teams can confirm their profile works before the range opens.
        ip saddr {{ 10.60.0.0/14, 10.64.0.0/16 }} ip daddr {SERVER_ADDRESS} tcp dport {CHECK_PORT} accept

        # The challenges, in their own chain so the server can close and
        # open them without regenerating anything (range-gate.sh).
        jump challenges

        # Everything else from the VPN — the server itself, other ports — is dropped.
        drop
    }}

    chain challenges {{
{challenges}
    }}

    chain vpn_forward {{
        type filter hook forward priority 0; policy accept;
        # Participants never reach one another.
        iifname "wg0" oifname "wg0" drop
        # Nothing on the server (an application) opens a connection to a participant.
        ip daddr {{ 10.60.0.0/14, 10.64.0.0/16 }} ct state new drop
    }}
}}
"""
