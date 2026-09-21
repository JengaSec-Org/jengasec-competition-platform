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
        "Session lifetime, invalidation and secure cookie attributes\n"
        "Cooling-off period or step-up authentication before a newly added beneficiary can receive funds\n"
        "Defence against injection, XSS, CSRF, IDOR and open redirect\n"
        "Rate limiting and lockout on all authentication and money-movement paths\n"
        "Statements generated server-side with authorisation checked at generation, not at link time\n"
        "A full audit trail the customer can retrieve",
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
        "Device registration and binding so a token issued to one device cannot be replayed from another\n"
        "Short-lived access tokens with refresh-token rotation and reuse detection\n"
        "Transaction signing or step-up confirmation independent of the session token\n"
        "Idempotency keys on every state-changing endpoint\n"
        "Strict schema validation that rejects unknown fields rather than ignoring them\n"
        "Per-device and per-account rate limiting\n"
        "No sensitive data in URLs, logs or error messages",
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
        "Memory-hard password hashing with per-user salt and a breached-password check\n"
        "MFA with at least two second factors, plus recovery codes issued once and stored hashed\n"
        "Signed short-lived tokens with audience and issuer claims, key rotation, and a published JWKS endpoint\n"
        "Token revocation and introspection so a compromised session ends enterprise-wide within seconds\n"
        "Password reset that does not disclose account existence, uses single-use time-limited tokens and invalidates all sessions\n"
        "Progressive lockout resistant to targeted brute force and credential-stuffing sprays\n"
        "Risk signals on every authentication (new device, new location, impossible travel, unusual hour), emitted even when allowed\n"
        "A tamper-evident audit log of every identity event",
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
        "Double-entry ledger where balance is derived from entries, never from a mutable field\n"
        "Atomic, isolated transfer execution provably safe under concurrent requests\n"
        "Idempotency on every payment instruction with a defined retention window\n"
        "Per-transaction, daily and velocity limits evaluated server-side\n"
        "Rules-based fraud check producing allow, review or block with a recorded reason, and a reviewable queue\n"
        "Maker-checker approval above a defined threshold where the approver may not be the initiator\n"
        "Reversals and refunds as compensating entries, never as mutation of history\n"
        "Append-only transaction log with independently verifiable sequence integrity\n"
        "End-of-day reconciliation that proves the ledger balances",
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
        "Stage transitions enforced server-side and impossible to skip by manipulating client state\n"
        "Document upload with content-type verified by inspection, size limits, malware scanning, private-bucket storage\n"
        "Documents served only through authorised, short-lived, single-use links with non-guessable references\n"
        "Deterministic credit-scoring component with a recorded input snapshot so any decision can be reproduced\n"
        "Segregation of duties between applicant, assessor and approver\n"
        "Approval authority matrix by amount, enforced in code, with an escalation path\n"
        "Disbursement issued only after approval, signed, idempotent, and impossible to trigger from the applicant's session\n"
        "Defined retention and deletion for the personal and financial documents collected",
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
        "RBAC with at least four staff roles whose permissions genuinely differ, enforced server-side for every action\n"
        "Maker-checker on every high-impact action, with a different and more senior checker\n"
        "Mandatory structured reason codes on every privileged action\n"
        "Just-in-time elevation, time-boxed and automatically revoked\n"
        "Data minimisation by role, masked account and identity numbers, unmasking as a separately audited action\n"
        "Administrative access restricted by network origin\n"
        "Session controls for a shared workstation: short idle timeout, explicit lock, concurrent-session limits\n"
        "Tamper-evident audit log viewable by an auditor role that cannot modify customer data\n"
        "Detection and alerting on insider-abuse patterns: bulk access, out-of-hours activity, repeated access to one customer, self-servicing",
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
        "Ticket lifecycle with an immutable conversation thread\n"
        "Two-sided messaging where internal notes are provably invisible to the customer\n"
        "Attachment upload from both sides with type verification by inspection, size limits, malware scanning, non-executing rendering\n"
        "Structured customer identity verification before any account-affecting action, with method and outcome recorded\n"
        "Output encoding everywhere customer-supplied text is rendered, including the agent interface\n"
        "Rate limiting on ticket and message creation\n"
        "Anything touching money or credentials handed to the back-office portal rather than performed here\n"
        "Detection and masking at ingestion of credentials or card numbers pasted into a ticket",
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
        "Scoring that consumes the payments ledger rather than duplicating it\n"
        "Analyst RBAC and case-level authorisation\n"
        "Tamper-evident case history where decisions cannot be rewritten\n"
        "Customer notification that does not itself disclose account detail\n"
        "An analyst feedback loop the customer cannot poison, and integrity protection on the rule set",
        "Score evasion through transaction shaping\n"
        "Case tampering and suppression of alerts\n"
        "Poisoning of the feedback loop",
        True,
    ),
    (
        "APP09",
        "Notifications and Statements Service",
        "Outbound email, SMS and push, plus server-generated account statements. Optional brief.",
        "Authorisation checked at generation time, not at link time\n"
        "Short-lived signed document links that are not guessable or enumerable\n"
        "Template rendering safe against injection\n"
        "Data minimisation in every message body\n"
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
