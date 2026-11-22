"""Check a Kubernetes Deployment snapshot without cluster access."""
import argparse
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit a JSON result on stdout")
    parser.add_argument(
        "--expect-image", nargs=2, metavar=("CONTAINER", "IMAGE"),
        help="verify that a named container uses the expected image after recovery",
    )
    args = parser.parse_args()
    try:
        deployment = json.load(sys.stdin)
        code, message = classify(deployment)
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
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        print(f"invalid Deployment snapshot: {error}", file=sys.stderr)
        return 3
    if args.json:
        state = {0: "complete", 1: "waiting", 2: "stalled", 4: "image_mismatch"}[code]
        print(json.dumps({"state": state, "exit_code": code, "message": message}))
    else:
        print(message)
    return code


if __name__ == "__main__":
    sys.exit(main())
