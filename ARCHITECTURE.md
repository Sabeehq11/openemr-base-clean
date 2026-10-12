# ARCHITECTURE.md: Clinical Co-Pilot

Traces to [USERS.md](USERS.md) (UC-1 to UC-4) and [AUDIT.md](AUDIT.md) (finding ids S/P/D/C). Status: plan. Nothing here is implemented yet.

## Summary (about 500 words)

**What we build.** A read-only, patient-scoped, multi-turn assistant for a primary care physician, docked in the OpenEMR Tabs shell. It serves four use cases: a 90-second pre-visit brief (UC-1), what changed since the last visit (UC-2), lab and medication safety flags (UC-3), and follow-up questions (UC-4). It does not diagnose, prescribe or write to the chart.

**Shape.** Two pieces. (1) A thin OpenEMR custom module, `oe-module-copilot`, mounts the chat dock using the `main.body.render.post` event, authenticates the browser session, and proxies each message. (2) A separate Python (FastAPI) agent service holds the LLM loop, the tools, the verification layer and observability. The agent reads OpenEMR only through the FHIR and REST API, using the physician's own OAuth2 token from a SMART EHR launch. We reuse OpenEMR's scope enforcement and its API logging, and we write no new SQL against OpenEMR's database.

**Key decisions and why.**
1. *Separate service, not PHP in-process.* The agent loop, evals and tracing are far easier to build and test in Python, and the PHP module stays small. The cost is a network hop and a second thing to deploy (accepted).
2. *API access with the user's token, not a service account or SQL.* OpenEMR checks ACLs per section, not per patient (S3), and the `isLocalApi` path bypasses scope checks. A user token makes OpenEMR's own ACL apply. On top of it the gateway adds a per-physician patient allow-list, because OpenEMR will not (S3).
3. *Fixed tool set, no free-form queries.* Six typed tools (Pydantic schemas) cover the use cases. The model cannot ask for arbitrary data, so minimum-necessary (C9) is enforced by construction.
4. *Verification after generation, with structured output.* The model returns claims, each with a source id. A verifier checks that every id resolves to a record fetched in this request and that the cited value matches. Unsupported claims are dropped or shown as "not found in the record". A deterministic rules layer recomputes lab flags and checks allergy-medication conflicts rather than trusting the model or the stored flag (D4).
5. *Absence is a first-class fact.* The tools return explicit "not documented" results with dates and ages. This fixes the false "no allergies" and stale-lab failures the audit found (D2, D3).
6. *Merge both medication stores.* One `get_medications` tool unions `lists` and `prescriptions` and reports conflicts (D1).
7. *Our own audit trail.* OpenEMR does not log reads (C1) and stores API bodies in plaintext (C2). The agent writes an append-only, HMAC-chained record per tool call and sets `api_log_option=1` for its client.
8. *PHI minimization and BAA.* Only allow-listed fields reach the model. Direct identifiers (name, address, phone, SSN, MRN) are replaced with placeholders and re-inserted after verification. We assume a signed BAA with zero retention for the model provider (given in the project brief). Observability is self-hosted Langfuse so prompts never leave our infrastructure (C3).

**Tradeoffs.** Latency versus completeness: UC-1 streams a first answer within about 3 s from a parallel fetch and a per-conversation patient snapshot, then follow-ups use the cache. Strict verification will sometimes drop a true but unattributable statement. We accept that, because a missed fact costs less than a wrong one. Placeholder substitution reduces model quality slightly for name-dependent phrasing.

**Known limits.** The verifier checks that a claim is grounded in a retrieved record. It does not prove clinical correctness, and it cannot detect an error that is already in the chart. FHIR does not expose every legacy field (for example `pnotes`), so some context will be missing. These are stated in the answer, not hidden.

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
  |-- Audit log (append-only HMAC chain), Langfuse traces (self-hosted)
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
   - Staleness: a lab older than its threshold (for example A1c over 6 months for a diabetic patient) is flagged.
   - Duplicate medication across the two stores.
   - Output rules: no diagnosis, no dose recommendation, no instruction to start or stop a drug. A response containing one is rejected and regenerated once, then replaced with a refusal.
5. **Display.** Every line shows its sources as links to the chart record. Flags come from the rules layer and are labelled as rule-based.

What it catches: invented values, wrong values, claims with no source, false "none" statements, stale data presented as current, out-of-scope advice.

What it does not catch (known limits):
- An error already in the chart. We report what is recorded.
- A true record that is incomplete (for example an unrecorded allergy).
- Omission: the model leaving out something important. The rules layer covers a fixed list of items, and nothing covers the rest.
- Semantic errors where the cited record exists but the wording over-reads it ("well controlled" from one value). Mitigated by restricting the output to factual claims and by evals, not eliminated.
- The allergen-to-class table covers only common classes, so cross-reactivity outside it is not flagged.

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

- **Correlation ID.** A UUID is created at the module (or the gateway if absent) and carried in every log line, tool call, FHIR request header, LLM call and trace.
- **Traces.** Self-hosted Langfuse. One trace per request, one span per step (gateway, each tool, LLM call, verifier). Each span records latency, status, token counts and cost. Prompt and response bodies are stored with identifiers already substituted.
- **Dashboard.** Requests, errors, p50/p95 latency, tool calls and failures, retries, verification pass rate and dropped claims, tokens and cost per request.
- **Alerts (defined with the dashboard).** p95 latency above 12 s for 5 minutes, error rate above 5% over 10 minutes, tool failure rate above 10% over 10 minutes. Each has a written on-call response.
- **Audit log.** Separate from tracing. Append-only, one record per tool call: user, patient UUID, tool, fields returned, model, correlation ID, time. Records are HMAC-chained and exported to storage the application cannot rewrite. This is the HIPAA 164.312(b) record that OpenEMR does not provide (C1, C4).
- **Health.** `/health` returns process liveness. `/ready` checks OpenEMR (FHIR metadata), the LLM provider and Langfuse, and fails if any is unreachable.

## 8. Evaluation plan

Cases live in `evals/` and run in CI. Each case names the failure mode it guards against.

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
| Tracing | Self-hosted Langfuse | Prompts hold chart content, so they stay in our infrastructure |
| OpenEMR side | Custom module `oe-module-copilot` (PHP 8.2, `declare(strict_types=1)`, PSR-4) | The supported extension path (A1, A2) |
| Browser tests | Playwright (`tests/playwright`) | Covers login, chart, chat dock, citations and denial flows |
| Deployment | Same VPS as OpenEMR (`deploy/`), agent and Langfuse as extra compose services | One place to size and secure |

## 10. Build order

1. Module skeleton, signed proxy, agent `/health` and `/ready`.
2. FHIR client with token handling, then the six tools with contracts and tests against the seeded patients.
3. Agent loop, then the verifier and rules (these come before any prompt tuning).
4. Audit log and Langfuse wiring, then the eval suite and dashboard.
5. Load tests, alerts and cost analysis.

## 11. Open questions and risks

- FHIR coverage must be checked field by field against the seeded data before the tools are final (A3 pitfall).
- Whether the EHR launch flow works cleanly in the Tabs shell, or whether a module-issued short-lived token is needed. Decided by a spike on day one of the build.
- Placeholder substitution may need care in free-text fields, where names appear inline.
- Cost per brief depends on the chart size. The cost analysis will measure it on the seeded charts.
