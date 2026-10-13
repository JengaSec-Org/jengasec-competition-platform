"""Document parsing service (PyMuPDF, python-docx, python-pptx, zipfile).

Extracts structured text from uploaded submissions across all allowed
formats (PDF, DOCX, PPTX, TXT, ZIP code archives):
    {"title": "...", "sections": [...], "text": "..."}
This output feeds the AI evaluation engine.
"""
from pathlib import Path
import zipfile, os

import fitz  # PyMuPDF
import docx  # python-docx
from pptx import Presentation




def detect_ext(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".pdf", ".docx", ".pptx", ".txt", ".zip"):
        return ext
    with open(path, "rb") as f:
        head = f.read(5)
    if head.startswith(b"%PDF"):
        return ".pdf"
    if head.startswith(b"PK"):
        try:
            names = zipfile.ZipFile(path).namelist()
        except zipfile.BadZipFile:
            return ext
        if any(n.startswith("word/") for n in names):
            return ".docx"
        if any(n.startswith("ppt/") for n in names):
            return ".pptx"
        return ".zip"
    return ".txt" if ext == "" else ext




def parse_pdf(path: str) -> dict:
    """Extract title, sections (one per page), and full text from a PDF."""
    try:
        document = fitz.open(path)
    except Exception as exc:
        raise ValueError(f"Could not open PDF '{path}': {exc}")

    try:
        title = (document.metadata or {}).get("title", "").strip()
        sections, full_text_parts = [], []

        for page_num, page in enumerate(document, start=1):
            page_text = page.get_text()
            full_text_parts.append(page_text)
            sections.append({"page": page_num, "text": page_text})

        full_text = "\n".join(full_text_parts)
        if not title:
            first_line = next((l.strip() for l in full_text.splitlines() if l.strip()), "")
            title = first_line[:200]

        return {"title": title, "sections": sections, "text": full_text}
    finally:
        document.close()


def parse_docx(path: str) -> dict:
    """Extract title, sections (split on real headings), and full text from a DOCX."""
    try:
        document = docx.Document(path)
    except Exception as exc:
        raise ValueError(f"Could not open DOCX '{path}': {exc}")

    title = ""
    sections = []
    current_section = {"heading": None, "text": ""}
    full_text_parts = []

    for para in document.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        full_text_parts.append(text)

        style_name = para.style.name if para.style else ""
        is_heading = style_name.startswith("Heading") or style_name == "Title"

        if is_heading:
            if current_section["text"] or current_section["heading"]:
                sections.append(current_section)
            current_section = {"heading": text, "text": ""}
            if not title and style_name in ("Title", "Heading 1"):
                title = text
        else:
            current_section["text"] += text + "\n"

    if current_section["text"] or current_section["heading"]:
        sections.append(current_section)

    full_text = "\n".join(full_text_parts)
    if not title:
        first_line = next((p.text.strip() for p in document.paragraphs if p.text.strip()), "")
        title = first_line[:200]

    return {"title": title, "sections": sections, "text": full_text}


def parse_pptx(path: str) -> dict:
    """Extract title, sections (one per slide), and full text from a PPTX."""
    try:
        presentation = Presentation(path)
    except Exception as exc:
        raise ValueError(f"Could not open PPTX '{path}': {exc}")

    title = ""
    sections, full_text_parts = [], []

    for slide_num, slide in enumerate(presentation.slides, start=1):
        slide_lines = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    line = "".join(run.text for run in para.runs).strip()
                    if line:
                        slide_lines.append(line)

        slide_text = "\n".join(slide_lines)
        full_text_parts.append(slide_text)
        sections.append({"slide": slide_num, "text": slide_text})

        if not title and slide_lines:
            title = slide_lines[0][:200]

    full_text = "\n".join(full_text_parts)
    return {"title": title, "sections": sections, "text": full_text}


def parse_txt(path: str) -> dict:
    """Read a plain text submission directly."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            full_text = f.read()
    except Exception as exc:
        raise ValueError(f"Could not read TXT '{path}': {exc}")

    first_line = next((l.strip() for l in full_text.splitlines() if l.strip()), "")
    title = first_line[:200]
    return {"title": title, "sections": [{"text": full_text}], "text": full_text}


# Code/text extensions worth extracting from a ZIP -- binaries, dependency
# folders, and build artifacts are deliberately skipped.
ZIP_TEXT_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".cpp", ".h", ".hpp",
    ".cs", ".go", ".rb", ".php", ".rs", ".swift", ".kt", ".sh", ".ps1",
    ".html", ".css", ".sql", ".yml", ".yaml", ".json", ".md", ".txt",
    ".toml", ".ini", ".cfg",
}
ZIP_SKIP_DIRS = {"node_modules", ".git", "__pycache__", "venv", "env", ".venv", "dist", "build"}
ZIP_MAX_FILE_BYTES = 200_000   # skip unusually large single files
ZIP_MAX_TOTAL_FILES = 200      # cap total files read from very large archives


def parse_zip(path: str) -> dict:
    """Extract readable code/text content from a code-archive submission.

    Not prose like the other formats -- walks the archive, concatenates
    every recognized text/code file (one section per file), and that
    concatenation becomes what the AI evaluation engine reads. Hard caps
    on file size/count exist specifically because this is the one format
    that could realistically exceed what a local small model can handle.
    """
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Could not open ZIP '{path}': {exc}")

    sections, full_text_parts = [], []
    files_read = 0

    try:
        for info in archive.infolist():
            if info.is_dir():
                continue
            file_path = Path(info.filename)

            if any(part in ZIP_SKIP_DIRS for part in file_path.parts):
                continue
            if file_path.suffix.lower() not in ZIP_TEXT_EXTENSIONS:
                continue
            if info.file_size > ZIP_MAX_FILE_BYTES:
                continue
            if files_read >= ZIP_MAX_TOTAL_FILES:
                break

            try:
                text = archive.read(info.filename).decode("utf-8", errors="replace")
            except Exception:
                continue  # unreadable single file -- skip it, don't fail the whole archive

            full_text_parts.append(f"\n--- {info.filename} ---\n{text}")
            sections.append({"file": info.filename, "text": text})
            files_read += 1
    finally:
        archive.close()

    full_text = "\n".join(full_text_parts)

    # Prefer a root README's first line as title; fall back to the archive's own filename
    title = Path(path).stem
    readme = next((s for s in sections if Path(s["file"]).name.lower().startswith("readme")), None)
    if readme:
        first_line = next((l.strip() for l in readme["text"].splitlines() if l.strip()), "")
        if first_line:
            title = first_line[:200]

    return {"title": title, "sections": sections, "text": full_text}


def parse_document(path: str) -> dict:
    """Dispatch to the right parser based on file type (extension or content)."""
    ext = detect_ext(path)
    if ext == ".pdf":
        return parse_pdf(path)
    if ext == ".docx":
        return parse_docx(path)
    if ext == ".pptx":
        return parse_pptx(path)
    if ext == ".txt":
        return parse_txt(path)
    if ext == ".zip":
        return parse_zip(path)
    raise ValueError(f"Unsupported document format: {path}")