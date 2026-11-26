#!/usr/bin/env bash
set -euo pipefail

namespace=${1:-rollout-recovery}
deployment=${2:-rollout-web}
container=${3:-web}
expected_image=${4:-nginx:1.28-alpine}
expected_generation=${5:-}
expected_revision=${6:-}
evidence_path=${7:-}
expected_observed_generation=${8:-}

snapshot=$(mktemp)
capture_error=$(mktemp)
cleanup() {
  rm -f "$snapshot" "$capture_error"
}
trap cleanup EXIT
if ! kubectl -n "$namespace" get deployment "$deployment" -o json >"$snapshot" 2>"$capture_error"; then
  printf '%s\n' '{"state":"capture_error","exit_code":3,"message":"kubectl could not capture Deployment JSON","recommended_action":"verify kubectl access, namespace, and Deployment name"}'
  exit 3
fi
if [[ -n "$evidence_path" ]]; then
  cp "$snapshot" "$evidence_path"
fi
checker_args=(--json --expect-image "$container" "$expected_image")
if [[ -n "$expected_generation" ]]; then
  checker_args+=(--expect-generation "$expected_generation")
fi
if [[ -n "$expected_revision" ]]; then
  checker_args+=(--expect-revision "$expected_revision")
fi
if [[ -n "$expected_observed_generation" ]]; then
  checker_args+=(--expect-observed-generation "$expected_observed_generation")
fi
python3 rollout_status.py "${checker_args[@]}" <"$snapshot"
