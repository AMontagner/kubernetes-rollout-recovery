"""Verify the exit codes consumed by CI and recovery scripts."""
import json
from pathlib import Path
import subprocess
import sys
import unittest


CHECKER = Path(__file__).with_name("rollout_status.py")


class RolloutStatusCLI(unittest.TestCase):
    def run_checker(self, snapshot, *args):
        return subprocess.run(
            [sys.executable, str(CHECKER), *args], input=snapshot, text=True,
            capture_output=True, timeout=5,
        )

    def test_rollout_states(self):
        deployment = {
            "kind": "Deployment",
            "metadata": {"generation": 2},
            "spec": {"replicas": 2},
        }
        healthy = {
            "observedGeneration": 2,
            "replicas": 2,
            "updatedReplicas": 2,
            "availableReplicas": 2,
        }
        expired = [{
            "type": "Progressing",
            "status": "False",
            "reason": "ProgressDeadlineExceeded",
        }]
        cases = [
            ("healthy", healthy, 0, "rollout complete"),
            ("old replica remains", dict(healthy, replicas=3), 1, "waiting"),
            ("updated replicas unavailable", dict(healthy, availableReplicas=1), 1, "waiting"),
            ("deadline exceeded", dict(healthy, conditions=expired), 2, "progress deadline"),
            ("availability condition", dict(healthy, conditions=[{"type": "Available", "status": "False"}]), 1, "Available condition"),
            ("stale deadline", dict(healthy, observedGeneration=1, conditions=expired), 1, "latest generation"),
        ]
        for name, status, code, message in cases:
            with self.subTest(name=name):
                result = self.run_checker(json.dumps(dict(deployment, status=status)))
                self.assertEqual(result.returncode, code, result)
                self.assertIn(message, result.stdout)
                self.assertEqual(result.stderr, "")

    def test_invalid_input(self):
        for snapshot in ("{", '{"kind":"Pod"}', '{"kind":"Deployment"}'):
            with self.subTest(snapshot=snapshot):
                result = self.run_checker(snapshot)
                self.assertEqual(result.returncode, 3, result)
                self.assertEqual(result.stdout, "")
                self.assertIn("invalid Deployment snapshot", result.stderr)

    def test_verify_rollback_image(self):
        snapshot = {
            "kind": "Deployment",
            "metadata": {"generation": 4},
            "spec": {
                "replicas": 1,
                "template": {"spec": {"containers": [
                    {"name": "metrics", "image": "metrics:v1"},
                    {"name": "web", "image": "web:v1"},
                ]}},
            },
            "status": {
                "observedGeneration": 4,
                "replicas": 1,
                "updatedReplicas": 1,
                "availableReplicas": 1,
            },
        }
        for name, image, code, state, detail in (
            ("web", "web:v1", 0, "complete", "rollout complete"),
            ("web", "web:v2", 4, "image_mismatch", "uses web:v1, expected web:v2"),
            ("worker", "web:v1", 4, "image_mismatch", "container worker is missing"),
        ):
            with self.subTest(name=name, image=image):
                result = self.run_checker(
                    json.dumps(snapshot), "--expect-image", name, image, "--json"
                )
                report = json.loads(result.stdout)
                self.assertEqual(result.returncode, code, result)
                self.assertEqual(report["exit_code"], code)
                self.assertEqual(report["state"], state)
                self.assertIn(detail, report["message"])
                self.assertEqual(result.stderr, "")

        snapshot["status"]["availableReplicas"] = 0
        result = self.run_checker(
            json.dumps(snapshot), "--expect-image", "web", "web:v2", "--json"
        )
        self.assertEqual(result.returncode, 1, result)
        self.assertEqual(json.loads(result.stdout)["state"], "waiting")

    def test_json_contract(self):
        snapshot = {
            "kind": "Deployment",
            "metadata": {"generation": 2},
            "spec": {"replicas": 1},
        }
        for status, code, state in (
            ({}, 1, "waiting"),
            ({"observedGeneration": 2, "replicas": 1,
              "updatedReplicas": 1, "availableReplicas": 1}, 0, "complete"),
            ({"observedGeneration": 2, "conditions": [
                {"type": "Progressing", "status": "False",
                 "reason": "ProgressDeadlineExceeded"},
            ]}, 2, "stalled"),
        ):
            with self.subTest(state=state):
                result = self.run_checker(json.dumps(dict(snapshot, status=status)), "--json")
                self.assertEqual(result.returncode, code, result)
                self.assertEqual(result.stderr, "")
                report = json.loads(result.stdout)
                self.assertEqual(report["state"], state)
                self.assertEqual(report["exit_code"], code)
                self.assertIsInstance(report["message"], str)

        malformed = self.run_checker("{", "--json")
        self.assertEqual(malformed.returncode, 3)
        self.assertEqual(malformed.stdout, "")
        self.assertIn("invalid Deployment snapshot", malformed.stderr)

    def test_invalid_counters(self):
        fields = {
            "metadata": {"generation": 2},
            "spec": {"replicas": 1},
            "status": {"observedGeneration": 2, "updatedReplicas": 1,
                       "replicas": 1, "availableReplicas": 1},
        }
        for section, values in fields.items():
            for field in values:
                for invalid in (True, 1.0, -1, "1", None):
                    with self.subTest(section=section, field=field, value=invalid):
                        snapshot = {name: dict(data) for name, data in fields.items()}
                        snapshot["kind"] = "Deployment"
                        snapshot[section][field] = invalid
                        result = self.run_checker(json.dumps(snapshot), "--json")
                        self.assertEqual(result.returncode, 3, result)
                        self.assertEqual(result.stdout, "")
                        self.assertIn(f"{section}.{field}", result.stderr)

    def test_scale_to_zero(self):
        snapshot = {
            "kind": "Deployment",
            "metadata": {"generation": 2},
            "spec": {"replicas": 0},
            "status": {"observedGeneration": 2},
        }
        result = self.run_checker(json.dumps(snapshot), "--json")
        self.assertEqual(result.returncode, 0, result)
        self.assertEqual(json.loads(result.stdout)["state"], "complete")
