from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import get_object_or_404, render

from competitions.models import Competition
from services.scoring_service import leaderboard


@staff_member_required
def leaderboard_view(request, competition_id=None):
    competitions = Competition.objects.all().order_by("-id")
    if competition_id:
        competition = get_object_or_404(Competition, pk=competition_id)
    else:
        competition = competitions.first()
    rows = leaderboard(competition) if competition else []
    return render(
        request,
        "judging/leaderboard.html",
        {"competition": competition, "competitions": competitions, "rows": rows},
    )