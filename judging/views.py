from decimal import Decimal
from django.db import transaction, IntegrityError
from django.db.models import Max

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Avg
from django.http import Http404
from django.shortcuts import render, redirect, get_object_or_404

from rubrics.models import Rubric, RubricCriterion
from competitions.models import Competition
from submissions.models import SubmissionType
from .models import Evaluation
from . import mock_data


def rubric_builder(request):
    """
    Screen 1: Rubric Builder.
    Lists real rubrics + criteria from the database. Both 'Add Criterion'
    and 'New Rubric' POST and persist.
    """
    if request.method == "POST":
        action = request.POST.get("action")

        if action == "add_criterion":
            rubric_id = request.POST.get("rubric_id")
            rubric = Rubric.objects.filter(pk=rubric_id).first()
            if rubric is None:
                messages.error(request, "That rubric no longer exists.")
                return redirect("judging:rubric_builder")

            criterion_name = request.POST.get("criterion_name")
            description = request.POST.get("description", "")
            max_points = request.POST.get("max_points") or 100
            weight = request.POST.get("weight") or 0

            saved = False
            for _ in range(5):
                try:
                    with transaction.atomic():
                        current_max = rubric.criteria.aggregate(m=Max("display_order"))["m"]
                        next_order = 0 if current_max is None else current_max + 1
                        RubricCriterion.objects.create(
                            rubric=rubric,
                            criterion=criterion_name,
                            description=description,
                            max_score=max_points,
                            weight=weight,
                            display_order=next_order,
                        )
                    saved = True
                    break
                except IntegrityError:
                    continue

            if saved:
                messages.success(request, f"Added criterion '{criterion_name}' to '{rubric.title}'.")
            else:
                messages.error(request, "Could not save the criterion after several attempts.")

        elif action == "new_rubric":
            title = request.POST.get("rubric_name")
            competition = Competition.objects.filter(pk=request.POST.get("competition_id")).first()
            submission_type = SubmissionType.objects.filter(pk=request.POST.get("submission_type_id")).first()

            if not (title and competition and submission_type):
                messages.error(request, "Rubric name, competition, and submission type are all required.")
            else:
                Rubric.objects.create(
                    title=title,
                    competition=competition,
                    submission_type=submission_type,
                )
                messages.success(request, f"Created rubric '{title}'.")

        return redirect("judging:rubric_builder")

    rubrics = Rubric.objects.select_related("competition", "submission_type").prefetch_related("criteria")
    competition_options = [{"value": c.id, "label": c.name} for c in Competition.objects.all()]
    submission_type_options = [{"value": st.id, "label": st.name} for st in SubmissionType.objects.all()]

    context = {
        "rubrics": rubrics,
        "competition_options": competition_options,
        "submission_type_options": submission_type_options,
        "active_tab": "rubrics",
    }
    return render(request, "judging/rubric_builder.html", context)


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
    Still on mock_data -- no real model exists for this yet.
    See team notes re: where this should actually live and what its
    schema should be.
    """
    configs = mock_data.get_scoring_configs()
    context = {
        "configs": configs,
        "active_tab": "scoring_config",
    }
    return render(request, "judging/scoring_configuration.html", context)