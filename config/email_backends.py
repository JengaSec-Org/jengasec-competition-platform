"""Email backends for hosts where plain SMTP is not an option.

Render's free instances cannot open outbound SMTP connections at all
(port 587 -> "Network is unreachable"), so Gmail app passwords only work
from a laptop. Two alternatives:

* `BrevoAPIBackend` -- delivers over HTTPS through Brevo's transactional
  API (free tier: 300 mails/day, no card, no domain; verify the sender
  address in Brevo once). Select with
      DJANGO_EMAIL_BACKEND=config.email_backends.BrevoAPIBackend
      BREVO_API_KEY=xkeysib-...
  and keep DJANGO_EMAIL_HOST_USER / DEFAULT_FROM_EMAIL as the verified sender.

* `IPv4SMTPBackend` -- Django's SMTP backend forced onto IPv4, for hosts
  that resolve smtp.gmail.com to an IPv6 address they cannot route.
  Select with
      DJANGO_EMAIL_BACKEND=config.email_backends.IPv4SMTPBackend

Both honour `fail_silently` exactly like the stock backends, so the
notification queue's retry logic is unchanged.
"""
import json
import logging
import smtplib
import socket
import urllib.error
import urllib.request

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.backends.smtp import EmailBackend as SMTPBackend

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SMTP over IPv4 only
# ---------------------------------------------------------------------------

class _IPv4SMTP(smtplib.SMTP):
    def _get_socket(self, host, port, timeout):
        infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
        if not infos:
            raise OSError(f"{host} has no IPv4 address")
        return socket.create_connection(infos[0][4], timeout, self.source_address)


class IPv4SMTPBackend(SMTPBackend):
    connection_class = _IPv4SMTP


# ---------------------------------------------------------------------------
# Brevo transactional API (HTTPS)
# ---------------------------------------------------------------------------

BREVO_URL = "https://api.brevo.com/v3/smtp/email"


def _split_address(address):
    """'Name <a@b>' -> {"name": "Name", "email": "a@b"}; 'a@b' -> {"email": "a@b"}."""
    address = (address or "").strip()
    if "<" in address and address.endswith(">"):
        name, email = address.rsplit("<", 1)
        return {"name": name.strip().strip('"'), "email": email[:-1].strip()}
    return {"email": address}


class BrevoAPIBackend(BaseEmailBackend):
    def __init__(self, fail_silently=False, api_key=None, timeout=None, **kwargs):
        super().__init__(fail_silently=fail_silently, **kwargs)
        self.api_key = api_key or getattr(settings, "BREVO_API_KEY", "")
        self.timeout = timeout or getattr(settings, "EMAIL_TIMEOUT", 10)

    def open(self):
        if not self.api_key:
            if self.fail_silently:
                return False
            raise RuntimeError("BREVO_API_KEY is not set")
        return True

    def close(self):
        pass

    def send_messages(self, email_messages):
        if not email_messages or not self.open():
            return 0
        sent = 0
        for message in email_messages:
            if self._send(message):
                sent += 1
        return sent

    def _send(self, message):
        recipients = [_split_address(a) for a in message.recipients()]
        if not recipients:
            return False
        payload = {
            "sender": _split_address(message.from_email or settings.DEFAULT_FROM_EMAIL),
            "to": [_split_address(a) for a in message.to],
            "subject": message.subject,
            "textContent": message.body,
        }
        if message.cc:
            payload["cc"] = [_split_address(a) for a in message.cc]
        if message.bcc:
            payload["bcc"] = [_split_address(a) for a in message.bcc]
        if getattr(message, "alternatives", None):
            for content, mimetype in message.alternatives:
                if mimetype == "text/html":
                    payload["htmlContent"] = content
        if message.reply_to:
            payload["replyTo"] = _split_address(message.reply_to[0])

        request = urllib.request.Request(
            BREVO_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "api-key": self.api_key,
                "content-type": "application/json",
                "accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return 200 <= response.status < 300
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            logger.error("Brevo refused the message (%s): %s", exc.code, detail)
            if not self.fail_silently:
                raise RuntimeError(f"Brevo API error {exc.code}: {detail}") from exc
            return False
        except (urllib.error.URLError, OSError) as exc:
            logger.error("Brevo unreachable: %s", exc)
            if not self.fail_silently:
                raise
            return False
