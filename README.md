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

A recovered deployment reports `"state": "complete"` and exits 0. If the rollout is healthy but the named container is missing or specifies a different image, the check reports `image_mismatch` and exits 4. If the revision annotation differs, it reports `revision_mismatch` and exits 5. Together these checks verify both the image reference and the Deployment revision reached after rollback. Use `--min-ready-replicas N` when recovery must prove a readiness floor; a complete snapshot below that threshold returns waiting (1) with the observed and expected counts. Use `--max-generation-lag N` to enforce a controller-convergence SLO; a stale snapshot beyond that bound returns observed-generation mismatch (6) with the measured lag. Use an image digest when you need immutable version identification.

For repeatable CI or incident checks, use the wrapper below. It fails fast if `kubectl` cannot capture the Deployment or if the recovered image does not match.

```sh
./verify_rollout.sh rollout-recovery rollout-web web nginx:1.28-alpine 2 7 incident/deployment.json 2
```

The optional seventh wrapper argument is an evidence path, the eighth is the expected controller-observed generation, the ninth is a minimum ready-replica threshold, and the tenth is the maximum allowed generation lag. When supplied, the exact Deployment JSON captured before verification is copied there even if an image, revision, readiness, or convergence guard fails; the observed-generation guard proves the controller acknowledged the intended update, the readiness guard proves enough Pods are ready, and the lag guard enforces a bounded convergence SLO:

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

Run the same checks used by CI with `make verify`. This validates the shell wrapper, executes every committed fixture against its expected exit code and state, checks the OpenMetrics contract on a healthy snapshot and a policy-breach snapshot, and runs the offline Python regression suite without requiring a Kubernetes cluster. The regression suite also feeds a committed snapshot through a fake `kubectl` binary to verify the wrapper passes image, generation, and revision guards correctly. GitHub Actions runs this same target for pushes and pull requests. Use `make fixtures` when you want the scenario matrix summary without the full test suite.

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

The metrics output includes a report schema version, numeric checker result code, a readable `rollout_state_info` label, generation lag, desired, available, ready, updated, and unavailable replica counts. It also exports counts for `Available=False`, `ReplicaFailure=True`, and `ProgressDeadlineExceeded` conditions so dashboards can alert on incident signals directly. Each gauge includes Prometheus `HELP` metadata describing its meaning. `readyReplicas` is preserved when Kubernetes reports it; snapshots from older clients fall back to `availableReplicas` so existing evidence remains readable. When policy flags are supplied, the output also exposes the configured thresholds and `rollout_ready_guard_passed` / `rollout_generation_lag_guard_passed` gauges (1 pass, 0 fail, -1 not requested). It keeps the same exit code semantics as the JSON mode, so a scrape can record gauges while CI still fails on a stalled or unverified rollout. The output contains no cluster credentials or mutable labels and is safe to generate from a read-only snapshot.

JSON details also expose `ready_replicas` alongside the available, updated, and unavailable counts. This keeps readiness visible for incident triage without changing the existing rollout classification; snapshots that predate `readyReplicas` use `availableReplicas` as a compatibility fallback. The `condition_summary` object counts `Available=False`, `ReplicaFailure=True`, and `ProgressDeadlineExceeded` conditions so incident automation can route on structured signals without scanning the full condition list. When a Pod template is present, `container_images` records the image reference for each named container so rollback evidence is self-contained.

The JSON and metrics reports also calculate a non-negative `ready_gap`: desired replicas minus ready replicas. A positive gap makes readiness debt visible to dashboards and incident timelines even when available capacity is temporarily unchanged. When policy guards are supplied, `details.verification.ready_guard_passed` and `details.verification.generation_lag_guard_passed` record their boolean results (`null` means the guard was not requested), while `details.policy` records the exact thresholds used. This makes an incident artifact self-describing and lets CI tooling consume the decision without parsing prose.

Exit codes are 0 for complete, 1 for waiting, 2 for an exceeded progress deadline, 3 for invalid input or a wrapper capture error, 4 for an expected image mismatch, 5 for a rollback revision mismatch, and 6 for an observed-generation mismatch. JSON reports include `schema_version: 1`; consumers should check this field before relying on the documented keys. The `details.verification` object exposes explicit booleans for generation convergence, replica convergence, availability and failure conditions, plus whether optional image and revision guards passed (`null` means that guard was not evaluated). Condition evidence preserves `lastUpdateTime` and `lastTransitionTime` when Kubernetes provides them, keeping failure and recovery timing available for incident timelines. Pass `--expect-observed-generation` when the change record names the generation the controller must acknowledge; this prevents a healthy-looking snapshot from being accepted before the controller has processed the intended update. Pass the optional sixth wrapper argument, or `--expect-revision` directly, to require the exact `deployment.kubernetes.io/revision` annotation after recovery. This proves the check reached the intended Deployment revision instead of only matching replica counts. Malformed condition payloads or revision annotations fail with exit 3 before classification, preventing misleading health results. The JSON mode also returns desired, metadata generation, controller-observed generation, a computed `generation_lag`, available, updated, and unavailable replica counts plus the deployment revision and condition type, status, reason, message, and timestamps for CI logs and incident triage. A zero lag proves the controller has observed the captured Deployment generation; a positive lag is a measurable convergence signal. Snapshots claiming an observed generation greater than metadata generation are rejected as invalid rather than producing misleading health data. An explicit `unavailableReplicas` count produces a waiting result with the remaining count, giving an operator a concrete recovery signal even when other counters are temporarily aligned. Comparing the two generation fields makes controller lag explicit in an incident timeline. It includes a deterministic `recommended_action` for each non-invalid result so an incident workflow can route the next step without parsing prose. The check evaluates a single snapshot. It also treats an explicit `Available=False` condition as waiting, even when replica counters temporarily match, so CI does not report a healthy rollout while Kubernetes is still declaring the minimum availability unavailable. Capture a fresh snapshot to check subsequent progress.
