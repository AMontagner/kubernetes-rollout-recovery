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

## Recover and clean up

```sh
kubectl -n rollout-recovery rollout undo deployment/rollout-web
kubectl -n rollout-recovery rollout status deployment/rollout-web --timeout=120s
kubectl -n rollout-recovery delete deployment rollout-web
```

## Check deployment health from a snapshot

Use this dependency-free Python 3 check to evaluate captured Deployment status locally or in CI.

```sh
kubectl -n rollout-recovery get deployment rollout-web -o json > deployment-snapshot.json
python3 rollout_status.py < deployment-snapshot.json
```

Exit codes are 0 for complete, 1 for waiting, 2 for an exceeded progress deadline, and 3 for invalid input. The check evaluates a single snapshot. Capture a fresh snapshot to check subsequent progress.
