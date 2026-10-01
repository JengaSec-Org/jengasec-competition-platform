from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Avg
from django.http import Http404
from django.shortcuts import render, redirect, get_object_or_404

from competitions.models import Competition
from submissions.models import SubmissionType
from .models import Evaluation, ScoringConfig


@login_required
def ai_evaluation_results(request):
    """
    Screen 2: AI Evaluation Results.
    Real Evaluation rows assigned to the logged-in judge. Filtering
    by status uses Evaluation.Status's real values (pending, ai_complete,
    judge_reviewing, approved, published) via ?status=<value>.
    """
    status_filter = request.GET.get("status", "all")

    qs = (
        Evaluation.objects.filter(judge=request.user)
        .select_related("submission", "submission__team", "rubric")
        .prefetch_related("ai_runs")
        .annotate(avg_confidence=Avg("criterion_scores__confidence"))
    )
    if status_filter != "all":
        qs = qs.filter(status=status_filter)

    evaluations = list(qs)
    for e in evaluations:
        if e.avg_confidence is None:
            e.confidence_level = None
        elif e.avg_confidence >= Decimal("0.8"):
            e.confidence_level = "high"
        elif e.avg_confidence >= Decimal("0.5"):
            e.confidence_level = "medium"
        else:
            e.confidence_level = "low"

    status_options = [{"value": "all", "label": "All"}] + [
        {"value": val, "label": label} for val, label in Evaluation.Status.choices
    ]

    context = {
        "evaluations": evaluations,
        "status_filter": status_filter,
        "status_options": status_options,
        "active_tab": "ai_results",
    }
    return render(request, "judging/ai_results.html", context)


@login_required
def ai_evaluation_detail(request, eval_id):
    evaluation = get_object_or_404(
        Evaluation.objects.select_related("submission", "submission__team", "rubric"),
        pk=eval_id,
    )

    if request.method == "POST":
        review_action = request.POST.get("review_action")
        criterion_scores = evaluation.criterion_scores.select_related("criterion")

        if review_action == "approve":
            evaluation.status = Evaluation.Status.APPROVED
            evaluation.save(update_fields=["status", "updated_at"])
            evaluation.recalculate_total()
            messages.success(request, "AI scores approved.")

        elif review_action == "save_overrides":
            reason = request.POST.get("override_reason", "").strip()

            # First pass: figure out if anything actually changed, and validate input
            changes = []
            for cs in criterion_scores:
                raw_value = request.POST.get(f"score_{cs.criterion.id}")
                if raw_value in (None, ""):
                    continue
                try:
                    new_value = Decimal(raw_value)
                except Exception:
                    messages.error(request, f"Invalid score for '{cs.criterion.criterion}'.")
                    return redirect("judging:ai_result_detail", eval_id=eval_id)

                if cs.final_score is None or new_value != cs.final_score:
                    changes.append((cs, new_value))

            if changes and not reason:
                messages.error(request, "A reason is required when changing any score.")
                return redirect("judging:ai_result_detail", eval_id=eval_id)

            for cs, new_value in changes:
                cs.apply_judge_score(new_value, judge=request.user, reason=reason)

            evaluation.status = Evaluation.Status.JUDGE_REVIEWING
            evaluation.save(update_fields=["status", "updated_at"])
            evaluation.recalculate_total()

            if changes:
                messages.success(request, f"Saved {len(changes)} override(s).")
            else:
                messages.success(request, "No changes to save.")

        return redirect("judging:ai_result_detail", eval_id=eval_id)

    criterion_scores = (
        evaluation.criterion_scores.select_related("criterion")
        .order_by("criterion__display_order")
    )
    latest_run = evaluation.ai_runs.first()
    submission_file = evaluation.submission.latest_file

    context = {
        "evaluation": evaluation,
        "criterion_scores": criterion_scores,
        "latest_run": latest_run,
        "submission_file": submission_file,
        "active_tab": "ai_results",
    }
    return render(request, "judging/ai_evaluation_detail.html", context)



def scoring_configuration(request):
    """
    Screen 3: Scoring Configuration.
    Real ScoringConfig rows -- no longer mock data.
    """
    configs = ScoringConfig.objects.select_related("competition")
    context = {"configs": configs, "active_tab": "scoring_config"}
    return render(request, "judging/scoring_configuration.html", context)



from django_q.tasks import async_task

@login_required
def run_ai_evaluation(request, eval_id):
    evaluation = get_object_or_404(Evaluation, pk=eval_id)
    if request.method == "POST":
        async_task("judging.tasks.run_ai_evaluation_task", evaluation.id)
        messages.success(request, "AI evaluation queued — refresh in a few minutes to see results.")
    return redirect("judging:ai_result_detail", eval_id=eval_id)
