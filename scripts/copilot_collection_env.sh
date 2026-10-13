#!/usr/bin/env bash
# Prints `export` lines for the signed Bruno requests (docs/api-collection/copilot). Prints values only; writes nothing.
#
#   eval "$(scripts/copilot_collection_env.sh)"          # then: cd docs/api-collection/copilot && npx @usebruno/cli run ...
#
# Exports:
#   COPILOT_USER_UUID      practitioner UUID of the fixture physician (agent/data/fixture.json)
#   COPILOT_PATIENT_UUID   a patient on that physician's care team
#   COPILOT_OTHER_PATIENT  a patient on another physician's care team (must be refused)
#   COPILOT_USER_TOKEN     short-lived read-only user token minted by the module's own issuer (expires in minutes,
#                          so run this again before each collection run)
#   COPILOT_SIGNING_SECRET MODULE_SIGNING_SECRET from the git-ignored agent/.env (never printed on its own)
#   COPILOT_DASHBOARD_TOKEN DASHBOARD_TOKEN from agent/.env
# Synthetic data only. The values go into your shell, not into any file in the repo.
set -euo pipefail
cd "$(dirname "$0")/.."
CONTAINER="${OPENEMR_CONTAINER:-development-easy-light-openemr-1}"

read_env() { sed -n "s/^$1=//p" agent/.env | head -1; }

eval "$(python3 - <<'PY'
import json
fx = json.load(open("agent/data/fixture.json"))
doc_a, doc_b = fx["user_ids"]["copilot_doc_a"], fx["user_ids"]["copilot_doc_b"]
mine = next(p for p, owner in sorted(fx["patient_owner"].items()) if owner == doc_a)
other = next(p for p, owner in sorted(fx["patient_owner"].items()) if owner == doc_b)
print(f'USER_UUID={fx["user_uuids"]["copilot_doc_a"]}')
print(f'PATIENT={fx["patient_uuids"][mine]}')
print(f'OTHER={fx["patient_uuids"][other]}')
PY
)"

TOKEN="$(docker exec -u apache "$CONTAINER" php /var/www/localhost/htdocs/openemr/scripts/copilot_issue_probe.php \
  "$(read_env OAUTH_CLIENT_ID)" "$USER_UUID" | tail -n 1)"
[ -n "$TOKEN" ] || { echo "could not mint a user token (is the OpenEMR container up?)" >&2; exit 1; }

echo "export COPILOT_USER_UUID='$USER_UUID'"
echo "export COPILOT_PATIENT_UUID='$PATIENT'"
echo "export COPILOT_OTHER_PATIENT='$OTHER'"
echo "export COPILOT_USER_TOKEN='$TOKEN'"
echo "export COPILOT_SIGNING_SECRET='$(read_env MODULE_SIGNING_SECRET)'"
echo "export COPILOT_DASHBOARD_TOKEN='$(read_env DASHBOARD_TOKEN)'"
