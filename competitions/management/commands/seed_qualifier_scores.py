"""Carry the Hack The Box qualifier result into the Entry stage.

    python manage.py seed_qualifier_scores --dry-run
    python manage.py seed_qualifier_scores --confirm

Application Red files no proposal; its Entry mark is the qualifier score
(Entry Guide, and the Application Red track page). The qualifier runs on
Hack The Box Enterprise, so the result arrives as a scoreboard rather than
as anything the platform can read — hence this command.

**Scaling.** Raw qualifier points are scaled to the Entry stage's 30. By
default the top scorer sets the ceiling, so the best team gets 30 and
everyone else sits proportionally below. Pass `--max` to scale against a
fixed figure instead, which is fairer if a published qualifying mark
exists — say so and nobody has to explain why a team's mark moved because
somebody else did well.

Teams are matched on team name, case-insensitively, after stripping
spaces and punctuation: the name on the Hack The Box scoreboard and the
name typed into the platform are rarely byte-identical. Anything that does
not match is printed rather than skipped silently, because an unmatched
team is a team with no Entry mark and nobody goes looking for it.
"""
import csv
import re
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

from accounts.models import Team
from competitions.constants import Track
from competitions.models import Competition
from services import scoring_service

# The JengaSec x Hack The Box Red Side Qualifier final scoreboard,
# 11-12 October 2026. Transcribed from the published poster.
QUALIFIER_2026 = [
    ("pwners", 487.5), ("M4LW4R3", 375.0), ("Middle Child", 275.0),
    ("CSK 101", 275.0), ("k0mrads", 275.0), ("Zeph3r", 265.0),
    ("The destroyers", 235.0), ("Roothunt3rs", 205.0), ("0x1_Z3r0_D4y", 205.0),
    ("AsusGate", 145.0), ("Les_4d4p7r1ces", 145.0), ("FAST & FURIOUS", 145.0),
    ("cyberchaji", 145.0), ("pr0n00bs", 115.0), ("techwiz", 115.0),
    ("WR4TH", 115.0), ("Watt The Hack", 110.0), ("Jaguar", 100.0),
    ("Amadeus", 70.0), ("zero day", 70.0), ("Phantom Phish", 65.0),
    ("Druids", 20.0), ("Null", 0.0), ("Vaipers", 0.0),
    ("H45HCR4CK3R5", 0.0), ("Green Dot Target", 0.0), ("B3alth S3ciety", 0.0),
    ("nullsec_ke", 0.0), ("Byteshield", 0.0), ("Chmod 777", 0.0),
]


def normalise_name(name):
    """A team name reduced to what two spellings of it have in common."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


class Command(BaseCommand):
    help = "Award Entry marks to Application Red teams from the qualifier."

    def add_arguments(self, parser):
        parser.add_argument("--competition", type=int)
        parser.add_argument(
            "--csv", help="A CSV of team,points instead of the built-in scoreboard."
        )
        parser.add_argument(
            "--max", type=float,
            help="Points that earn full marks. Default: the highest score present.",
        )
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--confirm", action="store_true")

    def handle(self, *args, **options):
        competition = self._competition(options.get("competition"))
        scores = self._scores(options.get("csv"))
        ceiling = options.get("max") or max((p for _, p in scores), default=0)
        if ceiling <= 0:
            raise CommandError("Every score is zero; there is nothing to scale.")

        stage_weight = self._stage_weight()
        self.stdout.write(
            f"{competition.name}: {len(scores)} qualifier result(s), "
            f"{ceiling:g} points = {stage_weight} marks"
        )

        teams = {
            normalise_name(t.team_name): t
            for t in Team.objects.filter(
                competition=competition, team_type=Team.TeamType.RED,
                track=Track.APPLICATION,
            ).exclude(status=Team.Status.WITHDRAWN)
        }

        matched, missing = [], []
        for name, points in scores:
            team = teams.pop(normalise_name(name), None)
            marks = round(points / ceiling * stage_weight, 2)
            if team is None:
                missing.append((name, points, marks))
            else:
                matched.append((team, name, points, marks))

        for team, name, points, marks in matched:
            self.stdout.write(f"  {marks:>5.2f}  {team.team_name}  ({points:g} pts)")
        for name, points, marks in missing:
            self.stdout.write(self.style.WARNING(
                f"  ----   no registered team called '{name}' ({points:g} pts)"
            ))
        for team in teams.values():
            self.stdout.write(self.style.ERROR(
                f"  NONE   {team.team_name} is registered but not on the scoreboard"
            ))

        if not options["confirm"]:
            self.stdout.write("")
            self.stdout.write("Nothing written. Re-run with --confirm.")
            return

        for team, name, points, marks in matched:
            scoring_service.award(
                team, "entry", Decimal(str(marks)),
                source="entry",
                note=f"Hack The Box qualifier: {points:g} points.",
                # Keyed, so re-running after a correction replaces the mark
                # rather than awarding it twice.
                dedupe_key="qualifier",
            )
            scoring_service.recompute(team)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"Entry marks recorded for {len(matched)} team(s)."
        ))
        if missing or teams:
            self.stdout.write(self.style.WARNING(
                f"{len(missing)} scoreboard row(s) matched nothing and "
                f"{len(teams)} registered team(s) have no qualifier score. "
                f"Both lists are above; neither is scored."
            ))

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

    def _scores(self, path):
        if not path:
            return list(QUALIFIER_2026)
        rows = []
        with open(path, newline="", encoding="utf-8-sig") as handle:
            for line in csv.reader(handle):
                if len(line) < 2 or not line[0].strip():
                    continue
                try:
                    rows.append((line[0].strip(), float(line[1])))
                except ValueError:
                    continue  # a header row
        if not rows:
            raise CommandError(f"No team,points rows found in {path}.")
        return rows

    def _stage_weight(self):
        from competitions import grading

        return next(w for name, w in grading.SCORE_WEIGHTS if name == "Entry")
