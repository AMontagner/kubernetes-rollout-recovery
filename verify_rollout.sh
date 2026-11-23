#!/usr/bin/env bash
set -euo pipefail

namespace=${1:-rollout-recovery}
deployment=${2:-rollout-web}
container=${3:-web}
expected_image=${4:-nginx:1.28-alpine}

snapshot=$(mktemp)
trap 'rm -f "$snapshot"' EXIT
kubectl -n "$namespace" get deployment "$deployment" -o json >"$snapshot"
python3 rollout_status.py --json --expect-image "$container" "$expected_image" <"$snapshot"
