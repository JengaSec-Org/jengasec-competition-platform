"""Tests for the deploy agent.

    cd tools/deploy-agent && python3 -m unittest -v

Standalone, because the agent runs on Server 3 and not inside Django.
What is tested is the part that decides things -- which registry, which
namespace, what the cluster is told, and which failures are the team's to
fix. The parts that shell out to docker and kubectl are deliberately thin
wrappers and are exercised by `--once --no-deploy` against a real image.
"""
import json
import os
import tempfile
import unittest

import agent


SUBMISSION = {
    "id": 41,
    "enterprise": "a",
    "cell": "APP01",
    "team": "JS26-B-014",
    "image_reference": "ghcr.io/amina-dev/internet-banking-portal:v3",
    "registry_username": "amina-dev",
    "pull_token": "ghp_not_a_real_token",
    "port": 8080,
    "health_path": "/healthz",
    "needs_database": True,
    "env_vars": {"APP_MODE": "production"},
    "extra_images": [],
}


class RegistryTests(unittest.TestCase):
    def test_a_host_with_a_dot_is_the_registry(self):
        self.assertEqual(agent.registry_of("ghcr.io/amina/app:v1"), "ghcr.io")

    def test_a_host_with_a_port_is_the_registry(self):
        self.assertEqual(agent.registry_of("registry.local:5000/amina/app:v1"),
                         "registry.local:5000")

    def test_a_bare_name_means_docker_hub(self):
        self.assertEqual(agent.registry_of("amina/app:v1"), "docker.io")


class DockerConfigTests(unittest.TestCase):
    def test_the_credential_is_written_for_the_right_registry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = agent.docker_config(SUBMISSION, directory)
            with open(path, encoding="utf-8") as handle:
                written = json.load(handle)
            self.assertIn("ghcr.io", written["auths"])

    def test_docker_hub_gets_its_odd_index_url(self):
        submission = {**SUBMISSION, "image_reference": "amina/app:v1"}
        with tempfile.TemporaryDirectory() as directory:
            with open(agent.docker_config(submission, directory), encoding="utf-8") as handle:
                written = json.load(handle)
            self.assertIn("https://index.docker.io/v1/", written["auths"])

    def test_no_token_means_an_anonymous_pull_not_a_broken_one(self):
        """Sending half a credential would blame the team for our problem."""
        submission = {**SUBMISSION, "pull_token": None}
        with tempfile.TemporaryDirectory() as directory:
            with open(agent.docker_config(submission, directory), encoding="utf-8") as handle:
                written = json.load(handle)
            self.assertEqual(written["auths"], {})

    def test_the_config_is_not_world_readable(self):
        """It holds somebody else's registry credential."""
        if os.name == "nt":
            self.skipTest("POSIX permissions only")
        with tempfile.TemporaryDirectory() as directory:
            path = agent.docker_config(SUBMISSION, directory)
            self.assertEqual(os.stat(path).st_mode & 0o077, 0)


class NamespaceTests(unittest.TestCase):
    def test_the_architecture_naming(self):
        self.assertEqual(
            agent.namespace_for(SUBMISSION, agent.DEFAULT_NAMESPACE),
            "jengasec-ent-a-app01",
        )

    def test_a_team_with_no_cell_still_gets_a_valid_name(self):
        submission = {**SUBMISSION, "cell": ""}
        self.assertEqual(
            agent.namespace_for(submission, agent.DEFAULT_NAMESPACE),
            "jengasec-ent-a-unassigned",
        )


class ManifestTests(unittest.TestCase):
    def build(self, submission=None):
        docs = agent.manifests(
            submission or SUBMISSION, "jengasec-ent-a-app01",
            "ghcr.io/amina-dev/internet-banking-portal:v3",
        )
        return {(d["kind"], d["metadata"]["name"]): d for d in docs}

    def test_the_health_path_becomes_the_readiness_probe(self):
        """Healthy must mean the check the team was told about."""
        container = self.build()[("Deployment", "app")]["spec"]["template"]["spec"]["containers"][0]
        probe = container["readinessProbe"]["httpGet"]
        self.assertEqual(probe["path"], "/healthz")
        self.assertEqual(probe["port"], 8080)

    def test_a_database_is_only_created_when_asked_for(self):
        self.assertIn(("Deployment", "db"), self.build())
        without = self.build({**SUBMISSION, "needs_database": False})
        self.assertNotIn(("Deployment", "db"), without)

    def test_the_database_details_are_passed_in_as_the_guide_promises(self):
        container = self.build()[("Deployment", "app")]["spec"]["template"]["spec"]["containers"][0]
        names = {item["name"] for item in container["env"]}
        self.assertTrue({"DATABASE_URL", "PGHOST", "PGUSER", "PGPASSWORD"} <= names)

    def test_the_team_env_vars_and_port_are_passed_in(self):
        container = self.build()[("Deployment", "app")]["spec"]["template"]["spec"]["containers"][0]
        env = {item["name"]: item["value"] for item in container["env"]}
        self.assertEqual(env["APP_MODE"], "production")
        self.assertEqual(env["PORT"], "8080")

    def test_the_cell_stays_inside_its_limits(self):
        pod = self.build()[("Deployment", "app")]["spec"]["template"]["spec"]
        self.assertEqual(pod["containers"][0]["resources"]["limits"]["memory"], agent.APP_MEMORY)
        self.assertFalse(pod["containers"][0]["securityContext"]["allowPrivilegeEscalation"])

    def test_the_pull_secret_is_referenced(self):
        pod = self.build()[("Deployment", "app")]["spec"]["template"]["spec"]
        self.assertEqual(pod["imagePullSecrets"], [{"name": "team-registry"}])

    def test_the_service_points_at_the_declared_port(self):
        service = self.build()[("Service", "app")]
        self.assertEqual(service["spec"]["ports"][0]["targetPort"], 8080)


class ScanReportTests(unittest.TestCase):
    """A scan that did not run must not read as a scan that found nothing."""

    def test_a_skipped_scan_is_marked_skipped(self):
        report, blocking = agent.scan("whatever:v1")
        if "skipped" not in report:
            self.skipTest("Trivy is installed here, so there is nothing to skip")
        self.assertIsNone(blocking)
        self.assertIn("not installed", report["skipped"])


class StopTests(unittest.TestCase):
    def test_a_stop_carries_the_status_the_team_will_see(self):
        stop = agent.Stop("built for arm64", status="rejected")
        self.assertEqual(stop.status, "rejected")
        self.assertEqual(str(stop), "built for arm64")

    def test_the_default_is_failed(self):
        self.assertEqual(agent.Stop("something broke").status, "failed")


if __name__ == "__main__":
    unittest.main()
