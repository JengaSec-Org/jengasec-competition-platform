"""Private, verified archives bound to the exact calculated input snapshot."""
import hashlib
import json
import re
import tempfile

from django.core.files.base import ContentFile
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from jengasec_scoring_policy import canonical_json, digest
from scoring.models import ScoringEvidence
from scoring.protocol import MAX_BODY_BYTES, ProtocolError, enrollment_for, validate_snapshot

PREFIXES = {"application": "evt_", "infrastructure": "infra_", "metrics": "metric_",
            "scenario": "case_", "ai": "ai_"}
DENIED_KEYS = {"password", "passwd", "secret", "token", "access_token", "refresh_token",
               "authorization", "cookie", "set_cookie", "credentials", "raw_body",
               "request_body", "response_body", "body", "prompt", "raw_prompt",
               "email", "participant_name", "phone", "session_cookie"}


def _sanitized(value, depth=0):
    if depth > 12:
        raise ProtocolError("evidence nesting exceeds twelve levels")
    if isinstance(value, dict):
        if len(value) > 100:
            raise ProtocolError("evidence object has too many fields")
        for key, item in value.items():
            normalized = key.lower().replace("-", "_")
            if normalized in DENIED_KEYS or normalized.endswith(("_password", "_secret", "_token")):
                raise ProtocolError("evidence contains a prohibited sensitive field")
            _sanitized(item, depth + 1)
    elif isinstance(value, list):
        if len(value) > 10000:
            raise ProtocolError("evidence list is too long")
        for item in value:
            _sanitized(item, depth + 1)
    elif isinstance(value, str) and len(value) > 8192:
        raise ProtocolError("evidence text is too long")


def validate_archive_payload(payload):
    if set(payload) != {"schema_version", "competition", "team", "family", "snapshot",
                        "records", "queries"}:
        raise ProtocolError("invalid evidence envelope fields")
    if payload["schema_version"] != "scoring-evidence/1.0":
        raise ProtocolError("unsupported evidence schema")
    if payload["family"] not in set(PREFIXES) | {"mixed"}:
        raise ProtocolError("unsupported evidence family")
    records, queries = payload["records"], payload["queries"]
    if not isinstance(records, list) or len(records) > 1000:
        raise ProtocolError("records must be a list of at most 1000 items")
    if not isinstance(queries, list) or len(queries) > 16 or any(
        not isinstance(q, str) or not 1 <= len(q) <= 4096 for q in queries
    ):
        raise ProtocolError("queries must be bounded strings")
    if not records:
        raise ProtocolError("evidence requires measured records; a query alone is not proof")
    for row in records:
        if not isinstance(row, dict) or row.get("family") not in PREFIXES:
            raise ProtocolError("each evidence record requires a typed family")
        identifier = row.get("id")
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", identifier):
            raise ProtocolError("invalid evidence record identifier")
        if not identifier.startswith(PREFIXES[row["family"]]):
            raise ProtocolError("evidence identifier does not match its family")
        if payload["family"] != "mixed" and row["family"] != payload["family"]:
            raise ProtocolError("record family differs from its archive")
    _sanitized(payload)


@transaction.atomic
def archive(competition, team, payload):
    validate_archive_payload(payload)
    enrollment = enrollment_for(competition, lock=True)
    if enrollment.finalized_at:
        raise ProtocolError("automatic scoring has been finalized", code="finalized")
    validate_snapshot(competition, team, payload["snapshot"], enrollment=enrollment)
    data = canonical_json(payload)
    if len(data) > MAX_BODY_BYTES:
        raise ProtocolError("archive exceeds one MiB", code="too_large")
    sha256 = hashlib.sha256(data).hexdigest()
    prior = ScoringEvidence.objects.filter(competition=competition, team=team, sha256=sha256).first()
    if prior:
        verified_bytes(prior)
        return prior
    used = ScoringEvidence.objects.filter(competition=competition).aggregate(n=Sum("byte_size"))["n"] or 0
    if used + len(data) > enrollment.archive_budget_bytes:
        raise ProtocolError("approved evidence capacity exhausted", code="archive_full", retryable=True)
    evidence = ScoringEvidence(
        competition=competition, team=team, family=payload["family"], sha256=sha256,
        input_sha256=digest(payload["snapshot"]), snapshot=payload["snapshot"], byte_size=len(data),
        verified_at=timezone.now(), retain_until=competition.settings.appeal_window_closes_at,
    )
    # A commit can fail after an object-store write. A content-addressed path
    # lets the retry verify and adopt those bytes without accumulating copies.
    name = f"scoring/evidence/{competition.pk}/{team.pk}/{sha256}.json"
    if evidence.archive.storage.exists(name):
        evidence.archive.name = name
    else:
        evidence.archive.save(f"{competition.pk}/{team.pk}/{sha256}.json", ContentFile(data), save=False)
    verified_bytes(evidence)
    evidence.save(force_insert=True)
    return evidence


def verified_bytes(evidence):
    try:
        with evidence.archive.open("rb") as file:
            data = file.read(MAX_BODY_BYTES + 1)
    except (OSError, ValueError) as exc:
        raise ProtocolError("evidence archive is unavailable", code="archive_unavailable",
                            retryable=True) from exc
    if len(data) != evidence.byte_size or hashlib.sha256(data).hexdigest() != evidence.sha256:
        raise ProtocolError("evidence integrity verification failed", code="evidence_integrity")
    try:
        content = json.loads(data)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProtocolError("evidence archive is invalid", code="evidence_integrity") from exc
    if content.get("snapshot") != evidence.snapshot or digest(evidence.snapshot) != evidence.input_sha256:
        raise ProtocolError("evidence input snapshot changed", code="evidence_integrity")
    return data


def resolve_references(competition, team, references, snapshot):
    if not isinstance(references, list) or not 1 <= len(references) <= 128:
        raise ProtocolError("one to 128 verified evidence references are required")
    resolved, seen = [], set()
    for reference in references:
        if not isinstance(reference, dict) or set(reference) != {"id", "sha256"}:
            raise ProtocolError("evidence reference requires id and sha256")
        if not isinstance(reference["id"], str) or not isinstance(reference["sha256"], str):
            raise ProtocolError("invalid evidence reference")
        if len(reference["id"]) != 36 or not re.fullmatch(r"[a-f0-9]{64}", reference["sha256"]):
            raise ProtocolError("invalid evidence identifier or digest")
        try:
            evidence = ScoringEvidence.objects.get(
                pk=reference["id"], competition=competition, team=team,
                sha256=reference["sha256"], verified_at__isnull=False)
        except (ScoringEvidence.DoesNotExist, ValueError, ValidationError) as exc:
            raise ProtocolError("verified evidence not found for this team") from exc
        if str(evidence.pk) in seen:
            raise ProtocolError("duplicate evidence reference")
        seen.add(str(evidence.pk))
        if evidence.snapshot != snapshot or evidence.input_sha256 != digest(snapshot):
            raise ProtocolError("evidence does not match these calculation inputs")
        verified_bytes(evidence)
        resolved.append(evidence)
    return resolved


def _scan_finding_file(storage, name, *, copy_to=None):
    from cyberrange.forms import EVIDENCE_MAX_BYTES

    size, checksum = 0, hashlib.sha256()
    with storage.open(name, "rb") as source:
        while chunk := source.read(256 * 1024):
            size += len(chunk)
            if size > EVIDENCE_MAX_BYTES:
                raise ValueError("finding evidence exceeds the upload limit")
            checksum.update(chunk)
            if copy_to is not None:
                copy_to.write(chunk)
    return size, checksum.hexdigest()


def finding_file_snapshot(field):
    """Freeze a private, non-overwriting upload name and its reviewed bytes.

    Storage retains replaced uploads. A missing legacy file remains an
    explicit proof gap; a judge's existing review never fabricates a hash.
    """
    if not field:
        return None
    descriptor = {"name": field.name}
    try:
        size, checksum = _scan_finding_file(field.storage, field.name)
    except Exception:
        return {**descriptor, "status": "unavailable"}
    return {**descriptor, "status": "verified", "size": size, "sha256": checksum}


def open_verified_finding_file(descriptor):
    """Verify the revision's retained object before returning any bytes.

    The Finding row and its current FileField are deliberately not read.
    A spooled copy bounds memory and avoids serving changed object bytes
    between an integrity check and a second storage read.
    """
    from cyberrange.models import Finding
    from cyberrange.forms import EVIDENCE_MAX_BYTES

    if (not isinstance(descriptor, dict) or descriptor.get("status") != "verified"
            or not isinstance(descriptor.get("name"), str) or not descriptor["name"]
            or type(descriptor.get("size")) is not int or not 0 <= descriptor["size"] <= EVIDENCE_MAX_BYTES
            or not isinstance(descriptor.get("sha256"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", descriptor["sha256"])):
        raise ProtocolError("this revision has no verified attachment", code="evidence_integrity")
    copy = tempfile.SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b")
    try:
        size, checksum = _scan_finding_file(
            Finding._meta.get_field("evidence_file").storage, descriptor["name"], copy_to=copy)
        if size != descriptor["size"] or checksum != descriptor["sha256"]:
            raise ValueError("retained attachment changed")
        copy.seek(0)
        return copy
    except Exception as exc:
        copy.close()
        raise ProtocolError("retained finding evidence is unavailable or changed",
                            code="archive_unavailable", retryable=True) from exc
