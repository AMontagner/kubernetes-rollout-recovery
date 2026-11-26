<!-- weekly-project: 2026-11-23 -->
# Kubernetes Rollout Recovery

**Operational companion:** [Incident response runbook](INCIDENT_RESPONSE.md) for evidence capture, failure classification, rollback, and verification.

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
  python3 rollout_status.py --json --expect-image web nginx:1.28-alpine --expect-revision 7 < deployment-snapshot.json
```

A recovered deployment reports `"state": "complete"` and exits 0. If the rollout is healthy but the named container is missing or specifies a different image, the check reports `image_mismatch` and exits 4. If the revision annotation differs, it reports `revision_mismatch` and exits 5. Together these checks verify both the image reference and the Deployment revision reached after rollback. Use an image digest when you need immutable version identification.

For repeatable CI or incident checks, use the wrapper below. It fails fast if `kubectl` cannot capture the Deployment or if the recovered image does not match.

```sh
./verify_rollout.sh rollout-recovery rollout-web web nginx:1.28-alpine 2 7 incident/deployment.json 2
```

The optional seventh wrapper argument is an evidence path and the eighth is the expected controller-observed generation. When supplied, the exact Deployment JSON captured before verification is copied there even if an image or revision guard fails, while the observed-generation guard proves the controller acknowledged the intended update:

```sh
./verify_rollout.sh rollout-recovery rollout-web web nginx:1.28-alpine 2 7 incident/deployment.json 2
```

Keep that snapshot with the JSON report and rollout history so incident review can reproduce the decision from the same evidence.

When finished, remove the example Deployment.

```sh
kubectl -n rollout-recovery delete deployment rollout-web
```

## Reproduce offline scenarios

The committed fixtures exercise the same CLI used in CI:

```sh
python3 rollout_status.py --json < fixtures/healthy.json
python3 rollout_status.py --json < fixtures/stalled-replica-failure.json
python3 rollout_status.py --json < fixtures/waiting-available.json
python3 rollout_status.py --json < fixtures/waiting-unavailable.json
python3 rollout_status.py --json < fixtures/stale-generation.json
python3 rollout_status.py --json --expect-image web nginx:1.28-alpine --expect-revision 7 < fixtures/rollback-verified.json
```

The healthy fixture exits 0. The stalled fixture exits 2 and preserves the controller failure reason in the JSON message. The availability fixture exits 1 even though replica counters match, demonstrating why the controller's `Available=False` signal must be honored. The unavailable-replica fixture exits 1 and reports the count that still needs to recover. The stale-generation fixture exits 1 while the controller catches up, preventing a matching replica count from being mistaken for validation of the newest revision. The rollback fixture exits 0 only when both the expected image and revision annotation match. Together these fixtures give reviewers deterministic health outcomes without a cluster.

## Repeatable local validation

Run the same checks used by CI with `make verify`. This validates the shell wrapper, executes every committed fixture against its expected exit code and state, and runs the offline Python regression suite without requiring a Kubernetes cluster. The regression suite also feeds a committed snapshot through a fake `kubectl` binary to verify the wrapper passes image, generation, and revision guards correctly. GitHub Actions runs this same target for pushes and pull requests. Use `make fixtures` when you want the scenario matrix summary without the full test suite.

## Check deployment health from a snapshot

Use this dependency-free Python 3 check to evaluate captured Deployment status locally or in CI.

```sh
kubectl -n rollout-recovery get deployment rollout-web -o json > deployment-snapshot.json
python3 rollout_status.py --expect-generation 2 --expect-observed-generation 2 < deployment-snapshot.json
```

For monitoring integrations that consume OpenMetrics-style gauges, use the same snapshot with `--metrics`:

```sh
python3 rollout_status.py --metrics < deployment-snapshot.json
```

The metrics output includes the checker result code, generation lag, desired, available, updated, and unavailable replica counts. It keeps the same exit code semantics as the JSON mode, so a scrape can record gauges while CI still fails on a stalled or unverified rollout. The output contains no cluster credentials or mutable labels and is safe to generate from a read-only snapshot.

Exit codes are 0 for complete, 1 for waiting, 2 for an exceeded progress deadline, 3 for invalid input or a wrapper capture error, 4 for an expected image mismatch, 5 for a rollback revision mismatch, and 6 for an observed-generation mismatch. Pass `--expect-observed-generation` when the change record names the generation the controller must acknowledge; this prevents a healthy-looking snapshot from being accepted before the controller has processed the intended update. Pass the optional sixth wrapper argument, or `--expect-revision` directly, to require the exact `deployment.kubernetes.io/revision` annotation after recovery. This proves the check reached the intended Deployment revision instead of only matching replica counts. Malformed condition payloads or revision annotations fail with exit 3 before classification, preventing misleading health results. The JSON mode also returns desired, metadata generation, controller-observed generation, a computed `generation_lag`, available, updated, and unavailable replica counts plus the deployment revision and condition type, status, reason, and controller message for CI logs and incident triage. A zero lag proves the controller has observed the captured Deployment generation; a positive lag is a measurable convergence signal. Snapshots claiming an observed generation greater than metadata generation are rejected as invalid rather than producing misleading health data. An explicit `unavailableReplicas` count produces a waiting result with the remaining count, giving an operator a concrete recovery signal even when other counters are temporarily aligned. Comparing the two generation fields makes controller lag explicit in an incident timeline. It includes a deterministic `recommended_action` for each non-invalid result so an incident workflow can route the next step without parsing prose. The check evaluates a single snapshot. It also treats an explicit `Available=False` condition as waiting, even when replica counters temporarily match, so CI does not report a healthy rollout while Kubernetes is still declaring the minimum availability unavailable. Capture a fresh snapshot to check subsequent progress.
