"""Recording the platform audit trail.

One entry point, and it never raises: an audit write failing must not
take down the action it was recording. Failures go to the Django log so
they surface in monitoring rather than silently vanishing.
"""
import logging

from audit.models import AuditLog

logger = logging.getLogger(__name__)

# Which category each action belongs to, so callers pass one argument.
ACTION_CATEGORY = {
    AuditLog.Action.LOGIN_SUCCESS: AuditLog.Category.AUTH,
    AuditLog.Action.LOGIN_FAILED: AuditLog.Category.AUTH,
    AuditLog.Action.LOGOUT: AuditLog.Category.AUTH,
    AuditLog.Action.ROLE_CHANGED: AuditLog.Category.AUTH,
    AuditLog.Action.FILE_UPLOADED: AuditLog.Category.SUBMISSION,
    AuditLog.Action.SUBMISSION_SUBMITTED: AuditLog.Category.SUBMISSION,
    AuditLog.Action.FILE_DOWNLOADED: AuditLog.Category.SUBMISSION,
    AuditLog.Action.PROPOSAL_SELECTION: AuditLog.Category.SUBMISSION,
    AuditLog.Action.MARKING_CHANGED: AuditLog.Category.SUBMISSION,
    AuditLog.Action.COMPETITION_CREATED: AuditLog.Category.COMPETITION,
    AuditLog.Action.COMPETITION_UPDATED: AuditLog.Category.COMPETITION,
    AuditLog.Action.LIFECYCLE_CHANGED: AuditLog.Category.COMPETITION,
    AuditLog.Action.SCORE_OVERRIDDEN: AuditLog.Category.JUDGING,
}


def record(action, request=None, actor=None, target=None, description="", **metadata):
    """Write one audit row. Returns it, or None if recording failed.

    `target` is any model instance; its type, pk and str() are stored so
    the entry still reads sensibly after the object is deleted.
    """
    try:
        if actor is None and request is not None:
            candidate = getattr(request, "user", None)
            if candidate is not None and candidate.is_authenticated:
                actor = candidate

        ip = user_agent = None
        if request is not None:
            from accounts.security import client_ip

            ip = client_ip(request) or None
            user_agent = (request.META.get("HTTP_USER_AGENT") or "")[:300]

        return AuditLog.objects.create(
            actor=actor if actor is not None and actor.pk else None,
            actor_label=(getattr(actor, "username", "") or "system")[:150],
            category=ACTION_CATEGORY.get(action, AuditLog.Category.ADMIN),
            action=action,
            target_type=type(target).__name__ if target is not None else "",
            target_id=str(getattr(target, "pk", "") or "")[:40],
            target_label=str(target)[:200] if target is not None else "",
            description=description,
            ip_address=ip,
            user_agent=user_agent or "",
            metadata=metadata or None,
        )
    except Exception:  # noqa: BLE001 — auditing must never break the caller
        logger.exception("Failed to write audit entry for action %s", action)
        return None


def recent(limit=10):
    """Latest entries, for the Command Center card."""
    return AuditLog.objects.select_related("actor")[:limit]
