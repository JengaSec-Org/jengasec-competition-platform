"""Give every Application Red team the application it may attack.

    python manage.py assign_targets --dry-run
    python manage.py assign_targets --confirm
    python manage.py assign_targets --confirm --activate

The architecture asks for three Red teams per application (the 1:3 rule).
Turnout rarely divides that neatly, so this spreads whatever turned up as
evenly as it can: with nineteen Red teams and eight applications, five get
three and three get two, rather than six getting three and the last one
getting one.

**Strength is dealt round-robin.** Red teams are sorted by their qualifier
mark and dealt out one at a time, so the three strongest land on three
different applications. Dealing them in blocks would put the top three on
one Blue team and the bottom three on another, which decides the result
before anyone connects.

**A Red team attacks one application only.** That is the scope rule the
gateway enforces, so a team already assigned is never given a second.

Nothing opens until `--activate`: an assignment with `is_active` false is
visible to organisers and invisible to the Red team, which is what lets
the whole allocation be prepared in advance and switched on when the
window opens.
"""
from django.core.management.base import BaseCommand, CommandError

from accounts.models import Team
from competitions.constants import Track
from competitions.models import Competition, TargetAssignment


class Command(BaseCommand):
    help = "Assign Application Red teams to the applications they may attack."

    def add_arguments(self, parser):
        parser.add_argument("--competition", type=int)
        parser.add_argument(
            "--per", type=int, default=3,
            help="Most Red teams per application (default 3, the 1:3 rule).",
        )
        parser.add_argument(
            "--activate", action="store_true",
            help="Open the engagement window on every assignment.",
        )
        parser.add_argument(
            "--reassign", action="store_true",
            help="Clear existing assignments and deal again from scratch.",
        )
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--confirm", action="store_true")

    def handle(self, *args, **options):
        competition = self._competition(options.get("competition"))

        blue = list(self._teams(competition, Team.TeamType.BLUE))
        red = list(self._teams(competition, Team.TeamType.RED))
        if not blue:
            raise CommandError("No Application Blue teams to attack.")
        if not red:
            raise CommandError("No Application Red teams to assign.")

        if options["reassign"]:
            existing = TargetAssignment.objects.filter(competition=competition)
            self.stdout.write(f"Clearing {existing.count()} existing assignment(s).")
            if options["confirm"]:
                existing.delete()
            already = {}
        else:
            already = {
                a.red_team_id: a
                for a in TargetAssignment.objects.filter(competition=competition)
                .select_related("target_team")
            }

        plan, load = self._deal(
            blue, red, already, options["per"], self._strength_map(competition)
        )

        self.stdout.write(
            f"{competition.name}: {len(red)} Red team(s), {len(blue)} application(s)"
        )
        for team in blue:
            assigned = load.get(team.pk, [])
            label = team.cell_label or team.team_name
            self.stdout.write(f"  {label:<22} {len(assigned)} attacker(s)")
            for red_team in assigned:
                mark = "keeps" if red_team.pk in already else "new  "
                self.stdout.write(f"      {mark}  {red_team.team_name}")

        unplaced = [r for r in red if r.pk not in {t.pk for t in sum(load.values(), [])}]
        for red_team in unplaced:
            self.stdout.write(self.style.ERROR(
                f"  NOT PLACED  {red_team.team_name} — every application is full at "
                f"{options['per']}. Raise --per or add an application."
            ))

        if not options["confirm"]:
            self.stdout.write("")
            self.stdout.write("Nothing written. Re-run with --confirm.")
            return

        made = 0
        for red_team, target in plan:
            _, created = TargetAssignment.objects.update_or_create(
                competition=competition, red_team=red_team, target_team=target,
                defaults={"is_active": options["activate"]},
            )
            made += 1 if created else 0
        if options["activate"]:
            TargetAssignment.objects.filter(competition=competition).update(is_active=True)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"{made} new assignment(s); {len(plan)} in total."
        ))
        self.stdout.write(
            "Endpoints and access notes are per assignment — set them in Django "
            "admin, or let the range push them."
            if not options["activate"] else
            "Engagement window OPEN: Red teams can now see their targets and file "
            "findings against them."
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

    def _teams(self, competition, side):
        return (
            Team.objects.filter(
                competition=competition, team_type=side, track=Track.APPLICATION
            )
            .exclude(status=Team.Status.WITHDRAWN)
            .order_by("enterprise", "cell_id", "team_name")
        )

    def _strength_map(self, competition):
        """Qualifier marks by team, for dealing strong teams apart.

        One query rather than a `select_related`: a team has one score per
        competition, so the reverse accessor is a manager, not an object.
        """
        from scoring.models import TeamScore

        return {
            row.team_id: float(row.entry + row.bonus)
            for row in TeamScore.objects.filter(competition=competition)
        }

    def _deal(self, blue, red, already, per, strength=None):
        """(plan, load) — who attacks what, and how full each application is."""
        load = {team.pk: [] for team in blue}
        by_pk = {team.pk: team for team in blue}
        plan = []

        # Keep what exists: re-dealing a live engagement would hand a team a
        # target it has already been working against.
        for red_team in red:
            kept = already.get(red_team.pk)
            if kept and kept.target_team_id in load:
                load[kept.target_team_id].append(red_team)

        strength = strength or {}
        placed = {r.pk for group in load.values() for r in group}
        waiting = sorted(
            (r for r in red if r.pk not in placed),
            key=lambda t: strength.get(t.pk, 0.0), reverse=True,
        )

        for red_team in waiting:
            # The emptiest application wins, ties broken by cell so the
            # result is the same every run.
            options = [
                pk for pk in load
                if len(load[pk]) < per
            ]
            if not options:
                continue
            target_pk = min(
                options,
                key=lambda pk: (len(load[pk]), by_pk[pk].cell_id or "", by_pk[pk].pk),
            )
            load[target_pk].append(red_team)
            plan.append((red_team, by_pk[target_pk]))
        return plan, load
