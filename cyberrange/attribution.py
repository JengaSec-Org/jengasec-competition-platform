"""Who is behind a venue Wi-Fi address.

At the venue participants join the Wi-Fi and get an address from the SSID
pool (VENUE_PARTICIPANT_CIDRS, 10.52.0.0/18). There is no VPN and no NAT, so
the address a laptop uses to open this site is the address it attacks or
defends from. When a signed-in member of an approved team loads any page
from such an address, the address is recorded against that person and team
(`WifiClaim`). Observability reads the claims through the database view
`obs_ip_allocations_v1` (contracts/observability-data-interface.md in the
infrastructure repository) and attributes every event from that address.

One address belongs to one person at a time: when someone else signs in from
it, the earlier claim ends. The address is taken from X-Real-IP, which nginx
overwrites with the connecting address. X-Forwarded-For is never used here:
its left-most entry is whatever the browser sent, so a Red team could claim
another team's address and frame them.
"""
import ipaddress

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from accounts.models import Team
from accounts.roles import JUDGE, user_in_role
from competitions.constants import Track
from services.submission_service import teams_for

# A busy page loads many URLs; touch the table at most once a minute per session.
REFRESH_SECONDS = 60
SESSION_KEY = "wifi_claim"


def _pools():
    nets = []
    for cidr in getattr(settings, "VENUE_PARTICIPANT_CIDRS", []):
        try:
            nets.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            continue
    return nets


def venue_address(request):
    """The connecting Wi-Fi address, or None if it is not in a participant pool."""
    raw = (request.META.get("HTTP_X_REAL_IP") or "").strip()
    if not raw and not getattr(settings, "TRUST_FORWARDED_FOR", not settings.DEBUG):
        raw = request.META.get("REMOTE_ADDR", "")   # runserver / tests, no proxy in front
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return None
    return str(ip) if any(ip in net for net in _pools()) else None


def team_label(team):
    side = "blue" if team.team_type == Team.TeamType.BLUE else "red"
    return f"{side}-{'app' if team.track == Track.APPLICATION else 'ai'}"


def identity_for(user):
    """(team, label, team_id, namespace) for the claim, or None if the person is not competing."""
    team = (teams_for(user).filter(status=Team.Status.APPROVED).exclude(team_type="")
            .exclude(track="").order_by("-competition__start_date", "pk").first())
    if team is not None:
        namespace = team.namespace if team.team_type == Team.TeamType.BLUE else ""
        return team, team_label(team), team.team_identifier or f"team-{team.pk}", namespace
    if user_in_role(user, JUDGE):
        return None, "judge", "", ""
    if user.is_staff or user.is_superuser:
        return None, "staff", "", ""
    return None


def record(request):
    """Claim the request's Wi-Fi address for the signed-in person. Returns the claim or None."""
    from .models import WifiClaim

    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return None
    ip = venue_address(request)
    if ip is None:
        return None
    now = timezone.now()
    seen = request.session.get(SESSION_KEY)
    if seen and seen.get("ip") == ip and now.timestamp() - seen.get("at", 0) < REFRESH_SECONDS:
        return None
    identity = identity_for(user)
    if identity is None:
        return None
    team, label, team_id, namespace = identity
    with transaction.atomic():
        current = (WifiClaim.objects.select_for_update()
                   .filter(ip_address=ip, ended_at__isnull=True).order_by("-first_seen"))
        mine = None
        for claim in current:
            if claim.user_id == user.pk and claim.team_identifier == team_id and mine is None:
                mine = claim
            else:
                claim.ended_at = now          # someone else has this address now
                claim.save(update_fields=["ended_at"])
        if mine is None:
            mine = WifiClaim.objects.create(
                ip_address=ip, user=user, team=team, team_identifier=team_id, team_label=label,
                namespace=namespace, participant_id=f"JS26-P-{user.pk:04d}", first_seen=now, last_seen=now,
            )
        else:
            mine.last_seen = now
            mine.save(update_fields=["last_seen"])
    request.session[SESSION_KEY] = {"ip": ip, "at": now.timestamp()}
    return mine


class WifiAttributionMiddleware:
    """Runs `record` on every signed-in request. Never breaks a page."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            record(request)
        except Exception:  # noqa: BLE001 -- attribution must never take the site down
            import logging

            logging.getLogger(__name__).exception("Wi-Fi attribution failed")
        return self.get_response(request)
