#!/usr/bin/env bash
# Run all example payloads against a locally running service.
# Usage: ./examples/run_examples.sh [base_url]
set -euo pipefail

BASE="${1:-http://127.0.0.1:8000}"

for f in examples/*.json; do
  echo "=== ${f} ==="
  curl -sS -X POST "${BASE}/v1/precheck" \
    -H 'Content-Type: application/json' \
    --data-binary "@${f}" | python3 -m json.tool
  echo
done
