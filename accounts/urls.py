from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from . import views

app_name = "accounts"

urlpatterns = [
    # Public competitor sign-up: Team Member or Captain, never staff.
    path("register/", views.register, name="register"),
    # Email verification (Guide s.6 step 1): the account is inert until done.
    path("verify/", views.verify_pending, name="verify_pending"),
    path("verify/resend/", views.resend_verification, name="resend_verification"),
    path("verify/<str:token>/", views.verify_email, name="verify_email"),
    # Code of conduct (Guide s.6 step 4)
    path("conduct/", views.conduct, name="conduct"),
    path("choose-role/", views.choose_role, name="choose_role"),
    path("team-form/", views.team_form, name="team_form"),
    # Members wait for their captain's invitation here (Guide s.6 step 7).
    path("choose-team/", views.choose_team, name="choose_team"),
    path("invitations/<str:token>/", views.invitation_open, name="invitation_open"),
    path(
        "invitations/<str:token>/<str:decision>/",
        views.invitation_respond,
        name="invitation_respond",
    ),
    # My Team (captain: invite, remove, mark specialists; member: read-only)
    path("team-requests/", views.team_requests, name="team_requests"),
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
            email_template_name="accounts/password_reset_email.txt",
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
    path("roles/", views.role_list, name="role_list"),
    path("teams/", views.team_list, name="team_list"),
    path("teams/<int:pk>/", views.team_detail, name="team_detail"),
    path("teams/<int:pk>/decide/", views.team_decide, name="team_decide"),
    path("teams/<int:pk>/welcome-pack/", views.team_welcome_pack, name="team_welcome_pack"),
]
