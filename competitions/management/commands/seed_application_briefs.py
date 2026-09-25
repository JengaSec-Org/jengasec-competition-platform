"""Create the JengaBank application catalogue teams propose against.

    python manage.py seed_application_briefs --competition 1
    python manage.py seed_application_briefs --competition 1 --cap 10

Entry Guide section 5. Both enterprises run the JengaBank architecture,
so the same nine briefs are seeded into ENTA and ENTB under the same
codes -- every brief is built twice, by two different teams. Seven are
core; Fraud Monitoring and Notifications & Statements are optional.
Briefs are per-competition, so this is a command rather than a data
migration -- run it once per competition and edit the results in the
admin. Re-running updates in place.
"""
from django.core.management.base import BaseCommand, CommandError

from competitions.constants import Enterprise, Track
from competitions.models import ApplicationBrief, Competition

# code, name, summary, requirements ("security it must get right"),
# red_exposure ("what Red will try"), optional
BRIEFS = [
    (
        "APP01",
        "Internet Banking Portal",
        "The customer web front door — balances, transaction history, statements, transfers and profile.",
        "Server-side authorisation on every request\n"
        "Session lifetime, invalidation and secure cookies\n"
        "Step-up authentication before a new beneficiary is paid\n"
        "Injection, XSS, CSRF and IDOR defence, with rate limiting",
        "Authentication bypass, session fixation and hijacking\n"
        "Horizontal privilege escalation between customers\n"
        "Business-logic abuse of the beneficiary and transfer flow\n"
        "Forced browsing to unlinked endpoints",
        False,
    ),
    (
        "APP02",
        "Mobile Banking API",
        "The same bank for a client that cannot be trusted to enforce anything, on a device that may be compromised.",
        "Device binding, so a token cannot be replayed elsewhere\n"
        "Short-lived tokens with refresh rotation and reuse detection\n"
        "Transaction signing independent of the session token\n"
        "Idempotency and strict schema validation on every write",
        "Token theft and replay across devices\n"
        "Refresh-token abuse\n"
        "Idempotency and race-condition attacks on transfers\n"
        "Mass assignment and API enumeration for unprotected endpoints",
        False,
    ),
    (
        "APP03",
        "Customer Authentication and Identity Service",
        "The identity provider every other cell trusts. The most security-dense brief in the catalogue.",
        "Memory-hard password hashing and a breached-password check\n"
        "MFA with two second factors, plus hashed recovery codes\n"
        "Signed short-lived tokens, key rotation, enterprise-wide revocation\n"
        "Non-disclosing password reset and progressive lockout\n"
        "Risk signals and a tamper-evident log on every identity event",
        "Credential stuffing\n"
        "User enumeration through timing or message differences\n"
        "MFA bypass through flow manipulation\n"
        "Token forgery and algorithm confusion\n"
        "Password-reset token prediction or reuse\n"
        "Race conditions in enrolment",
        False,
    ),
    (
        "APP04",
        "Payment Processing Service",
        "The service that actually moves money — the ledger, the rules that govern a transfer, and the controls that stop money moving when it should not.",
        "Double-entry ledger; balance derived, never a mutable field\n"
        "Atomic transfers, provably safe under concurrent requests\n"
        "Idempotency, server-side limits, and a fraud allow/review/block\n"
        "Maker-checker above a threshold; reversals as new entries\n"
        "Append-only log that reconciles at end of day",
        "Race conditions and TOCTOU attacks on balance\n"
        "Negative and precision-abuse amounts; currency and rounding manipulation\n"
        "Replay of payment instructions\n"
        "Limit bypass through decomposition\n"
        "Forging or replaying the settlement webhook",
        False,
    ),
    (
        "APP05",
        "Loan Origination System",
        "Application to decision — a multi-stage workflow with document upload, automated scoring, human approval and a disbursement instruction.",
        "Stage transitions enforced server-side, impossible to skip\n"
        "Uploads verified by inspection, scanned, privately stored\n"
        "Reproducible scoring with a recorded input snapshot\n"
        "Segregation of duties and an approval matrix by amount",
        "Workflow-stage skipping\n"
        "IDOR across applicants\n"
        "Malicious file upload and path traversal\n"
        "Tampering with declared income or score inputs\n"
        "Privilege escalation from applicant to assessor or approver",
        False,
    ),
    (
        "APP06",
        "Internal Back-Office Portal",
        "The staff-facing system where tellers, supervisors and administrators service customer accounts. The highest-privilege application in the enterprise.",
        "Four staff roles whose permissions genuinely differ\n"
        "Maker-checker with a senior checker, and reason codes\n"
        "Just-in-time elevation, masked data, restricted admin origin\n"
        "Tamper-evident audit log and insider-abuse detection",
        "Privilege escalation between staff roles\n"
        "Forced browsing to administrative functions\n"
        "Maker-checker bypass through self-approval or replay\n"
        "Mass data extraction through search and export\n"
        "Abuse of elevation flows",
        False,
    ),
    (
        "APP07",
        "Customer Support and Ticketing System",
        "Free text and file attachments moving between two parties at very different privilege levels, with identity verification under pressure.",
        "Immutable thread; internal notes invisible to the customer\n"
        "Attachments verified by inspection, scanned, rendered inertly\n"
        "Identity verification before any account-affecting action\n"
        "Output encoding everywhere, including the agent interface",
        "Stored XSS aimed at agents\n"
        "IDOR across tickets\n"
        "Leakage of internal notes into the customer view\n"
        "Malicious attachments\n"
        "Social-engineering flows that trick the system into treating an unverified caller as verified",
        False,
    ),
    (
        "APP08",
        "Fraud and Transaction Monitoring Service",
        "Near-real-time scoring of transactions from the payments service, with case management for analysts and a feedback loop. Optional brief.",
        "Scores the payments ledger rather than duplicating it\n"
        "Analyst RBAC and case-level authorisation\n"
        "Tamper-evident case history; decisions cannot be rewritten\n"
        "A feedback loop the customer cannot poison",
        "Score evasion through transaction shaping\n"
        "Case tampering and suppression of alerts\n"
        "Poisoning of the feedback loop",
        True,
    ),
    (
        "APP09",
        "Notifications and Statements Service",
        "Outbound email, SMS and push, plus server-generated account statements. Optional brief.",
        "Authorisation checked at generation, not at link time\n"
        "Short-lived signed links that cannot be guessed or enumerated\n"
        "Injection-safe templates and data minimisation in every message\n"
        "Rate limiting, and a full delivery audit trail",
        "Account enumeration through notification behaviour\n"
        "Statement link prediction\n"
        "Template injection\n"
        "Notification flooding used as cover for other activity",
        True,
    ),
]


class Command(BaseCommand):
    help = "Seed the nine JengaBank application briefs into both enterprises."

    def add_arguments(self, parser):
        parser.add_argument("--competition", type=int, required=True, help="Competition id.")
        parser.add_argument(
            "--cap", type=int, default=10,
            help="Maximum proposals per brief (default 10, per the Entry Guide).",
        )

    def handle(self, *args, **options):
        try:
            competition = Competition.objects.get(pk=options["competition"])
        except Competition.DoesNotExist:
            raise CommandError(f"No competition with id {options['competition']}.")

        created = updated = 0
        for enterprise, _label in Enterprise.choices:
            for code, name, summary, requirements, red_exposure, optional in BRIEFS:
                _, was_created = ApplicationBrief.objects.update_or_create(
                    competition=competition,
                    enterprise=enterprise,
                    code=code,
                    defaults={
                        "name": name,
                        "track": Track.APPLICATION,
                        "summary": summary,
                        "requirements": requirements,
                        "red_exposure": red_exposure,
                        "is_optional": optional,
                        "proposal_cap": options["cap"],
                        "is_open": True,
                    },
                )
                created += was_created
                updated += not was_created

        # Legacy rows from the earlier catalogue (APP-A1 ... / healthcare) close.
        stale = ApplicationBrief.objects.filter(competition=competition).exclude(
            code__in=[b[0] for b in BRIEFS]
        )
        closed = stale.update(is_open=False)

        self.stdout.write(
            self.style.SUCCESS(
                f"{competition.name}: {created} brief(s) created, {updated} updated, "
                f"{closed} legacy brief(s) closed (cap {options['cap']} proposals each)."
            )
        )
