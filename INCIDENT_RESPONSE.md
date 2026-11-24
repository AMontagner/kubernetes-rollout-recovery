# Rollout incident response

Use this runbook when a Kubernetes Deployment rollout is not healthy. It is designed for read-only inspection first and can be followed with a disposable namespace.

## 1. Capture evidence

Set the namespace and Deployment name, then capture controller and Pod state:

```sh
kubectl -n rollout-recovery get deployment rollout-web -o wide
kubectl -n rollout-recovery get pods -l app=rollout-web -o wide
kubectl -n rollout-recovery describe deployment rollout-web
kubectl -n rollout-recovery get events --sort-by=.lastTimestamp
```

Save a machine-readable snapshot before changing anything:

```sh
kubectl -n rollout-recovery get deployment rollout-web -o json > deployment-snapshot.json
python3 rollout_status.py --json < deployment-snapshot.json
```

The JSON report includes the exit code, replica counts, and controller conditions for incident notes.

## 2. Classify the failure

- **Waiting (1):** the controller has not observed the latest generation, replicas are still converging, or `Available=False` is present.
- **Stalled (2):** the progress deadline expired or Kubernetes reported `ReplicaFailure=True`. Record the condition reason and message.
- **Invalid input (3):** the snapshot is incomplete or contains invalid counter types; recapture it.
- **Image mismatch (4):** the recovered Deployment is healthy but does not use the expected image.

## 3. Recover safely

For a failed image rollout, inspect the current and previous revisions, then roll back the disposable Deployment:

```sh
kubectl -n rollout-recovery rollout history deployment/rollout-web
kubectl -n rollout-recovery rollout undo deployment/rollout-web
./verify_rollout.sh rollout-recovery rollout-web web nginx:1.28-alpine
```

Use `--expect-generation` when a known target generation is part of the change record. Keep the captured JSON and command output with the incident timeline.

## 4. Verify and close

A successful recovery requires exit code 0, the expected image, and no active failure condition. Confirm the rollout status once more, attach the JSON report to the incident record, and remove only the disposable example resources when finished.
