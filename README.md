<!-- weekly-project: 2026-11-23 -->
# Kubernetes Rollout Recovery

A reproducible Kubernetes deployment failure and recovery workflow. Diagnose a stalled rolling update, inspect Pod events, and verify rollback to a healthy revision. Use a disposable local Kubernetes cluster and `kubectl`. The example uses a dedicated namespace.

## Deploy a healthy baseline

```sh
kubectl create namespace rollout-recovery
kubectl apply -f deployment.json
kubectl -n rollout-recovery rollout status deployment/rollout-web --timeout=120s
```

## Stall the rollout

Use an invalid image hostname to trigger an image pull failure. The rolling update permits one extra Pod and keeps the healthy replicas available.

```sh
kubectl -n rollout-recovery set image deployment/rollout-web web=registry.invalid/rollout-web:broken
kubectl -n rollout-recovery get pods -l app=rollout-web
kubectl -n rollout-recovery describe deployment rollout-web
kubectl -n rollout-recovery rollout status deployment/rollout-web --timeout=110s
```

The last command should fail. Inspect the new Pod's events with `kubectl -n rollout-recovery describe pod <new-pod-name>` to confirm the image pull failure.

## Recover and verify

```sh
kubectl -n rollout-recovery rollout undo deployment/rollout-web
kubectl -n rollout-recovery rollout status deployment/rollout-web --timeout=120s
```

After rollback, verify the expected image and that every desired replica is available. Run the capture and check in the same shell so a failed capture prevents verification.

```sh
kubectl -n rollout-recovery get deployment rollout-web -o json > deployment-snapshot.json &&
  python3 rollout_status.py --json --expect-image web nginx:1.28-alpine < deployment-snapshot.json
```

A recovered deployment reports `"state": "complete"` and exits 0. If the rollout is healthy but the named container is missing or specifies a different image, the check reports `image_mismatch` and exits 4. This verifies the image reference in the Deployment template. Use an image digest when you need immutable version identification.

For repeatable CI or incident checks, use the wrapper below. It fails fast if `kubectl` cannot capture the Deployment or if the recovered image does not match.

```sh
./verify_rollout.sh rollout-recovery rollout-web web nginx:1.28-alpine 2
```

When finished, remove the example Deployment.

```sh
kubectl -n rollout-recovery delete deployment rollout-web
```

## Repeatable local validation

Run the same checks used by CI with `make verify`. This validates the shell wrapper and executes the offline Python regression suite without requiring a Kubernetes cluster.

## Check deployment health from a snapshot

Use this dependency-free Python 3 check to evaluate captured Deployment status locally or in CI.

```sh
kubectl -n rollout-recovery get deployment rollout-web -o json > deployment-snapshot.json
python3 rollout_status.py --expect-generation 2 < deployment-snapshot.json
```

Exit codes are 0 for complete, 1 for waiting, 2 for an exceeded progress deadline, 3 for invalid input, and 4 for an expected image mismatch. The JSON mode also returns desired, observed, available, and updated replica counts plus a compact condition summary for CI logs and incident triage. The check evaluates a single snapshot. It also treats an explicit `Available=False` condition as waiting, even when replica counters temporarily match, so CI does not report a healthy rollout while Kubernetes is still declaring the minimum availability unavailable. Capture a fresh snapshot to check subsequent progress.
