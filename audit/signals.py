"""Authentication events feed the audit trail.

Failed sign-ins matter as much as successful ones — paired with the
lockout in accounts.security they show a brute-force attempt as a run of
login_failed rows from one address.
"""
from django.contrib.auth.signals import (
    user_logged_in,
    user_logged_out,
    user_login_failed,
)
from django.dispatch import receiver

from .models import AuditLog


@receiver(user_logged_in)
def _log_login(sender, request, user, **kwargs):
    from services.audit_service import record

    record(AuditLog.Action.LOGIN_SUCCESS, request=request, actor=user, target=user)


@receiver(user_logged_out)
def _log_logout(sender, request, user, **kwargs):
    from services.audit_service import record

    if user is not None:
        record(AuditLog.Action.LOGOUT, request=request, actor=user, target=user)


@receiver(user_login_failed)
def _log_login_failed(sender, credentials, request=None, **kwargs):
    from services.audit_service import record

    record(
        AuditLog.Action.LOGIN_FAILED,
        request=request,
        description=f"Failed sign-in for '{credentials.get('username', '')}'",
        attempted_username=credentials.get("username", "")[:150],
    )
