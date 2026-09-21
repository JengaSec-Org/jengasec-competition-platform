"""Staff-issued accounts (Module 1: User Creation, Role Assignment).

Admins, judges and partners never use the public sign-up. Staff create
the account here; the person sets their own password through the emailed
link, built from Django's password-reset machinery -- so there is no
invite model, no token table, and no password ever travels by email.
"""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.db import transaction
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from accounts.models import UserProfile
from services import notification_catalogue as cat

User = get_user_model()

# Django's PASSWORD_RESET_TIMEOUT default, expressed for the email.
SET_PASSWORD_HOURS = 72


def set_password_url(user):
    """The one-time link that lets `user` choose a password.

    Valid until they use it or PASSWORD_RESET_TIMEOUT elapses; invalid the
    moment their password changes, so it cannot be replayed.
    """
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    path = reverse("accounts:password_reset_confirm", args=[uid, token])
    # Absolute, because it is going into an email.
    return settings.SITE_URL.rstrip("/") + path


@transaction.atomic
def issue_account(*, username, email, first_name, last_name, role, institution="",
                  issued_by=None):
    """Create a user with no usable password, assign the role, send the link."""
    user = User.objects.create_user(
        username=username, email=email, first_name=first_name, last_name=last_name,
    )
    user.set_unusable_password()
    user.save(update_fields=["password"])

    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.role = role
    profile.institution = institution
    profile.save()  # syncs the auth Group; admin role also sets is_staff

    # Sent after commit: the link must not go out for a user that rolls back.
    transaction.on_commit(
        lambda: cat.account_created(user, set_password_url(user), expires_hours=SET_PASSWORD_HOURS)
    )
    return user


def resend_set_password_link(user):
    """For a link that expired unused. Deliberately not deduplicated."""
    return cat.account_created(user, set_password_url(user), expires_hours=SET_PASSWORD_HOURS)


# ---------------------------------------------------------------------------
# Email verification (Guide section 6, step 1)
# ---------------------------------------------------------------------------

def verification_url(verification):
    path = reverse("accounts:verify_email", args=[verification.token])
    return settings.SITE_URL.rstrip("/") + path


def send_verification(user):
    """Issue a fresh 24-hour token and email it. Older tokens stop working.

    Not deduplicated: a person who asks for the link again gets it again.
    """
    import secrets
    from datetime import timedelta

    from django.utils import timezone

    from accounts.models import EmailVerification

    EmailVerification.objects.filter(user=user, used_at__isnull=True).delete()
    verification = EmailVerification.objects.create(
        user=user,
        token=secrets.token_urlsafe(32),
        expires_at=timezone.now() + timedelta(hours=EmailVerification.EXPIRY_HOURS),
    )
    cat.account_created(
        user, verification_url(verification), expires_hours=EmailVerification.EXPIRY_HOURS
    )
    return verification


class VerificationError(ValueError):
    """The link is wrong, used or expired. Message is safe to show."""


@transaction.atomic
def confirm_verification(token, user=None):
    """Mark the token's account verified. Returns the user.

    A signed-in `user` must be the token's owner; when nobody is signed in
    the token alone is accepted (the link came from that person's inbox).
    """
    from django.utils import timezone

    from accounts.models import EmailVerification, UserProfile

    verification = (
        EmailVerification.objects.select_related("user").filter(token=token).first()
    )
    if verification is None:
        raise VerificationError("That verification link is not recognised.")
    if user is not None and user.is_authenticated and user.pk != verification.user_id:
        raise VerificationError(
            "This link belongs to a different account. Sign out and open it again."
        )
    if not verification.is_open:
        raise VerificationError(
            "That verification link has expired or was already used. Request a new one."
        )
    verification.used_at = timezone.now()
    verification.save(update_fields=["used_at"])
    profile, _ = UserProfile.objects.get_or_create(user=verification.user)
    if profile.email_verified_at is None:
        profile.email_verified_at = timezone.now()
        profile.save(update_fields=["email_verified_at"])
    transaction.on_commit(lambda: cat.email_verified(verification.user))
    return verification.user


def register_competitor(form):
    """Public sign-up: create the user, tie them to their institution by
    email domain, and send the verification link.

    The account is inert until verified -- `accounts.views.verified_required`
    keeps it out of every team flow.
    """
    from accounts.institutions import recognise
    from accounts.models import UserProfile

    with transaction.atomic():
        user = form.save()
        profile, _ = UserProfile.objects.get_or_create(user=user)
        institutional, name = recognise(user.email)
        if name and not profile.institution:
            profile.institution = name
        profile.manual_verification_required = not institutional
        profile.save()
        transaction.on_commit(lambda: send_verification(user))
    return user


def mark_institution_verified(profile, by):
    """An organiser confirmed a manually-flagged account's student status."""
    from django.utils import timezone

    profile.manual_verified_at = timezone.now()
    profile.manual_verified_by = by
    profile.save(update_fields=["manual_verified_at", "manual_verified_by"])


# ---------------------------------------------------------------------------
# Code of conduct (Guide section 6, step 4)
# ---------------------------------------------------------------------------

def has_accepted_current_policy(user):
    """True when there is nothing to accept, or the user accepted the
    current version."""
    from accounts.models import PolicyAcceptance, PolicyVersion

    current = PolicyVersion.current()
    if current is None:
        return True
    return PolicyAcceptance.objects.filter(user=user, version=current).exists()


def accept_policy(user, version, ip_address=None):
    from accounts.models import PolicyAcceptance

    acceptance, _ = PolicyAcceptance.objects.get_or_create(
        user=user, version=version, defaults={"ip_address": ip_address}
    )
    return acceptance


@transaction.atomic
def publish_policy(version):
    """Make `version` the current one and tell every competitor to re-accept."""
    from django.utils import timezone

    from accounts.models import PolicyVersion, UserProfile

    PolicyVersion.objects.filter(is_current=True).exclude(pk=version.pk).update(is_current=False)
    version.is_current = True
    version.published_at = version.published_at or timezone.now()
    version.save(update_fields=["is_current", "published_at"])

    competitors = User.objects.filter(
        profile__role__in=(UserProfile.Role.BLUE_TEAM, UserProfile.Role.RED_TEAM),
        is_active=True,
    )
    accept_url = settings.SITE_URL.rstrip("/") + reverse("accounts:conduct")
    transaction.on_commit(
        lambda: cat.policy_updated(
            list(competitors), version.version, version.summary or "See the full text.", accept_url
        )
    )
    return version
