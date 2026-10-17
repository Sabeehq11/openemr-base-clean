#!/usr/bin/env bash
# Deploy the Week 2 Co-Pilot behind the existing Cloudflare quick tunnel. Idempotent and non-destructive.
#
#   scripts/copilot/deploy_w2.sh                       dry run (default): report what would change, change nothing
#   scripts/copilot/deploy_w2.sh --apply               do only the steps that are not already done
#   scripts/copilot/deploy_w2.sh --apply --enable-w2   also switch W2_ENABLED to true when agent/.env has it false
#   scripts/copilot/deploy_w2.sh --apply --seed        DESTRUCTIVE opt-in: re-seed the demo patients
#
# Never done by this script: down, down -v, volume removal, unharden, re-rotating existing passwords, overwriting
# agent/.env, printing a secret, or exposing the agent. Seeding is off by default because seed_demo_patients.py deletes
# every pid>=100 patient (and their lists, encounters, labs, prescriptions) before re-inserting. With --seed it
# refuses when any such patient has a document or the agent holds any extraction.
# Each step prints: ok, already done, changed, would change, skipped or FAILED. Synthetic data only.
set -euo pipefail
ROOT="${W2_DEPLOY_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"
cd "$ROOT"

APPLY=0; SEED=0; ENABLE_W2=0
for a in "$@"; do
    case "$a" in
        --apply) APPLY=1 ;;
        --dry-run) APPLY=0 ;;
        --seed) SEED=1 ;;
        --enable-w2) ENABLE_W2=1 ;;
        -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $a" >&2; exit 2 ;;
    esac
done

OE="${OE_CONTAINER:-development-easy-light-openemr-1}"
DB="${DB_CONTAINER:-development-easy-light-mysql-1}"
AGENT="${AGENT_CONTAINER:-agent-agent-1}"
AGENT_URL="${AGENT_URL:-http://127.0.0.1:8400}"
TUNNEL_ENV="deploy/tunnel/.env"
URL_FILE="deploy/tunnel/url.txt"
PID_FILE="deploy/tunnel/cloudflared.pid"
AGENT_ENV="agent/.env"
AGENT_COMPOSE="agent/docker-compose.yml"
MODULE_CONFIG="interface/modules/custom_modules/oe-module-copilot/config.local.php"
failed=0

say() { printf '%-14s %s\n' "[$1]" "$2"; }
fail() { say FAILED "$1"; failed=1; }
verb() { if [ "$APPLY" = 1 ]; then echo changed; else echo "would change"; fi; }

db_pass() { if [ -f "$TUNNEL_ENV" ]; then grep -E '^DB_ROOT_PASS=' "$TUNNEL_ENV" | head -1 | cut -d= -f2-; else echo root; fi; }
sql() { docker exec "$DB" mariadb -uroot -p"$(db_pass)" openemr -N -e "$1" 2>/dev/null | tr -d '[:space:]'; }
agent_docs() {
    docker exec "$AGENT" python -c "import sqlite3;print(sqlite3.connect('file:/srv/agent/data/w2.db?mode=ro',uri=True).execute('select count(*) from w2_documents').fetchone()[0])" 2>/dev/null | tr -d '[:space:]' || true
}
env_value() { [ -f "$AGENT_ENV" ] && grep -E "^$1=" "$AGENT_ENV" | tail -1 | cut -d= -f2- || true; }

PAGE_ROUTE='/v1/w2/documents/{document_id}/page'
# True when the RUNNING agent's own OpenAPI document lists the C5 page route with POST. This is what the module's
# source.php calls; a container built before C5 answers it with a bare 404 and the viewer shows "page image not available".
agent_has_page_route() {
    curl -s --max-time 5 "$AGENT_URL/openapi.json" 2>/dev/null | python3 -c '
import json, sys
try:
    paths = json.load(sys.stdin)["paths"]
except Exception:
    sys.exit(1)
sys.exit(0 if "post" in paths.get(sys.argv[1], {}) else 1)' "$PAGE_ROUTE"
}
# After a recreate, wait (up to ~30 s) for /ready before the route is judged; nothing is retried beyond that.
wait_for_agent() {
    local i
    for i in $(seq 1 15); do
        curl -s --max-time 2 "$AGENT_URL/ready" 2>/dev/null | grep -q '"status"' && return 0
        sleep 2
    done
    return 0
}

counts() {
    local label="$1"
    say "$label" "patients(pid>=100)=$(sql 'select count(*) from patient_data where pid>=100' || echo '?') documents=$(sql 'select count(*) from documents where deleted=0' || echo '?') w2_documents=$(agent_docs || echo '?')"
}

say mode "$([ "$APPLY" = 1 ] && echo apply || echo 'dry run, nothing will be changed')"

# 1. Preflight, read only.
if [ -n "$(docker ps -q --filter "name=$OE" 2>/dev/null)" ] && [ -n "$(docker ps -q --filter "name=$DB" 2>/dev/null)" ]; then
    say ok "OpenEMR and database containers are up"
else
    fail "OpenEMR stack is not running (start it first; this script never starts the base stack from scratch)"
fi
counts "before"

# 2. Hardening: only when it has never been done. Existing passwords are never rotated again; unharden never runs.
if [ -f "$TUNNEL_ENV" ]; then
    say "already done" "hardening (deploy/tunnel/.env exists; credentials untouched)"
elif [ "$APPLY" = 1 ]; then
    if ./scripts/copilot/tunnel.sh harden >/dev/null; then say changed "hardened the stack"; else fail "tunnel.sh harden"; fi
else
    say "would change" "run tunnel.sh harden (no deploy/tunnel/.env yet)"
fi

# 2b. The agent stays private: published on 127.0.0.1 only, and the tunnel origin is OpenEMR only. Checked BEFORE the
# agent is recreated, so a public bind is refused instead of being started and then reported.
agent_private=1
# Read the RESOLVED config as JSON. Compose normalises every published port to {host_ip, target, published} and leaves
# host_ip out when the port is bound to every interface, so "no host_ip" is exposure, as is any non-loopback address.
# Exit 0 only when every published port is bound to loopback. Anything unreadable counts as exposed (fail closed).
agent_ports_private() {
    python3 -c '
import json, sys
try:
    services = json.load(sys.stdin)["services"]
except Exception:
    sys.exit(1)
for svc in services.values():
    for port in svc.get("ports") or []:
        if not isinstance(port, dict) or port.get("host_ip") not in ("127.0.0.1", "::1"):
            sys.exit(1)
sys.exit(0)'
}
if ! docker compose -f "$AGENT_COMPOSE" config --format json 2>/dev/null | agent_ports_private; then
    agent_private=0; fail "the agent publishes a port on a non-local address (or its config cannot be read) in $AGENT_COMPOSE"
elif grep -E 'cloudflared +tunnel' scripts/copilot/tunnel.sh | grep -qE '8400|agent'; then
    agent_private=0; fail "the tunnel origin points at the agent"
else
    say ok "agent is private (127.0.0.1:8400 only; tunnel origin is OpenEMR only)"
fi

# 3. Agent: keep agent/.env and the agent_data volume. `up -d` only: never down, never -v.
if [ ! -f "$AGENT_ENV" ]; then
    fail "agent/.env is missing. Create it from agent/.env.example (this script does not invent secrets)"
else
    w2="$(env_value W2_ENABLED)"
    if [ "$w2" = "true" ] || [ "$w2" = "1" ]; then
        say "already done" "W2_ENABLED is on in agent/.env"
    elif [ "$ENABLE_W2" = 1 ] && [ "$APPLY" = 1 ]; then
        cp "$AGENT_ENV" "$AGENT_ENV.bak"
        if grep -q '^W2_ENABLED=' "$AGENT_ENV"; then
            sed -i.tmp 's/^W2_ENABLED=.*/W2_ENABLED=true/' "$AGENT_ENV" && rm -f "$AGENT_ENV.tmp"
        else
            printf '\nW2_ENABLED=true\n' >> "$AGENT_ENV"
        fi
        say changed "W2_ENABLED=true (only that key; backup at agent/.env.bak)"
    else
        say "would change" "set W2_ENABLED=true in agent/.env (needs --apply --enable-w2; only that key is edited)"
    fi
    # `up -d --build` alone can build a new image and leave the OLD container running ("Running"), which serves stale
    # code. --force-recreate replaces only this one container with the current image; --no-deps leaves every other
    # service alone. The named volume and agent/.env are reused as they are: never down, never -v.
    if [ "$agent_private" != 1 ]; then
        say skipped "agent recreate (refused while the agent is not private)"
    elif [ "$APPLY" = 1 ]; then
        if docker compose -f "$AGENT_COMPOSE" up -d --build --no-deps --force-recreate agent >/dev/null; then
            say changed "agent recreated from the current image (volume and .env kept)"
            wait_for_agent
        else
            fail "agent compose up"
        fi
    else
        say "would change" "docker compose -f $AGENT_COMPOSE up -d --build --no-deps --force-recreate agent (no down, no -v)"
    fi
fi

# 4. Module registration (insert if missing) and module config (created only if missing, never overwritten).
if [ "$(sql "select count(*) from modules where mod_directory='oe-module-copilot' and mod_active=1" || echo 0)" = "1" ]; then
    say "already done" "module oe-module-copilot is registered and active"
elif [ "$APPLY" = 1 ]; then
    if DB_ROOT_PASS="$(db_pass)" ./scripts/copilot/copilot_register_module.sh >/dev/null; then say changed "module registered"; else fail "module registration"; fi
else
    say "would change" "register and activate oe-module-copilot"
fi
if [ -f "$MODULE_CONFIG" ]; then
    say "already done" "module config.local.php exists (not modified)"
elif [ ! -f "$AGENT_ENV" ]; then
    fail "cannot create the module config without agent/.env"
elif [ "$APPLY" = 1 ]; then
    umask 077
    printf "<?php\n\nreturn [\n    'agent_base_url' => 'http://agent:8400',\n    'signing_secret' => '%s',\n    'oauth_client_id' => '%s',\n    'w2_chat' => true,\n];\n" \
        "$(env_value MODULE_SIGNING_SECRET)" "$(env_value OAUTH_CLIENT_ID)" > "$MODULE_CONFIG"
    say changed "created module config.local.php from agent/.env (values not printed)"
else
    say "would change" "create module config.local.php from agent/.env"
fi
client="$(env_value OAUTH_CLIENT_ID)"
if [ -n "$client" ] && [ "$(sql "select count(*) from oauth_clients where client_id='$client'" || echo 0)" = "1" ]; then
    say "already done" "OAuth client is registered"
else
    say skipped "OAuth client not found. Create it with the steps in agent/RUNBOOK.md; this script never creates clients"
fi

# 5. Seeding: off by default. With --seed it refuses to run over existing documents or extractions.
if [ "$SEED" = 1 ]; then
    docs="$(sql "select count(*) from documents d where d.foreign_id>=100 and d.deleted=0" || echo 0)"
    w2docs="$(agent_docs || echo 0)"
    if [ "${docs:-0}" != "0" ] || [ "${w2docs:-0}" != "0" ]; then
        fail "--seed refused: demo patients have documents (${docs:-0}) or the agent has extractions (${w2docs:-0}). Seeding deletes pid>=100 patients"
    elif [ "$APPLY" = 1 ]; then
        echo "WARNING: seeding is destructive for pid>=100 patients (none have documents)."
        if python3 scripts/copilot/seed_demo_patients.py | docker exec -i "$DB" mariadb -uroot -p"$(db_pass)" openemr; then say changed "seeded demo patients"; else fail "seeding"; fi
    else
        say "would change" "seed demo patients (destructive for pid>=100; none have documents)"
    fi
else
    say skipped "seeding (existing patients and documents are used as they are)"
fi

# 6. Tunnel: tunnel.sh up runs the security gate first and skips when a tunnel is already running.
# `status` also treats a dead or reused PID as not running and clears the stale url.txt / pid file.
if running_url="$(./scripts/copilot/tunnel.sh status 2>/dev/null)"; then
    say "already done" "tunnel running: $running_url"
elif [ "$APPLY" = 1 ]; then
    if ./scripts/copilot/tunnel.sh up; then say changed "tunnel started"; else fail "tunnel up (security gate or cloudflared)"; fi
else
    say "would change" "start the tunnel (the security gate runs first)"
fi

# 7. Verify. The public URL must reach OpenEMR and must not reach the agent.
ready="$(curl -s --max-time 5 "$AGENT_URL/ready" 2>/dev/null | sed -n 's/.*"status" *: *"\([a-z_]*\)".*/\1/p' | head -1 || true)"
case "$ready" in
    ready|degraded) say ok "agent /ready is $ready" ;;
    *) fail "agent /ready is '${ready:-unreachable}'" ;;
esac
if agent_has_page_route; then
    say ok "the running agent exposes POST $PAGE_ROUTE (C5 click-to-source)"
else
    fail "the running agent does not expose POST $PAGE_ROUTE: it is an older build than this checkout. Recreate it with: docker compose -f $AGENT_COMPOSE up -d --build --no-deps --force-recreate agent"
fi
if [ -f "$URL_FILE" ]; then
    url="$(cat "$URL_FILE")"
    if curl -sk --max-time 10 "$url/health" 2>/dev/null | grep -q '"status" *: *"ok"'; then
        fail "the agent answers through the tunnel ($url/health). It must stay private"
    else
        say ok "tunnel serves OpenEMR only (agent /health is not reachable through it)"
    fi
    say info "public URL: $url (a quick-tunnel URL changes every time the tunnel starts)"
fi
counts "after"
[ "$failed" = 0 ] || { echo "One or more steps failed." >&2; exit 1; }
say done "$([ "$APPLY" = 1 ] && echo 'deploy finished' || echo 'dry run finished; nothing was changed')"
