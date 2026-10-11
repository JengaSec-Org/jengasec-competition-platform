from django.conf import settings
from django.contrib.auth import views as auth_views
from django.urls import path, register_converter, reverse_lazy

from competitions.constants import EDITION

from . import views


def password_reset_email_context():
    """Everything the branded email layout needs that Django does not supply.

    PasswordResetView renders its templates itself, so the layout's
    context has to be handed over here rather than built in
    services.email_render like every other message.
    """
    from services.email_render import asset_url

    hours = settings.PASSWORD_RESET_TIMEOUT // 3600
    return {
        "site_url": settings.SITE_URL.rstrip("/"),
        "edition": EDITION,
        "kicker": "Password reset",
        "action_label": "Set a new password",
        "timeout_hours": hours,
        "notification": {"subject": "Reset your JengaSec password"},
        "blocks": [
            {"kind": "text",
             "text": "Someone asked to reset the password for your JengaSec account."},
            {"kind": "text",
             "text": f"The link below can be used once and expires in {hours} hours."},
        ],
        "note": ("If you did not ask for this, ignore this message — your password "
                 "is unchanged and nothing else will be sent."),
        "preheader": "Set a new password for your JengaSec account.",
        "logo_url": asset_url("images/logo.png"),
        "club_logo_url": asset_url("logo/scsc-logo.png"),
    }

app_name = "accounts"

from .converters import TokenConverter

# Named "token" so routes read `<token:token>`.
register_converter(TokenConverter, "token")

urlpatterns = [
    # Public competitor sign-up: Team Member or Captain, never staff.
    path("register/", views.register, name="register"),
    # Email verification (Guide s.6 step 1): the account is inert until done.
    path("verify/", views.verify_pending, name="verify_pending"),
    path("verify/resend/", views.resend_verification, name="resend_verification"),
    path("verify/<token:token>/", views.verify_email, name="verify_email"),
    # Code of conduct (Guide s.6 step 4)
    path("conduct/", views.conduct, name="conduct"),
    path("choose-role/", views.choose_role, name="choose_role"),
    path("team-form/", views.team_form, name="team_form"),
    # Members wait for their captain's invitation here (Guide s.6 step 7).
    path("choose-team/", views.choose_team, name="choose_team"),
    # Matching: the unassigned declare a side, captains fill the empty seats.
    path("find-team/", views.find_team, name="find_team"),
    path("recruit/", views.recruit, name="recruit"),
    path("invitations/<token:token>/", views.invitation_open, name="invitation_open"),
    path(
        "invitations/<token:token>/<str:decision>/",
        views.invitation_respond,
        name="invitation_respond",
    ),
    # My Team (captain: invite, remove, mark specialists; member: read-only)
    path("team-requests/", views.team_requests, name="team_requests"),
    # Captains correcting their own team: institution, track, brief.
    path("team-requests/edit/", views.team_edit, name="team_edit"),
    path("team-requests/submit/", views.registration_submit, name="registration_submit"),
    path("invitations/revoke/<int:pk>/", views.invitation_revoke, name="invitation_revoke"),
    path(
        "team-members/<int:member_id>/remove/",
        views.team_member_remove,
        name="team_member_remove",
    ),
    path(
        "team-members/<int:member_id>/specialist/",
        views.member_toggle_specialist,
        name="member_toggle_specialist",
    ),
    path(
        "team-members/<int:member_id>/deputy/",
        views.member_toggle_deputy,
        name="member_toggle_deputy",
    ),
    # Own profile
    path("profile/", views.profile, name="profile"),
    # Password reset (login/logout live in config.urls). The confirm URL is
    # also how staff-issued accounts set their first password.
    path(
        "password-reset/",
        auth_views.PasswordResetView.as_view(
            template_name="accounts/forgot_password.html",
            subject_template_name="accounts/password_reset_subject.txt",
            email_template_name="accounts/password_reset_email.txt",
            html_email_template_name="accounts/password_reset_email.html",
            extra_email_context=password_reset_email_context(),
            success_url=reverse_lazy("accounts:password_reset_done"),
        ),
        name="password_reset",
    ),
    path(
        "password-reset/sent/",
        auth_views.PasswordResetDoneView.as_view(
            template_name="accounts/password_reset_done.html"
        ),
        name="password_reset_done",
    ),
    path(
        "password-reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="accounts/password_reset_confirm.html",
            success_url=reverse_lazy("accounts:password_reset_complete"),
        ),
        name="password_reset_confirm",
    ),
    path(
        "password-reset/done/",
        auth_views.PasswordResetCompleteView.as_view(
            template_name="accounts/password_reset_complete.html"
        ),
        name="password_reset_complete",
    ),
    # User management (staff only). Admin / Judge / Partner accounts are
    # issued here, never through the public register form.
    path("users/", views.user_list, name="user_list"),
    path("users/new/", views.user_create, name="user_create"),
    path("users/<int:pk>/edit/", views.user_edit, name="user_edit"),
    path("users/<int:pk>/identity/", views.identity_document, name="identity_document"),
    # Staff-only deployment diagnostic: what store this process writes to.
    path("storage/", views.storage_status, name="storage_status"),
    # Who has agreed to the privacy notice and the code of conduct.
    path("consent/", views.consent_register, name="consent_register"),
    path("consent/export.csv", views.consent_export, name="consent_export"),
    path("roles/", views.role_list, name="role_list"),
    path("teams/", views.team_list, name="team_list"),
    # Rosters: one row per seat, filtered and exportable.
    path("members/", views.member_list, name="member_list"),
    path("members/export.csv", views.member_export, name="member_export"),
    path("teams/<int:pk>/", views.team_detail, name="team_detail"),
    path("teams/<int:pk>/decide/", views.team_decide, name="team_decide"),
    path("teams/<int:pk>/welcome-pack/", views.team_welcome_pack, name="team_welcome_pack"),
]
