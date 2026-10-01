"""What the *web* process thinks its file store is.

`manage.py check_media` answers the same question from a shell, and the
two can disagree: on Render a shell session is a new process that picks
up the current environment, while the gunicorn workers serving uploads
keep whatever environment they started with. Add the S3 variables and
the shell reports object storage immediately, while the running workers
are still writing to the container's disk until the service restarts.

That disagreement is invisible from either side alone -- the shell probe
succeeds, the upload form succeeds, and the bucket stays empty. This
reports from inside the process that actually handles the upload.
"""
import os
import uuid

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

# The names that matter, and whether the value is safe to show. A bucket
# and an endpoint are not secrets and a typo in them is the usual fault;
# the key and secret are reported as present or absent only.
S3_VARS = (
    ("S3_BUCKET", True),
    ("S3_ENDPOINT_URL", True),
    ("S3_REGION", True),
    ("S3_ACCESS_KEY_ID", False),
    ("S3_SECRET_ACCESS_KEY", False),
)


def report(probe=False):
    """Storage facts for a staff-only page. Never includes a secret."""
    backend = settings.STORAGES["default"]["BACKEND"]
    is_object_store = "s3" in backend.lower()

    variables = []
    for name, showable in S3_VARS:
        raw = os.environ.get(name)
        variables.append({
            "name": name,
            "state": "missing" if raw is None else ("empty" if not raw.strip() else "set"),
            "value": raw if (showable and raw and raw.strip()) else "",
        })

    data = {
        "backend": backend,
        "is_object_store": is_object_store,
        "variables": variables,
        "debug": settings.DEBUG,
        "media_root": str(settings.MEDIA_ROOT),
        "options": {},
        "probe": None,
    }
    if is_object_store:
        opts = settings.STORAGES["default"].get("OPTIONS", {})
        data["options"] = {
            "bucket": opts.get("bucket_name"),
            "endpoint": opts.get("endpoint_url"),
            "region": opts.get("region_name"),
        }
    if probe:
        data["probe"] = _probe()
    return data


def _probe():
    """Write, read and delete one object through this very process."""
    name = f"healthcheck/web-{uuid.uuid4().hex}.txt"
    payload = b"jengasec web probe"
    try:
        stored = default_storage.save(name, ContentFile(payload))
    except Exception as exc:  # noqa: BLE001 — the message is the diagnosis
        return {"ok": False, "stage": "write", "detail": f"{type(exc).__name__}: {exc}"}
    try:
        body = default_storage.open(stored, "rb").read()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "stage": "read", "detail": f"{type(exc).__name__}: {exc}",
                "name": stored}
    finally:
        try:
            default_storage.delete(stored)
        except Exception:  # noqa: BLE001 — a leftover probe file is harmless
            pass
    if body != payload:
        return {"ok": False, "stage": "verify", "detail": "read back the wrong bytes",
                "name": stored}
    return {"ok": True, "stage": "complete", "name": stored, "detail": ""}
