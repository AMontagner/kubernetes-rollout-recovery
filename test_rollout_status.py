"""Verify the exit codes consumed by CI and recovery scripts."""
import json
from pathlib import Path
import subprocess
import sys
import unittest


CHECKER = Path(__file__).with_name("rollout_status.py")


class RolloutStatusCLI(unittest.TestCase):
    def run_checker(self, snapshot):
        return subprocess.run(
            [sys.executable, str(CHECKER)], input=snapshot, text=True,
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
