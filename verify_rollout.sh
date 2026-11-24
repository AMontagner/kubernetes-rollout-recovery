#!/usr/bin/env bash
set -euo pipefail

namespace=${1:-rollout-recovery}
deployment=${2:-rollout-web}
container=${3:-web}
expected_image=${4:-nginx:1.28-alpine}
expected_generation=${5:-}

snapshot=$(mktemp)
trap 'rm -f "$snapshot"' EXIT
kubectl -n "$namespace" get deployment "$deployment" -o json >"$snapshot"
checker_args=(--json --expect-image "$container" "$expected_image")
if [[ -n "$expected_generation" ]]; then
  checker_args+=(--expect-generation "$expected_generation")
fi
python3 rollout_status.py "${checker_args[@]}" <"$snapshot"
