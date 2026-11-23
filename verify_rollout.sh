#!/usr/bin/env bash
set -euo pipefail

namespace=${1:-rollout-recovery}
deployment=${2:-rollout-web}
expected_image=${3:-nginx:1.28-alpine}

snapshot=$(mktemp)
trap 'rm -f "$snapshot"' EXIT
kubectl -n "$namespace" get deployment "$deployment" -o json >"$snapshot"
python3 rollout_status.py --json --expect-image web "$expected_image" <"$snapshot"
