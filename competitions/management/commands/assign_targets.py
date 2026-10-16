"""Give every Application Red team one application to attack, at random.

    python manage.py assign_targets --dry-run \\
        --app "Cipher squad=http://10.250.0.1:30002/" \\
        --app "The Box=http://10.250.0.1:30009/" \\
        --app "CPR=http://10.250.0.1:30010/"
    python manage.py assign_targets --confirm  (same --app lines)
    python manage.py assign_targets --confirm --activate

**Random, but evenly spread.** Red teams are shuffled and each is dealt to
whichever application has the fewest attackers so far, ties broken at random.
Ten Red teams on three applications become 4, 3 and 3 — never 10, 0 and 0.
`--seed` makes a draw repeatable, e.g. to show the room it was not rigged.

**The applications are named, not inferred.** `--app "Team name=URL"` (once
per application) says which Blue teams' applications exist and where they
are; the URL becomes each assignment's endpoint, shown on the Red team's
target page. Without `--app`, every approved Application Blue team counts,
with no endpoint.

**A Red team attacks one application only**, and an existing assignment is
kept on a rerun — a team already working on a target never has it swapped
underneath it. `--reassign` clears everything and draws again.

**Decoys are optional.** `--decoy URL` (once per decoy) gives every Red team
one decoy as well, spread evenly at random. The team's target page then lists
two URLs — the real one and the decoy — in an order drawn per team, so it
cannot tell which is which. Only this command's output says; keep it. A
finding filed against the decoy earns nothing. Without `--decoy`, nothing
changes.

**No cap by default**: with few applications every Red team still gets one.
`--per N` limits an application to N attackers if needed.

Nothing opens until `--activate`: an inactive assignment is visible to
organisers and invisible to the Red team.
"""
import random

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from accounts.models import Team
from competitions.constants import Track
from competitions.models import Competition, TargetAssignment


class Command(BaseCommand):
    help = "Assign each Application Red team one application to attack, at random."

    def add_arguments(self, parser):
        parser.add_argument("--competition", type=int)
        parser.add_argument(
            "--app", action="append", default=[], metavar="TEAM=URL",
            help='An application and where it is, e.g. "CPR=http://10.250.0.1:30010/". '
                 "Repeat for each. The team can be named or given by id.",
        )
        parser.add_argument(
            "--decoy", action="append", default=[], metavar="URL",
            help="OPTIONAL. A decoy's URL; repeat for each. Every Red team then also "
                 "gets one decoy, and its target page lists both URLs in random order.",
        )
        parser.add_argument(
            "--per", type=int, default=0,
            help="Most Red teams per application (default: no limit).",
        )
        parser.add_argument("--seed", type=int, help="Make the random draw repeatable.")
        parser.add_argument(
            "--activate", action="store_true",
            help="Open the engagement window on every assignment.",
        )
        parser.add_argument(
            "--reassign", action="store_true",
            help="Clear existing assignments and draw again from scratch.",
        )
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--confirm", action="store_true")

    def handle(self, *args, **options):
        competition = self._competition(options.get("competition"))
        apps = self._applications(competition, options["app"])  # [(team, url)]
        blue = [team for team, _ in apps]
        endpoint = {team.pk: url for team, url in apps}
        red = list(self._teams(competition, Team.TeamType.RED))
        if not blue:
            raise CommandError("No applications to attack — pass --app \"Team name=URL\".")
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

        decoys = [url.strip() for url in options["decoy"] if url.strip()]
        if decoys and not all(endpoint.values()):
            raise CommandError("--decoy needs a URL on every --app, or the real target "
                               "would be the only one missing from the list.")

        rng = random.Random(options["seed"])
        plan, load = self._deal(blue, red, already, options["per"], rng)
        decoy_of = self._deal_decoys(load, already, decoys, rng)

        self.stdout.write(
            f"{competition.name}: {len(red)} Red team(s), {len(blue)} application(s)"
            + (f", seed {options['seed']}" if options["seed"] is not None else "")
        )
        for team in blue:
            assigned = load.get(team.pk, [])
            where = endpoint.get(team.pk) or "no endpoint"
            self.stdout.write(f"  {team.team_name:<22} {len(assigned)} attacker(s)   {where}")
            for red_team in assigned:
                mark = "keeps" if red_team.pk in already else "new  "
                decoy = f"   + decoy {decoy_of[red_team.pk]}" if red_team.pk in decoy_of else ""
                self.stdout.write(f"      {mark}  {red_team.team_name}{decoy}")

        placed = {r.pk for group in load.values() for r in group}
        for red_team in red:
            if red_team.pk not in placed:
                kept = already.get(red_team.pk)
                why = (f"its current target {kept.target_team.team_name} is not one of the "
                       f"applications — use --reassign" if kept else
                       f"every application is full at {options['per']} — raise --per")
                self.stdout.write(self.style.ERROR(f"  NOT PLACED  {red_team.team_name} — {why}"))

        if not options["confirm"]:
            self.stdout.write("")
            self.stdout.write("Nothing written. Re-run with --confirm (and the same --app lines).")
            return

        made = 0
        for red_team, target in plan:
            _, created = TargetAssignment.objects.update_or_create(
                competition=competition, red_team=red_team, target_team=target,
                defaults={"is_active": options["activate"]},
            )
            made += 1 if created else 0
        # Endpoints follow the --app list, for kept assignments too. With a
        # decoy, both URLs in an order drawn per team, so position gives
        # nothing away; only this command's output says which is which.
        for team_pk, red_group in load.items():
            url = endpoint.get(team_pk)
            if not url:
                continue
            for red_team in red_group:
                urls = [url]
                if red_team.pk in decoy_of:
                    urls.append(decoy_of[red_team.pk])
                    rng.shuffle(urls)
                TargetAssignment.objects.filter(
                    competition=competition, red_team=red_team, target_team_id=team_pk
                ).update(endpoint="  ·  ".join(urls))
        if options["activate"]:
            TargetAssignment.objects.filter(competition=competition).update(is_active=True)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"{made} new assignment(s); {len(plan)} drawn this run."))
        self.stdout.write(
            "Engagement window OPEN: Red teams can now see their target and file findings."
            if options["activate"] else
            "Not visible to Red teams yet — rerun with --confirm --activate when the window opens."
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
            Team.objects.filter(competition=competition, team_type=side, track=Track.APPLICATION)
            .exclude(status=Team.Status.WITHDRAWN)
            .order_by("team_name")
        )

    def _applications(self, competition, specs):
        """[(blue team, url)] from --app, or every Application Blue team."""
        if not specs:
            return [(team, "") for team in self._teams(competition, Team.TeamType.BLUE)]
        blue = self._teams(competition, Team.TeamType.BLUE)
        apps = []
        for spec in specs:
            name, _, url = spec.partition("=")
            name, url = name.strip(), url.strip()
            match = blue.filter(Q(team_name__iexact=name) | Q(pk=int(name) if name.isdigit() else -1))
            if match.count() != 1:
                known = ", ".join(blue.values_list("team_name", flat=True)) or "none"
                raise CommandError(f'--app "{spec}": no single Application Blue team called '
                                   f'"{name}". Known: {known}.')
            team = match.get()
            if any(team.pk == t.pk for t, _ in apps):
                raise CommandError(f'--app names "{team.team_name}" twice.')
            apps.append((team, url))
        return apps

    def _deal_decoys(self, load, already, decoys, rng):
        """{red team pk: decoy URL} — one each, evenly spread, kept on reruns."""
        if not decoys:
            return {}
        counts = {url: 0 for url in decoys}
        decoy_of = {}
        reds = [r for group in load.values() for r in group]
        # Keep a decoy already shown to a team, if it is still in the list.
        for red_team in reds:
            kept = already.get(red_team.pk)
            for url in (kept.endpoint.split("  ·  ") if kept else []):
                if url in counts:
                    decoy_of[red_team.pk] = url
                    counts[url] += 1
        waiting = [r for r in reds if r.pk not in decoy_of]
        rng.shuffle(waiting)
        for red_team in waiting:
            fewest = min(counts.values())
            url = rng.choice(sorted(u for u, n in counts.items() if n == fewest))
            decoy_of[red_team.pk] = url
            counts[url] += 1
        return decoy_of

    def _deal(self, blue, red, already, per, rng):
        """(plan, load) — who attacks what, and how full each application is."""
        load = {team.pk: [] for team in blue}
        by_pk = {team.pk: team for team in blue}
        plan = []

        # Keep what exists: re-drawing a live engagement would hand a team a
        # target it has never seen, and throw away its work on the old one.
        for red_team in red:
            kept = already.get(red_team.pk)
            if kept and kept.target_team_id in load:
                load[kept.target_team_id].append(red_team)

        placed = {r.pk for group in load.values() for r in group}
        waiting = [r for r in red if r.pk not in placed and r.pk not in already]
        rng.shuffle(waiting)

        for red_team in waiting:
            open_apps = [pk for pk in load if not per or len(load[pk]) < per]
            if not open_apps:
                continue
            fewest = min(len(load[pk]) for pk in open_apps)
            target_pk = rng.choice(sorted(pk for pk in open_apps if len(load[pk]) == fewest))
            load[target_pk].append(red_team)
            plan.append((red_team, by_pk[target_pk]))
        return plan, load
