# Clinical Co-Pilot agent service

A FastAPI service that answers questions about one patient chart at a time. It is read-only, it only talks to OpenEMR
with the logged-in physician's own OAuth token, and every answer passes a verifier before it is shown.
Synthetic data only. Design: [../docs/week1/ARCHITECTURE.md](../docs/week1/ARCHITECTURE.md). Authorization findings: [AUTHZ_PROOF.md](AUTHZ_PROOF.md).
On-call steps: [RUNBOOK.md](RUNBOOK.md).

```
Browser -> oe-module-copilot (PHP, session + CSRF, signs request) -> agent (FastAPI)
                                                                      |-> OpenEMR FHIR (user's token)
                                                                      |-> OpenRouter / Claude Sonnet (or stub)
                                                                      '-> SQLite: traces + HMAC audit chain
```

## Setup (local, about 10 minutes after the stack is up)

Needs Docker. The host needs only Python 3.10+ if you want to run the tests outside the container.

1. **Start OpenEMR and seed it.** From the repo root: `./scripts/copilot/setup-local.sh` (see the root README).
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
     development-easy-light-openemr-1 php /var/www/localhost/htdocs/openemr/scripts/copilot/authz_fixture.php
   ```
   It is idempotent and only adds rows. Checkpoint 1 patients are not changed.
4. **OAuth client.** The agent's client is a confidential authorization-code client with read scopes only
   (`user/Patient.rs`, `user/CareTeam.rs`, ... see AUTHZ_PROOF.md). Register it with OpenEMR's dynamic registration
   (`POST /oauth2/default/registration`), enable it (`oauth_clients.is_enabled=1`, the equivalent of admin approval),
   and put `OAUTH_CLIENT_ID` and `OAUTH_CLIENT_SECRET` in `agent/.env`. The password grant must stay off
   (`oauth_password_grant=0`); the tests check that it is refused.
5. **Module.** `cp interface/modules/custom_modules/oe-module-copilot/config.local.php.example .../config.local.php`
   and set `signing_secret` (same as `MODULE_SIGNING_SECRET`), `oauth_client_id`, and `agent_base_url`
   (`http://agent:8400`). Then `./scripts/copilot/copilot_register_module.sh` registers and activates the module.
6. **Start the agent:** `docker compose -f agent/docker-compose.yml up -d --build`. It joins OpenEMR's compose network
   and is published on `127.0.0.1:8400` only.
7. **Check it:** `curl localhost:8400/ready` returns `"status":"ready"`. Log in at https://localhost:9300 as
   `copilot_doc_a`, open one of that physician's patients, and the dock appears at the bottom right.

## Tests

| Suite | Command | What it covers |
|---|---|---|
| Agent unit (no network) | `cd agent && .venv/bin/python -m pytest` | Contracts, tools, loop, verifier, rules, audit chain, tracing, metrics, dashboard, CLI |
| Agent live (needs the stack) | `cd agent && OE_LIVE=1 .venv/bin/python -m pytest` | Real OAuth flow, token expiry and revocation, cross-physician denial, FHIR coverage |
| Evals, stub tier | `PYTHONPATH=agent agent/.venv/bin/python -m evals.run` | 61 cases tagged by failure mode, results in `evals/results/` |
| Evals, live Sonnet | `EVAL_MAX_USD=1.00 PYTHONPATH=agent agent/.venv/bin/python -m evals.run --tier live` | Same invariants on real model output, spend capped, opt-in |
| Module (PHPUnit isolated) | `docker exec development-easy-light-openemr-1 sh -c 'cd /var/www/localhost/htdocs/openemr && vendor/bin/phpunit -c phpunit-isolated.xml --filter Copilot'` | Request signing parity, pid to UUID resolution |
| Browser | `./scripts/copilot/e2e_copilot.sh` | Playwright, 24 tests (OpenEMR smoke plus Co-Pilot dock, citations, declines, denials, LLM-down), normal and LLM-down modes |
| HTTP checks, including signed requests | `eval "$(scripts/copilot/copilot_collection_env.sh)"`, then `npx @usebruno/cli run --env stub --env-file environments/stub.bru --insecure` in `docs/api-collection/copilot` | Liveness, refusals, dashboard, signed UC-1/2/4, denial, tool contracts (stub model, no spend) |

**Live tests and the database root password.** A few live tests (token revocation and expiry) change `api_token` rows
through `docker exec ... mariadb -uroot`. That uses the `DB_ROOT_PASS` environment variable and falls back to the
stock development password, so on an unmodified dev stack nothing is needed. If you ran `scripts/copilot/tunnel.sh harden`, the
root password was rotated and the new value is in the git-ignored `deploy/tunnel/.env`. Load it into your shell for the
run, without printing it:

```bash
set -a; . deploy/tunnel/.env; set +a
cd agent && OE_LIVE=1 .venv/bin/python -m pytest
```

Without it, `test_expired_then_revoked_tokens_are_refused` fails at its first SQL statement with an access-denied error
from MariaDB (the agent code is not involved). Never put the value in a tracked file, a command line you share, or CI logs.

## Endpoints

| Path | Who calls it | Auth |
|---|---|---|
| `GET /health` | compose, monitors | none, liveness only |
| `GET /ready` | operators | none; reports OpenEMR, LLM provider, trace store, audit chain |
| `POST /v1/chat` (SSE) | the OpenEMR module only | HMAC signature (integrity) plus the user's bearer token and a patient-level decision on every call |
| `POST /v1/tools/{name}` | the module / tests | same as chat |
| `GET /dashboard`, `/dashboard.json` | operator | HTTP Basic, password `DASHBOARD_TOKEN`; 404 when unset. Shows in-flight and shed chats; the page refreshes every 15 s (`?refresh=N`, 0 = off) |

| `POST /v1/w2/documents/{id}/page` | the module only (click-to-source) | same signature and care-team check; the bytes must hash to the stored extraction. Full list: [../docs/week2/openapi.yaml](../docs/week2/openapi.yaml) |

The agent port is not published through the tunnel. Only OpenEMR is.

## Deployed section (Week 2)

`scripts/copilot/deploy_w2.sh` (dry run by default, `--apply` to act) brings the Week 2 stack up behind the existing
tunnel. It is idempotent and non-destructive: it uses `docker compose up -d` only, keeps `agent/.env` and the
`agent_data` volume, and checks that the agent is still bound to `127.0.0.1:8400` and not reachable through the tunnel
URL. Seeding is a separate, refused-by-default option. See the top-level README, "Deployed app (Week 2)".

## Observability

- **Trace store** (`data/traces.db`): one trace per request, one span per step (gateway, each tool, LLM call, verifier)
  with latency, status, retries, tokens and cost.
- **Audit chain** (`data/audit.db`): append-only, one record per tool call and per denial, HMAC-chained so editing or
  deleting any row breaks verification. `python -m app.cli verify-audit` (exit 3 if tampered), `export-audit` writes
  JSONL for storage the app cannot rewrite.
- **Dashboard:** requests by outcome, errors, p50 and p95, tool calls and failures per tool, retries, verification
  pass rate and dropped claims, tokens, cost per request, alert states.
- **Alerts:** `python -m app.cli alerts` (exit 0 clear, 2 firing). Three rules: p95 latency above 25 s over 5 minutes,
  error rate above 5% over 10 minutes, tool failure rate above 10% over 10 minutes. Each has a response in RUNBOOK.md.

## Load tests and performance (Checkpoint 3)

- Baselines, bottleneck analysis and rerun commands: [../docs/week1/PERFORMANCE.md](../docs/week1/PERFORMANCE.md).
- Cost per request and projections: [../docs/week1/AI_COST_ANALYSIS.md](../docs/week1/AI_COST_ANALYSIS.md).
- Harness: [../loadtest/](../loadtest/). It runs against a separate `agent-load` container (own data volume), so demo
  traces and the audit chain are untouched. Every report is labelled `stub`, `mock` or `real`. `real` runs need
  `LOADTEST_MAX_USD` (at most 2.00) and a fixed request count.

Behaviour added by the measurements:

- **LLM retry:** one retry with 0.5-1.0 s jittered backoff on a 429, a 5xx or a connect failure. Read timeouts are not
  retried. Counted in the trace `retries` field.
- **`MAX_INFLIGHT_CHATS`** (default `0`, off): over the limit, `/v1/chat` returns `503` with `Retry-After: 5`. Off by
  default because the 50-user results were mixed (PERFORMANCE.md).
- **p95 latency alert** moved from 12 s to 25 s; healthy real-model requests have p95 of about 19 s.

Constraints that stay: one uvicorn worker (nonce cache, rate limiter and conversation store are per-process memory),
authorization is a fresh decision on every call (no cache), and OpenEMR, not the agent, is the first thing to saturate.

## Not in this checkpoint

Public deployment, a 50-user run against the real model, and the comparison of recorded spend with the OpenRouter
account page (see the cost analysis, "What was not measured").
