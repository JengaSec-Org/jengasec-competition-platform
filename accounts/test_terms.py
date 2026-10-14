"""Seeing and agreeing to the Participant Terms.

    python manage.py test accounts.test_terms

The rules that matter are legal ones, so they are pinned individually:

* the terms are a separate agreement from the code of conduct, and
  publishing one must not disturb the other;
* nothing optional is ever pre-ticked, and no tick implies another;
* a black lanyard and a portrait consent cannot both be true;
* "no tick, no entry" — an unaccepted member blocks the registration;
* somebody who has already agreed can change a choice without agreeing
  again, because clause 7.1 says a change never reduces a choice already
  made.
"""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from accounts.models import ParticipantConsent, PolicyVersion
from accounts.tests import make_competition, make_team, make_user
from services import account_service


def publish(kind, version, title):
    row = PolicyVersion.objects.create(
        kind=kind, version=version, title=title, body="The body of the document."
    )
    account_service.publish_policy(row)
    return row


class TwoDocumentsTests(TestCase):
    def test_each_kind_keeps_its_own_current_version(self):
        conduct = publish(PolicyVersion.Kind.CONDUCT, "1.0", "Code of Conduct")
        terms = publish(PolicyVersion.Kind.TERMS, "4.0", "Participant Terms")
        conduct.refresh_from_db()
        terms.refresh_from_db()
        self.assertTrue(conduct.is_current)
        self.assertTrue(terms.is_current)

    def test_publishing_terms_does_not_unpublish_the_conduct(self):
        """The old single-current constraint would have done exactly that."""
        conduct = publish(PolicyVersion.Kind.CONDUCT, "1.0", "Code of Conduct")
        publish(PolicyVersion.Kind.TERMS, "4.0", "Participant Terms")
        conduct.refresh_from_db()
        self.assertTrue(conduct.is_current)

    def test_a_new_version_replaces_only_its_own_kind(self):
        first = publish(PolicyVersion.Kind.TERMS, "3.0", "Participant Terms")
        conduct = publish(PolicyVersion.Kind.CONDUCT, "1.0", "Code of Conduct")
        publish(PolicyVersion.Kind.TERMS, "4.0", "Participant Terms")
        first.refresh_from_db()
        conduct.refresh_from_db()
        self.assertFalse(first.is_current)
        self.assertTrue(conduct.is_current)

    def test_current_defaults_to_the_conduct(self):
        """Every pre-existing caller means the conduct."""
        publish(PolicyVersion.Kind.CONDUCT, "1.0", "Code of Conduct")
        publish(PolicyVersion.Kind.TERMS, "4.0", "Participant Terms")
        self.assertEqual(PolicyVersion.current().kind, PolicyVersion.Kind.CONDUCT)

    def test_the_same_version_number_may_exist_for_both_kinds(self):
        publish(PolicyVersion.Kind.CONDUCT, "1.0", "Code of Conduct")
        publish(PolicyVersion.Kind.TERMS, "1.0", "Participant Terms")
        self.assertEqual(PolicyVersion.objects.filter(version="1.0").count(), 2)


class AgreeingTests(TestCase):
    def setUp(self):
        self.comp = make_competition()
        self.terms = publish(PolicyVersion.Kind.TERMS, "4.0", "Participant Terms")
        self.user = make_user("amina")
        self.client.force_login(self.user)
        self.url = reverse("accounts:terms")

    def post(self, **boxes):
        data = {"accept": "on"}
        data.update({k: "on" for k, v in boxes.items() if v})
        return self.client.post(self.url, data)

    def test_the_document_is_shown_in_full_not_behind_a_link(self):
        self.assertContains(self.client.get(self.url), "The body of the document.")

    def test_agreeing_records_the_acceptance(self):
        self.post()
        self.assertTrue(
            account_service.has_accepted_current_policy(
                self.user, PolicyVersion.Kind.TERMS
            )
        )

    def test_the_agreement_tick_is_required(self):
        self.client.post(self.url, {})
        self.assertFalse(
            account_service.has_accepted_current_policy(
                self.user, PolicyVersion.Kind.TERMS
            )
        )

    def test_nothing_optional_is_ticked_by_default(self):
        self.post()
        consent = ParticipantConsent.objects.get(user=self.user)
        for field, _, _ in ParticipantConsent.MEDIA_FIELDS + ParticipantConsent.RECRUITER_FIELDS:
            self.assertFalse(getattr(consent, field), field)
        self.assertFalse(consent.black_lanyard)

    def test_one_tick_does_not_imply_another(self):
        self.post(m1_name_and_placing=True)
        consent = ParticipantConsent.objects.get(user=self.user)
        self.assertTrue(consent.m1_name_and_placing)
        self.assertFalse(consent.m2_quotes)
        self.assertFalse(consent.m8_ncba)
        self.assertFalse(consent.r1_contact_details)

    def test_agreeing_to_the_terms_is_not_agreeing_to_the_conduct(self):
        publish(PolicyVersion.Kind.CONDUCT, "1.0", "Code of Conduct")
        self.post()
        self.assertTrue(account_service.has_accepted_current_policy(
            self.user, PolicyVersion.Kind.TERMS))
        self.assertFalse(account_service.has_accepted_current_policy(self.user))

    def test_the_version_agreed_to_is_recorded(self):
        self.post()
        self.assertEqual(
            ParticipantConsent.objects.get(user=self.user).version, self.terms
        )


class BlackLanyardTests(TestCase):
    def setUp(self):
        self.comp = make_competition()
        publish(PolicyVersion.Kind.TERMS, "4.0", "Participant Terms")
        self.user = make_user("juma")
        self.client.force_login(self.user)
        self.url = reverse("accounts:terms")

    def test_a_black_lanyard_is_recorded_and_named_for_the_desk(self):
        self.client.post(self.url, {"accept": "on", "black_lanyard": "on"})
        consent = ParticipantConsent.objects.get(user=self.user)
        self.assertTrue(consent.black_lanyard)
        self.assertEqual(consent.lanyard, "Black")

    def test_everyone_else_gets_a_standard_lanyard(self):
        self.client.post(self.url, {"accept": "on"})
        self.assertEqual(
            ParticipantConsent.objects.get(user=self.user).lanyard, "Standard"
        )

    def test_portraits_and_a_black_lanyard_are_refused_together(self):
        """Clause 5.4. Refused, not silently unticked."""
        response = self.client.post(
            self.url,
            {"accept": "on", "black_lanyard": "on", "m4_portraits": "on"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "not available with a black lanyard")
        self.assertFalse(ParticipantConsent.objects.filter(user=self.user).exists())

    def test_the_model_refuses_the_contradiction_too(self):
        """Whichever screen created it."""
        from django.core.exceptions import ValidationError

        consent = ParticipantConsent(
            user=self.user, black_lanyard=True, m4_portraits=True
        )
        with self.assertRaises(ValidationError):
            consent.clean()


class ChangingChoicesTests(TestCase):
    def setUp(self):
        self.comp = make_competition()
        publish(PolicyVersion.Kind.TERMS, "4.0", "Participant Terms")
        self.user = make_user("zawadi")
        self.client.force_login(self.user)
        self.url = reverse("accounts:terms")
        self.client.post(self.url, {"accept": "on"})

    def test_a_choice_can_be_added_without_agreeing_again(self):
        """Clause 7.1: a change never reduces a choice already made."""
        response = self.client.post(self.url, {"r4_shortlist": "on"})
        self.assertEqual(response.status_code, 302)
        consent = ParticipantConsent.objects.get(user=self.user)
        self.assertTrue(consent.r4_shortlist)
        self.assertTrue(
            account_service.has_accepted_current_policy(
                self.user, PolicyVersion.Kind.TERMS
            )
        )

    def test_a_choice_can_be_withdrawn(self):
        self.client.post(self.url, {"m1_name_and_placing": "on"})
        self.assertTrue(
            ParticipantConsent.objects.get(user=self.user).m1_name_and_placing
        )
        self.client.post(self.url, {})
        self.assertFalse(
            ParticipantConsent.objects.get(user=self.user).m1_name_and_placing
        )

    def test_only_one_consent_row_per_person(self):
        for _ in range(3):
            self.client.post(self.url, {"m2_quotes": "on"})
        self.assertEqual(
            ParticipantConsent.objects.filter(user=self.user).count(), 1
        )


class NoTickNoEntryTests(TestCase):
    """The terms must actually block, not only be displayed."""

    def setUp(self):
        self.comp = make_competition()
        publish(PolicyVersion.Kind.TERMS, "4.0", "Participant Terms")
        self.user = make_user("cap")
        self.team = make_team(self.comp, self.user, name="Simba")

    def outstanding(self):
        from notifications.management.commands.send_outstanding_digest import (
            outstanding_for_user,
        )

        return outstanding_for_user(self.user)

    def test_an_unagreed_member_is_listed_as_outstanding(self):
        self.assertTrue(
            any("Participant Terms" in item for item in self.outstanding())
        )

    def test_agreeing_clears_it(self):
        self.client.force_login(self.user)
        self.client.post(reverse("accounts:terms"), {"accept": "on"})
        self.assertFalse(
            any("Participant Terms" in item for item in self.outstanding())
        )

    def test_both_documents_are_chased_not_just_one(self):
        publish(PolicyVersion.Kind.CONDUCT, "1.0", "Code of Conduct")
        items = self.outstanding()
        self.assertTrue(any("Participant Terms" in i for i in items))
        self.assertTrue(any("Code of Conduct" in i for i in items))


class RegisterTests(TestCase):
    """What the check-in desk reads."""

    def setUp(self):
        self.comp = make_competition()
        publish(PolicyVersion.Kind.TERMS, "4.0", "Participant Terms")
        self.user = make_user("mwangi")
        make_team(self.comp, self.user, name="Simba")
        self.staff = User.objects.create_user(
            "desk", password="x", is_staff=True, is_superuser=True
        )

    def test_a_black_lanyard_shows_on_the_register(self):
        self.client.force_login(self.user)
        self.client.post(reverse("accounts:terms"),
                         {"accept": "on", "black_lanyard": "on"})
        self.client.force_login(self.staff)
        body = self.client.get(reverse("accounts:consent_register")).content.decode()
        self.assertIn("BLACK", body)

    def test_somebody_who_has_not_agreed_is_flagged_no_entry(self):
        self.client.force_login(self.staff)
        self.assertContains(
            self.client.get(reverse("accounts:consent_register")), "No entry"
        )

    def test_the_register_is_staff_only(self):
        self.client.force_login(self.user)
        self.assertNotEqual(
            self.client.get(reverse("accounts:consent_register")).status_code, 200
        )
