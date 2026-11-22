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
