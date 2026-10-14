# API collection (Bruno)

Two groups of requests:

- **Top level** (unsigned): liveness and readiness, the refusals (unsigned chat, unsigned tool call, dashboard without
  credentials, FHIR without a token) and the authenticated dashboard.
- **`signed/`**: the real authenticated flow, signed the way the OpenEMR module signs it (`agent/app/signing.py`).
  UC-1 brief, UC-2 what changed, UC-4 follow-up in the same conversation, a declined advice request, a denied patient,
  tool calls (`get_changes_since`, an invalid date, a denied patient) and one tampered signature that must get 401.

## Run it (default: stub model, no spend)

The default environment, `stub`, points at the load-test agent on port 8401, which uses the **stub model**. Running the
collection there costs nothing and never touches the demo agent's data or audit chain.

```bash
# 1. start the stub agent once (from the repo root)
docker compose -p agent-load -f loadtest/docker-compose.yml up -d --build

# 2. put the values for this run into your shell. Tokens live for minutes, so repeat before each run.
eval "$(scripts/copilot/copilot_collection_env.sh)"

# 3. run everything
cd docs/api-collection/copilot
npx @usebruno/cli run --env stub --env-file environments/stub.bru --insecure
```

`--insecure` is only for the local OpenEMR dev stack, which has a self-signed certificate (the two `fhir-*` requests
call it directly). The signed requests go to the agent over plain HTTP.

`scripts/copilot/copilot_collection_env.sh` only prints `export` lines. It reads the fixture user and patient UUIDs from
`agent/data/fixture.json`, mints a short-lived read-only user token with the module's own issuer, and reads the signing
secret and dashboard password from the git-ignored `agent/.env`. Nothing secret is stored in this folder: the `stub`
environment refers to `process.env.COPILOT_*`.

Last verified run: 17 of 17 requests, 11 tests and 20 assertions passed, twice in a row.

### What the signing script does

`signed/folder.bru` signs each request in a pre-request script: HMAC-SHA256 over
`v1`, method, path, timestamp, nonce, SHA-256 of the body, user UUID and SHA-256 of the token, joined by newlines. It
generates a **fresh timestamp and a fresh random nonce for every request**, so replay protection is exercised, not
bypassed. The signature only proves integrity; every call is still authorized against the user's token and the patient's
care team.

## Real-model run (spends money)

The demo agent on port 8400 uses the real model (OpenRouter). Pointing `signed/` at it makes real model calls (about a
cent or two per chat request). Only do it deliberately, by overriding the `agent` variable:

```bash
npx @usebruno/cli run signed --env stub --env-file environments/stub.bru --env-var agent=http://127.0.0.1:8400
```

The per-user rate limit on the demo agent is 60 requests per minute, and each nonce can be used once.

## Notes

- The stub model reads the four core tools whatever the question, so UC-2 and UC-4 prove the signed path, the
  conversation reuse and the answer shape. The real-model behaviour of UC-2 and UC-4 is covered by the live evals
  (`evals/live.py`).
- The Playwright specs in `tests/playwright/specs/copilot-*.spec.ts` cover the same flow through the browser.

### Week 2 (`w2/`, needs `W2_ENABLED=true`)

`w2/` signs requests the same way as `signed/` and covers the four C1 endpoints: upload a synthetic lab PDF (twice, to see
`duplicate: true`), read the stored extraction, report written OpenEMR record uuids, and guideline retrieval, plus an
upload for another physician's patient (403). `w2-upload-unsigned.bru` at the top level must get 401. Set `lab_pdf_b64` to
`base64 -i agent/tests/fixtures/w2/lab_report.pdf | tr -d '\n'`. For an offline demo run the agent with
`W2_ENABLED=true W2_USE_STUB_VLM=true`. The real OpenEMR lab rows are written by the module (`upload.php`), not by these requests.
