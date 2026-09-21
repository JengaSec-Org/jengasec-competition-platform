"""Forms for user profiles, teams, and team members."""
import datetime

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.utils import timezone

from competitions.constants import Enterprise
from competitions.models import ApplicationBrief, Competition

from .institutions import recognise
from .models import PolicyVersion, Team, TeamMember, UserProfile

# Identity document rules (Guide section 6, step 2).
IDENTITY_MAX_BYTES = 5 * 1024 * 1024
IDENTITY_TYPES = {
    ".pdf": (b"%PDF-",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
}


class UserProfileForm(forms.ModelForm):
    """Assign a platform role. Saving syncs the matching auth Group."""

    class Meta:
        model = UserProfile
        fields = ["role", "institution"]


class UserRegistrationForm(UserCreationForm):
    """Self-registration / admin user creation.

    Role is assigned separately by an admin, so new accounts land on the
    "role not assigned" page until approved.
    """

    first_name = forms.CharField(max_length=100)
    last_name = forms.CharField(max_length=100)
    email = forms.EmailField(max_length=254)

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email"]

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    @property
    def institution_hint(self):
        """For the template: what the typed address would be recognised as."""
        email = self.data.get("email", "")
        institutional, name = recognise(email)
        return name or ("recognised institution" if institutional else "")


class ResendVerificationForm(forms.Form):
    email = forms.EmailField(max_length=254, label="Email address")


class ConductAcceptanceForm(forms.Form):
    """One tick: I have read and accept this version."""

    accept = forms.BooleanField(
        required=True,
        label="I have read the code of conduct and agree to abide by it.",
        error_messages={"required": "You must accept the code of conduct to continue."},
    )

    def __init__(self, *args, version=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.version = version or PolicyVersion.current()


class TeamForm(forms.ModelForm):
    class Meta:
        model = Team
        fields = [
            "competition",
            "team_name",
            "track",
            "captain",
            "institution",
            "status",
        ]


class TeamMemberForm(forms.ModelForm):
    class Meta:
        model = TeamMember
        fields = ["student_name", "email", "role", "user"]


# Inline formset: edit a team's roster alongside the team itself.
TeamMemberFormSet = forms.inlineformset_factory(
    Team, TeamMember, form=TeamMemberForm, extra=3, can_delete=True
)


class TeamCreateForm(forms.ModelForm):
    """Captain registers a team (Entry Guide section 6, step 5).

    Side and specialisation give one of the four tracks. Application
    teams pick the JengaBank brief they intend to build; the same brief
    exists in both enterprises, so the picker shows it once per
    enterprise. The captain's institution is recorded as the lead
    institution.
    """

    class Meta:
        model = Team
        fields = [
            "competition",
            "team_name",
            "team_type",
            "track",
            "application_brief",
            "institution",
        ]
        widgets = {"team_type": forms.RadioSelect, "track": forms.RadioSelect}
        labels = {
            "team_type": "Which side are you competing on?",
            "track": "Specialisation",
            "application_brief": "JengaBank application (Application track only)",
            "institution": "Lead institution",
        }
        help_texts = {
            "team_name": "Three to forty characters.",
            "track": "Application Blue and AI Defence Blue submit a proposal. "
                     "AI Red submits a proposal; Application Red goes through "
                     "challenges from 1 October.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["competition"].queryset = Competition.objects.filter(
            status__in=[
                Competition.Status.DRAFT,
                Competition.Status.OPEN,
                Competition.Status.IN_PROGRESS,
            ]
        ).order_by("-start_date")
        self.fields["application_brief"].queryset = (
            ApplicationBrief.objects.filter(is_open=True)
            .select_related("competition")
            .order_by("enterprise", "code")
        )
        self.fields["application_brief"].required = False
        self.fields["application_brief"].empty_label = "— choose a brief —"
        self.fields["institution"].required = False

    def clean_team_name(self):
        name = self.cleaned_data["team_name"].strip()
        if not 3 <= len(name) <= 40:
            raise forms.ValidationError("Team names are three to forty characters.")
        return name

    def clean(self):
        data = super().clean()
        track = data.get("track")
        brief = data.get("application_brief")
        side = data.get("team_type")
        if track == Team.Track.APPLICATION and side == Team.TeamType.BLUE and not brief:
            self.add_error("application_brief", "Choose the JengaBank application you will build.")
        if brief and data.get("competition") and brief.competition_id != data["competition"].pk:
            self.add_error("application_brief", "That brief belongs to a different competition.")
        if track != Team.Track.APPLICATION:
            data["application_brief"] = None
        return data


class InviteForm(forms.Form):
    """Captain invites one address (Guide section 6, step 6)."""

    email = forms.EmailField(max_length=254, label="Member's email address")
    as_reserve = forms.BooleanField(required=False, label="Invite as the reserve")


class StaffUserCreateForm(forms.ModelForm):
    """Staff issues an Admin / Judge / Partner (or team) account.

    No password field on purpose: the account is created with an unusable
    password and the person sets their own through the emailed link. That
    link is the "separate URL" the spec asks for -- staff never see or
    transmit a password.
    """

    role = forms.ChoiceField(choices=UserProfile.Role.choices, label="Platform role")
    institution = forms.CharField(max_length=160, required=False)

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("first_name", "last_name", "email"):
            self.fields[name].required = True

    def clean_email(self):
        email = self.cleaned_data["email"]
        qs = User.objects.filter(email__iexact=email)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email


class StaffUserEditForm(StaffUserCreateForm):
    """Same fields, plus the switch that locks an account without deleting it."""

    is_active = forms.BooleanField(required=False, label="Account active")

    class Meta(StaffUserCreateForm.Meta):
        fields = ["username", "first_name", "last_name", "email"]


class ProfileForm(forms.ModelForm):
    """What a person may change about themselves. Role is not on the list.

    Profile completion (Guide section 6, step 2): everything a competitor
    has to supply before their team's registration can be submitted. The
    fields are optional *here* so a half-finished profile can be saved;
    the team page lists what is still missing.
    """

    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    email = forms.EmailField(max_length=254)

    class Meta:
        model = UserProfile
        fields = [
            "institution",
            "date_of_birth",
            "phone",
            "student_number",
            "programme",
            "year_of_study",
            "repo_handle",
            "identity_document",
        ]
        widgets = {"date_of_birth": forms.DateInput(attrs={"type": "date"})}
        labels = {
            "repo_handle": "Repository handle",
            "identity_document": "Student or national ID",
            "year_of_study": "Year of study",
        }
        help_texts = {
            "date_of_birth": "Competitors must be 18 to 25 on the registration date.",
            "identity_document": "PDF, JPG or PNG, up to 5 MB. Seen only by organisers.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        user = self.instance.user
        self.fields["first_name"].initial = user.first_name
        self.fields["last_name"].initial = user.last_name
        self.fields["email"].initial = user.email

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exclude(pk=self.instance.user_id).exists():
            raise forms.ValidationError("That email belongs to another account.")
        return email

    def clean_date_of_birth(self):
        dob = self.cleaned_data.get("date_of_birth")
        if dob is None:
            return dob
        today = timezone.localdate()
        if dob > today:
            raise forms.ValidationError("That date is in the future.")
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        if age > UserProfile.MAX_AGE + 1 or age < UserProfile.MIN_AGE - 1:
            # Saved anyway -- the age rule is enforced on the team checklist
            # against the registration date -- but flagged now so nobody
            # discovers it at submission.
            self.add_error(
                None,
                f"Note: competitors must be {UserProfile.MIN_AGE} to "
                f"{UserProfile.MAX_AGE} on the registration date.",
            )
        return dob

    def clean_year_of_study(self):
        year = self.cleaned_data.get("year_of_study")
        if year is not None and not 1 <= year <= 7:
            raise forms.ValidationError("Year of study is 1 to 7.")
        return year

    def clean_identity_document(self):
        upload = self.cleaned_data.get("identity_document")
        if not upload or not hasattr(upload, "read"):
            return upload  # unchanged existing file, or cleared
        name = upload.name.lower()
        ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
        if ext not in IDENTITY_TYPES:
            raise forms.ValidationError("The ID must be a PDF, JPG or PNG.")
        if upload.size > IDENTITY_MAX_BYTES:
            raise forms.ValidationError("The ID must be 5 MB or smaller.")
        head = upload.read(16)
        upload.seek(0)
        if not any(head.startswith(sig) for sig in IDENTITY_TYPES[ext]):
            raise forms.ValidationError(
                f"That file is not a valid {ext[1:].upper()} -- its contents do not match."
            )
        return upload

    def save(self, commit=True):
        profile = super().save(commit=False)
        user = profile.user
        user.first_name = self.cleaned_data["first_name"]
        user.last_name = self.cleaned_data["last_name"]
        new_email = self.cleaned_data["email"]
        email_changed = (user.email or "").lower() != new_email.lower()
        user.email = new_email
        if "identity_document" in self.changed_data and profile.identity_document:
            profile.identity_uploaded_at = timezone.now()
        if email_changed:
            # A changed address is an unproven one.
            profile.email_verified_at = None
            institutional, name = recognise(new_email)
            profile.manual_verification_required = not institutional
            profile.manual_verified_at = None
            if name and not self.cleaned_data.get("institution"):
                profile.institution = name
        if commit:
            user.save(update_fields=["first_name", "last_name", "email"])
            profile.save()
        self.email_changed = email_changed
        return profile


class RegistrationSubmitForm(forms.Form):
    """The captain's final step (Guide section 6, step 9).

    Ranked track preferences within their side, an optional enterprise
    preference, the skills declaration, and the optional mentor.
    """

    first_track = forms.ChoiceField(label="First-choice track")
    second_track = forms.ChoiceField(label="Second-choice track", required=False)
    enterprise_preference = forms.ChoiceField(
        choices=[("", "— no preference —")] + list(Enterprise.choices),
        required=False,
        label="Enterprise preference",
        help_text="Optional. Organisers balance the two enterprises; a preference is not a guarantee.",
    )
    skills_declaration = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 8}),
        label="Skills declaration",
        help_text=(
            f"{Team.SKILLS_MIN} to {Team.SKILLS_MAX} characters: the languages, "
            f"frameworks, security tooling and prior work the team brings."
        ),
    )
    mentor_name = forms.CharField(max_length=150, required=False, label="Mentor name")
    mentor_email = forms.EmailField(max_length=254, required=False, label="Mentor email")
    mentor_phone = forms.CharField(max_length=16, required=False, label="Mentor phone")
    mentor_institution = forms.CharField(max_length=200, required=False, label="Mentor institution")
    confirm = forms.BooleanField(
        label="I confirm the details are accurate and understand the registration is locked once submitted.",
        error_messages={"required": "Tick the confirmation to submit."},
    )

    def __init__(self, *args, team, **kwargs):
        super().__init__(*args, **kwargs)
        from services.team_service import tracks_for_side

        self.team = team
        tracks = tracks_for_side(team.team_type)
        self.fields["first_track"].choices = tracks
        self.fields["second_track"].choices = [("", "— none —")] + tracks
        if not self.is_bound:
            prefs = team.track_preferences or ([team.track] if team.track else [])
            self.initial.update(
                first_track=prefs[0] if prefs else "",
                second_track=prefs[1] if len(prefs) > 1 else "",
                enterprise_preference=team.enterprise_preference,
                skills_declaration=team.skills_declaration,
                mentor_name=team.mentor_name,
                mentor_email=team.mentor_email,
                mentor_phone=team.mentor_phone,
                mentor_institution=team.mentor_institution,
            )

    def clean_skills_declaration(self):
        text = self.cleaned_data["skills_declaration"].strip()
        if not Team.SKILLS_MIN <= len(text) <= Team.SKILLS_MAX:
            raise forms.ValidationError(
                f"{len(text)} characters; the declaration must be "
                f"{Team.SKILLS_MIN} to {Team.SKILLS_MAX}."
            )
        return text

    def clean(self):
        data = super().clean()
        first, second = data.get("first_track"), data.get("second_track")
        if first and second and first == second:
            self.add_error("second_track", "Second choice must differ from the first.")
        if data.get("mentor_email") and not data.get("mentor_name"):
            self.add_error("mentor_name", "Give the mentor's name as well.")
        return data

    @property
    def track_preferences(self):
        prefs = [self.cleaned_data.get("first_track")]
        if self.cleaned_data.get("second_track"):
            prefs.append(self.cleaned_data["second_track"])
        return [p for p in prefs if p]

    @property
    def mentor(self):
        return {
            key: self.cleaned_data.get(f"mentor_{key}", "")
            for key in ("name", "email", "phone", "institution")
        }


class WelcomePackForm(forms.ModelForm):
    """Organisers record the access issued to an approved team (step 12)."""

    class Meta:
        model = Team
        fields = ["repository_url", "namespace"]
        labels = {"repository_url": "Repository URL"}


# Guide section 6: the reasons a registration is turned down. Organisers
# tick the ones that apply; the captain receives them verbatim plus any
# free-text detail.
REJECTION_REASONS = [
    ("roster_size", "Roster is outside three to four members (plus one optional reserve)."),
    ("no_specialist", "No member is identified as the team's security specialist."),
    ("age", "One or more members are outside the 18-25 age band on the registration date."),
    ("identity", "An identity document is missing, unreadable, or does not match the account."),
    ("institution", "Student status could not be confirmed for one or more members."),
    ("duplicate", "A member is already registered with another team or on the other side."),
    ("skills", "The skills declaration does not show the team can deliver the track chosen."),
    ("brief", "The JengaBank brief chosen is closed or does not match the track."),
    ("conduct", "Not every member has accepted the current code of conduct."),
    ("contact", "The captain or deputy has no working phone number."),
]


class TeamDecisionForm(forms.Form):
    """Organiser outcome for a registration: approve or reject.

    There is no waitlist (Guide section 4): places are awarded at proposal
    selection, and the reserve named then is the only fallback.
    """

    DECISIONS = (
        (Team.Status.APPROVED, "Approve registration"),
        (Team.Status.REJECTED, "Reject"),
    )
    decision = forms.ChoiceField(choices=DECISIONS)
    reasons = forms.MultipleChoiceField(
        choices=REJECTION_REASONS,
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Rejection reasons (Guide section 6)",
    )
    enterprise = forms.ChoiceField(
        choices=[("", "— auto —")] + list(Enterprise.choices), required=False,
        help_text="Leave on auto unless you are placing the team deliberately.",
    )
    cell_id = forms.CharField(
        max_length=20, required=False, label="Cell override (e.g. APPRED02)",
        help_text="Only Application Red teams get a cell at registration; "
                  "everyone else is placed when their proposal is selected.",
    )
    rejection_reason = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 4}),
        required=False,
        help_text="Sent to the captain verbatim. Say what to correct.",
    )

    def clean(self):
        data = super().clean()
        if data.get("decision") == Team.Status.REJECTED:
            labels = dict(REJECTION_REASONS)
            picked = [labels[r] for r in data.get("reasons", []) if r in labels]
            detail = data.get("rejection_reason", "").strip()
            if not picked and not detail:
                self.add_error("rejection_reason", "A rejection must say why: tick a reason or write one.")
            data["rejection_reason"] = "\n".join(picked + ([detail] if detail else []))
        return data
