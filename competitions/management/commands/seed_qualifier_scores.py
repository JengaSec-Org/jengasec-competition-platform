"""Carry the Hack The Box qualifier result into the Entry stage.

    python manage.py seed_qualifier_scores --dry-run
    python manage.py seed_qualifier_scores --confirm

Application Red files no proposal; its Entry mark is the qualifier score
(Entry Guide, and the Application Red track page). The qualifier runs on
Hack The Box Enterprise, so the result arrives as a scoreboard rather than
as anything the platform can read — hence this command.

**Scaling.** `--max` is the points that earn the full Entry stage: 360 by
default, so 360 points is 30 marks and 180 is 15. A fixed figure rather
than a curve, so a team's mark never moves because somebody else did
well, and anyone can check it with a calculator.

**Above the mark is bonus.** A team that scored beyond `--max` keeps the
excess as bonus marks rather than having it clipped at 30 — finishing far
ahead should count for something. Bonus sits outside the hundred, so a
total can read 104/100. `--no-bonus` turns that off and simply caps at 30.

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
            "--max", type=float, default=360.0,
            help="Points that earn the full Entry stage (default 360).",
        )
        parser.add_argument(
            "--no-bonus", action="store_true",
            help="Cap at the Entry stage instead of carrying the excess as bonus.",
        )
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--confirm", action="store_true")

    def handle(self, *args, **options):
        competition = self._competition(options.get("competition"))
        scores = self._scores(options.get("csv"))
        ceiling = options.get("max") or 360.0
        if ceiling <= 0:
            raise CommandError("--max must be above zero.")

        stage_weight = self._stage_weight()
        bonus_on = not options["no_bonus"]
        self.stdout.write(
            f"{competition.name}: {len(scores)} qualifier result(s), "
            f"{ceiling:g} points = {stage_weight} marks"
            f"{'; above that is bonus' if bonus_on else '; capped at the stage'}"
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
            earned = points / ceiling * stage_weight
            marks = round(min(earned, stage_weight), 2)
            bonus = round(earned - stage_weight, 2) if (bonus_on and earned > stage_weight) else 0.0
            if team is None:
                missing.append((name, points, marks, bonus))
            else:
                matched.append((team, name, points, marks, bonus))

        for team, name, points, marks, bonus in matched:
            tail = f" +{bonus:g} bonus" if bonus else ""
            self.stdout.write(
                f"  {marks:>5.2f}{tail:<12}  {team.team_name}  ({points:g} pts)"
            )
        for name, points, marks, bonus in missing:
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

        from competitions import grading

        for team, name, points, marks, bonus in matched:
            scoring_service.award(
                team, "entry", Decimal(str(marks)),
                source="entry",
                note=f"Hack The Box qualifier: {points:g} points.",
                # Keyed, so re-running after a correction replaces the mark
                # rather than awarding it twice.
                dedupe_key="qualifier",
            )
            # Always written, even at zero, so lowering --max on a re-run
            # removes a bonus a team no longer earns.
            scoring_service.award(
                team, grading.BONUS_STAGE, Decimal(str(bonus)),
                source="entry",
                note=(
                    f"Qualifier bonus: {points:g} points, "
                    f"{ceiling:g} earns the full stage."
                ) if bonus else "No qualifier bonus.",
                dedupe_key="qualifier_bonus",
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
