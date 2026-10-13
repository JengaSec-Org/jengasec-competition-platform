"""Turn DOCX / PPTX / ZIP submissions into safe HTML for the judge viewer."""
import html
import os
import zipfile


def _page(body):
    return (
        "<!doctype html><meta charset='utf-8'>"
        "<style>body{font-family:system-ui,sans-serif;padding:1rem;line-height:1.5;"
        "color:#111;background:#fff}h2{border-bottom:1px solid #ccc}"
        "table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:4px 8px}"
        "img{max-width:100%}</style>" + body
    )


def docx_to_html(path):
    import mammoth

    with open(path, "rb") as f:
        result = mammoth.convert_to_html(f)
    return _page(result.value or "<p>(empty document)</p>")


def pptx_to_html(path):
    from pptx import Presentation

    prs = Presentation(path)
    parts = []
    for i, slide in enumerate(prs.slides, 1):
        parts.append(f"<h2>Slide {i}</h2>")
        for shape in slide.shapes:
            if shape.has_text_frame:
                for p in shape.text_frame.paragraphs:
                    text = "".join(r.text for r in p.runs).strip()
                    if text:
                        parts.append(f"<p>{html.escape(text)}</p>")
    return _page("".join(parts) or "<p>(no text found)</p>")


def zip_to_html(path, limit=300):
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
    items = "".join(f"<li>{html.escape(n)}</li>" for n in names[:limit])
    more = f"<p>...and {len(names) - limit} more files</p>" if len(names) > limit else ""
    return _page(f"<h2>Archive contents ({len(names)} files)</h2><ul>{items}</ul>{more}")


def txt_to_html(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return _page(f"<pre style='white-space:pre-wrap'>{html.escape(f.read())}</pre>")


def preview_html(path):
    """Return HTML for a file, or None if the type has no HTML preview (PDF)."""
    ext = os.path.splitext(path)[1].lower()
    handlers = {
        ".docx": docx_to_html,
        ".pptx": pptx_to_html,
        ".zip": zip_to_html,
        ".txt": txt_to_html,
    }
    fn = handlers.get(ext)
    return fn(path) if fn else None