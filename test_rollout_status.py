"""Verify the exit codes consumed by CI and recovery scripts."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest


CHECKER = Path(__file__).with_name("rollout_status.py")
WRAPPER = Path(__file__).with_name("verify_rollout.sh")


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
            ("replica failure", dict(healthy, conditions=[{"type": "ReplicaFailure", "status": "True", "reason": "FailedCreate"}]), 2, "replica failure: FailedCreate"),
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

    def test_committed_fixtures(self):
        cases = (
            ("healthy.json", 0, "complete"),
            ("stalled-replica-failure.json", 2, "stalled"),
            ("waiting-available.json", 1, "waiting"),
            ("waiting-unavailable.json", 1, "waiting"),
            ("stale-generation.json", 1, "waiting"),
        )
        for filename, code, state in cases:
            with self.subTest(filename=filename):
                fixture = Path(__file__).with_name("fixtures") / filename
                result = subprocess.run([sys.executable, str(CHECKER), "--json"], input=fixture.read_text(), text=True, capture_output=True, timeout=5)
                self.assertEqual(result.returncode, code, result)
                self.assertEqual(json.loads(result.stdout)["state"], state)

    def test_committed_rollback_evidence(self):
        fixture = Path(__file__).with_name("fixtures") / "rollback-verified.json"
        result = subprocess.run(
            [sys.executable, str(CHECKER), "--json", "--expect-image", "web", "nginx:1.28-alpine", "--expect-revision", "7"],
            input=fixture.read_text(), text=True, capture_output=True, timeout=5,
        )
        report = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0, result)
        self.assertEqual(report["state"], "complete")
        self.assertEqual(report["details"]["deployment_revision"], "7")
        self.assertIn("rollout complete", report["message"])

    def test_wrapper_propagates_rollback_guards(self):
        fixture = Path(__file__).with_name("fixtures") / "rollback-verified.json"
        with tempfile.TemporaryDirectory() as temp_dir:
            kubectl = Path(temp_dir) / "kubectl"
            kubectl.write_text(f"#!/bin/sh\ncat {shlex.quote(str(fixture))}\n")
            kubectl.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{temp_dir}{os.pathsep}{environment['PATH']}"
            for expected_revision, code, state in (("7", 0, "complete"), ("6", 5, "revision_mismatch")):
                with self.subTest(expected_revision=expected_revision):
                    result = subprocess.run(
                        ["bash", str(WRAPPER), "rollout-recovery", "rollout-web", "web", "nginx:1.28-alpine", "4", expected_revision],
                        cwd=CHECKER.parent, env=environment, text=True,
                        capture_output=True, timeout=5,
                    )
                    report = json.loads(result.stdout)
                    self.assertEqual(result.returncode, code, result)
                    self.assertEqual(report["state"], state)
                    self.assertEqual(report["details"]["deployment_revision"], "7")

    def test_wrapper_preserves_evidence_snapshot_on_failed_verification(self):
        fixture = Path(__file__).with_name("fixtures") / "rollback-verified.json"
        with tempfile.TemporaryDirectory() as temp_dir:
            kubectl = Path(temp_dir) / "kubectl"
            evidence = Path(temp_dir) / "evidence" / "deployment.json"
            evidence.parent.mkdir()
            kubectl.write_text(f"#!/bin/sh\ncat {shlex.quote(str(fixture))}\n")
            kubectl.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{temp_dir}{os.pathsep}{environment['PATH']}"
            result = subprocess.run(
                ["bash", str(WRAPPER), "rollout-recovery", "rollout-web", "web", "nginx:1.28-alpine", "4", "6", str(evidence)],
                cwd=CHECKER.parent, env=environment, text=True,
                capture_output=True, timeout=5,
            )
            self.assertEqual(result.returncode, 5, result)
            self.assertTrue(evidence.exists())
            self.assertEqual(json.loads(evidence.read_text())["metadata"]["generation"], 4)

    def test_wrapper_propagates_observed_generation_guard(self):
        fixture = Path(__file__).with_name("fixtures") / "rollback-verified.json"
        with tempfile.TemporaryDirectory() as temp_dir:
            kubectl = Path(temp_dir) / "kubectl"
            kubectl.write_text(f"#!/bin/sh\ncat {shlex.quote(str(fixture))}\n")
            kubectl.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{temp_dir}{os.pathsep}{environment['PATH']}"
            for expected_observed, code, state in (("4", 0, "complete"), ("3", 6, "observed_generation_mismatch")):
                with self.subTest(expected_observed=expected_observed):
                    result = subprocess.run(
                        ["bash", str(WRAPPER), "rollout-recovery", "rollout-web", "web", "nginx:1.28-alpine", "4", "7", "", expected_observed],
                        cwd=CHECKER.parent, env=environment, text=True,
                        capture_output=True, timeout=5,
                    )
                    report = json.loads(result.stdout)
                    self.assertEqual(result.returncode, code, result)
                    self.assertEqual(report["state"], state)

    def test_wrapper_reports_capture_failure_as_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            kubectl = Path(temp_dir) / "kubectl"
            kubectl.write_text("#!/bin/sh\nexit 7\n")
            kubectl.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{temp_dir}{os.pathsep}{environment['PATH']}"
            result = subprocess.run(
                ["bash", str(WRAPPER), "rollout-recovery", "rollout-web"],
                cwd=CHECKER.parent, env=environment, text=True,
                capture_output=True, timeout=5,
            )
            report = json.loads(result.stdout)
            self.assertEqual(result.returncode, 3, result)
            self.assertEqual(report["state"], "capture_error")
            self.assertEqual(report["exit_code"], 3)
            self.assertEqual(result.stderr, "")

    def test_malformed_conditions_are_invalid(self):
        snapshot = {"kind": "Deployment", "metadata": {"generation": 1}, "spec": {"replicas": 1}, "status": {"observedGeneration": 1, "conditions": {"type": "Available"}}}
        result = self.run_checker(json.dumps(snapshot), "--json")
        self.assertEqual(result.returncode, 3, result)
        self.assertEqual(result.stdout, "")
        self.assertIn("status.conditions must be a list of objects", result.stderr)

    def test_malformed_condition_entries_are_invalid(self):
        for conditions in (None, [None], ["Available"], [1]):
            with self.subTest(conditions=conditions):
                snapshot = {
                    "kind": "Deployment",
                    "metadata": {"generation": 1},
                    "spec": {"replicas": 1},
                    "status": {"observedGeneration": 1, "conditions": conditions},
                }
                result = self.run_checker(json.dumps(snapshot), "--json")
                self.assertEqual(result.returncode, 3, result)
                self.assertEqual(result.stdout, "")
                self.assertIn("status.conditions must be a list of objects", result.stderr)

    def test_malformed_conditions_do_not_bypass_stale_generation(self):
        snapshot = {
            "kind": "Deployment",
            "metadata": {"generation": 2},
            "spec": {"replicas": 1},
            "status": {"observedGeneration": 1, "conditions": {"type": "Available"}},
        }
        result = self.run_checker(json.dumps(snapshot), "--json")
        self.assertEqual(result.returncode, 3, result)
        self.assertEqual(result.stdout, "")
        self.assertIn("status.conditions must be a list of objects", result.stderr)

    def test_malformed_revision_annotations_are_invalid(self):
        for annotations, error in (
            ([], "metadata.annotations must be an object"),
            ({"deployment.kubernetes.io/revision": 7}, "deployment.kubernetes.io/revision must be a string"),
        ):
            with self.subTest(annotations=annotations):
                snapshot = {
                    "kind": "Deployment",
                    "metadata": {"generation": 1, "annotations": annotations},
                    "spec": {"replicas": 0},
                    "status": {"observedGeneration": 1},
                }
                result = self.run_checker(json.dumps(snapshot), "--json")
                self.assertEqual(result.returncode, 3, result)
                self.assertEqual(result.stdout, "")
                self.assertIn(error, result.stderr)

    def test_replica_failure_detail_in_json(self):
        snapshot = {"kind": "Deployment", "metadata": {"generation": 3}, "spec": {"replicas": 1}, "status": {"observedGeneration": 3, "conditions": [{"type": "ReplicaFailure", "status": "True", "reason": "FailedCreate", "message": "quota exceeded", "lastUpdateTime": "2026-11-26T19:00:00Z", "lastTransitionTime": "2026-11-26T18:59:00Z"}]}}
        result = self.run_checker(json.dumps(snapshot), "--json")
        report = json.loads(result.stdout)
        self.assertEqual(result.returncode, 2, result)
        self.assertIn("FailedCreate", report["message"])
        self.assertEqual(report["details"]["conditions"][0]["reason"], "FailedCreate")
        self.assertEqual(report["details"]["conditions"][0]["message"], "quota exceeded")
        self.assertEqual(report["details"]["conditions"][0]["lastUpdateTime"], "2026-11-26T19:00:00Z")
        self.assertEqual(report["details"]["conditions"][0]["lastTransitionTime"], "2026-11-26T18:59:00Z")

    def test_unavailable_replica_detail_in_json(self):
        snapshot = {
            "kind": "Deployment",
            "metadata": {"generation": 3},
            "spec": {"replicas": 2},
            "status": {
                "observedGeneration": 3,
                "replicas": 2,
                "updatedReplicas": 2,
                "availableReplicas": 1,
                "unavailableReplicas": 1,
            },
        }
        result = self.run_checker(json.dumps(snapshot), "--json")
        report = json.loads(result.stdout)
        self.assertEqual(result.returncode, 1, result)
        self.assertEqual(report["state"], "waiting")
        self.assertEqual(report["details"]["unavailable_replicas"], 1)
        self.assertIn("1 unavailable replica to recover", report["message"])

    def test_expect_generation(self):
        snapshot = {"kind": "Deployment", "metadata": {"generation": 4}, "spec": {"replicas": 0}, "status": {"observedGeneration": 4}}
        for expected, code, state in ((4, 0, "complete"), (3, 1, "waiting")):
            with self.subTest(expected=expected):
                result = self.run_checker(json.dumps(snapshot), "--expect-generation", str(expected), "--json")
                report = json.loads(result.stdout)
                self.assertEqual(result.returncode, code, result)
                self.assertEqual(report["state"], state)

    def test_expect_observed_generation(self):
        snapshot = {
            "kind": "Deployment",
            "metadata": {"generation": 4},
            "spec": {"replicas": 0},
            "status": {"observedGeneration": 4},
        }
        for expected, code, state, detail in (
            (4, 0, "complete", "no action required"),
            (3, 6, "observed_generation_mismatch", "observed generation 4, expected 3"),
        ):
            with self.subTest(expected=expected):
                result = self.run_checker(json.dumps(snapshot), "--expect-observed-generation", str(expected), "--json")
                report = json.loads(result.stdout)
                self.assertEqual(result.returncode, code, result)
                self.assertEqual(report["state"], state)
                self.assertIn(detail, report["recommended_action"] if code == 0 else report["message"])

    def test_min_ready_replicas_guard(self):
        snapshot = {
            "kind": "Deployment",
            "metadata": {"generation": 4},
            "spec": {"replicas": 2},
            "status": {
                "observedGeneration": 4,
                "replicas": 2,
                "updatedReplicas": 2,
                "availableReplicas": 2,
                "readyReplicas": 1,
            },
        }
        for minimum, code, state, detail in (
            (1, 0, "complete", "rollout complete"),
            (2, 1, "waiting", "ready replicas 1, expected at least 2"),
        ):
            with self.subTest(minimum=minimum):
                result = self.run_checker(json.dumps(snapshot), "--min-ready-replicas", str(minimum), "--json")
                report = json.loads(result.stdout)
                self.assertEqual(result.returncode, code, result)
                self.assertEqual(report["state"], state)
                self.assertIn(detail, report["message"])

        result = self.run_checker(json.dumps(snapshot), "--min-ready-replicas", "-1", "--json")
        self.assertEqual(result.returncode, 3, result)
        self.assertEqual(result.stdout, "")
        self.assertIn("must be nonnegative", result.stderr)

    def test_wrapper_propagates_min_ready_replicas_guard(self):
        fixture = Path(__file__).with_name("fixtures") / "rollback-verified.json"
        with tempfile.TemporaryDirectory() as temp_dir:
            kubectl = Path(temp_dir) / "kubectl"
            kubectl.write_text(f"#!/bin/sh\ncat {shlex.quote(str(fixture))}\n")
            kubectl.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{temp_dir}{os.pathsep}{environment['PATH']}"
            result = subprocess.run(
                ["bash", str(WRAPPER), "rollout-recovery", "rollout-web", "web", "nginx:1.28-alpine", "4", "7", "", "4", "2"],
                cwd=CHECKER.parent, env=environment, text=True,
                capture_output=True, timeout=5,
            )
            report = json.loads(result.stdout)
            self.assertEqual(result.returncode, 1, result)
            self.assertEqual(report["state"], "waiting")
            self.assertIn("expected at least 2", report["message"])

    def test_verify_rollback_revision(self):
        snapshot = {
            "kind": "Deployment",
            "metadata": {
                "generation": 4,
                "annotations": {"deployment.kubernetes.io/revision": "7"},
            },
            "spec": {"replicas": 0},
            "status": {"observedGeneration": 4},
        }
        for expected, code, state, detail in (
            ("7", 0, "complete", "no action required"),
            ("6", 5, "revision_mismatch", "deployment revision 7, expected 6"),
        ):
            with self.subTest(expected=expected):
                result = self.run_checker(json.dumps(snapshot), "--expect-revision", expected, "--json")
                report = json.loads(result.stdout)
                self.assertEqual(result.returncode, code, result)
                self.assertEqual(report["state"], state)
                self.assertEqual(report["exit_code"], code)
                self.assertEqual(report["details"]["deployment_revision"], "7")
                self.assertIn(detail, report["recommended_action"] if code == 0 else report["message"])
        
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
                self.assertIsInstance(report["recommended_action"], str)
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
                self.assertEqual(report["schema_version"], 1)
                self.assertEqual(report["state"], state)
                self.assertEqual(report["exit_code"], code)
                self.assertIsInstance(report["message"], str)
                actions = {
                    0: "no action required; rollout is complete",
                    1: "continue monitoring until the expected replicas are available",
                    2: "inspect Pod events and initiate rollback or remediation",
                }
                self.assertEqual(report["recommended_action"], actions[code])
                self.assertIn("details", report)
                self.assertIsInstance(report["details"]["conditions"], list)
                self.assertEqual(report["details"]["desired_replicas"], 1)
                self.assertEqual(report["details"]["deployment_generation"], 2)
                self.assertEqual(report["details"]["observed_generation"], status.get("observedGeneration", 0))
                self.assertEqual(report["details"]["generation_lag"], 2 - status.get("observedGeneration", 0))
                self.assertEqual(report["details"]["unavailable_replicas"], status.get("unavailableReplicas", 0))
                verification = report["details"]["verification"]
                self.assertEqual(verification["generation_converged"], status.get("observedGeneration", 0) == 2)
                self.assertEqual(verification["replica_counts_converged"], all(status.get(field, 0) == 1 for field in ("updatedReplicas", "replicas", "availableReplicas")))
                conditions = status.get("conditions", [])
                self.assertEqual(verification["availability_condition_clear"], not any(
                    condition.get("type") == "Available" and condition.get("status") == "False"
                    for condition in conditions
                ))
                self.assertEqual(verification["failure_conditions_clear"], not any(
                    (condition.get("type") == "ReplicaFailure" and condition.get("status") == "True")
                    or (condition.get("type") == "Progressing" and condition.get("status") == "False" and condition.get("reason") == "ProgressDeadlineExceeded")
                    for condition in conditions
                ))
                self.assertIsNone(verification["image_guard_passed"])
                self.assertIsNone(verification["revision_guard_passed"])

        malformed = self.run_checker("{", "--json")
        self.assertEqual(malformed.returncode, 3)
        self.assertEqual(malformed.stdout, "")
        self.assertIn("invalid Deployment snapshot", malformed.stderr)

    def test_metrics_contract(self):
        snapshot = {
            "kind": "Deployment",
            "metadata": {"generation": 4},
            "spec": {"replicas": 2},
            "status": {
                "observedGeneration": 4,
                "replicas": 2,
                "updatedReplicas": 2,
                "availableReplicas": 2,
                "readyReplicas": 1,
                "unavailableReplicas": 0,
            },
        }
        result = self.run_checker(json.dumps(snapshot), "--metrics")
        self.assertEqual(result.returncode, 0, result)
        self.assertEqual(result.stderr, "")
        self.assertIn("rollout_report_schema_version 1", result.stdout)
        self.assertIn("rollout_state_code 0", result.stdout)
        self.assertIn('rollout_state_info{state="complete"} 1', result.stdout)
        self.assertIn("rollout_generation_lag 0", result.stdout)
        self.assertIn("rollout_desired_replicas 2", result.stdout)
        self.assertIn("rollout_available_replicas 2", result.stdout)
        self.assertIn("rollout_ready_replicas 1", result.stdout)
        self.assertIn("rollout_ready_gap 1", result.stdout)
        self.assertIn("rollout_unavailable_replicas 0", result.stdout)

    def test_ready_replica_detail_and_backward_compatibility(self):
        snapshot = {
            "kind": "Deployment",
            "metadata": {"generation": 4},
            "spec": {"replicas": 2},
            "status": {
                "observedGeneration": 4,
                "replicas": 2,
                "updatedReplicas": 2,
                "availableReplicas": 2,
                "readyReplicas": 1,
            },
        }
        result = self.run_checker(json.dumps(snapshot), "--json")
        self.assertEqual(result.returncode, 0, result)
        report = json.loads(result.stdout)
        self.assertEqual(report["details"]["ready_replicas"], 1)
        self.assertEqual(report["details"]["ready_gap"], 1)

        legacy = dict(snapshot)
        legacy["status"] = {key: value for key, value in snapshot["status"].items() if key != "readyReplicas"}
        result = self.run_checker(json.dumps(legacy), "--json")
        self.assertEqual(result.returncode, 0, result)
        report = json.loads(result.stdout)
        self.assertEqual(report["details"]["ready_replicas"], 2)
        self.assertEqual(report["details"]["ready_gap"], 0)

    def test_invalid_ready_replica_count(self):
        snapshot = {
            "kind": "Deployment",
            "metadata": {"generation": 2},
            "spec": {"replicas": 1},
            "status": {"observedGeneration": 2, "readyReplicas": "1"},
        }
        result = self.run_checker(json.dumps(snapshot), "--json")
        self.assertEqual(result.returncode, 3, result)
        self.assertEqual(result.stdout, "")
        self.assertIn("status.readyReplicas", result.stderr)

    def test_observed_generation_cannot_exceed_metadata_generation(self):
        snapshot = {
            "kind": "Deployment",
            "metadata": {"generation": 2},
            "spec": {"replicas": 1},
            "status": {"observedGeneration": 3},
        }
        result = self.run_checker(json.dumps(snapshot), "--json")
        self.assertEqual(result.returncode, 3, result)
        self.assertEqual(result.stdout, "")
        self.assertIn("status.observedGeneration cannot exceed metadata.generation", result.stderr)

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
