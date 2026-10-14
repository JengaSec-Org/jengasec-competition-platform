"""`POST /api/feed` — what observability pushes to the website.

Architecture section 14.1: obs pushes live events per application and
published scores to the website over HTTPS on the functions VLAN. This is
that endpoint, and `docs/CONTRACT-feed.md` is what the other side builds
against.

Raw telemetry never arrives here. Vector posts that to the AI API's own
`/api/v1/ingest` on the obs VM, and section 8.2 is explicit that the
website never reads it. What reaches the website is already a judgement:
a mark, with the event ids behind it as evidence.

Guarded the same way as every other machine endpoint on this site: 404
when the token is unset, `secrets.compare_digest`, POST only.
"""
import json
import logging
import secrets

from django.conf import settings
from django.http import Http404, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from accounts.models import Team
from audit.models import AuditLog
from competitions.constants import ENTERPRISE_CODES
from competitions.models import Competition
from services import scoring_service
from services.audit_service import record as audit

logger = logging.getLogger(__name__)

TOKEN_HEADER = "X-Feed-Token"

# Their cell codes are "enta-app03"; ours are enterprise "a" + cell "APP03".
_ENTERPRISE_BY_CODE = {code.lower(): key for key, code in ENTERPRISE_CODES.items()}


def _configured_token():
    return (getattr(settings, "FEED_TOKEN", "") or "").strip()


def resolve_team(competition, row):
    """Find the team a pushed mark belongs to, or None.

    Accepts either the platform's own team identifier or the range's cell
    naming. An unmatched row is rejected in the response rather than
    dropped: a mark that silently lands nowhere is worse than one that
    fails loudly, because nobody goes looking for it.
    """
    identifier = (row.get("team") or "").strip()
    if identifier:
        team = Team.objects.filter(
            competition=competition, team_identifier__iexact=identifier
        ).first()
        if team:
            return team

    cell = (row.get("cell") or "").strip()
    enterprise = (row.get("enterprise") or "").strip().lower()
    if cell and "-" in cell and not enterprise:
        # "enta-app03" carries both halves.
        enterprise, _, cell = cell.partition("-")
    enterprise = _ENTERPRISE_BY_CODE.get(enterprise, enterprise)
    if cell and enterprise:
        return Team.objects.filter(
            competition=competition, enterprise=enterprise, cell_id__iexact=cell
        ).first()
    return None


@csrf_exempt
@never_cache
@require_POST
def feed(request):
    """Record pushed marks. Body: {"competition": <id>, "scores": [...]}.

    Each score: {team|cell|enterprise, stage, criterion, marks, note,
    day, key}. `key` makes a push idempotent — re-sending the same key
    replaces that mark rather than adding a second one, which matters
    because an uptime figure is republished every few minutes.
    """
    expected = _configured_token()
    if not expected:
        raise Http404("Not found")
    offered = request.headers.get(TOKEN_HEADER, "")
    if not offered or not secrets.compare_digest(offered, expected):
        logger.warning("Feed endpoint rejected a request with a bad or missing token.")
        return JsonResponse({"error": "forbidden"}, status=403)

    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "body is not JSON"}, status=400)

    competition = (
        Competition.objects.filter(pk=body.get("competition")).first()
        if body.get("competition")
        else Competition.objects.filter(
            status__in=(Competition.Status.OPEN, Competition.Status.IN_PROGRESS)
        ).order_by("-start_date").first()
    )
    if competition is None:
        return JsonResponse({"error": "no such competition"}, status=404)

    accepted, rejected = 0, []
    touched = set()
    for index, row in enumerate(body.get("scores") or []):
        team = resolve_team(competition, row)
        if team is None:
            rejected.append({"index": index, "error": "no team matched",
                             "team": row.get("team"), "cell": row.get("cell")})
            continue
        stage = (row.get("stage") or "main").strip()
        try:
            marks = row["marks"]
        except KeyError:
            rejected.append({"index": index, "error": "marks missing"})
            continue
        try:
            scoring_service.award(
                team, stage, marks,
                source="telemetry",
                criterion=(row.get("criterion") or "").strip(),
                note=(row.get("note") or "").strip(),
                day=row.get("day"),
                dedupe_key=(row.get("key") or "").strip(),
            )
        except Exception as exc:  # noqa: BLE001 — one bad row must not lose the rest
            logger.exception("Feed row %s could not be recorded", index)
            rejected.append({"index": index, "error": str(exc)[:200]})
            continue
        accepted += 1
        touched.add(team)

    for team in touched:
        scoring_service.recompute(team)

    if accepted:
        audit(
            AuditLog.Action.SCORE_OVERRIDDEN,
            request=request,
            actor=None,
            description=f"Observability pushed {accepted} mark(s) for {len(touched)} team(s)",
        )

    # 202: some rows may have been rejected while others landed, and the
    # caller needs to see which rather than a flat success or failure.
    return JsonResponse(
        {"accepted": accepted, "rejected": rejected}, status=202 if rejected else 200
    )
