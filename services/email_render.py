"""Turn a notification's plain-text body into a branded HTML email.

Every notification already has a plain-text body written for a person to
read, and a per-type `.txt` template that wraps it. That text stays the
source of truth and the plain-text alternative of the message; this module
renders the *same words* with the competition's furniture around them.

The bodies follow three shapes the catalogue writes by hand, so they are
parsed back out rather than re-authored eighteen times:

    Reference JS26-B-014            -> a field row
    Track: Application Blue         -> a field row
      - Upload your student ID      -> a bullet in a list
    Anything else                   -> a paragraph

Nothing here fails a send: if parsing or rendering goes wrong the caller
falls back to the plain-text body, which is always correct.
"""
import logging
import re

from django.conf import settings
from django.template import TemplateDoesNotExist
from django.template.loader import render_to_string

from competitions.constants import EDITION
from notifications.models import Notification

logger = logging.getLogger(__name__)

BULLET = re.compile(r"^\s*[-*•]\s+(.*)$")
# "Label: value", where the label is short and has no sentence punctuation --
# narrow on purpose, so ordinary prose containing a colon stays a paragraph.
# Indentation is allowed: the catalogue indents the rows under a heading
# line such as "Deadlines:".
FIELD = re.compile(r"^([A-Z][A-Za-z /&'-]{1,28}):\s+(\S.*)$")
# A line that is only a label, e.g. "Deadlines:" -- introduces the rows
# under it, so it must not be glued onto the previous paragraph.
LEAD_IN = re.compile(r"^([A-Z][A-Za-z /&'-]{1,28}):$")

# What the button should say, by notification type. Anything not listed
# gets the generic label -- a new type is never undeliverable.
ACTION_LABELS = {
    Notification.Type.ACCOUNT_CREATED: "Verify my email",
    Notification.Type.EMAIL_VERIFIED: "Go to my dashboard",
    Notification.Type.INVITATION_SENT: "View the invitation",
    Notification.Type.INVITATION_ACCEPTED: "Open my team",
    Notification.Type.OUTSTANDING_ITEMS: "Finish these items",
    Notification.Type.REGISTRATION_SUBMITTED: "View the registration",
    Notification.Type.REGISTRATION_APPROVED: "Open my dashboard",
    Notification.Type.REGISTRATION_REJECTED: "Correct the registration",
    Notification.Type.REGISTRATION_CLOSING: "Finish the registration",
    Notification.Type.POLICY_UPDATED: "Read and accept",
    Notification.Type.SUBMISSION_CONFIRMED: "View the submission",
    Notification.Type.EVAL_COMPLETE: "View the result",
    Notification.Type.RESULTS_PUBLISHED: "See the results",
    Notification.Type.APPEAL_SUBMITTED: "View the appeal",
    Notification.Type.APPEAL_RESOLVED: "Read the decision",
    Notification.Type.DEADLINE_APPROACHING: "Upload now",
    
}
DEFAULT_ACTION_LABEL = "Open the platform"


def parse_body(body):
    """Group the plain body into paragraph / list / field blocks."""
    blocks = []
    paragraph = []
    items = None   # open bullet list
    fields = None  # open field table

    def flush():
        nonlocal paragraph, items, fields
        if paragraph:
            blocks.append({"kind": "text", "text": " ".join(paragraph)})
            paragraph = []
        if items:
            blocks.append({"kind": "list", "items": items})
            items = None
        if fields:
            blocks.append({"kind": "fields", "items": fields})
            fields = None

    for raw in (body or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            flush()
            continue

        bullet = BULLET.match(line)
        if bullet:
            if paragraph or fields:
                text, paragraph = " ".join(paragraph), []
                if text:
                    blocks.append({"kind": "text", "text": text})
                if fields:
                    blocks.append({"kind": "fields", "items": fields})
                    fields = None
            items = items or []
            items.append(bullet.group(1).strip())
            continue

        stripped = line.strip()

        lead_in = LEAD_IN.match(stripped)
        if lead_in:
            flush()
            blocks.append({"kind": "text", "text": stripped})
            continue

        field = FIELD.match(stripped)
        if field:
            if paragraph or items:
                flush()
            fields = fields or []
            fields.append((field.group(1), field.group(2)))
            continue

        if items or fields:
            flush()
        paragraph.append(stripped)

    flush()
    return blocks


def _plain_text(notification):
    """The per-type .txt template, or the body itself."""
    context = {
        "notification": notification,
        "user": notification.user,
        "site_url": getattr(settings, "SITE_URL", ""),
    }
    for name in (f"notifications/email/{notification.type}.txt",
                 "notifications/email/default.txt"):
        try:
            return render_to_string(name, context)
        except TemplateDoesNotExist:
            continue
    return notification.body


def asset_url(path):
    """Absolute URL for a static file, safe for an email client.

    Images in email must be fetchable by a stranger's mail server, so the
    URL has to be absolute and properly encoded. `{% static %}` gives a
    site-relative path and does not encode, so both are handled here.
    """
    from urllib.parse import quote

    from django.templatetags.static import static

    url = static(path)
    if url.startswith(("http://", "https://")):
        return quote(url, safe=":/?#[]@!$&'()*+,;=%~")
    site = getattr(settings, "SITE_URL", "").rstrip("/")
    return site + quote(url, safe="/?#[]@!$&'()*+,;=%~")


def absolute(link):
    """A link in an email has to be absolute, and SITE_URL is the only
    thing that knows the public host."""
    if not link:
        return ""
    if link.startswith(("http://", "https://")):
        return link
    return getattr(settings, "SITE_URL", "").rstrip("/") + link


def render_html(notification):
    """The branded HTML alternative, or None if it cannot be produced."""
    try:
        greeting = None
        user = notification.user
        if user is not None and user.first_name:
            greeting = f"Hello {user.first_name},"

        action_url = absolute(notification.link)
        blocks = _without_bare_url(parse_body(notification.body), action_url)
        if greeting:
            blocks.insert(0, {"kind": "text", "text": greeting})

        return render_to_string("notifications/email/base.html", {
            "notification": notification,
            "user": user,
            "blocks": blocks,
            "kicker": notification.get_type_display(),
            "edition": EDITION,
            "site_url": getattr(settings, "SITE_URL", "").rstrip("/"),
            "action_url": action_url,
            "action_label": ACTION_LABELS.get(notification.type, DEFAULT_ACTION_LABEL),
            "preheader": " ".join(notification.body.split())[:140],
            "note": _note_for(notification),
            "logo_url": asset_url("images/logo.png"),
            "club_logo_url": asset_url("logo/scsc-logo.png"),
        })
    except Exception:  # noqa: BLE001 - a styling failure must not stop the mail
        logger.exception("HTML email rendering failed for notification %s", notification.pk)
        return None


def _without_bare_url(blocks, action_url):
    """Drop a paragraph that is only the link the button already carries.

    The plain-text bodies spell the URL out because plain text has no
    buttons; in HTML that would print the same link three times -- body,
    button, and the button's own fallback line.
    """
    if not action_url:
        return blocks
    return [
        b for b in blocks
        if not (b["kind"] == "text" and b["text"].strip().rstrip("/") == action_url.rstrip("/"))
    ]


def _note_for(notification):
    """The one caveat worth pulling out of the body into its own panel."""
    if notification.type == Notification.Type.ACCOUNT_CREATED:
        return ("If you did not create this account, ignore this message — "
                "the link expires on its own and nothing else will be sent.")
    if notification.type == Notification.Type.REGISTRATION_APPROVED:
        return ("Keep this message: it is the record of your team identifier and "
                "cell assignment. Credentials and repository access are issued "
                "separately and never sent by email.")
    if notification.type == Notification.Type.INVITATION_SENT:
        return "The invitation expires after seven days. You need a verified JengaSec account under this exact address to accept it."
    return ""


def parts(notification):
    """(plain_text, html_or_None) for one notification."""
    return _plain_text(notification), render_html(notification)


def send_branded(subject, body, to, *, link="", kicker="JengaSec", note="",
                 action_label=DEFAULT_ACTION_LABEL, fail_silently=True):
    """Send one branded email to an address with no account behind it.

    The invitation a captain sends is the case that matters: the invitee
    usually has no user row, so there is no Notification to queue and no
    per-type template to reach for -- but it is the first thing they ever
    see from JengaSec, and it should not be the one plain-text outlier.
    """
    from django.core.mail import EmailMultiAlternatives

    text = f"{body}\n\n--\nJengaSec Evaluation Platform\nStrathmore Cybersecurity Club\n"
    message = EmailMultiAlternatives(subject=subject, body=text, to=[to])
    try:
        html = render_to_string("notifications/email/base.html", {
            "notification": {"subject": subject, "body": body},
            "blocks": parse_body(body),
            "kicker": kicker,
            "edition": EDITION,
            "site_url": getattr(settings, "SITE_URL", "").rstrip("/"),
            "action_url": absolute(link),
            "action_label": action_label,
            "preheader": " ".join(body.split())[:140],
            "note": note,
            "logo_url": asset_url("images/logo.png"),
            "club_logo_url": asset_url("logo/scsc-logo.png"),
        })
        message.attach_alternative(html, "text/html")
    except Exception:  # noqa: BLE001 - plain text still goes out
        logger.exception("HTML rendering failed for direct mail to %s", to)
    try:
        message.send(fail_silently=fail_silently)
    except Exception:  # noqa: BLE001
        logger.exception("Direct mail to %s failed", to)
        if not fail_silently:
            raise
    return message
