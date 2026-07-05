"""Report generation service (ReportLab).

Outputs: individual evaluation reports, judge summaries, competition
statistics — exportable as PDF/CSV.
"""


def generate_team_report(team_id: int) -> bytes:
    """Render a team's evaluation report as PDF bytes."""
    raise NotImplementedError("Pair 2 / Dev 5: ReportLab PDF export.")


def generate_competition_summary(competition_id: int) -> bytes:
    """Render whole-competition results (rankings, stats) as PDF bytes."""
    raise NotImplementedError
