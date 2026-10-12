# Clinical Co-Pilot agent service

A FastAPI service that answers questions about one patient chart at a time. It is read-only, it only talks to OpenEMR
with the logged-in physician's own OAuth token, and every answer passes a verifier before it is shown.
Synthetic data only. Design: [../ARCHITECTURE.md](../ARCHITECTURE.md). Authorization findings: [AUTHZ_PROOF.md](AUTHZ_PROOF.md).
On-call steps: [RUNBOOK.md](RUNBOOK.md).

```
Browser -> oe-module-copilot (PHP, session + CSRF, signs request) -> agent (FastAPI)
                                                                      |-> OpenEMR FHIR (user's token)
                                                                      |-> OpenRouter / Claude Sonnet (or stub)
                                                                      '-> SQLite: traces + HMAC audit chain
```

## Setup (local, about 10 minutes after the stack is up)

Needs Docker. The host needs only Python 3.10+ if you want to run the tests outside the container.

1. **Start OpenEMR and seed it.** From the repo root: `./scripts/setup-local.sh` (see the root README).
2. **Create `agent/.env`.** `cp agent/.env.example agent/.env`, then fill in:
   - `MODULE_SIGNING_SECRET`, `AUDIT_HMAC_KEY`: `openssl rand -hex 32` each.
   - `DASHBOARD_TOKEN`: `openssl rand -hex 16`.
   - `COPILOT_FIXTURE_PASSWORD`: 12+ characters, used for the seeded test users.
   - `LLM_PROVIDER=stub` for no-network deterministic answers, or `openrouter` plus `OPENROUTER_API_KEY`.
   `agent/.env` is git-ignored. The key is never sent to the browser and never logged.
3. **Seed the authorization fixture** (two physicians, a nurse, a front-desk user, patients split between the
   physicians, one active CareTeam per patient):
   ```bash
   docker exec -e COPILOT_FIXTURE_PASSWORD="$(grep ^COPILOT_FIXTURE_PASSWORD agent/.env | cut -d= -f2)" \
     development-easy-light-openemr-1 php /var/www/localhost/htdocs/openemr/scripts/authz_fixture.php
   ```
   It is idempotent and only adds rows. Checkpoint 1 patients are not changed.
4. **OAuth client.** The agent's client is a confidential authorization-code client with read scopes only
   (`user/Patient.rs`, `user/CareTeam.rs`, ... see AUTHZ_PROOF.md). Register it with OpenEMR's dynamic registration
   (`POST /oauth2/default/registration`), enable it (`oauth_clients.is_enabled=1`, the equivalent of admin approval),
   and put `OAUTH_CLIENT_ID` and `OAUTH_CLIENT_SECRET` in `agent/.env`. The password grant must stay off
   (`oauth_password_grant=0`); the tests check that it is refused.
5. **Module.** `cp interface/modules/custom_modules/oe-module-copilot/config.local.php.example .../config.local.php`
   and set `signing_secret` (same as `MODULE_SIGNING_SECRET`), `oauth_client_id`, and `agent_base_url`
   (`http://agent:8400`). Then `./scripts/copilot_register_module.sh` registers and activates the module.
6. **Start the agent:** `docker compose -f agent/docker-compose.yml up -d --build`. It joins OpenEMR's compose network
   and is published on `127.0.0.1:8400` only.
7. **Check it:** `curl localhost:8400/ready` returns `"status":"ready"`. Log in at https://localhost:9300 as
   `copilot_doc_a`, open one of that physician's patients, and the dock appears at the bottom right.

## Tests

| Suite | Command | What it covers |
|---|---|---|
| Agent unit (no network) | `cd agent && .venv/bin/python -m pytest` | Contracts, tools, loop, verifier, rules, audit chain, tracing, metrics, dashboard, CLI |
| Agent live (needs the stack) | `cd agent && OE_LIVE=1 .venv/bin/python -m pytest` | Real OAuth flow, token expiry and revocation, cross-physician denial, FHIR coverage |
| Evals, stub tier | `PYTHONPATH=agent agent/.venv/bin/python -m evals.run` | 56 cases tagged by failure mode, results in `evals/results/` |
| Evals, live Sonnet | `EVAL_MAX_USD=1.00 PYTHONPATH=agent agent/.venv/bin/python -m evals.run --tier live` | Same invariants on real model output, spend capped, opt-in |
| Module (PHPUnit isolated) | `docker exec development-easy-light-openemr-1 sh -c 'cd /var/www/localhost/htdocs/openemr && vendor/bin/phpunit -c phpunit-isolated.xml --filter Copilot'` | Request signing parity, pid to UUID resolution |
| Browser | `./scripts/e2e_copilot.sh` | Playwright, 24 tests (OpenEMR smoke plus Co-Pilot dock, citations, declines, denials, LLM-down), normal and LLM-down modes |
| HTTP checks | `npx @usebruno/cli run --env local --insecure` in `docs/api-collection/copilot` | Liveness, refusals, dashboard |

## Endpoints

| Path | Who calls it | Auth |
|---|---|---|
| `GET /health` | compose, monitors | none, liveness only |
| `GET /ready` | operators | none; reports OpenEMR, LLM provider, trace store, audit chain |
| `POST /v1/chat` (SSE) | the OpenEMR module only | HMAC signature (integrity) plus the user's bearer token and a patient-level decision on every call |
| `POST /v1/tools/{name}` | the module / tests | same as chat |
| `GET /dashboard`, `/dashboard.json` | operator | HTTP Basic, password `DASHBOARD_TOKEN`; 404 when unset |

The agent port is not published through the tunnel. Only OpenEMR is.

## Observability

- **Trace store** (`data/traces.db`): one trace per request, one span per step (gateway, each tool, LLM call, verifier)
  with latency, status, retries, tokens and cost.
- **Audit chain** (`data/audit.db`): append-only, one record per tool call and per denial, HMAC-chained so editing or
  deleting any row breaks verification. `python -m app.cli verify-audit` (exit 3 if tampered), `export-audit` writes
  JSONL for storage the app cannot rewrite.
- **Dashboard:** requests by outcome, errors, p50 and p95, tool calls and failures per tool, retries, verification
  pass rate and dropped claims, tokens, cost per request, alert states.
- **Alerts:** `python -m app.cli alerts` (exit 0 clear, 2 firing). Three rules: p95 latency above 12 s over 5 minutes,
  error rate above 5% over 10 minutes, tool failure rate above 10% over 10 minutes. Each has a response in RUNBOOK.md.

## Not in this checkpoint

Load tests (10 and 50 concurrent users) and the formal AI cost analysis are deferred to Checkpoint 3. Per-request
token and cost data is already stored in the traces for that work.
