"""Tests for the range lifecycle parser and health decisions."""
import json
import pathlib
import unittest
import tempfile
from unittest.mock import Mock, patch

import agent
import range_ops


class WireGuardTests(unittest.TestCase):
    def test_maps_allowed_ips_to_profiles_and_uses_recent_handshakes(self):
        peers = [
            {"id": 7, "address": "10.60.1.10/32"},
            {"id": 8, "address": "10.60.1.11/32"},
            {"id": 9, "address": ""},
        ]
        dump = "\n".join([
            "wg0\tprivate\tpublic\t51820\toff",
            "peer-a\t-\tendpoint\t10.60.1.10/32\t950\t1\t2\toff",
            "peer-b\t-\tendpoint\t10.60.1.11/32\t700\t3\t4\toff",
        ])
        observations = range_ops.parse_wireguard_dump(dump, peers, now=1000, active_seconds=180)
        by_id = {row["id"]: row for row in observations}
        self.assertTrue(by_id[7]["active"])
        self.assertEqual(by_id[7]["last_handshake_at"], "1970-01-01T00:15:50Z")
        self.assertFalse(by_id[8]["active"])
        self.assertIsNone(by_id[9]["last_handshake_at"])

    def test_an_unmapped_peer_is_not_reported_as_a_participant(self):
        observations = range_ops.parse_wireguard_dump(
            "peer\t-\tendpoint\t10.60.1.10/32\t999\t1\t2\toff",
            [{"id": 4, "address": "10.60.1.11/32"}],
            now=1000,
        )
        self.assertEqual(observations[0]["id"], 4)
        self.assertFalse(observations[0]["active"])


class PodObservationTests(unittest.TestCase):
    def test_reports_healthy_only_when_rollout_and_running_digest_match(self):
        expected = "registry.internal/apps/app@sha256:" + "a" * 64
        deployment = {
            "spec": {
                "replicas": 1,
                "template": {"spec": {"containers": [{"name": "app", "image": expected}]}},
            },
            "status": {"readyReplicas": 1, "availableReplicas": 1},
        }
        pods = {
            "items": [{
                "status": {
                    "containerStatuses": [{
                        "name": "app",
                        "ready": True,
                        "imageID": "containerd://sha256:" + "a" * 64,
                    }]
                }
            }]
        }
        cell = {
            "team_id": 3,
            "namespace": "jengasec-ent-a-app01",
            "service_ip": "10.108.0.11",
            "image_digest": expected,
        }
        service = {
            "spec": {"type": "LoadBalancer", "loadBalancerIP": "10.108.0.11"},
            "status": {"loadBalancer": {"ingress": [{"ip": "10.108.0.11"}]}},
        }
        with patch.object(agent, "kubectl", side_effect=[
            (0, __import__("json").dumps(deployment), ""),
            (0, __import__("json").dumps(pods), ""),
            (0, __import__("json").dumps(service), ""),
        ]):
            result = range_ops.pod_observation(cell)
        self.assertTrue(result["healthy"])
        self.assertEqual(result["status"], "live")
        self.assertIn("sha256:" + "a" * 64, result["image_digest"])

    def test_does_not_treat_unfrozen_cells_as_live(self):
        result = range_ops.pod_observation(
            {"team_id": 9, "namespace": "jengasec-ent-a-app02", "image_digest": ""}
        )
        self.assertEqual(result["status"], "building")
        self.assertFalse(result["healthy"])

    def test_reports_image_drift(self):
        expected = "registry.internal/apps/app@sha256:" + "a" * 64
        deployment = {
            "spec": {
                "replicas": 1,
                "template": {"spec": {"containers": [{"name": "app", "image": "other:v1"}]}},
            },
            "status": {"readyReplicas": 1, "availableReplicas": 1},
        }
        service = {
            "spec": {"type": "LoadBalancer", "loadBalancerIP": "10.108.0.11"},
            "status": {"loadBalancer": {"ingress": [{"ip": "10.108.0.11"}]}},
        }
        with patch.object(
            agent,
            "kubectl",
            side_effect=[
                (0, __import__("json").dumps(deployment), ""),
                (0, '{"items": []}', ""),
                (0, __import__("json").dumps(service), ""),
            ],
        ):
            result = range_ops.pod_observation({
                "team_id": 3,
                "namespace": "jengasec-ent-a-app01",
                "service_ip": "10.108.0.11",
                "image_digest": expected,
            })
        self.assertEqual(result["status"], "down")
        self.assertIn("does not match", result["detail"])

    def test_reports_down_when_metallb_has_not_assigned_the_reserved_ip(self):
        expected = "registry.internal/apps/app@sha256:" + "a" * 64
        deployment = {
            "spec": {
                "replicas": 1,
                "template": {"spec": {"containers": [{"name": "app", "image": expected}]}},
            },
            "status": {"readyReplicas": 1, "availableReplicas": 1},
        }
        pods = {
            "items": [{
                "status": {
                    "containerStatuses": [{
                        "name": "app",
                        "ready": True,
                        "imageID": "containerd://sha256:" + "a" * 64,
                    }]
                }
            }]
        }
        service = {
            "spec": {"type": "LoadBalancer", "loadBalancerIP": "10.108.0.11"},
            "status": {"loadBalancer": {"ingress": []}},
        }
        with patch.object(agent, "kubectl", side_effect=[
            (0, __import__("json").dumps(deployment), ""),
            (0, __import__("json").dumps(pods), ""),
            (0, __import__("json").dumps(service), ""),
        ]):
            result = range_ops.pod_observation({
                "team_id": 3,
                "namespace": "jengasec-ent-a-app01",
                "service_ip": "10.108.0.11",
                "image_digest": expected,
            })
        self.assertEqual(result["status"], "down")
        self.assertIn("not assigned", result["detail"])


class SafetyTests(unittest.TestCase):
    def test_namespace_validation_rejects_untrusted_values(self):
        for value in ("", "../etc", "UpperCase", "a" * 64):
            with self.subTest(value=value), self.assertRaises(agent.Stop):
                range_ops.require_namespace(value)

    def test_registry_requires_private_registry_prefix(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(agent.Stop):
                range_ops.registry_prefix("")
        self.assertEqual(
            range_ops.registry_prefix("registry.local:5000/apps/"),
            "registry.local:5000/apps",
        )


class FreezeTests(unittest.TestCase):
    candidate = {
        "id": 41,
        "namespace": "jengasec-ent-a-app01",
        "cell": "APP01",
        "team": "JS26-B-014",
        "image_reference": "ghcr.io/amina/app:v1",
        "extra_images": [],
        "needs_database": False,
        "pull_token": "not-a-real-token",
    }
    digest = "registry.local/apps/jengasec-ent-a-app01@sha256:" + "a" * 64

    class Api:
        def __init__(self, *, fail_first_freeze=False, missing=None):
            self.fail_first_freeze = fail_first_freeze
            self.missing = missing or []
            self.recorded = []

        def _call(self, path, payload=None):
            if path.startswith("/deployments/api/freeze-candidates/"):
                return {"candidates": [FreezeTests.candidate], "missing": self.missing}
            if payload is not None:
                if self.fail_first_freeze:
                    self.fail_first_freeze = False
                    raise agent.Stop("temporary API failure")
                self.recorded.append(payload)
                return {"frozen": True}
            raise AssertionError(f"Unexpected API call: {path}")

    def test_missing_teams_abort_before_copying_any_image(self):
        api = self.Api(missing=[{"cell": "APP02"}])
        with tempfile.TemporaryDirectory() as state_dir, patch.object(
            range_ops, "copy_image"
        ) as copy_image:
            with self.assertRaises(agent.Stop):
                range_ops.freeze(api, 1, state_dir, "registry.local/apps")
        copy_image.assert_not_called()

    def test_retry_reuses_the_recorded_digest_after_an_api_failure(self):
        api = self.Api(fail_first_freeze=True)
        with tempfile.TemporaryDirectory() as state_dir:
            with patch.object(range_ops, "copy_image", return_value=self.digest) as copy_image:
                with self.assertRaises(agent.Stop):
                    range_ops.freeze(api, 1, state_dir, "registry.local/apps")
                self.assertEqual(copy_image.call_count, 1)

            with patch.object(range_ops, "copy_image") as copy_image:
                range_ops.freeze(api, 1, state_dir, "registry.local/apps")
            copy_image.assert_not_called()
        self.assertEqual(api.recorded[0]["digest"], self.digest)


class ResetTests(unittest.TestCase):
    def test_invalid_live_scope_stops_before_any_application_mutation(self):
        namespace = "jengasec-ent-a-app01"
        digest = "registry.local/apps/app@sha256:" + "a" * 64
        test_case = self

        class Api:
            def _call(self, path, payload=None):
                test_case.assertEqual(
                    path, "/deployments/api/range-state/?competition=7"
                )
                return {"cells": [{
                    "namespace": namespace,
                    "image_digest": digest,
                    "service_ip": "10.108.0.11",
                    "port": 8080,
                    "health_path": "/healthz",
                    "needs_database": False,
                    "env_vars": {},
                }]}

        with tempfile.TemporaryDirectory() as state_dir:
            competition_dir = pathlib.Path(state_dir) / "competition-7"
            competition_dir.mkdir()
            (competition_dir / "freeze.json").write_text(json.dumps({
                "cells": {
                    namespace: {
                        "image_digest": digest,
                        "extra_digests": [],
                        "seed_snapshot": "",
                        "seed_sha256": "",
                    }
                }
            }), encoding="utf-8")
            scope_script = pathlib.Path(state_dir) / "make-scope.py"
            scope_script.write_text("# test stub\n", encoding="utf-8")
            failed_preflight = Mock(returncode=1, stderr="invalid live scope", stdout="")
            with patch.object(
                range_ops.subprocess, "run", return_value=failed_preflight
            ) as run_scope, patch.object(agent, "kubectl") as kubectl:
                with self.assertRaisesRegex(
                    agent.Infra, "Scope preflight failed before resetting"
                ):
                    range_ops.reset_day(
                        Api(),
                        7,
                        2,
                        state_dir,
                        "registry.local/apps",
                        scope_script=str(scope_script),
                    )

        self.assertEqual(run_scope.call_args.args[0][-1], "--dry-run")
        self.assertEqual(run_scope.call_args.kwargs["env"]["JENGASEC_COMPETITION_ID"], "7")
        self.assertEqual(run_scope.call_args.kwargs["env"]["JENGASEC_ROUND"], "2")
        kubectl.assert_not_called()


if __name__ == "__main__":
    unittest.main()
