"""Seed the predefined attack scenarios.

Reference: Final Competition Overview §8 (controlled scenarios keep
scoring objective and reproducible) and §13 (point values).
"""
from django.db import migrations

SCENARIOS = [
    # code, track, name, objective, expected_evidence, success_condition, red, blue
    (
        "APP-01",
        "application",
        "Authentication Bypass",
        "Exploit an authentication weakness in the target application.",
        "Authentication bypass captured in application and HTTP logs.",
        "A protected endpoint is accessed without valid credentials.",
        20,
        -15,
    ),
    (
        "APP-02",
        "application",
        "Injection to Data Access",
        "Reach application data through an injection flaw.",
        "Database query log plus the originating HTTP request.",
        "Data outside the attacker's authorisation is returned.",
        20,
        -15,
    ),
    (
        "APP-03",
        "application",
        "Broken Access Control",
        "Act on another tenant's or user's resource.",
        "Application audit log showing cross-account action.",
        "An action succeeds against a resource the account does not own.",
        15,
        -10,
    ),
    (
        "CLOUD-03",
        "cloud",
        "Excessive IAM Privilege",
        "Obtain excessive IAM privilege in the cloud environment.",
        "Cloud IAM event trail for the escalation.",
        "A restricted resource is accessed with escalated privilege.",
        25,
        -20,
    ),
    (
        "CLOUD-04",
        "cloud",
        "Exposed Storage",
        "Locate and read an improperly exposed storage object.",
        "Storage access log entry for the object.",
        "A private object is read from outside the trust boundary.",
        20,
        -15,
    ),
    (
        "AI-02",
        "ai",
        "Defensive Agent Manipulation",
        "Cause the defensive agent to make an incorrect decision.",
        "Agent decision trace and the triggering input.",
        "The agent performs an unauthorised action.",
        30,
        -20,
    ),
    (
        "AI-03",
        "ai",
        "Agent Privilege Abuse",
        "Drive the defensive agent beyond its permitted action set.",
        "Agent action log showing an out-of-policy tool call.",
        "The agent invokes a tool or action outside its permitted scope.",
        25,
        -20,
    ),
]


def seed(apps, schema_editor):
    AttackScenario = apps.get_model("competitions", "AttackScenario")
    for (
        code,
        track,
        name,
        objective,
        expected_evidence,
        success_condition,
        red_points,
        blue_points,
    ) in SCENARIOS:
        AttackScenario.objects.get_or_create(
            code=code,
            defaults={
                "track": track,
                "name": name,
                "objective": objective,
                "expected_evidence": expected_evidence,
                "success_condition": success_condition,
                "red_points": red_points,
                "blue_points": blue_points,
                "is_active": True,
            },
        )


def unseed(apps, schema_editor):
    AttackScenario = apps.get_model("competitions", "AttackScenario")
    AttackScenario.objects.filter(code__in=[s[0] for s in SCENARIOS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("competitions", "0004_attackscenario_competitionobjective_targetassignment"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
