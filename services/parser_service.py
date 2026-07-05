"""Document parsing service (PyMuPDF, python-docx).

Extracts structured text from uploaded PDF/DOCX submissions:
    {"title": "...", "sections": [...], "text": "..."}
This output feeds the AI evaluation engine.
"""


def parse_pdf(path: str) -> dict:
    """Extract title, sections, and full text from a PDF (PyMuPDF)."""
    raise NotImplementedError("Pair 1: document processing engine.")


def parse_docx(path: str) -> dict:
    """Extract title, sections, and full text from a DOCX (python-docx)."""
    raise NotImplementedError


def parse_document(path: str) -> dict:
    """Dispatch to the right parser based on file extension."""
    lowered = path.lower()
    if lowered.endswith(".pdf"):
        return parse_pdf(path)
    if lowered.endswith(".docx"):
        return parse_docx(path)
    raise ValueError(f"Unsupported document format: {path}")
