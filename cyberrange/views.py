"""Findings (Red teams → judges) and WireGuard downloads.

Participants see only their own team's findings and their own profile.
Staff and judges see every finding. Routes that a user may not use return
404 rather than 403, as elsewhere on the site, so they stay invisible.
"""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.models import Team
from accounts.roles import JUDGE, user_in_role
from audit.models import AuditLog
from competitions.models import TargetAssignment
from services import stored_files
from services.audit_service import record as audit
from services.submission_service import teams_for

from .forms import FindingForm
from .models import ROUND_CHOICES, Finding, WireGuardProfile


def _is_reviewer(user):
    return user.is_active and (user.is_staff or user.is_superuser or user_in_role(user, JUDGE))


def _red_team_for(user):
    """The approved Red team this user is on, or None."""
    return (
        teams_for(user)
        .filter(team_type=Team.TeamType.RED, status=Team.Status.APPROVED)
        .first()
    )


def _active_scope(red_team):
    """Target team pks this Red team may report against right now."""
    return list(
        TargetAssignment.objects.filter(red_team=red_team, is_active=True).values_list(
            "target_team_id", flat=True
        )
    )


def _can_view(user, finding):
    if _is_reviewer(user):
        return True
    return teams_for(user).filter(pk=finding.red_team_id).exists()


# ------------------------------------------------------------------ findings


@login_required
def finding_list(request):
    reviewer = _is_reviewer(request.user)
    red_team = None if reviewer else _red_team_for(request.user)
    if not reviewer and red_team is None:
        raise Http404("Not found")

    findings = Finding.objects.select_related("red_team", "target_team")
    if reviewer:
        round_filter = request.GET.get("round", "")
        status_filter = request.GET.get("status", "")
        if round_filter.isdigit():
            findings = findings.filter(round=int(round_filter))
        if status_filter in Finding.Status.values:
            findings = findings.filter(status=status_filter)
    else:
        findings = findings.filter(red_team=red_team)
        round_filter = status_filter = ""

    return render(request, "cyberrange/finding_list.html", {
        "findings": findings,
        "reviewer": reviewer,
        "red_team": red_team,
        "has_scope": bool(red_team and _active_scope(red_team)),
        "rounds": ROUND_CHOICES,
        "statuses": Finding.Status.choices,
        "round_filter": round_filter,
        "status_filter": status_filter,
    })


@login_required
def finding_new(request):
    red_team = _red_team_for(request.user)
    if red_team is None:
        raise Http404("Not found")
    in_scope = _active_scope(red_team)

    form = FindingForm(
        request.POST or None, request.FILES or None, red_team=red_team, in_scope=in_scope
    )
    if request.method == "POST" and form.is_valid():
        finding = form.save(commit=False)
        finding.competition = red_team.competition
        finding.red_team = red_team
        finding.submitted_by = request.user
        finding.save()
        audit(
            AuditLog.Action.FINDING_SUBMITTED,
            request=request,
            target=finding,
            description=f"Round {finding.round}: {finding.title}",
            target_cell=finding.target_team.cell_label,
        )
        messages.success(request, "Finding submitted. The findings desk will review it.")
        return redirect("cyberrange:finding_detail", pk=finding.pk)

    return render(request, "cyberrange/finding_form.html", {
        "form": form, "red_team": red_team, "has_scope": bool(in_scope),
    })


class ReviewForm(forms.ModelForm):
    class Meta:
        model = Finding
        fields = ["status", "severity", "duplicate_of", "reviewer_notes"]
        widgets = {"reviewer_notes": forms.Textarea(attrs={"rows": 3, "class": "textarea"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # A duplicate can only point at an earlier report against the same cell. "Earlier" is
        # insertion order (pk), not created_at: two reports can share a timestamp.
        self.fields["duplicate_of"].queryset = Finding.objects.filter(
            target_team=self.instance.target_team, pk__lt=self.instance.pk
        )
        for name in ("status", "severity", "duplicate_of"):
            self.fields[name].widget.attrs.setdefault("class", "input")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("status") == Finding.Status.DUPLICATE and not cleaned.get("duplicate_of"):
            self.add_error("duplicate_of", "Say which earlier finding this duplicates.")
        return cleaned


@login_required
def finding_detail(request, pk):
    finding = get_object_or_404(
        Finding.objects.select_related("red_team", "target_team", "duplicate_of"), pk=pk
    )
    if not _can_view(request.user, finding):
        raise Http404("Not found")

    review_form = None
    if _is_reviewer(request.user):
        review_form = ReviewForm(request.POST or None, instance=finding)
        if request.method == "POST" and review_form.is_valid():
            review_form.save()
            audit(
                AuditLog.Action.FINDING_REVIEWED,
                request=request,
                target=finding,
                description=f"{finding.get_status_display()} / {finding.severity or 'no severity'}",
            )
            messages.success(request, "Review saved.")
            return redirect("cyberrange:finding_detail", pk=finding.pk)

    return render(request, "cyberrange/finding_detail.html", {
        "finding": finding, "review_form": review_form,
    })


@login_required
def finding_evidence(request, pk):
    finding = get_object_or_404(Finding, pk=pk)
    if not finding.evidence_file or not _can_view(request.user, finding):
        raise Http404("Not found")
    return stored_files.serve(request, finding.evidence_file, label="evidence file")


# ----------------------------------------------------------------- WireGuard


@login_required
def wireguard(request):
    profile = WireGuardProfile.objects.filter(user=request.user).first()
    return render(request, "cyberrange/wireguard.html", {"profile": profile})


@login_required
def wireguard_download(request):
    """The owner's own configuration file -- and nobody else's, staff included.

    Staff who need a copy regenerate it with NET's script; the private key
    is not something an organiser account should be able to pull by URL.
    """
    profile = WireGuardProfile.objects.filter(user=request.user, revoked=False).first()
    if profile is None or not profile.config:
        raise Http404("Not found")
    profile.downloaded_at = timezone.now()
    profile.save(update_fields=["downloaded_at"])
    audit(AuditLog.Action.WIREGUARD_DOWNLOADED, request=request, target=profile)
    return stored_files.serve(
        request,
        profile.config,
        filename=f"jengasec-{request.user.username}.conf",
        label="WireGuard configuration",
    )
