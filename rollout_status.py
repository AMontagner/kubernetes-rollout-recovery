"""Check a Kubernetes Deployment snapshot without cluster access."""
import json
import sys


def classify(deployment):
    if deployment["kind"] != "Deployment":
        raise ValueError("expected a Deployment")
    generation = deployment["metadata"]["generation"]
    desired = deployment["spec"].get("replicas", 1)
    status = deployment.get("status", {})
    if status.get("observedGeneration", 0) < generation:
        return 1, "waiting for controller to observe the latest generation"
    if any(
        condition.get("type") == "Progressing"
        and condition.get("status") == "False"
        and condition.get("reason") == "ProgressDeadlineExceeded"
        for condition in status.get("conditions", [])
    ):
        return 2, "rollout exceeded its progress deadline"
    if all(
        status.get(field, 0) == desired
        for field in ("updatedReplicas", "replicas", "availableReplicas")
    ):
        return 0, f"rollout complete: {desired} updated and available replicas"
    return 1, "waiting for updated replicas to become available and old replicas to terminate"


def main():
    try:
        code, message = classify(json.load(sys.stdin))
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        print(f"invalid Deployment snapshot: {error}", file=sys.stderr)
        return 3
    print(message)
    return code


if __name__ == "__main__":
    sys.exit(main())
