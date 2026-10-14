"""Enough data to actually exercise the live-competition pages.

    python manage.py seed_live_demo --dry-run
    python manage.py seed_live_demo --confirm

Three things block a walkthrough of the live days on a fresh database,
and none of them can be created from the team-facing site:

* **Cells.** A team with no `cell_id` has no namespace and no label, so
  the range pages have nothing to point at.
* **Target assignments.** `cyberrange.finding_new` answers 404 unless the
  red team has an active `TargetAssignment` — correctly, since a finding
  outside scope is not a finding. Without one there is no way to test the
  findings pipeline at all.
* **WireGuard profiles.** `/range/wireguard/` has nothing to offer, so
  the download path cannot be checked.

Refuses to touch a database that looks like production: this seeds demo
rows, and demo rows in front of 324 people is its own incident. Safe to
run twice — everything is get-or-create.
"""
from django.core.management.base import BaseCommand, CommandError
from django.core.files.base import ContentFile

from accounts.models import Team
from competitions.constants import Enterprise, Track
from competitions.models import Competition, TargetAssignment
from cyberrange.models import WireGuardProfile

# One /24 per team, one address per person (architecture section 5.2).
RED_RANGE = "10.60.{team}.{host}/32"
BLUE_RANGE = "10.61.{team}.{host}/32"


class Command(BaseCommand):
    help = "Seed cells, target assignments and WireGuard profiles for a live walkthrough."

    def add_arguments(self, parser):
        parser.add_argument("--competition", type=int)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--confirm", action="store_true")
        parser.add_argument(
            "--force", action="store_true",
            help="Seed even though the database looks like production.",
        )

    def handle(self, *args, **options):
        competition = self._competition(options.get("competition"))
        self._refuse_production(competition, options["force"])

        blue = list(
            Team.objects.filter(
                competition=competition, team_type=Team.TeamType.BLUE,
                track=Track.APPLICATION,
            ).exclude(status=Team.Status.WITHDRAWN).order_by("pk")
        )
        red = list(
            Team.objects.filter(
                competition=competition, team_type=Team.TeamType.RED
            ).exclude(status=Team.Status.WITHDRAWN).order_by("pk")
        )
        if not blue or not red:
            raise CommandError(
                f"Need at least one Application Blue team and one Red team; found "
                f"{len(blue)} and {len(red)}. Run seed_dev_data first."
            )

        plan = self._plan(blue, red)
        for line in plan["notes"]:
            self.stdout.write(f"  {line}")

        if not options["confirm"]:
            self.stdout.write("")
            self.stdout.write("Nothing written. Re-run with --confirm.")
            return

        self._apply(competition, plan)
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"Seeded {len(plan['cells'])} cell(s), {len(plan['targets'])} target "
            f"assignment(s), {len(plan['profiles'])} WireGuard profile(s)."
        ))
        self.stdout.write(
            "Set the phase to Live under Competitions to open Target Access, "
            "Live Status and the findings form."
        )

    # ------------------------------------------------------------------

    def _competition(self, pk):
        if pk:
            try:
                return Competition.objects.get(pk=pk)
            except Competition.DoesNotExist:
                raise CommandError(f"No competition with id {pk}.")
        competition = (
            Competition.objects.filter(
                status__in=(Competition.Status.OPEN, Competition.Status.IN_PROGRESS)
            ).order_by("-start_date").first()
        )
        if competition is None:
            raise CommandError("No open competition. Pass --competition <id>.")
        return competition

    def _refuse_production(self, competition, force):
        """Demo rows in front of the real field is its own incident."""
        if force:
            return
        real = Team.objects.filter(competition=competition).exclude(
            captain__email__endswith="@jengasec.test"
        ).count()
        if real > 10:
            raise CommandError(
                f"{real} teams here do not look like seed accounts, so this is "
                f"probably the real database. Seeding demo cells and target "
                f"assignments into it would put fake rows in front of the field. "
                f"Pass --force only if you are certain."
            )

    def _plan(self, blue, red):
        """What would change, without changing anything."""
        cells, targets, profiles, notes = [], [], [], []

        for index, team in enumerate(blue, start=1):
            if not team.cell_id:
                cells.append((team, Enterprise.A if index % 2 else Enterprise.B,
                              f"APP{index:02d}"))
                notes.append(f"cell      {team.team_name} -> APP{index:02d}")

        # The 1:3 rule, as far as the teams present allow: every red team is
        # pointed at a blue team, cycling if there are more red than blue.
        for index, team in enumerate(red):
            target = blue[index % len(blue)]
            targets.append((team, target))
            notes.append(f"target    {team.team_name} -> {target.team_name}")

        for kind, teams, template in (("red", red, RED_RANGE), ("blue", blue, BLUE_RANGE)):
            for number, team in enumerate(teams, start=1):
                people = [team.captain] + [
                    m.user for m in team.members.select_related("user") if m.user
                ]
                for host, user in enumerate({p.pk: p for p in people if p}.values(), start=10):
                    address = template.format(team=number, host=host)
                    profiles.append((user, team, address))
                    notes.append(f"wireguard {user.username} -> {address}")

        return {"cells": cells, "targets": targets, "profiles": profiles, "notes": notes}

    def _apply(self, competition, plan):
        for team, enterprise, cell in plan["cells"]:
            team.enterprise = enterprise
            team.cell_id = cell
            team.save(update_fields=["enterprise", "cell_id"])

        for red_team, target in plan["targets"]:
            TargetAssignment.objects.update_or_create(
                competition=competition, red_team=red_team, target_team=target,
                defaults={
                    "is_active": True,
                    "endpoint": f"http://10.108.0.{10 + target.pk % 80}",
                    "access_notes": "Demo assignment from seed_live_demo. "
                                    "Replace with the real allocation before the event.",
                },
            )

        for user, team, address in plan["profiles"]:
            profile, created = WireGuardProfile.objects.get_or_create(
                user=user, defaults={"team": team, "address": address}
            )
            if created or not profile.config:
                profile.team = team
                profile.address = address
                # A syntactically real config so the download path can be
                # exercised; the keys are obviously not usable.
                profile.config.save(
                    f"{user.username}.conf",
                    ContentFile(
                        "# JengaSec demo profile - seed_live_demo, not a working tunnel\n"
                        "[Interface]\n"
                        f"Address = {address}\n"
                        "PrivateKey = DEMO-PROFILE-NOT-A-REAL-KEY\n\n"
                        "[Peer]\n"
                        "PublicKey = DEMO-PROFILE-NOT-A-REAL-KEY\n"
                        "Endpoint = 10.109.0.1:51820\n"
                        "AllowedIPs = 10.108.0.0/24\n".encode()
                    ),
                    save=True,
                )
