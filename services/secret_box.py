"""Encrypting a credential the platform must store but must never show.

One case today: the read-only registry token a blue team gives us so the
range can pull their image (`deployments.AppSubmission`). The Image
Submission Guide promises teams it is "stored encrypted, shown to no
one", and a registry token is somebody else's credential, so it does not
sit in the database in clear text.

Key material comes from `DJANGO_TOKEN_KEY` when it is set. When it is
not, the key is derived from `SECRET_KEY`, which production already has
and `preflight` already insists on -- so a team can submit without an
organiser having to add a new Render variable first. Rotating
`SECRET_KEY` therefore makes previously stored tokens unreadable; that is
the right trade for a credential whose owner can re-enter it in ten
seconds, and `decrypt()` returns None rather than raising so a rotation
shows up as "ask the team again" instead of a 500.
"""
import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

logger = logging.getLogger(__name__)


def _key():
    """A 32-byte urlsafe-base64 Fernet key."""
    configured = (getattr(settings, "TOKEN_KEY", "") or "").strip()
    if configured:
        return configured.encode()
    # Derived, not the raw value: SECRET_KEY is not 32 bytes and is not
    # base64, and hashing it keeps the two uses from being interchangeable.
    digest = hashlib.sha256(f"jengasec.secret_box:{settings.SECRET_KEY}".encode()).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt(plaintext):
    """Ciphertext for `plaintext`, or "" when there is nothing to store."""
    if not plaintext:
        return ""
    return Fernet(_key()).encrypt(plaintext.encode()).decode()


def decrypt(ciphertext):
    """The plaintext, or None when it is absent or no longer readable."""
    if not ciphertext:
        return None
    try:
        return Fernet(_key()).decrypt(ciphertext.encode()).decode()
    except (InvalidToken, ValueError, TypeError):
        # A rotated key, or a value written by something else. Never fatal:
        # the caller asks the team to re-enter it.
        logger.warning("Stored secret could not be decrypted; the key may have changed.")
        return None
