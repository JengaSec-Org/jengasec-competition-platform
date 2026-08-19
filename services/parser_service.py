"""Document Processing Engine (Module 5).

Reads uploaded PDF/DOCX submissions and produces the structured payload
the AI evaluation engine consumes:

    {
      "title": "...",
      "sections": [{"heading": "...", "level": 1, "text": "..."}],
      "text": "...",
      "tables": [{"page": 1, "rows": [[...], ...]}],
      "metadata": {"page_count": 12, "word_count": 3200,
                   "image_count": 4, "table_count": 2, "format": "pdf"},
      "warnings": ["..."]
    }

Backend tools: PyMuPDF (text + spans + images), pdfplumber (tables),
python-docx (DOCX styles/tables). Parsing degrades gracefully — a
scanned, image-only PDF yields empty text plus a warning rather than
raising, so an upload is never lost to a parsing quirk.
"""
import os
import re
from collections import Counter

# Bump when extraction logic changes, so stored parses can be compared
# and selectively re-run.
PARSER_VERSION = "1.0"

# A span is treated as a heading when it is this much larger than the
# document's dominant body-text size.
HEADING_SIZE_RATIO = 1.15
MAX_HEADING_WORDS = 25


# Guards against hostile documents. A .docx is a zip that python-docx
# expands in-process, so a small file can claim to hold gigabytes.
MAX_PARSE_BYTES = 50 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
MAX_COMPRESSION_RATIO = 120
# Figure extraction caps, so one pathological document cannot flood storage.
MAX_IMAGES_PER_DOCUMENT = 40
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MIN_IMAGE_PIXELS = 100 * 100  # ignore bullets, rules and spacer graphics


class ParseError(Exception):
    """Raised when a document cannot be read at all (corrupt/unsupported)."""


def _guard_size(path):
    """Refuse a file too big to parse in-request."""
    size = os.path.getsize(path)
    if size > MAX_PARSE_BYTES:
        raise ParseError(
            f"File is too large to process ({size // 1048576} MB)."
        )


def _guard_zip_bomb(path):
    """Refuse an archive whose declared contents are absurd.

    Reads only the central directory — no extraction — so the check
    itself cannot be turned into the attack.
    """
    import zipfile

    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            total = sum(info.file_size for info in infos)
            compressed = sum(info.compress_size for info in infos) or 1
    except zipfile.BadZipFile as exc:
        raise ParseError(f"Not a readable archive: {exc}") from exc

    if total > MAX_UNCOMPRESSED_BYTES:
        raise ParseError(
            f"Archive expands to {total // 1048576} MB, which exceeds the limit."
        )
    if total / compressed > MAX_COMPRESSION_RATIO:
        raise ParseError(
            f"Archive compression ratio {total // compressed}:1 looks like a "
            "decompression bomb."
        )


def _clean(text: str) -> str:
    """Collapse runaway whitespace while keeping paragraph breaks."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _word_count(text: str) -> int:
    return len(text.split())


def _heading_level(size: float, body_size: float) -> int:
    """Map a font size to a heading level (1 = biggest)."""
    if body_size <= 0:
        return 1
    ratio = size / body_size
    if ratio >= 1.6:
        return 1
    if ratio >= 1.35:
        return 2
    return 3


def _looks_like_heading(text: str) -> bool:
    stripped = text.strip()
    if not stripped or len(stripped.split()) > MAX_HEADING_WORDS:
        return False
    # Headings rarely end in a sentence period.
    return not stripped.endswith((".", ";", ","))


def _build_sections(blocks, title):
    """Turn (is_heading, level, text) tuples into contiguous sections."""
    sections = []
    current = None
    for is_heading, level, text in blocks:
        if is_heading:
            if current:
                current["text"] = _clean(current["text"])
                sections.append(current)
            current = {"heading": text.strip(), "level": level, "text": ""}
        else:
            if current is None:
                # Body text before any heading — attribute it to the title.
                current = {"heading": title or "Introduction", "level": 1, "text": ""}
            current["text"] += text + "\n"
    if current:
        current["text"] = _clean(current["text"])
        sections.append(current)
    return sections


def parse_pdf(path: str) -> dict:
    """Extract title, sections, text, tables and metadata from a PDF."""
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise ParseError("PyMuPDF is not installed (pip install PyMuPDF)") from exc

    warnings = []
    _guard_size(path)
    try:
        doc = fitz.open(path)
    except Exception as exc:
        raise ParseError(f"Could not open PDF: {exc}") from exc

    try:
        # Pass 1: find the dominant body-text size.
        sizes = Counter()
        pages = []
        for page in doc:
            page_dict = page.get_text("dict")
            pages.append(page_dict)
            for block in page_dict.get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        if span.get("text", "").strip():
                            sizes[round(span["size"], 1)] += len(span["text"])
        body_size = sizes.most_common(1)[0][0] if sizes else 0.0

        # Pass 2: classify each line as heading or body.
        blocks = []
        full_text = []
        image_count = 0
        for page_dict, page in zip(pages, doc):
            image_count += len(page.get_images(full=True))
            for block in page_dict.get("blocks", []):
                for line in block.get("lines", []):
                    spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
                    if not spans:
                        continue
                    line_text = "".join(s["text"] for s in spans).strip()
                    max_size = max(s["size"] for s in spans)
                    bold = any("bold" in s.get("font", "").lower() for s in spans)
                    is_heading = (
                        _looks_like_heading(line_text)
                        and (
                            max_size >= body_size * HEADING_SIZE_RATIO
                            or (bold and max_size >= body_size)
                        )
                    )
                    blocks.append(
                        (is_heading, _heading_level(max_size, body_size), line_text)
                    )
                    full_text.append(line_text)

        text = _clean("\n".join(full_text))
        if not text:
            warnings.append(
                "No extractable text — the PDF may be a scan or image-only. "
                "OCR would be required."
            )

        # Title: first heading, else first non-empty line, else filename.
        title = next((t for is_h, _, t in blocks if is_h), None)
        if not title:
            title = full_text[0] if full_text else os.path.basename(path)

        tables = _extract_pdf_tables(path, warnings)

        return {
            "title": title.strip()[:300],
            "sections": _build_sections(blocks, title),
            "text": text,
            "tables": tables,
            "metadata": {
                "format": "pdf",
                "page_count": doc.page_count,
                "word_count": _word_count(text),
                "image_count": image_count,
                "table_count": len(tables),
            },
            "warnings": warnings,
        }
    finally:
        doc.close()


def _extract_pdf_tables(path: str, warnings: list) -> list:
    """Tables via pdfplumber. Never fatal — tables are a bonus signal."""
    try:
        import pdfplumber
    except ImportError:
        warnings.append("pdfplumber not installed — table extraction skipped.")
        return []

    tables = []
    try:
        with pdfplumber.open(path) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                for raw in page.extract_tables() or []:
                    rows = [
                        [(cell or "").strip() for cell in row]
                        for row in raw
                        if any(cell for cell in row)
                    ]
                    if rows:
                        tables.append({"page": page_number, "rows": rows})
    except Exception as exc:
        warnings.append(f"Table extraction failed: {exc}")
    return tables


def parse_docx(path: str) -> dict:
    """Extract title, sections, text, tables and metadata from a DOCX."""
    try:
        import docx
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise ParseError("python-docx is not installed (pip install python-docx)") from exc

    warnings = []
    _guard_size(path)
    _guard_zip_bomb(path)
    try:
        document = docx.Document(path)
    except Exception as exc:
        raise ParseError(f"Could not open DOCX: {exc}") from exc

    blocks = []
    full_text = []
    for paragraph in document.paragraphs:
        content = paragraph.text.strip()
        if not content:
            continue
        style = (paragraph.style.name or "").lower() if paragraph.style else ""
        if style.startswith("heading"):
            match = re.search(r"(\d+)", style)
            level = int(match.group(1)) if match else 1
            blocks.append((True, min(level, 6), content))
        elif style == "title":
            blocks.append((True, 1, content))
        else:
            blocks.append((False, 0, content))
        full_text.append(content)

    tables = []
    for table in document.tables:
        rows = [
            [cell.text.strip() for cell in row.cells]
            for row in table.rows
        ]
        rows = [row for row in rows if any(row)]
        if rows:
            tables.append({"page": None, "rows": rows})

    text = _clean("\n".join(full_text))
    if not text:
        warnings.append("Document contains no readable paragraph text.")

    # Prefer core-properties title, then first heading, then first line.
    title = (document.core_properties.title or "").strip()
    if not title:
        title = next((t for is_h, _, t in blocks if is_h), "")
    if not title:
        title = full_text[0] if full_text else os.path.basename(path)

    image_count = sum(
        1 for rel in document.part.rels.values() if "image" in rel.reltype
    )

    return {
        "title": title.strip()[:300],
        "sections": _build_sections(blocks, title),
        "text": text,
        "tables": tables,
        "metadata": {
            "format": "docx",
            "page_count": None,  # DOCX has no fixed pagination
            "word_count": _word_count(text),
            "image_count": image_count,
            "table_count": len(tables),
        },
        "warnings": warnings,
    }


def parse_document(path: str) -> dict:
    """Dispatch to the right parser based on file extension."""
    lowered = path.lower()
    if lowered.endswith(".pdf"):
        return parse_pdf(path)
    if lowered.endswith(".docx"):
        return parse_docx(path)
    raise ParseError(
        f"Unsupported document format: {os.path.basename(path)} "
        "(only PDF and DOCX can be parsed; ZIP evidence bundles are stored as-is)"
    )


# ---------------------------------------------------------------- figures


def extract_pdf_images(path):
    """Embedded images from a PDF.

    Yields dicts of {page, index, data, width, height, format}. Skips
    anything too small to be a real figure (bullets, rules, spacers) and
    stops at MAX_IMAGES_PER_DOCUMENT.
    """
    try:
        import fitz
    except ImportError:  # pragma: no cover - dependency guard
        return []

    figures = []
    doc = fitz.open(path)
    try:
        for page_number, page in enumerate(doc, start=1):
            for index, info in enumerate(page.get_images(full=True)):
                if len(figures) >= MAX_IMAGES_PER_DOCUMENT:
                    return figures
                xref = info[0]
                try:
                    raw = doc.extract_image(xref)
                except Exception:  # a broken xref must not fail the parse
                    continue
                data = raw.get("image") or b""
                width, height = raw.get("width", 0), raw.get("height", 0)
                if not data or len(data) > MAX_IMAGE_BYTES:
                    continue
                if width * height < MIN_IMAGE_PIXELS:
                    continue
                figures.append(
                    {
                        "page": page_number,
                        "index": index,
                        "data": data,
                        "width": width,
                        "height": height,
                        "format": (raw.get("ext") or "png").lower(),
                    }
                )
    finally:
        doc.close()
    return figures


def extract_docx_images(path):
    """Embedded images from a DOCX, read from its relationship parts."""
    try:
        import docx
    except ImportError:  # pragma: no cover - dependency guard
        return []

    _guard_zip_bomb(path)
    document = docx.Document(path)
    figures = []
    for index, rel in enumerate(document.part.rels.values()):
        if "image" not in rel.reltype:
            continue
        if len(figures) >= MAX_IMAGES_PER_DOCUMENT:
            break
        try:
            data = rel.target_part.blob
        except Exception:
            continue
        if not data or len(data) > MAX_IMAGE_BYTES:
            continue
        extension = (rel.target_part.partname.ext or "png").lower()
        figures.append(
            {
                "page": None,  # DOCX has no fixed pagination
                "index": index,
                "data": data,
                "width": 0,
                "height": 0,
                "format": extension,
            }
        )
    return figures


def extract_images(path):
    """Dispatch figure extraction by file type. Never raises."""
    lowered = path.lower()
    try:
        if lowered.endswith(".pdf"):
            return extract_pdf_images(path)
        if lowered.endswith(".docx"):
            return extract_docx_images(path)
    except Exception:  # figures are a bonus; never fail the parse for them
        return []
    return []
