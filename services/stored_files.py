"""Serving a file that the database knows about but storage may not have.

A `FileField` row and the bytes behind it are two separate things, and
they come apart in practice: uploads taken while the app was still
writing to a host's own disk do not exist in the bucket it was later
pointed at, and Render wipes that disk on every deploy. The row survives
in Postgres, the object does not.

Handed straight to `FileResponse`, that mismatch is an unhandled
exception and the organiser sees "Server Error (500)" with nothing to
act on. `serve()` turns it into a page that says which file is missing
and what to do about it, and logs the underlying storage error so the
cause -- a missing object, bad credentials, an unreachable endpoint --
is distinguishable in the Render log.
"""
import logging

from django.http import FileResponse
from django.shortcuts import render

logger = logging.getLogger(__name__)


def serve(request, field, *, filename=None, as_attachment=True, label="file"):
    name = getattr(field, "name", "") or ""
    try:
        handle = field.open("rb")
    except FileNotFoundError:
        # The row points at an object the bucket does not hold.
        logger.warning("Stored file missing from storage: %s", name)
        return _missing(request, label, name, reason="missing")
    except Exception:
        # Credentials, endpoint, permissions -- an operator problem, not a
        # lost file. Log the traceback; do not leak it to the browser.
        logger.exception("Storage unreachable while serving %s", name)
        return _missing(request, label, name, reason="unreachable")

    return FileResponse(
        handle,
        as_attachment=as_attachment,
        filename=filename or name.rsplit("/", 1)[-1],
    )


def _missing(request, label, name, *, reason):
    status = 410 if reason == "missing" else 503
    return render(
        request,
        "errors/file_missing.html",
        {"label": label, "name": name, "reason": reason},
        status=status,
    )
