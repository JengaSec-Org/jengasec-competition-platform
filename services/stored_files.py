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
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

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


@contextmanager
def local_copy(field):
    """A real filesystem path for a stored file, whatever the backend.

    The parser and the image extractor both need a path they can hand to
    PyMuPDF and python-docx, and both reached for `field.path`. That only
    exists on `FileSystemStorage`: `S3Storage.path()` raises
    NotImplementedError by design, because an object in a bucket has no
    local path. Moving uploads to Backblaze therefore made every parse
    fail -- recorded as FAILED rather than crashing, so uploads kept
    working and nothing surfaced except "document processing is broken".

    Uses the real path when the backend has one (no copy, no temp file),
    and otherwise streams the object to a temporary file and removes it
    afterwards.
    """
    try:
        path = field.path
    except (NotImplementedError, AttributeError, ValueError):
        path = None

    if path:
        yield path
        return

    suffix = Path(field.name).suffix or ""
    handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        with field.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 256), b""):
                handle.write(chunk)
        handle.close()
        yield handle.name
    finally:
        try:
            handle.close()
        except Exception:  # noqa: BLE001 — already closed
            pass
        try:
            os.unlink(handle.name)
        except OSError:
            # A temp file we cannot remove is untidy, not a failure.
            logger.warning("Could not remove temporary copy %s", handle.name)
