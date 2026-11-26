"""Check a Kubernetes Deployment snapshot without cluster access."""
import argparse
import json
import sys


def nonnegative_integer(value, field):
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a nonnegative integer")
    return value


def validate_conditions(conditions):
    if type(conditions) is not list or any(type(condition) is not dict for condition in conditions):
        raise ValueError("status.conditions must be a list of objects")
    return conditions


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
    replica_counts = [
        nonnegative_integer(status.get(field, 0), f"status.{field}")
        for field in ("updatedReplicas", "replicas", "availableReplicas")
    ]
    if observed < generation:
        return 1, "waiting for controller to observe the latest generation"
    conditions = validate_conditions(status.get("conditions", []))
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
    }[code]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit a JSON result on stdout")
    parser.add_argument("--expect-generation", type=int, help="require an exact Deployment metadata.generation")
    parser.add_argument("--expect-revision", help="require an exact Deployment revision annotation after recovery")
    parser.add_argument(
        "--expect-image", nargs=2, metavar=("CONTAINER", "IMAGE"),
        help="verify that a named container uses the expected image after recovery",
    )
    args = parser.parse_args()
    try:
        deployment = json.load(sys.stdin)
        code, message = classify(deployment)
        revision = deployment_revision(deployment)
        if args.expect_generation is not None and deployment["metadata"]["generation"] != args.expect_generation:
            code = 1
            message = f"snapshot generation {deployment["metadata"]["generation"]}, expected {args.expect_generation}"
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
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        print(f"invalid Deployment snapshot: {error}", file=sys.stderr)
        return 3
    if args.json:
        state = {0: "complete", 1: "waiting", 2: "stalled", 4: "image_mismatch", 5: "revision_mismatch"}[code]
        status = deployment.get("status", {})
        details = {
            "desired_replicas": deployment["spec"].get("replicas", 1),
            "deployment_generation": deployment["metadata"]["generation"],
            "observed_generation": status.get("observedGeneration", 0),
            "generation_lag": deployment["metadata"]["generation"] - status.get("observedGeneration", 0),
            "available_replicas": status.get("availableReplicas", 0),
            "updated_replicas": status.get("updatedReplicas", 0),
            "deployment_revision": revision,
            "conditions": [
                {key: condition.get(key) for key in ("type", "status", "reason", "message") if condition.get(key) is not None}
                for condition in status.get("conditions", [])
            ],
        }
        print(json.dumps({
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
