"""Create the application catalogue blue teams propose against.

    python manage.py seed_application_briefs --competition 1
    python manage.py seed_application_briefs --competition 1 --cap 10

Enterprise A is the bank from the Final Competition Overview §5;
Enterprise B is the healthcare equivalent. Briefs are per-competition,
so this is a command rather than a data migration — run it once per
competition and edit the results in the admin.
"""
from django.core.management.base import BaseCommand, CommandError

from competitions.models import ApplicationBrief, Competition
from competitions.constants import Enterprise, Track

# code, name, summary, requirements
ENTERPRISE_A = [
    (
        "APP-A1",
        "Internet Banking",
        "Customer-facing web banking: accounts, transfers, statements.",
        "Session management\nTransaction authorisation\nStatement generation\nAudit logging",
    ),
    (
        "APP-A2",
        "Mobile Banking API",
        "The API layer the mobile client consumes.",
        "Token-based auth\nRate limiting\nRequest signing\nVersioned endpoints",
    ),
    (
        "APP-A3",
        "Customer Authentication Service",
        "Central authentication for every banking channel.",
        "MFA\nBrute-force protection\nSecure session handling\nAudit logging",
    ),
    (
        "APP-A4",
        "Payment Processing Service",
        "Moves money between accounts and to external rails.",
        "Idempotent transfers\nAuthorisation checks\nFraud signals\nReconciliation log",
    ),
    (
        "APP-A5",
        "Loan Application System",
        "Loan origination, scoring workflow and decisioning.",
        "Application workflow\nDocument handling\nDecision audit trail\nRole separation",
    ),
    (
        "APP-A6",
        "Internal Employee Portal",
        "Staff-facing portal onto internal banking systems.",
        "Role-based access control\nPrivileged action logging\nSSO integration",
    ),
    (
        "APP-A7",
        "Customer Support System",
        "Ticketing and customer servicing with access to account data.",
        "Least-privilege data access\nPII redaction\nTicket audit trail",
    ),
]

ENTERPRISE_B = [
    (
        "APP-B1",
        "Patient Portal",
        "Patient-facing access to records, appointments and results.",
        "Patient identity verification\nConsent handling\nRecord access logging",
    ),
    (
        "APP-B2",
        "Clinical Records API",
        "The API serving clinical records to internal systems.",
        "Fine-grained authorisation\nAudit trail\nData minimisation",
    ),
    (
        "APP-B3",
        "Appointment Scheduling",
        "Booking and clinician calendar management.",
        "Double-booking prevention\nNotification handling\nAccess control",
    ),
    (
        "APP-B4",
        "Prescription Service",
        "Prescribing workflow and pharmacy dispatch.",
        "Prescriber verification\nControlled-substance rules\nImmutable audit log",
    ),
    (
        "APP-B5",
        "Lab Results Pipeline",
        "Ingests laboratory results and routes them to clinicians.",
        "Result integrity\nCritical-result escalation\nChain of custody",
    ),
    (
        "APP-B6",
        "Staff Directory & Access",
        "Clinical staff identity, roles and system access.",
        "Role lifecycle\nJoiner/mover/leaver handling\nPrivileged access review",
    ),
    (
        "APP-B7",
        "Billing & Claims",
        "Patient billing and insurance claim submission.",
        "Claim validation\nPayment reconciliation\nFraud checks",
    ),
]


class Command(BaseCommand):
    help = "Seed the application briefs blue teams propose against."

    def add_arguments(self, parser):
        parser.add_argument(
            "--competition", type=int, required=True, help="Competition ID."
        )
        parser.add_argument(
            "--cap",
            type=int,
            default=20,
            help="Maximum proposals per application (default 20).",
        )

    def handle(self, *args, **options):
        try:
            competition = Competition.objects.get(pk=options["competition"])
        except Competition.DoesNotExist:
            raise CommandError(f"No competition with id {options['competition']}.")

        created = updated = 0
        for enterprise, briefs in (
            (Enterprise.A, ENTERPRISE_A),
            (Enterprise.B, ENTERPRISE_B),
        ):
            for code, name, summary, requirements in briefs:
                _, was_created = ApplicationBrief.objects.update_or_create(
                    competition=competition,
                    code=code,
                    defaults={
                        "name": name,
                        "enterprise": enterprise,
                        "track": Track.APPLICATION,
                        "summary": summary,
                        "requirements": requirements,
                        "proposal_cap": options["cap"],
                        "is_open": True,
                    },
                )
                created += was_created
                updated += not was_created

        self.stdout.write(
            self.style.SUCCESS(
                f"{competition.name}: {created} brief(s) created, {updated} updated "
                f"(cap {options['cap']} proposals each)."
            )
        )
