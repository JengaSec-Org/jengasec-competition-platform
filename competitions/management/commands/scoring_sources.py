"""Operator issuance for canonical targets, labelled cases and producer access."""
import json
import os
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from competitions.models import AttackScenario, Competition, CompetitionObjective, ScenarioVersion, ScoringProducer
from cyberrange.models import ActionScope, RangeApplication
from services import scoring_identity as identity, scoring_scenarios as scenarios


class Command(BaseCommand):
    help = "Issue scoring sources and scope with an explicit organiser identity."

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="operation", required=True)
        sync = sub.add_parser("sync")
        sync.add_argument("--competition", type=int, required=True)
        for name in ("register-target", "grant-actions", "register-producer", "freeze-scenario", "issue-case", "exclude", "revoke-producer"):
            command = sub.add_parser(name)
            command.add_argument("--competition", type=int, required=True)
            command.add_argument("--actor", required=True, help="Active organiser username; never a competitor.")
            if name == "register-target":
                command.add_argument("--file", required=True, help="Bounded JSON target declaration.")
            elif name == "grant-actions":
                command.add_argument("--team", required=True)
                command.add_argument("--target", required=True)
                command.add_argument("--actions", required=True)
                command.add_argument("--source-cidr", action="append", default=[])
            elif name == "register-producer":
                command.add_argument("--kind", choices=["runner", "verifier", "gateway", "competitor", "deployment"], required=True)
                command.add_argument("--team")
                command.add_argument("--until", required=True)
                command.add_argument("--credential-file", required=True)
            elif name == "freeze-scenario":
                command.add_argument("--scenario", required=True)
                command.add_argument("--version", type=int, required=True)
                command.add_argument("--label", choices=["attack", "legitimate"], required=True)
                command.add_argument("--family", choices=sorted(scenarios.FAMILIES), required=True)
                command.add_argument("--assertions-file", required=True)
            elif name == "issue-case":
                command.add_argument("--objective", type=int, required=True)
                command.add_argument("--scenario-version", type=int, required=True)
                command.add_argument("--target", required=True)
                command.add_argument("--starts-at", required=True)
                command.add_argument("--deadline-at", required=True)
                command.add_argument("--opportunity-key", required=True)
                command.add_argument("--red-team")
                command.add_argument("--defender-team")
                command.add_argument("--environment", choices=["competition", "rehearsal"], default="competition")
                command.add_argument("--producer", action="append", help="Explicit producer UUIDs when more than one service qualifies.")
            elif name == "exclude":
                command.add_argument("--start", required=True)
                command.add_argument("--end", required=True)
                command.add_argument("--incident", required=True)
            elif name == "revoke-producer":
                command.add_argument("--producer", required=True)

    @staticmethod
    def _json_file(path):
        file = Path(path)
        if file.stat().st_size > 65536:
            raise CommandError("Input JSON is larger than 64 KiB.")
        with file.open(encoding="utf-8") as stream:
            return json.load(stream)

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            competition = Competition.objects.get(pk=options["competition"])
            if options["operation"] == "sync":
                state = identity.refresh_identity(competition)
                self.stdout.write(f"Identity revision {state.revision} refreshed.")
                return
            actor = get_user_model().objects.get(username=options["actor"])
            scenarios._staff(actor)
            operation = options["operation"]
            if operation == "register-target":
                row = self._json_file(options["file"])
                required = {"team", "service_name", "ip_address", "port"}
                if not isinstance(row, dict) or not required.issubset(row) or set(row) - required - {"protocol", "frozen_digest", "enabled"}:
                    raise CommandError("Target requires team, service_name, ip_address and port; optional protocol, frozen_digest, enabled.")
                team = identity.resolve_team(competition, row["team"])
                enterprise = identity._enterprise(team)
                digest = row.get("frozen_digest", "")
                if team.track == "ai" and not identity.DIGEST.fullmatch(digest):
                    raise CommandError("An AI target requires its accepted immutable artifact digest.")
                if team.track == "application" and digest:
                    raise CommandError("Application digests come from the authenticated deployment and freeze workflow.")
                target, _ = RangeApplication.objects.update_or_create(
                    competition=competition, application_instance_id=f"{enterprise}/{team.cell_id}",
                    defaults=dict(team=team, enterprise=enterprise, application=team.cell_id,
                        namespace=team.namespace, service_name=row["service_name"], ip_address=row["ip_address"],
                        port=row["port"], protocol=row.get("protocol", "tcp"), status="live",
                        optional=team.cell_id in {"APP08", "APP09"}, enabled=row.get("enabled", True),
                        image_digest=digest, verified_at=None),
                )
                self.stdout.write(f"Registered {target.application_instance_id}; actual artifact confirmation is required for eligibility.")
            elif operation == "grant-actions":
                team = identity.resolve_team(competition, options["team"])
                target = RangeApplication.objects.get(competition=competition, application_instance_id=options["target"])
                permission, _ = ActionScope.objects.update_or_create(competition=competition, team=team, target=target,
                    defaults=dict(allowed_actions=options["actions"].split(","), source_cidrs=options["source_cidr"],
                                  active=True, approved_by=actor, approved_at=timezone.now()))
                self.stdout.write(f"Action scope {permission.pk} issued.")
            elif operation == "register-producer":
                team = identity.resolve_team(competition, options["team"]) if options.get("team") else None
                producer, token = scenarios.register_producer(competition, kind=options["kind"], team=team,
                    valid_until=scenarios._timestamp(options["until"], "until"), registered_by=actor)
                credential = json.dumps({"producer_id": str(producer.producer_id), "token": token, "competition": competition.pk}) + "\n"
                # Exclusive creation prevents following a symlink or replacing
                # another credential. Never emit the token to console output.
                fd = os.open(options["credential_file"], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as stream:
                        stream.write(credential)
                        stream.flush()
                        os.fsync(stream.fileno())
                except BaseException:
                    Path(options["credential_file"]).unlink(missing_ok=True)
                    raise
                self.stdout.write(f"Producer {producer.producer_id} registered; credential written to the requested private file.")
            elif operation == "freeze-scenario":
                scenario = AttackScenario.objects.get(code=options["scenario"])
                version = scenarios.freeze_scenario(scenario, version=options["version"], label=options["label"],
                    family=options["family"], assertions=self._json_file(options["assertions_file"]), issued_by=actor)
                self.stdout.write(f"Scenario version {version.pk} issued.")
            elif operation == "issue-case":
                case = scenarios.issue_case(competition,
                    objective=CompetitionObjective.objects.select_related("team").get(pk=options["objective"]),
                    scenario_version=ScenarioVersion.objects.select_related("scenario").get(pk=options["scenario_version"]),
                    target_id=options["target"], starts_at=scenarios._timestamp(options["starts_at"], "starts_at"),
                    deadline_at=scenarios._timestamp(options["deadline_at"], "deadline_at"),
                    opportunity_key=options["opportunity_key"], issued_by=actor,
                    actor_team=identity.resolve_team(competition, options["red_team"]) if options.get("red_team") else None,
                    defender_team=identity.resolve_team(competition, options["defender_team"]) if options.get("defender_team") else None,
                    environment=options["environment"], required_producer_ids=options["producer"])
                self.stdout.write(f"Case {case.case_id} issued.")
            elif operation == "exclude":
                exclusion = scenarios.approve_exclusion(competition, start=scenarios._timestamp(options["start"], "start"),
                    end=scenarios._timestamp(options["end"], "end"), incident_reference=options["incident"], approved_by=actor)
                self.stdout.write(f"Infrastructure exclusion {exclusion.pk} recorded.")
            elif operation == "revoke-producer":
                producer = ScoringProducer.objects.get(competition=competition, producer_id=options["producer"])
                producer.revoked_at = timezone.now()
                producer.save(update_fields=["revoked_at"])
                self.stdout.write(f"Producer {producer.producer_id} revoked.")
        except (Competition.DoesNotExist, get_user_model().DoesNotExist, RangeApplication.DoesNotExist,
                AttackScenario.DoesNotExist, ScenarioVersion.DoesNotExist, CompetitionObjective.DoesNotExist,
                ScoringProducer.DoesNotExist, identity.IdentityError, scenarios.ScenarioError,
                ValidationError, OSError, ValueError, TypeError) as exc:
            raise CommandError(str(exc)) from exc
