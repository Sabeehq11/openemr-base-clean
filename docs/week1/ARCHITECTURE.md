# ARCHITECTURE.md: Clinical Co-Pilot

Traces to [USERS.md](USERS.md) (UC-1 to UC-4) and [AUDIT.md](AUDIT.md) (finding ids S/P/D/C). Status: implemented for Checkpoint 2 (agent, module, verifier, audit, dashboard, evals, e2e). Where the build differs from this plan, section 12 says so and wins.

## Summary (about 500 words)

**What we build.** A read-only, patient-scoped, multi-turn assistant for a primary care physician, docked in the OpenEMR Tabs shell. It serves four use cases: a 90-second pre-visit brief (UC-1), what changed since the last visit (UC-2), lab and medication safety flags (UC-3), and follow-up questions (UC-4). It does not diagnose, prescribe or write to the chart.

**Shape.** Two pieces. (1) A thin OpenEMR custom module, `oe-module-copilot`, mounts the chat dock using the `main.body.render.post` event, authenticates the browser session, and signs each proxied message. (2) A separate Python (FastAPI) agent service holds the LLM loop, the tools, the verification layer and observability. The agent reads OpenEMR only through the FHIR and REST API, using the physician's own OAuth2 token. We reuse OpenEMR's scope enforcement and write no new SQL against its database.

**Key decisions and why.**
1. *Separate service, not PHP in-process.* The loop, evals and tracing are easier to build and test in Python, and the PHP module stays small. The cost is a network hop and a second deployable.
2. *The user's token, not a service account or SQL.* OpenEMR checks ACLs per section, not per patient (S3). A user token makes its own ACL apply, and the agent adds a fail-closed patient-level decision on every call: active CareTeam membership, read with that token.
3. *Fixed tool set.* Six typed read tools with Pydantic input and output contracts. The model cannot ask for arbitrary data or choose the patient, so minimum-necessary (C9) holds by construction.
4. *Verification after generation.* The model returns claims, each with a source id. A verifier checks that every id resolves to a record fetched in this request and that cited values match. Unsupported claims are dropped. A rules layer recomputes lab flags and allergy-medication conflicts instead of trusting the model or the stored flag (D4).
5. *Absence is a first-class fact.* Tools return explicit "not documented" results with dates, fixing the false "no allergies" and stale-lab failures (D2, D3).
6. *Medications.* The tool reads FHIR `MedicationRequest` (the prescriptions store) and says so in every result, because the `lists` store is not exposed by FHIR (D1, section 12).
7. *Our own audit trail.* OpenEMR does not log reads (C1). The agent writes an append-only, HMAC-chained record per tool call.
8. *PHI minimization.* Only allow-listed fields reach the model. Direct identifiers are replaced with placeholders and restored after verification. Traces hold counts and timings, never prompts or chart text (C3).

**Tradeoffs.** Latency versus completeness: tools run in parallel and follow-ups reuse a per-conversation snapshot, which still re-checks authorization. Strict verification will sometimes drop a true but unattributable statement. We accept that, because a missed fact costs less than a wrong one.

**Known limits.** The verifier checks that a claim is grounded in a retrieved record. It does not prove clinical correctness or detect an error already in the chart. FHIR does not expose every legacy field, so some context is missing. The answer states these limits.

## 1. Components and trust boundaries

```
Physician browser
  |  OpenEMR session cookie (TB1)
  v
OpenEMR (PHP)  -- oe-module-copilot: chat dock + BFF endpoint (session + CSRF, resolves pid -> patient UUID)
  |  HTTPS, short-lived signed request: {user, patient UUID, correlation ID} (TB2)
  v
Agent service (FastAPI)
  |-- Auth/Gateway: validates request, checks patient allow-list, assigns correlation ID
  |-- Agent loop (LLM + tool calling, max N steps)
  |-- Tools (typed) --> OpenEMR FHIR/REST with the user's OAuth2 token (TB3)
  |-- Verifier + rules --> final answer with citations
  |-- Audit log (append-only HMAC chain), trace store (SQLite) and /dashboard
  v
LLM provider (TLS, BAA, zero retention) (TB4)
```

| Boundary | What crosses it | How it is enforced |
|----------|-----------------|--------------------|
| TB1 browser to OpenEMR | Chat text | Session, CSRF token, `pid` read server-side from the session, never from the browser |
| TB2 OpenEMR to agent | User id, patient UUID, correlation ID | Signed (HMAC) short-lived request, TLS. The agent rejects anything unsigned or older than 60 s |
| TB3 agent to OpenEMR API | FHIR reads | OAuth2 token of that user, OpenEMR scope and ACL checks, per-physician allow-list in the gateway, `api_log_option=1` |
| TB4 agent to LLM | Allow-listed, de-identified fields | Field allow-list in each tool's output schema, identifier placeholders, TLS verification, BAA |

Chart free text (notes, problem titles, allergy reactions) is data. It is wrapped in delimiters, labelled as untrusted in the prompt, and the model has no tool that writes or sends anything, so an injected instruction has nothing to act on (S10).

## 2. Tools (contracts first)

All inputs and outputs are Pydantic models, and the schemas in `agent/contracts/` are the source of truth. Every output record carries `source_id` (resource type and id), `as_of` (date) and `status`.

| Tool | Reads | Used by | Notes |
|------|-------|---------|-------|
| `get_patient_summary` | Patient, Encounter | UC-1, UC-2 | Age and sex, last encounter date and reason. Reports "DOB not on file" instead of guessing. Runs the duplicate check (D5) |
| `get_problems` | Condition | UC-1, UC-2 | Parses the code system prefix (ICD-9 or ICD-10), flags uncoded items (D7) |
| `get_medications` | MedicationRequest, list medications | UC-1, UC-2, UC-3, UC-4 | Merges both stores, reports conflicts, resolves dose and unit codes (D1, D8) |
| `get_allergies` | AllergyIntolerance | UC-1, UC-3 | Returns "none documented" when empty, never "no allergies" (D2) |
| `get_labs` | Observation (labs), DiagnosticReport | UC-1, UC-2, UC-3, UC-4 | Date window and result cap. Recomputes flags from value and range (D3, D4) |
| `get_changes_since` | Encounter plus the tools above | UC-2 | Finds the last encounter date, then runs the others with that date (the only tool chain) |

If a tool fails or times out (default 5 s, one retry), it returns a typed `ToolError` with a reason. The answer states which source was unavailable and does not fill the gap.

## 3. Conversation model

- One conversation is bound to one patient UUID, set by the module from the chart currently open. Switching patients starts a new conversation. This keeps context from leaking between patients (UC-4 only needs same-patient follow-ups).
- State is the message list plus a per-conversation snapshot (the tool results already fetched, with their fetch times). Follow-ups reuse the snapshot unless it is older than 5 minutes.
- The agent loop is capped at 6 tool steps and 20 s total. If the cap is hit, the answer says what was and was not retrieved.
- Single agent, no sub-agents. The use cases need one loop with tool calls, and nothing in USERS.md needs more.

## 4. Verification design

Where it runs: after generation and before anything is shown. There is no path from the model to the UI that skips it.

1. **Structured output.** The model returns a list of claims. Each has `text`, `source_ids`, and a `kind` (`fact`, `absence` or `flag`).
2. **Source attribution check.** Every `fact` must cite at least one `source_id` that was returned by a tool in this request. The verifier then checks the claim against the record: medication names, lab values, dates and counts are compared with the record's fields by exact match after normalization. A claim that fails is dropped, and the answer notes "N statements could not be verified and were removed".
3. **Absence check.** An `absence` claim ("no allergy documented") must correspond to an empty result from the right tool, and must be worded as not documented.
4. **Domain rules (deterministic, not model-generated).**
   - Lab flag recomputed from numeric value and reference range. A disagreement with the stored flag is reported.
   - Allergy to active medication conflict, using an explicit allergen-to-drug-class table (small, reviewed, versioned).
   - Monitoring evidence gap (`agent/app/monitoring.py`): for an active medication in a small reviewed drug-to-lab table (e.g. warfarin to INR), states that no such result is in the retrieved lab results. Reported only when both the medication and lab tools succeeded; it never says a result is required.
   - Staleness: a lab older than its threshold (for example A1c over 6 months for a diabetic patient) is flagged.
   - Duplicate medication across the two stores.
   - Output rules: no diagnosis, no dose recommendation, no instruction to start or stop a drug. A response containing one is rejected and regenerated once, then replaced with a refusal.
   - Advice requests: after authorization, the question itself is checked (`agent/app/advice.py`). A request for the assistant's own judgment ("what dose should I raise it to?", "what do you recommend?", "is it safe to give aspirin?") is declined with a plain notice and the model is never called. Chart lookups that reuse the same words ("what is her diagnosis code?", "did they increase the dose?") are answered. Anything that slips past the question check is still caught by the output rules above, and the physician is told that statements were withheld.
5. **Display.** Every line shows its sources as links to the chart record. Flags come from the rules layer and are labelled as rule-based.

What it catches: invented values, wrong values, claims with no source, false "none" statements, stale data presented as current, out-of-scope advice.

What it does not catch (known limits):
- An error already in the chart. We report what is recorded.
- A true record that is incomplete (for example an unrecorded allergy).
- Omission: the model leaving out something important. The rules layer covers a fixed list of items, and nothing covers the rest.
- Semantic errors where the cited record exists but the wording over-reads it ("well controlled" from one value). Mitigated by restricting the output to factual claims and by evals, not eliminated.
- The allergen-to-class table covers only common classes, so cross-reactivity outside it is not flagged.
- The drug-to-lab table is small and needs clinician review; drugs not in it are not checked. The verifier also drops model claims about what a drug requires, how risky it is or what to monitor, and reports counts of rejection reason codes (`rejections`) without claim text.

## 5. Failure modes

| Situation | Behavior |
|-----------|----------|
| Tool timeout or error | Typed `ToolError`. The answer names the missing source. No guessing |
| Empty or missing record | Explicit "not documented" with the date checked |
| Patient not on the physician's allow-list or ACL denies | "I can't access that patient". No confirmation the patient exists |
| Two records match the same patient | Show both ids and ask the physician to choose |
| Model returns invalid structure | One retry, then a safe error message and a logged failure |
| Verification removes most claims | Show what survived plus "limited verified information", and offer the raw sources |
| LLM provider down | Return the raw structured tool results (problems, meds, allergies, labs) without prose. This degraded mode is still useful |
| OpenEMR API down | `/ready` fails, the dock shows "chart data unavailable" |
| Prompt injection in chart text | Treated as data. No write-capable tools. Eval cases cover it |
| Token expired | One refresh, then fail closed |

## 6. Authorization

- The browser never supplies a patient id. The module resolves it from the session and signs the request.
- The OAuth2 token belongs to the logged-in physician (SMART EHR launch, scopes `launch`, `openid`, `fhirUser` and `patient/*.rs` for only the six resource types used). The password grant is disabled.
- The gateway checks a per-physician allow-list (patients where the physician is the provider of record, plus an explicit care-team list) before any tool runs. This closes the gap in S3.
- Nurse and resident roles are not supported in the MVP. The module refuses to open the dock for users outside the physician ACL group.
- Denials are logged with the correlation ID and user.

### 6.1 Update after the authorization proof (Checkpoint 2)

Measured results are in `agent/AUTHZ_PROOF.md`. Where they differ from the text above, this section wins.

- OpenEMR checks token and scope only. Any clinician token reads any patient over FHIR, and the SMART patient-binding hook (`checkUserHasAccessToPatient`) is a stub. A `providerID` allow-list is therefore not used.
- The patient-level decision is made by the agent from OpenEMR data on every request: live token introspection, required scopes, token subject equals the claimed user, and the user must be an active practitioner on the patient's active FHIR CareTeam, read with the user's own token. Any error is a denial.
- The scopes used are `user/*` read scopes with a user-bound token from the authorization-code flow, not `patient/*`. Patient-bound `patient/*` tokens narrow reads but cannot read CareTeam, so they cannot make the decision.
- The module's HMAC signature is integrity only and never grants access.
- Password grant was enabled in the local install and is now turned off (tested).

## 7. Observability and audit

- **Correlation ID.** A UUID is created at the module (or the gateway if absent). It is in every log line (including one structured line per span), the trace row, the audit rows, and the `x-correlation-id` header on every outbound call to OpenEMR (FHIR and token introspection) and to the model provider.
- **Traces.** Own SQLite trace store (`traces.db`), not Langfuse (section 12). One trace per request, one span per step (authorization, each tool, each LLM call, verifier). Each span records latency and status, and the trace records token counts and cost. Prompt and response bodies are not stored, so the store holds no chart text.
- **Dashboard.** `/dashboard` and `/dashboard.json` (HTTP Basic, off unless `DASHBOARD_TOKEN` is set): requests, errors, p50/p95 latency, tool calls and failures, retries, verification pass rate and dropped claims, tokens and cost, alert states, audit-chain integrity, and the load numbers **in flight now** and **shed since start**. The HTML page refreshes itself every 15 s (`?refresh=N`, 0 turns it off). There is no queue depth: a chat starts at once or, with `MAX_INFLIGHT_CHATS` set and reached, gets a 503, so in-flight and shed are the honest load figures.
- **Alerts (defined with the dashboard).** p95 latency above 25 s for 5 minutes (raised from 12 s in Checkpoint 3 after load measurements, section 13), error rate above 5% over 10 minutes, tool failure rate above 10% over 10 minutes. Each has a written on-call response.
- **Audit log.** Separate from tracing. Append-only, one record per tool call: user, patient UUID, tool, fields returned, model, correlation ID, time. Records are HMAC-chained and exported to storage the application cannot rewrite. This is the HIPAA 164.312(b) record that OpenEMR does not provide (C1, C4).
- **Health.** `/health` returns process liveness. `/ready` checks OpenEMR (FHIR metadata), the LLM provider, that the data directory is writable, and the audit chain, and returns 503 if any check fails.

## 8. Evaluation plan

Cases live in `evals/`. The stub tier runs under pytest (`agent/tests/test_evals_stub.py`) and with `python -m evals.run`; the live tier runs on demand with a spend cap. Neither is wired into a CI workflow yet. Each case names the failure mode it guards against.

- **Invariants:** every fact cites a source, every cited source was retrieved, no write tool exists.
- **Boundaries:** empty chart, missing DOB, no allergy row, no labs, stale labs, ICD-9 and uncoded problems, dose with no unit, duplicate patient.
- **Conflicts:** medication active in one store and inactive in the other, flag disagrees with value.
- **Authorization:** another physician's patient, a patient id typed into the prompt, a request to list all patients.
- **Adversarial:** instructions inside a note ("ignore previous instructions"), requests for a diagnosis or a dose, requests to change the chart.
- **Failure injection:** tool timeout, malformed tool output, provider error.
- **Regression:** each audit finding D1 to D11 has at least one case.

Pass and fail is mostly deterministic (schema, citation resolution, exact-match values, rule outputs). A small set of rubric-graded cases check wording, such as "not documented" versus "none".

## 9. Technology choices

| Area | Choice | Reason |
|------|--------|--------|
| Agent service | Python 3.12, FastAPI, Pydantic v2 | Typed contracts and a mature eval and tracing ecosystem |
| Agent loop | A small explicit tool-calling loop on the provider SDK, no heavy framework | Easy to test, and the verifier must sit on every path |
| LLM | Provider with a BAA and zero retention, with structured output and tool calling. Final model chosen from latency and cost on our evals | Chosen in week 2 with data |
| Tracing | SQLite trace store plus `/dashboard` (Langfuse was the plan; see section 12) | No chart text is stored, and it fits on a laptop |
| OpenEMR side | Custom module `oe-module-copilot` (PHP 8.2, `declare(strict_types=1)`, PSR-4) | The supported extension path (A1, A2) |
| Browser tests | Playwright (`tests/playwright`) | Covers login, chart, chat dock, citations and denial flows |
| Deployment | Same VPS as OpenEMR (`deploy/`), the agent as an extra compose service | One place to size and secure |

## 10. Build order

1. Module skeleton, signed proxy, agent `/health` and `/ready`.
2. FHIR client with token handling, then the six tools with contracts and tests against the seeded patients.
3. Agent loop, then the verifier and rules (these come before any prompt tuning).
4. Audit log and trace store, then the eval suite and dashboard.
5. Load tests, alerts and cost analysis.

## 11. Open questions and risks

- FHIR coverage must be checked field by field against the seeded data before the tools are final (A3 pitfall).
- Whether the EHR launch flow works cleanly in the Tabs shell, or whether a module-issued short-lived token is needed. Decided by a spike on day one of the build.
- Placeholder substitution may need care in free-text fields, where names appear inline.
- Cost per brief depends on the chart size. The cost analysis will measure it on the seeded charts.

## 12. Checkpoint 2 as built (appended; supersedes the plan above where they differ)

What exists: `agent/` (FastAPI, Pydantic v2, Python 3.12 container), `interface/modules/custom_modules/oe-module-copilot/`,
`evals/`, and Playwright specs in `tests/playwright/specs/`. Setup and endpoints: [agent/README.md](../../agent/README.md).

| Plan said | As built | Why |
|---|---|---|
| Per-physician allow-list from provider of record | Active FHIR CareTeam membership, read with the user's own token, checked on every request (section 6.1, [agent/AUTHZ_PROOF.md](../../agent/AUTHZ_PROOF.md)) | Measured: OpenEMR lets any clinician token read any patient, and `providerID` is not exposed in FHIR |
| Token from SMART EHR launch | Authorization-code token for the logged-in user, issued by the module for the session; `sub` must equal the claimed user. Password grant is off | The Tabs shell has no clean EHR-launch hook; the token is still user-bound, introspected and expiry-checked on every call |
| Nurse and resident roles unsupported | Nurse and front-desk users are denied by the same fail-closed check (no care-team membership) | Tested in pytest, evals and Playwright |
| One `get_medications` tool merges `lists` and `prescriptions` | Reads FHIR `MedicationRequest` (prescriptions) only; every result carries a note saying so | FHIR does not expose the `lists` store. None of the seeded patients has list-only medications, so nothing is lost in the demo data |
| Self-hosted Langfuse traces | Own trace store (SQLite) plus `/dashboard`; Langfuse not deployed | Four extra containers do not fit next to OpenEMR on a laptop. The span schema is Langfuse-shaped, so export is possible later |
| Audit exported to storage the app cannot rewrite | HMAC-chained SQLite log with `verify-audit` and `export-audit`. Off-box export is manual | Tamper-evident now; tamper-proof storage needs infrastructure outside this checkpoint |
| LLM chosen from evals | Claude Sonnet through OpenRouter. A deterministic stub is used in all automated tests | Live runs are opt-in with a spend cap |
| BAA with zero retention | Not applicable to synthetic data. Direct identifiers are still replaced with placeholders before the prompt | Keeps the production design honest |

**Verified results.**

- Agent pytest: 321 passed with the live stack (`OE_LIVE=1`), including the real OAuth flow, revocation, expiry and cross-physician denial.
- Evals, stub tier: 56/56, across invariant, boundary, conflict, authorization, adversarial and failure-injection categories ([evals/results/stub-latest.md](../../evals/results/stub-latest.md)). Audit findings D1 to D9 each have a case. D10 (no vitals tool) and D11 (fixed in the seeder) are not covered, and D5 is a recorded known gap.
- PHPUnit isolated: 23 tests for request signing parity, pid-to-UUID resolution and the chat proxy.
- Playwright: 24 tests (the original 7 plus dock, clickable citation cards, advice declines, follow-up, denials, markup safety and LLM-down mode).
- Bruno HTTP collection: 12 assertions ([docs/api-collection/copilot](../api-collection/copilot)).

**Known limits carried forward.** The care team is the access list, so care-team edits are an access-control change and
are not yet audited. Any care-team practitioner is admitted, not only physicians. Load testing and the AI cost
analysis were done in Checkpoint 3 (section 13). The live-Sonnet eval
tier is on demand and its results are not part of the 56/56 figure.

## 13. Checkpoint 3 as built (load tests, baselines, cost, hardening)

Documents: [PERFORMANCE.md](PERFORMANCE.md) (baselines and rerun commands) and
[AI_COST_ANALYSIS.md](AI_COST_ANALYSIS.md) (spend, per-request cost, projections). Harness: `loadtest/`.

**Method.** Load runs hit a separate agent container with its own data volume, with real signed requests and real OAuth
tokens. Every result is labelled `stub` (no model), `mock` (local replay of measured model latency, a simulation) or
`real` (OpenRouter). Real spend was capped at 2.00 USD in code and came to 0.85 USD (33 requests). There is no 50-user
`real` run.

**What the data said.**

- The first bottleneck is OpenEMR, not the agent and not the model. Every tool call costs two OpenEMR requests for
  authorization (introspection, CareTeam) before the FHIR read. Past roughly 10-15 concurrent users OpenEMR saturates,
  authorization times out, and the agent fails closed: at 50 stub users 97% of requests were denied, including
  physicians' own patients. With the model in the loop (mock, 50 users) it was 10%.
- At 10 users everything is healthy: stub p95 9.7 s, mock p95 15.7 s, real p95 20.3 s, zero errors.
- Failure modes behave as the runbook says. A model outage gives degraded answers (not paged, by design). An OpenEMR
  slowdown shows up as denials and is caught only by the latency alert.

**Changes made, each tied to a measurement.**

| change | evidence |
|---|---|
| One LLM retry with jittered backoff on 429, 5xx and connect errors | 5% injected provider 429s degraded 9.1% of answers; after the change, 0 of 83 |
| p95 alert threshold 12 s to 25 s | healthy real requests have p95 of 19-20 s, so 12 s fired on normal traffic |
| `MAX_INFLIGHT_CHATS` guard (503 with Retry-After), **default off** | stub 50 users: 36 answers became 397. Mock 50 users: 392 became about 260. Mixed, so opt-in |

**Measured and not changed:** shared HTTP client (a fresh connection costs about 40 ms of 324 ms), parallel tool calls
(tools are 5-9% of a real request), SQLite WAL (no lock errors; a write is about 1 ms), authorization caching
(rejected on principle, see AUTHZ_PROOF.md).

**Limits.** One uvicorn worker, because the nonce cache, rate limiter and conversation store are in process memory.
Single laptop, one run per cell, load generator on the same machine. The recorded spend has not been compared with the
OpenRouter account page. Public deployment was out of scope for this checkpoint.
