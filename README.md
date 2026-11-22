<!-- weekly-project: 2026-11-23 -->
# Kubernetes Rollout Lab

Practice diagnosing a stalled Deployment and recovering with a rollback. Use a disposable local Kubernetes cluster and `kubectl`. The example uses a dedicated namespace.

## Deploy a healthy baseline

```sh
kubectl create namespace rollout-lab
kubectl apply -f deployment.json
kubectl -n rollout-lab rollout status deployment/rollout-web --timeout=120s
```

## Stall the rollout

Use an invalid image hostname to trigger an image pull failure. The rolling update permits one extra Pod and keeps the healthy replicas available.

```sh
kubectl -n rollout-lab set image deployment/rollout-web web=registry.invalid/rollout-web:broken
kubectl -n rollout-lab get pods -l app=rollout-web
kubectl -n rollout-lab describe deployment rollout-web
kubectl -n rollout-lab rollout status deployment/rollout-web --timeout=110s
```

The last command should fail. Inspect the new Pod's events with `kubectl -n rollout-lab describe pod <new-pod-name>` to confirm the image pull failure.

## Recover and clean up

```sh
kubectl -n rollout-lab rollout undo deployment/rollout-web
kubectl -n rollout-lab rollout status deployment/rollout-web --timeout=120s
kubectl -n rollout-lab delete deployment rollout-web
```
