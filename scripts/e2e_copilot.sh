#!/usr/bin/env bash
# Runs the full Playwright suite in two phases: normal agent (stub LLM), then agent with the LLM unavailable.
# The agent is restored to the stub provider afterwards. Needs the local dev stack up and agent/.env populated.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . agent/.env; set +a
COMPOSE="docker compose -f agent/docker-compose.yml"
restore() { sed -i.bak 's/^LLM_PROVIDER=.*/LLM_PROVIDER=stub/' agent/.env && rm -f agent/.env.bak; $COMPOSE up -d >/dev/null; }
trap restore EXIT
sed -i.bak 's/^LLM_PROVIDER=.*/LLM_PROVIDER=stub/' agent/.env && rm -f agent/.env.bak
$COMPOSE up -d --build >/dev/null; sleep 4
(cd tests/playwright && npx playwright test)
sed -i.bak 's/^LLM_PROVIDER=.*/LLM_PROVIDER=unavailable/' agent/.env && rm -f agent/.env.bak
$COMPOSE up -d >/dev/null; sleep 4
(cd tests/playwright && COPILOT_E2E_MODE=degraded npx playwright test specs/copilot-flow.spec.ts)
