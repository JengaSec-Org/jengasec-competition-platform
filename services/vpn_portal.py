"""The website's side of NET's WireGuard pipeline.

NET's control plane (`jengasec-control-plane`, 127.0.0.1:8080 on the platform
VM, `services/control_plane.py` in their kit) turns a registration into a
WireGuard peer and a per-team firewall rule:

    website                          control plane            wireguard-vm
    -------                          -------------            ------------
    member pastes their public key
    register()  ──POST /v1/participants──►  pending
                ──POST .../approve───────►  job queued  ──►  worker adds the peer
    profile()   ──GET  .../vpn-profile───►  address, endpoint, allowed IPs

The website is the "trusted portal" the kit asks for: it has already signed
the person in and checked they are on an approved team, so it approves them
itself. The private key is created and kept by the WireGuard app on the
participant's own device; only the public key ever reaches this server.

Server-side only. The admin token can revoke anyone's VPN access, so it never
reaches a template or a browser.
"""
import base64
import json
import urllib.error
import urllib.request

from django.conf import settings

from accounts.models import Team
from accounts.roles import JUDGE, user_in_role
from services import range_service
from services.submission_service import teams_for


class PortalError(Exception):
    """The control plane said no, or could not be reached."""

    def __init__(self, status, detail):
        super().__init__(f"VPN API returned {status}: {detail}")
        self.status = status
        self.detail = detail


def configured():
    return bool(settings.PORTAL_API_URL and settings.PORTAL_ADMIN_TOKEN)


def _request(path, method="GET", payload=None):
    if not configured():
        raise PortalError(503, "The VPN API is not configured on this site")
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        settings.PORTAL_API_URL.rstrip("/") + path, data=data, method=method,
        headers={"Authorization": f"Bearer {settings.PORTAL_ADMIN_TOKEN}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode(errors="replace")).get("detail", "")
        except (ValueError, AttributeError):
            detail = ""
        raise PortalError(exc.code, str(detail)[:300]) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PortalError(503, "The VPN API is not answering") from exc


def valid_public_key(value):
    """A WireGuard public key: 32 bytes, standard base64, 44 characters."""
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        return False
    return len(raw) == 32 and base64.b64encode(raw).decode() == value


def vpn_identity(user):
    """(role, team, team_code) for the VPN, or None if this person gets no profile.

    Competitors: an approved Blue or Red team with a NET team code (a track,
    and for Blue a cell). Judges and staff: no team, access by NET's policies.
    """
    team = (teams_for(user).filter(status=Team.Status.APPROVED)
            .exclude(team_type="").order_by("-competition__start_date", "pk").first())
    if team is not None:
        code = range_service.kit_team_codes(team.competition).get(team.pk)
        if code:
            role = "blue_participant" if team.team_type == Team.TeamType.BLUE else "red_participant"
            return role, team, code
    if user_in_role(user, JUDGE):
        return "judge", None, None
    if user.is_staff or user.is_superuser:
        return "infrastructure_staff", None, None
    return None


def register(registration):
    """Register and approve one person. Updates and saves `registration`."""
    reply = _request("/v1/participants", "POST", {
        "external_identity": registration.external_identity,
        # Only what NET needs to recognise the person; no email address.
        "display_name": registration.user.get_username(),
        "email": None,
        "role": registration.role,
        "team_code": registration.team_code or None,
        "public_key": registration.public_key,
    })
    registration.participant_id = reply["participant_id"]
    if not reply.get("eligible"):
        _request(f"/v1/admin/participants/{int(registration.participant_id)}/approve", "POST", {})
    registration.status = registration.Status.APPROVED
    registration.last_error = ""
    registration.save()
    return registration


def profile(registration):
    """The tunnel settings, once the worker has made the peer."""
    return _request(f"/v1/participants/{int(registration.participant_id)}/vpn-profile")


def revoke(registration):
    if registration.participant_id:
        _request(f"/v1/admin/participants/{int(registration.participant_id)}/revoke", "POST", {})
    registration.status = registration.Status.REVOKED
    registration.save(update_fields=["status", "updated_at"])
    return registration


def tunnel_settings(data):
    """The lines the participant adds under the PrivateKey their app created.

    Deliberately not a whole .conf file: that would need the private key,
    which this server never sees.
    """
    return "\n".join([
        f"Address = {data['address']}",
        "",
        "[Peer]",
        f"PublicKey = {data['server_public_key']}",
        f"Endpoint = {data['endpoint']}",
        f"AllowedIPs = {', '.join(data['allowed_ips'])}",
        "PersistentKeepalive = 25",
    ])
