from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404

from judging.models import Evaluation
from services.preview_service import preview_html


@login_required
def document_preview(request, eval_id):
    """HTML preview of the evaluation's latest file (DOCX/PPTX/ZIP/TXT)."""
    evaluation = get_object_or_404(Evaluation, pk=eval_id)
    if not request.user.is_staff and evaluation.judge_id != request.user.id:
        raise Http404
    sf = evaluation.submission.latest_file
    if not sf:
        raise Http404
    try:
        body = preview_html(sf.file.path)
    except Exception as exc:  # corrupt file, etc.
        body = f"<p>Preview failed: {exc}</p>"
    if body is None:
        raise Http404
    resp = HttpResponse(body)
    # Allow our own iframe, forbid scripts inside the preview.
    resp["Content-Security-Policy"] = "script-src 'none'; frame-ancestors 'self'"
    return resp