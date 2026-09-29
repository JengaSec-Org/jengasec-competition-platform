from django.shortcuts import render 

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import transaction
from django.shortcuts import redirect, get_object_or_404

from .models import Rubric
from .forms import RubricForm, RubricCriterionFormSet


@login_required
def rubric_list(request):
    """Read-only rubric listing -- visible to judges and admins alike."""
    rubrics = (
        Rubric.objects.select_related("competition", "submission_type")
        .prefetch_related("criteria")
    )
    context = {"rubrics": rubrics, "active_tab": "rubrics"}
    return render(request, "rubrics/rubric_list.html", context)


@login_required
def rubric_create(request):
    if request.method == "POST":
        form = RubricForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    rubric = form.save()
                    formset = RubricCriterionFormSet(request.POST, instance=rubric)
                    if not formset.is_valid():
                        raise ValueError("formset invalid")
                    formset.save()
                messages.success(request, f"Created rubric '{rubric.title}'.")
                return redirect("rubrics:rubric_list")
            except ValueError:
                messages.error(request, "Please fix the errors in the criteria below.")
        else:
            formset = RubricCriterionFormSet(request.POST)
    else:
        form = RubricForm()
        formset = RubricCriterionFormSet()

    context = {"form": form, "formset": formset, "active_tab": "rubrics"}
    return render(request, "rubrics/rubric_form.html", context)


@login_required 
def rubric_edit(request, pk):
    rubric = get_object_or_404(Rubric, pk=pk)
    if request.method == "POST":
        form = RubricForm(request.POST, instance=rubric)
        formset = RubricCriterionFormSet(request.POST, instance=rubric)
        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                form.save()
                formset.save()
            messages.success(request, f"Updated rubric '{rubric.title}'.")
            return redirect("rubrics:rubric_list")
    else:
        form = RubricForm(instance=rubric)
        formset = RubricCriterionFormSet(instance=rubric)

    context = {"form": form, "formset": formset, "rubric": rubric, "active_tab": "rubrics"}
    return render(request, "rubrics/rubric_form.html", context)