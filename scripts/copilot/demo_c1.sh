#!/usr/bin/env bash
# C1 offline demo: upload the synthetic lab PDF twice, read it back, retrieve a guideline snippet.
# Uses the stub VLM and an in-process app with a fake OpenEMR; no network, no keys, no real PHI.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="${PYTHON:-$ROOT/agent/.venv/bin/python}"
cd "$ROOT/agent"
W2_ENABLED=true W2_USE_STUB_VLM=true PYTHONPATH="$ROOT/agent" "$PY" -m pytest -q -s tests/test_w2_api_unit.py -k "upload or retrieve" "$@"
echo
echo "Fixtures: agent/tests/fixtures/w2/ (all stamped SYNTHETIC). Live demo: set W2_ENABLED=true and keys, then use docs/api-collection/copilot/w2/."
