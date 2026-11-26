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

The JSON report includes the exit code, replica counts, generation lag, unavailable replica count, and controller conditions for incident notes. Preserve the snapshot as evidence; it is the input used for every later decision.

## 2. Classify the failure

- **Waiting (1):** the controller has not observed the latest generation, replicas are still converging, or `Available=False` is present.
- **Stalled (2):** the progress deadline expired or Kubernetes reported `ReplicaFailure=True`. Record the condition reason and message.
- **Invalid input (3):** the snapshot is incomplete or contains invalid counter types; recapture it.
- **Image mismatch (4):** the recovered Deployment is healthy but does not use the expected image.
- **Revision mismatch (5):** the recovered Deployment is healthy but does not report the expected revision annotation.

## 3. Recover safely

For a failed image rollout, inspect the current and previous revisions, then roll back the disposable Deployment:

```sh
kubectl -n rollout-recovery rollout history deployment/rollout-web
kubectl -n rollout-recovery rollout undo deployment/rollout-web
./verify_rollout.sh rollout-recovery rollout-web web nginx:1.28-alpine 2 7 incident/deployment-after-rollback.json
```

The optional seventh argument preserves the exact `kubectl` snapshot at the supplied path before the checker runs. This keeps failed verification evidence available for triage instead of deleting the temporary capture.

Use `--expect-generation` when a known target generation is part of the change record. If the rollback target revision is known, verify it alongside the image:

```sh
./verify_rollout.sh rollout-recovery rollout-web web nginx:1.28-alpine 2 7
```

The optional sixth argument is the expected `deployment.kubernetes.io/revision` annotation. Keep the captured JSON, command output, expected revision, and `kubectl rollout history` output with the incident timeline.

## 4. Verify and close

A successful recovery requires exit code 0, the expected image, the intended revision annotation when supplied, and no active failure condition. Exit 5 (`revision_mismatch`) means the Deployment is healthy but the rollback target is not proven; stop promotion and reconcile the change record with `kubectl rollout history`. Confirm the rollout status once more, attach the JSON report to the incident record, and remove only the disposable example resources when finished.
