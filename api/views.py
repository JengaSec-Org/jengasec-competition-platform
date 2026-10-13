from django.shortcuts import get_object_or_404
from django_q.tasks import async_task
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import Team
from judging.models import Evaluation
from services.scoring_service import team_result
from .permissions import IsJudgeOrStaff


def _own(request, evaluation):
    return request.user.is_staff or evaluation.judge_id == request.user.id


class RunEvaluationView(APIView):
    """POST /api/evaluations/<id>/run/ - queue an AI evaluation."""
    permission_classes = [IsJudgeOrStaff]

    def post(self, request, pk):
        ev = get_object_or_404(Evaluation, pk=pk)
        if not _own(request, ev):
            return Response({"detail": "Not your evaluation."}, status=403)
        async_task("judging.tasks.run_ai_evaluation_task", ev.id)
        return Response({"detail": "queued", "evaluation": ev.id}, status=202)


class TeamScoreView(APIView):
    """POST /api/teams/<id>/score/ - compute the team's final score."""
    permission_classes = [IsAdminUser]

    def post(self, request, pk):
        team = get_object_or_404(Team, pk=pk)
        r = team_result(team)
        return Response({
            "team": team.team_name,
            "components": {k: (str(v) if v is not None else None) for k, v in r["components"].items()},
            "missing": r["missing"],
            "complete": r["complete"],
            "final": r["final"],
        })


class FeedbackView(APIView):
    """GET /api/evaluations/<id>/feedback/ - per-criterion scores and reasoning."""
    permission_classes = [IsJudgeOrStaff]

    def get(self, request, pk):
        ev = get_object_or_404(Evaluation, pk=pk)
        if not _own(request, ev):
            return Response({"detail": "Not your evaluation."}, status=403)
        items = [{
            "criterion": cs.criterion.criterion,
            "score": str(cs.final_score) if cs.final_score is not None else None,
            "max": str(cs.criterion.max_score),
            "evidence": cs.evidence,
            "reasoning": cs.reasoning,
        } for cs in ev.criterion_scores.select_related("criterion")]
        return Response({"status": ev.status, "total": str(ev.weighted_total), "criteria": items})