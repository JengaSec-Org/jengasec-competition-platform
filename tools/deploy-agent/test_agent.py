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
from unittest.mock import patch

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
    "service_ip": "10.108.0.11",
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

    def test_an_assigned_namespace_overrides_the_default(self):
        self.assertEqual(
            agent.namespace_for(
                {**SUBMISSION, "namespace": "jengasec-ent-a-app01"},
                agent.DEFAULT_NAMESPACE,
            ),
            "jengasec-ent-a-app01",
        )

    def test_an_invalid_platform_namespace_is_rejected(self):
        with self.assertRaises(agent.Stop):
            agent.namespace_for(
                {**SUBMISSION, "namespace": "../secrets"},
                agent.DEFAULT_NAMESPACE,
            )

    def test_a_team_with_no_cell_still_gets_a_valid_name(self):
        submission = {**SUBMISSION, "cell": ""}
        self.assertEqual(
            agent.namespace_for(submission, agent.DEFAULT_NAMESPACE),
            "jengasec-ent-a-unassigned",
        )

    def test_a_missing_or_invalid_service_ip_is_refused(self):
        for value in ("", "example.com", "2001:db8::1"):
            with self.subTest(value=value), self.assertRaises(agent.Stop):
                agent.require_service_ip(value)


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

    def test_agent_leaves_infrastructure_owned_resources_alone(self):
        resources = self.build()
        self.assertEqual(set(resources), {("Deployment", "app"), ("Service", "app")})
        without_database = self.build({**SUBMISSION, "needs_database": False})
        app = without_database[("Deployment", "app")]["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(app["envFrom"], [])
        self.assertFalse(any(item["name"].startswith("PG") for item in app["env"]))

    def test_the_database_details_are_passed_in_as_the_guide_promises(self):
        container = self.build()[("Deployment", "app")]["spec"]["template"]["spec"]["containers"][0]
        names = {item["name"] for item in container["env"]}
        self.assertTrue({"PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD"} <= names)
        self.assertEqual(container["envFrom"], [{"secretRef": {"name": "db-credentials"}}])
        references = {
            item["name"]: item["valueFrom"]["secretKeyRef"]
            for item in container["env"]
            if "valueFrom" in item
        }
        self.assertEqual(references["PGPASSWORD"]["key"], "DB_PASSWORD")

    def test_the_team_env_vars_and_port_are_passed_in(self):
        container = self.build()[("Deployment", "app")]["spec"]["template"]["spec"]["containers"][0]
        env = {
            item["name"]: item["value"]
            for item in container["env"]
            if "value" in item
        }
        self.assertEqual(env["APP_MODE"], "production")
        self.assertEqual(env["PORT"], "8080")

    def test_the_cell_stays_inside_its_limits(self):
        pod = self.build()[("Deployment", "app")]["spec"]["template"]["spec"]
        app = pod["containers"][0]
        self.assertEqual(app["resources"]["limits"]["memory"], "1920Mi")
        self.assertEqual(app["resources"]["limits"]["cpu"], "900m")
        self.assertFalse(app["securityContext"]["allowPrivilegeEscalation"])
        self.assertEqual(app["securityContext"]["capabilities"]["drop"], ["ALL"])
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertEqual(pod["serviceAccountName"], "application")
        self.assertTrue(pod["securityContext"]["runAsNonRoot"])
        self.assertEqual(pod["securityContext"]["seccompProfile"]["type"], "RuntimeDefault")

    def test_extra_images_run_as_bounded_sidecars_inside_cell_quota(self):
        docs = agent.manifests(
            {**SUBMISSION, "extra_images": ["ghcr.io/amina/worker:v1"]},
            "jengasec-ent-a-app01",
            "ghcr.io/amina/app:v1",
        )
        deployment = next(
            doc for doc in docs
            if doc["kind"] == "Deployment" and doc["metadata"]["name"] == "app"
        )
        containers = deployment["spec"]["template"]["spec"]["containers"]
        self.assertEqual([item["name"] for item in containers], ["app", "extra-1"])
        cpu_milli = sum(
            int(item["resources"]["limits"]["cpu"][:-1])
            for item in containers
        )
        memory_mi = sum(
            int(item["resources"]["limits"]["memory"][:-2])
            for item in containers
        )
        self.assertEqual(cpu_milli, 900)
        self.assertEqual(memory_mi, 1920)

    def test_the_pull_secret_is_referenced(self):
        pod = self.build()[("Deployment", "app")]["spec"]["template"]["spec"]
        self.assertEqual(pod["imagePullSecrets"], [{"name": "team-registry"}])

    def test_the_service_points_at_the_declared_port(self):
        service = self.build()[("Service", "app")]
        self.assertEqual(service["spec"]["ports"][0]["targetPort"], 8080)
        self.assertEqual(service["spec"]["type"], "LoadBalancer")
        self.assertEqual(service["spec"]["loadBalancerIP"], "10.108.0.11")
        self.assertEqual(
            service["metadata"]["annotations"]["metallb.io/loadBalancerIPs"],
            "10.108.0.11",
        )

    def test_deployment_only_applies_inside_the_preprovisioned_namespace(self):
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as config:
            config.write('{"auths": {}}')
            config.flush()
            with patch.object(agent, "kubectl", return_value=(0, "", "")) as kubectl:
                agent.deploy(
                    SUBMISSION,
                    "jengasec-ent-a-app01",
                    SUBMISSION["image_reference"],
                    config.name,
                )

        self.assertEqual(kubectl.call_count, 2)
        secret = json.loads(kubectl.call_args_list[0].kwargs["stdin"])
        applied = json.loads(kubectl.call_args_list[1].kwargs["stdin"])
        self.assertEqual(secret["kind"], "Secret")
        self.assertEqual(secret["metadata"]["namespace"], "jengasec-ent-a-app01")
        self.assertEqual(
            {(item["kind"], item["metadata"]["name"]) for item in applied["items"]},
            {("Deployment", "app"), ("Service", "app")},
        )


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
