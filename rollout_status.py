"""Check a Kubernetes Deployment snapshot without cluster access."""
import argparse
import hashlib
import json
import sys


STATE_NAMES = {
    0: "complete",
    1: "waiting",
    2: "stalled",
    4: "image_mismatch",
    5: "revision_mismatch",
    6: "observed_generation_mismatch",
}
REPORT_SCHEMA_VERSION = 1


def nonnegative_integer(value, field):
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a nonnegative integer")
    return value


def validate_conditions(conditions):
    if type(conditions) is not list or any(type(condition) is not dict for condition in conditions):
        raise ValueError("status.conditions must be a list of objects")
    return conditions


def condition_summary(conditions):
    return {
        "available_false": sum(
            condition.get("type") == "Available" and condition.get("status") == "False"
            for condition in conditions
        ),
        "replica_failure_true": sum(
            condition.get("type") == "ReplicaFailure" and condition.get("status") == "True"
            for condition in conditions
        ),
        "progress_deadline_exceeded": sum(
            condition.get("type") == "Progressing"
            and condition.get("status") == "False"
            and condition.get("reason") == "ProgressDeadlineExceeded"
            for condition in conditions
        ),
    }


def container_images(deployment):
    containers = (
        deployment.get("spec", {})
        .get("template", {})
        .get("spec", {})
        .get("containers", [])
    )
    if type(containers) is not list:
        return {}
    return {
        container.get("name"): container.get("image")
        for container in containers
        if type(container) is dict
        and type(container.get("name")) is str
        and type(container.get("image")) is str
    }


def evidence_fingerprint(deployment):
    """Return a stable digest for correlating a report with its raw snapshot."""
    canonical = json.dumps(
        deployment,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def deployment_revision(deployment):
    annotations = deployment["metadata"].get("annotations", {})
    if type(annotations) is not dict:
        raise ValueError("metadata.annotations must be an object")
    revision = annotations.get("deployment.kubernetes.io/revision")
    if revision is not None and type(revision) is not str:
        raise ValueError("deployment.kubernetes.io/revision must be a string")
    return revision


def classify(deployment):
    if deployment["kind"] != "Deployment":
        raise ValueError("expected a Deployment")
    generation = nonnegative_integer(deployment["metadata"]["generation"], "metadata.generation")
    desired = nonnegative_integer(deployment["spec"].get("replicas", 1), "spec.replicas")
    status = deployment.get("status", {})
    observed = nonnegative_integer(status.get("observedGeneration", 0), "status.observedGeneration")
    if observed > generation:
        raise ValueError("status.observedGeneration cannot exceed metadata.generation")
    conditions = validate_conditions(status.get("conditions", []))
    replica_counts = [
        nonnegative_integer(status.get(field, 0), f"status.{field}")
        for field in ("updatedReplicas", "replicas", "availableReplicas")
    ]
    # Older snapshots may omit readyReplicas; availableReplicas is the
    # compatibility fallback while still validating the field when present.
    nonnegative_integer(
        status.get("readyReplicas", status.get("availableReplicas", 0)),
        "status.readyReplicas",
    )
    unavailable = nonnegative_integer(status.get("unavailableReplicas", 0), "status.unavailableReplicas")
    if observed < generation:
        return 1, "waiting for controller to observe the latest generation"
    if any(
        condition.get("type") == "Progressing"
        and condition.get("status") == "False"
        and condition.get("reason") == "ProgressDeadlineExceeded"
        for condition in conditions
    ):
        return 2, "rollout exceeded its progress deadline"
    if desired and any(
        condition.get("type") == "Available"
        and condition.get("status") == "False"
        for condition in conditions
    ):
        return 1, "waiting for the Deployment Available condition"
    replica_failure = next((
        condition for condition in conditions
        if condition.get("type") == "ReplicaFailure"
        and condition.get("status") == "True"
    ), None)
    if replica_failure:
        detail = replica_failure.get("reason") or replica_failure.get("message")
        suffix = f": {detail}" if detail else ""
        return 2, f"Deployment reported a replica failure{suffix}"
    if unavailable:
        return 1, f"waiting for {unavailable} unavailable replica{'s' if unavailable != 1 else ''} to recover"
    if all(count == desired for count in replica_counts):
        return 0, f"rollout complete: {desired} updated and available replicas"
    return 1, "waiting for updated replicas to become available and old replicas to terminate"


def recommended_action(code):
    return {
        0: "no action required; rollout is complete",
        1: "continue monitoring until the expected replicas are available",
        2: "inspect Pod events and initiate rollback or remediation",
        4: "verify the recovered image and Deployment template before proceeding",
        5: "verify the rollback revision annotation and Deployment history before proceeding",
        6: "wait for the controller to observe the expected Deployment generation",
    }[code]


def metrics_report(code, deployment, min_ready_replicas=None, max_generation_lag=None):
    status = deployment.get("status", {})
    generation = deployment["metadata"]["generation"]
    observed = status.get("observedGeneration", 0)
    ready = status.get("readyReplicas", status.get("availableReplicas", 0))
    generation_lag = generation - observed
    summary = condition_summary(status.get("conditions", []))
    values = {
        "rollout_report_schema_version": REPORT_SCHEMA_VERSION,
        "rollout_state_code": code,
        "rollout_generation_lag": generation_lag,
        "rollout_desired_replicas": deployment["spec"].get("replicas", 1),
        "rollout_available_replicas": status.get("availableReplicas", 0),
        "rollout_ready_replicas": ready,
        "rollout_ready_gap": max(
            deployment["spec"].get("replicas", 1)
            - ready,
            0,
        ),
        "rollout_min_ready_replicas": -1 if min_ready_replicas is None else min_ready_replicas,
        "rollout_ready_guard_passed": -1 if min_ready_replicas is None else int(ready >= min_ready_replicas),
        "rollout_max_generation_lag": -1 if max_generation_lag is None else max_generation_lag,
        "rollout_generation_lag_guard_passed": -1 if max_generation_lag is None else int(generation_lag <= max_generation_lag),
        "rollout_available_condition_false": summary["available_false"],
        "rollout_replica_failure_true": summary["replica_failure_true"],
        "rollout_progress_deadline_exceeded": summary["progress_deadline_exceeded"],
        "rollout_updated_replicas": status.get("updatedReplicas", 0),
        "rollout_unavailable_replicas": status.get("unavailableReplicas", 0),
    }
    lines = [
        "# HELP rollout_state_code Checker result code (0 complete, 1 waiting, 2 stalled, 4 image mismatch, 5 revision mismatch, 6 observed-generation mismatch).",
        "# TYPE rollout_state_code gauge",
    ]
    state = STATE_NAMES[code]
    lines.extend([
        "# HELP rollout_state_info Current rollout state as a readable label.",
        "# TYPE rollout_state_info gauge",
        f'rollout_state_info{{state="{state}"}} 1',
    ])
    help_text = {
        "rollout_report_schema_version": "JSON and metrics report schema version.",
        "rollout_generation_lag": "Metadata generation minus controller-observed generation.",
        "rollout_desired_replicas": "Desired Deployment replica count.",
        "rollout_available_replicas": "Deployment replicas available for service.",
        "rollout_ready_replicas": "Deployment replicas currently reporting Ready.",
        "rollout_ready_gap": "Desired replicas minus Ready replicas, clamped at zero.",
        "rollout_min_ready_replicas": "Configured minimum Ready replica policy, or -1 when unset.",
        "rollout_ready_guard_passed": "Minimum Ready replica guard result: 1 pass, 0 fail, -1 unset.",
        "rollout_max_generation_lag": "Configured maximum generation lag policy, or -1 when unset.",
        "rollout_generation_lag_guard_passed": "Generation lag guard result: 1 pass, 0 fail, -1 unset.",
        "rollout_available_condition_false": "Count of Available=False Deployment conditions.",
        "rollout_replica_failure_true": "Count of ReplicaFailure=True Deployment conditions.",
        "rollout_progress_deadline_exceeded": "Count of ProgressDeadlineExceeded conditions.",
        "rollout_updated_replicas": "Deployment replicas updated to the current template.",
        "rollout_unavailable_replicas": "Deployment replicas currently unavailable.",
    }
    for name, value in values.items():
        if name != "rollout_state_code":
            lines.extend([f"# HELP {name} {help_text[name]}", f"# TYPE {name} gauge"])
        lines.append(f"{name} {value}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="emit a JSON result on stdout")
    output.add_argument("--metrics", action="store_true", help="emit OpenMetrics-style gauges on stdout")
    parser.add_argument("--expect-generation", type=int, help="require an exact Deployment metadata.generation")
    parser.add_argument("--expect-observed-generation", type=int, help="require an exact controller-observed generation")
    parser.add_argument("--min-ready-replicas", type=int, help="require at least this many ready replicas")
    parser.add_argument("--max-generation-lag", type=int, help="fail when metadata.generation exceeds observedGeneration by more than this")
    parser.add_argument("--expect-revision", help="require an exact Deployment revision annotation after recovery")
    parser.add_argument(
        "--expect-image", nargs=2, metavar=("CONTAINER", "IMAGE"),
        help="verify that a named container uses the expected image after recovery",
    )
    args = parser.parse_args()
    ready_guard_passed = None
    generation_lag_guard_passed = None
    try:
        deployment = json.load(sys.stdin)
        code, message = classify(deployment)
        revision = deployment_revision(deployment)
        status = deployment.get("status", {})
        generation_lag = deployment["metadata"]["generation"] - status.get("observedGeneration", 0)
        if args.max_generation_lag is not None:
            if args.max_generation_lag < 0:
                raise ValueError("--max-generation-lag must be nonnegative")
            generation_lag_guard_passed = generation_lag <= args.max_generation_lag
            if code in (0, 1) and generation_lag > args.max_generation_lag:
                code = 6
                message = f"generation lag {generation_lag}, expected at most {args.max_generation_lag}"
        if args.expect_generation is not None and deployment["metadata"]["generation"] != args.expect_generation:
            code = 1
            message = f"snapshot generation {deployment["metadata"]["generation"]}, expected {args.expect_generation}"
        if args.expect_observed_generation is not None and status.get("observedGeneration", 0) != args.expect_observed_generation:
            code = 6
            message = f"observed generation {status.get('observedGeneration', 0)}, expected {args.expect_observed_generation}"
        if code == 0 and args.expect_image:
            name, expected = args.expect_image
            containers = deployment["spec"]["template"]["spec"]["containers"]
            actual = next(
                (container["image"] for container in containers if container["name"] == name),
                None,
            )
            if actual != expected:
                code = 4
                message = (
                    f"container {name} is missing" if actual is None
                    else f"container {name} uses {actual}, expected {expected}"
                )
        if code == 0 and args.expect_revision is not None and revision != args.expect_revision:
            code = 5
            message = f"deployment revision {revision}, expected {args.expect_revision}"
        if args.min_ready_replicas is not None:
            if args.min_ready_replicas < 0:
                raise ValueError("--min-ready-replicas must be nonnegative")
            ready = status.get("readyReplicas", status.get("availableReplicas", 0))
            ready_guard_passed = ready >= args.min_ready_replicas
            if code == 0 and ready < args.min_ready_replicas:
                code = 1
                message = f"ready replicas {ready}, expected at least {args.min_ready_replicas}"
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        print(f"invalid Deployment snapshot: {error}", file=sys.stderr)
        return 3
    if args.metrics:
        print(metrics_report(code, deployment, args.min_ready_replicas, args.max_generation_lag))
    elif args.json:
        state = STATE_NAMES[code]
        desired = deployment["spec"].get("replicas", 1)
        conditions = status.get("conditions", [])
        image_guard_passed = None if not args.expect_image or code not in (0, 4) else code == 0
        revision_guard_passed = None if not args.expect_revision or code not in (0, 5) else code == 0
        details = {
            "desired_replicas": desired,
            "deployment_generation": deployment["metadata"]["generation"],
            "observed_generation": status.get("observedGeneration", 0),
            "generation_lag": deployment["metadata"]["generation"] - status.get("observedGeneration", 0),
            "available_replicas": status.get("availableReplicas", 0),
            "ready_replicas": status.get("readyReplicas", status.get("availableReplicas", 0)),
            "ready_gap": max(
                desired - status.get("readyReplicas", status.get("availableReplicas", 0)),
                0,
            ),
            "policy": {
                "min_ready_replicas": args.min_ready_replicas,
                "max_generation_lag": args.max_generation_lag,
            },
            "updated_replicas": status.get("updatedReplicas", 0),
            "unavailable_replicas": status.get("unavailableReplicas", 0),
            "deployment_revision": revision,
            "container_images": container_images(deployment),
            "snapshot_sha256": evidence_fingerprint(deployment),
            "condition_summary": condition_summary(conditions),
            "verification": {
                "generation_converged": deployment["metadata"]["generation"] == status.get("observedGeneration", 0),
                "replica_counts_converged": all(
                    status.get(field, 0) == desired
                    for field in ("updatedReplicas", "replicas", "availableReplicas")
                ),
                "availability_condition_clear": not any(
                    condition.get("type") == "Available" and condition.get("status") == "False"
                    for condition in conditions
                ),
                "failure_conditions_clear": not any(
                    (condition.get("type") == "ReplicaFailure" and condition.get("status") == "True")
                    or (condition.get("type") == "Progressing" and condition.get("status") == "False" and condition.get("reason") == "ProgressDeadlineExceeded")
                    for condition in conditions
                ),
                "image_guard_passed": image_guard_passed,
                "revision_guard_passed": revision_guard_passed,
                "ready_guard_passed": ready_guard_passed,
                "generation_lag_guard_passed": generation_lag_guard_passed,
            },
            "conditions": [
                {key: condition.get(key) for key in ("type", "status", "reason", "message", "lastUpdateTime", "lastTransitionTime") if condition.get(key) is not None}
                for condition in status.get("conditions", [])
            ],
        }
        print(json.dumps({
            "schema_version": REPORT_SCHEMA_VERSION,
            "state": state,
            "exit_code": code,
            "message": message,
            "recommended_action": recommended_action(code),
            "details": details,
        }))
    else:
        print(message)
    return code


if __name__ == "__main__":
    sys.exit(main())

