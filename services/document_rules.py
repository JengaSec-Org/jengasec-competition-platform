"""Submission document rules (Entry Guide sections 7, 9 and 10).

Two jobs, both pure functions over bytes or parsed output so the form,
the service layer and the tests can share them:

* `validate_upload()` -- what a file must be before it is stored: type,
  size, encryption, extractable text, page band, mandatory file name.
  Every failure is a `RuleViolation` whose message names the Guide rule.
* `structure_check()` -- what a parsed proposal must contain: the front
  matter that returns a submission as incomplete when missing, and the
  required sections, each mapped to the rubric criterion it feeds.
"""
import re

from competitions.constants import (
    EVIDENCE_MAX_BYTES,
    FRONT_MATTER,
    NON_BODY_PAGE_MARKERS,
    PROPOSAL_MAX_BYTES,
    PROPOSAL_PAGE_BANDS,
    proposal_file_pattern,
    required_sections_for,
)

GUIDE_FORMAT = "Entry Guide section 7 (submission format)"
GUIDE_STRUCTURE = "Entry Guide section 9 (proposal structure)"

# How little text on a page marks it as scanned / image-only.
MIN_CHARS_PER_PAGE = 40
# A document counts as text-extractable when this share of pages has text.
MIN_TEXT_PAGE_RATIO = 0.6


class RuleViolation(ValueError):
    """A Guide rule was broken. The message quotes the rule and is shown
    to the uploader verbatim."""


def _fitz():
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:  # pragma: no cover - dependency missing
        raise RuleViolation(
            f"{GUIDE_FORMAT}: PDFs must be checked before acceptance, but PyMuPDF "
            f"is not installed on this server. Tell the organisers."
        ) from exc
    return fitz


# ---------------------------------------------------------------------------
# PDF inspection
# ---------------------------------------------------------------------------

def inspect_pdf(data):
    """Open PDF bytes and return {page_count, text_pages, body_pages,
    encrypted, page_texts}. Raises RuleViolation for an unreadable file."""
    fitz = _fitz()
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001 - any parser failure is a bad file
        raise RuleViolation(f"{GUIDE_FORMAT}: the PDF could not be opened ({exc}).") from exc
    try:
        if doc.is_encrypted or doc.needs_pass:
            return {"encrypted": True, "page_count": doc.page_count, "text_pages": 0,
                    "body_pages": 0, "page_texts": []}
        page_texts = [page.get_text("text") or "" for page in doc]
    finally:
        doc.close()
    text_pages = sum(1 for t in page_texts if len(t.strip()) >= MIN_CHARS_PER_PAGE)
    return {
        "encrypted": False,
        "page_count": len(page_texts),
        "text_pages": text_pages,
        "body_pages": count_body_pages(page_texts),
        "page_texts": page_texts,
    }


def count_body_pages(page_texts):
    """Total pages minus the cover and any contents / references / appendix
    pages, recognised from the first lines of each page."""
    if not page_texts:
        return 0
    body = 0
    for index, text in enumerate(page_texts):
        if index == 0:
            continue  # the cover
        head = " ".join(text.strip().lower().split()[:12])
        if any(head.startswith(marker) or f" {marker}" in head[:60] for marker in NON_BODY_PAGE_MARKERS):
            continue
        body += 1
    return body


# ---------------------------------------------------------------------------
# Upload validation
# ---------------------------------------------------------------------------

def validate_upload(*, upload_name, size, head, data_loader, submission, next_version):
    """Check an upload against the rules for its submission type.

    `head` is the first bytes (for the signature); `data_loader()` returns
    the whole file only when a PDF has to be opened. Returns a dict of
    facts worth storing (page counts) or raises RuleViolation.
    """
    from services.submission_service import PROPOSAL_TYPES, SHARED_TYPES

    type_name = submission.submission_type.name
    lowered = (upload_name or "").lower()
    facts = {}

    if type_name in PROPOSAL_TYPES:
        if not lowered.endswith(".pdf") or not head.startswith(b"%PDF-"):
            raise RuleViolation(f"{GUIDE_FORMAT}: proposals are submitted as PDF only.")
        if size > PROPOSAL_MAX_BYTES:
            raise RuleViolation(
                f"{GUIDE_FORMAT}: a proposal PDF must be 25 MB or smaller "
                f"(this one is {size / 1048576:.1f} MB)."
            )
        expected = proposal_file_pattern(submission.team, type_name, next_version)
        if upload_name.lower() != expected.lower():
            raise RuleViolation(
                f"{GUIDE_FORMAT}: the file must be named exactly {expected} "
                f"(JS26_<ENT>_<CELL>_<TEAMID>_<TYPE>_v<N>.pdf, N = this version)."
            )
        info = inspect_pdf(data_loader())
        if info["encrypted"]:
            raise RuleViolation(
                f"{GUIDE_FORMAT}: the PDF must not be password-protected or encrypted."
            )
        if info["page_count"] == 0 or info["text_pages"] < info["page_count"] * MIN_TEXT_PAGE_RATIO:
            raise RuleViolation(
                f"{GUIDE_FORMAT}: the PDF must have extractable text -- scanned or "
                f"image-only pages cannot be evaluated. Export it from your editor "
                f"rather than scanning a printout."
            )
        low, high = PROPOSAL_PAGE_BANDS.get(type_name, (0, 10**6))
        if not low <= info["body_pages"] <= high:
            raise RuleViolation(
                f"{GUIDE_STRUCTURE}: a {type_name} is {low} to {high} body pages "
                f"(cover, contents, references and appendices excluded); this one "
                f"has {info['body_pages']}."
            )
        facts.update(page_count=info["page_count"], body_pages=info["body_pages"])
        return facts

    if type_name in SHARED_TYPES:  # Supporting Evidence
        if not lowered.endswith(".zip") or not (head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06")):
            raise RuleViolation(f"{GUIDE_FORMAT}: supporting evidence is submitted as a ZIP bundle only.")
        if size > EVIDENCE_MAX_BYTES:
            raise RuleViolation(f"{GUIDE_FORMAT}: an evidence bundle must be 50 MB or smaller.")
        return facts

    # Other document types keep the general rule: PDF or DOCX, 50 MB.
    if lowered.endswith(".zip"):
        raise RuleViolation(f"{GUIDE_FORMAT}: ZIP bundles are only accepted as Supporting Evidence.")
    if size > EVIDENCE_MAX_BYTES:
        raise RuleViolation(f"{GUIDE_FORMAT}: documents must be 50 MB or smaller.")
    return facts


# ---------------------------------------------------------------------------
# Structure check
# ---------------------------------------------------------------------------

_NUMBERING = re.compile(r"^[\s\d\.\-–:)(]*")


def _norm(heading):
    return _NUMBERING.sub("", (heading or "").lower()).strip()


def _find_heading(headings, aliases):
    for heading in headings:
        text = _norm(heading)
        if any(alias in text for alias in aliases):
            return heading
    return ""


def structure_check(parsed_content, type_name, page_texts=None):
    """Report front matter and required sections for a parsed proposal.

    Front matter may be a heading or simply present on the first pages
    (a cover page rarely has a heading called "cover page"), so those are
    also searched in the first two pages' text.
    """
    sections = (parsed_content or {}).get("sections", [])
    headings = [s.get("heading", "") for s in sections]
    early_text = " ".join((page_texts or [])[:3]).lower()
    # The cover page is page one by definition when it carries the team
    # identifier / title and little else; treat a document with pages as
    # having one, and only require the two statements to be findable.
    front = []
    for key, (name, aliases) in FRONT_MATTER.items():
        heading = _find_heading(headings, aliases)
        present = bool(heading) or any(alias in early_text for alias in aliases)
        if key == "cover_page":
            present = present or bool(page_texts)
        front.append({"key": key, "name": name, "present": present, "heading": heading})

    body = []
    for key, name, aliases, criterion in required_sections_for(type_name):
        heading = _find_heading(headings, aliases)
        body.append({
            "key": key, "name": name, "present": bool(heading),
            "heading": heading, "criterion": criterion,
        })
    return {"front_matter": front, "sections": body}
