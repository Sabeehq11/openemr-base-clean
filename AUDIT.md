# AUDIT.md: OpenEMR audit for the Clinical Co-Pilot

Scope: this fork of OpenEMR (master), run locally in Docker with 3 stock demo patients and 13 synthetic patients (`scripts/seed_demo_patients.py`). Method: code reading with file references, live SQL (SELECT only), and timed HTTP calls. Not done: dynamic SQLi/XSS/CSRF testing, a production-scale load test, or review of non-core modules.

## Summary (key findings)

OpenEMR is a capable but legacy system. It can safely serve an agent, but only if the agent's design compensates for six things it does not do for us. Ranked by impact on the agent:

1. **No patient-level authorization.** ACLs are checked per section (for example `patients/demo`), not per patient. `AclMain::aclCheckCore` (`src/Common/Acl/AclMain.php:166`) takes a section and a value only. The `squad` field is written but never enforced in the REST/FHIR controllers, and the `sensitivities` ACL is used in very few places (`EncounterService.php:450`). Any identity that can read one chart can read all of them. The agent needs its own per-physician patient allow-list, and the agent must act with the user's token, not a shared service account.
2. **Reads of patient records are not audited.** `audit_events_query` is off, and `patient-record-select` has 0 events. Writes dominate the log. A HIPAA 164.312(b) trail for what the agent reads does not exist unless we build one. `api_log` also stores full API response bodies (PHI) in plaintext, and the log is only protected by unkeyed checksums in the same database.
3. **Medications live in two places that disagree.** `lists` (type `medication`) and `prescriptions` are separate stores. In the stock data, patient 2's Lisinopril is inactive in one and active in the other. In the seeded data only 2 patients have list medications while 13 have prescriptions. An agent that reads one store gives a wrong medication list. It must read both and show conflicts.
4. **"No data" looks like "no problem".** 7 of 16 patients have no allergy row, and OpenEMR has no structured "no known allergies" assertion, so "not documented" and "none" are indistinguishable. 7 of 16 have no labs; 3 of the 9 patients with labs have a most recent lab older than a year. Dosage and unit are often free text or integer codes into `list_options` (31 of 32 seeded prescriptions have no unit). The agent must say "not documented" and show dates and ages.
5. **Exposed defaults and secrets.** The dev compose files publish MariaDB (`0.0.0.0:8320`) and phpMyAdmin with `root/root`, and commit a GitHub token (`docker/development-easy/docker-compose.yml:75-77`). `sites/default/sqlconf.php` is tracked with `openemr/openemr`. A deployed stack must use none of these.
6. **Latency is fine, and the cost is in the fixed overhead.** Reads take 0.1 to 0.3 s on the local dev stack (Xdebug loaded). Each API request writes about 3 `log` rows plus 1 `api_log` row, and token issuance is the slowest call at about 0.4 s. There are no composite indexes like `lists(pid,type)`. The agent should fetch in parallel and cache a patient snapshot per conversation.

Compliance consequences for sending PHI to an LLM: a signed BAA with zero retention, a field allow-list (minimum necessary, 164.502(b)), and any observability tool that stores prompts must be self-hosted or covered by a BAA. OpenEMR has no breach-notification tooling and no retention policy for logs.

**How this changed the plan:** the agent will call OpenEMR only over OAuth2/SMART-on-FHIR with the physician's own token. It adds its own per-patient allow-list and its own append-only audit log. It labels uncertainty, and where FHIR cannot expose a store (the `lists` medications) it says so in the result. It treats free-text chart content as untrusted input.

## 1. Security audit

| # | Sev | Finding | Evidence | Agent impact and mitigation |
|---|-----|---------|----------|-----------------------------|
| S1 | Critical | Committed secrets and default credentials | GitHub PAT in plaintext and encoded in `docker/development-easy/docker-compose.yml:75-77` and `development-insane/...:224-226`. Defaults `root`, `admin/pass`, `openemr/openemr`. `sites/default/sqlconf.php:8-9` is tracked. | Rotate and revoke the token. Our deployment uses generated secrets from `deploy/.env` (git-ignored). Never give the agent DB credentials. |
| S2 | Critical | DB and phpMyAdmin exposed on all interfaces | Live containers publish `0.0.0.0:8320->3306`, `0.0.0.0:8310->80`. `root/root` worked. | Anyone on the network bypasses ACL and audit. The deploy compose publishes only Caddy on 80/443. |
| S3 | High | Authorization is per section, not per patient | `AclMain.php:166`. No `squad`, care-team or provider check found in `src/RestControllers`. 42 of 42 encounters have no `high` sensitivity. | Build a per-physician patient allow-list in the agent gateway, and add tests that ask for another physician's patient. |
| S4 | High | API/OAuth configuration | `oauth_password_grant=3`, `oauth_app_manual_approval=0`. `AuthorizationListener.php:171-176` has a role-only gate with `TODO`s. System-scope tokens carry no user ACL. | Do not use the password grant. Use authorization code with EHR launch (user token, so the user's ACL applies). Require manual client approval. |
| S5 | High | Weak authentication policy | `password_max_failed_logins=20`, `ip_max_failed_logins=100`, no expiry, no complexity (`secure_password` blank). `login_mfa_registrations` is empty. `AuthUtils.php:1123-1220` | Enforce MFA and lower lockouts on the deployed instance. Replace default passwords. |
| S6 | Medium | Session handling | `timeout=7200`, `cookie_secure=false`, httponly false for core (`SessionUtil.php:8-18`). | Agent traffic uses bearer tokens, not session cookies. Deploy over HTTPS only and shorten the timeout. |
| S7 | Medium | PHI in logs, log encryption off | `enable_auditlog_encryption` blank. `log` has 2,434 rows with PHI in `comments` (`EventAuditLogger.php:181-217`). | Turn on log encryption. Agent logs must not contain raw PHI by default. |
| S8 | Medium | Document encryption keys beside the data | `drive_encryption=1`, but keys under `sites/default/documents`. The `documents` table was empty so this was not exercised. | Keep documents out of agent scope unless a use case needs them. |
| S9 | Medium | Debug flags | `XDEBUG_ON=1`, profiler on, `DEVELOPER_TOOLS=yes` in dev compose. Xdebug is loaded locally. | Never deploy the dev compose. |
| S10 | Medium | Legacy SQL/XSS surface | Sampled code uses bind parameters. A narrow regex found no raw request values in SQL. Full tree not audited. | Treat free-text chart fields as untrusted input to the model (prompt-injection defense). |
| S11 | Low | CSRF coverage unverified | `CsrfUtils` used on forms, SameSite=Strict default. | The agent endpoint requires a CSRF token. |
| S12 | Low | Portal enabled with a legacy hash | `portal_onsite_enable=1`; `portal_offsite_password` looks like unsalted SHA-1. | Disable the portal in the deployment. |

## 2. Performance audit

Environment: local Docker, 16 patients, Xdebug loaded. Numbers are a floor for a small dataset, not a production forecast. The production deployment has no Xdebug.

| Call (5 runs, local) | Range |
|----------------------|-------|
| `/meta/health/readyz` | 0.096 to 0.115 s |
| `/interface/login/login.php` | 0.119 to 0.156 s |
| FHIR/REST reads (Patient, Condition, MedicationRequest, AllergyIntolerance, Observation) | about 0.1 to 0.3 s each |
| OAuth2 token issuance | about 0.4 s (slowest call) |

Findings:

- **P1. Fixed per-request cost.** Each request bootstraps globals and auth, roughly 0.1 s before any work. Four or five sequential tool calls cost about 1 s before the model runs. Fetch the sources for one patient in parallel.
- **P2. Audit writes on every read.** About 3 `log` rows and 1 `api_log` row per API call. The `api_log` row stores the full response body, so it grows with chart size and holds PHI.
- **P3. Paging is unreliable.** `_count` is applied per inner service for Observation, and bundles have no `next` link. Always scope searches by patient and by date range, and cap results in the tool layer.
- **P4. Indexes.** Patient-scoped tables have basic indexes on `pid`/`patient_id`. There are no composite indexes such as `lists(pid,type)`. `log` has no index for time or user filters.
- **P5. Recent-labs query.** `EXPLAIN` for "latest 10 results for a patient" (order, report, result joins) shows `Using temporary; Using filesort` on `procedure_order`. It is fine at 36 rows. At real volumes the agent should cap by date window and cache.
- **P6. Token cost.** An OAuth2 token costs about 0.4 s. The agent service should cache a token for its lifetime and refresh before expiry.
- **P7. Latency budget.** Target for UC-1 is a first token within 3 s. The data fetch should use about 0.5 s of that, which leaves the budget for the LLM call. That drives a design with a precomputed per-patient snapshot and parallel fetch.

## 3. Architecture audit

Layers: browser Tabs shell (`interface/main/tabs/main.php`, one iframe per tab) calls legacy procedural pages (`interface/`, `library/`, `controllers/`), which call a modern PSR-4 layer (`src/Services`, `src/RestControllers`, `src/Events`) over MariaDB. External callers go through `apis/dispatch.php`, `ApiApplication` and the RestControllers. OAuth2 and OIDC are in `src/RestControllers/AuthorizationController.php` and `src/Common/Auth/OpenIDConnect`.

Where data lives:

| Domain | Tables | Service |
|--------|--------|---------|
| Demographics | `patient_data` | `PatientService` |
| Encounters | `form_encounter`, `forms` | `EncounterService` |
| Problems, allergies, medication list | `lists` (by `type`) | `ConditionService`, `AllergyIntoleranceService`, `ListService` |
| Prescriptions | `prescriptions` | `PrescriptionService` |
| Labs | `procedure_order`, `procedure_report`, `procedure_result` | `ProcedureService`, `ObservationLabService` |
| Vitals | `form_vitals` | `VitalsService` |
| Notes and documents | `form_clinical_notes`, `pnotes`, `documents` | `ClinicalNotesService`, `DocumentService` |

Integration points (no AI or chat code exists in the repo):

- **A1. Custom module.** `interface/modules/custom_modules/oe-module-*`, loaded by `ModulesApplication` from `openemr.bootstrap.php`. The best template is `oe-module-dashboard-context`.
- **A2. UI hooks.** `Main\Tabs\RenderEvent` (`main.body.render.pre/nav/post`) in the shell, `MenuEvent::MENU_UPDATE` for a menu entry, and `PatientDemographics\RenderEvent` for a chart card. `main.body.render.post` is the right place for a chat dock that persists across tabs.
- **A3. Data access.** REST and FHIR (`_rest_routes.inc.php`, `apis/routes/`) with OAuth2 scopes enforced by `Subscriber/AuthorizationListener`. SMART-on-FHIR EHR launch is supported (`src/FHIR/SMART/SmartLaunchController`). A module can add routes and scopes with `RestApiCreateEvent` and `RestApiScopeEvent`.
- **A4. Patient context** lives in the PHP session (`library/pid.inc.php`, `PatientSessionUtil`) and in the shell JS. A separate service cannot see it, so the module must pass a patient UUID and re-validate it server-side.

Pitfalls:

- The `isLocalApi` and `skipAuthorization` paths bypass scope checks. Do not use them.
- System-scope (`client_credentials`) tokens work on FHIR only and carry no user ACL.
- The REST, FHIR and OAuth APIs are off by default and need an HTTPS base URL the agent can reach.
- FHIR coverage has gaps. `pnotes` has no confirmed mapping, and some fields live only in legacy forms.

## 4. Data quality audit

Caveat: 3 patients are stock demo data and 13 are synthetic, with defects injected on purpose. The counts show failure modes the agent will meet. They are not defect rates of a real population. The seeder defects are marked (S); the rest is native to OpenEMR (N).

| # | Finding | Evidence (current data) | Agent failure mode and mitigation |
|---|---------|-------------------------|-----------------------------------|
| D1 | Medications in two stores (N) | Patients with list meds: 2. Patients with prescriptions: 13. Stock patient 2 Lisinopril: inactive in `lists`, active in `prescriptions`. | Wrong med list. Read both, label source and status, show conflicts. |
| D2 | "No allergy" is indistinguishable from "not asked" (N) | 7 of 16 patients have no allergy row. No NKDA assertion exists. All allergies are free text with no code. | False "no allergies". Say "none documented". |
| D3 | Missing labs and stale labs | 7 of 16 patients have no orders. 3 of the 9 patients with labs have a latest lab older than a year (S). | "Normal" claimed with no data. Always show the collection date and age. |
| D4 | Abnormal flag disagrees with value (S: random flags) | 26 of 36 results where the flag and the value-vs-range disagree. Real feeds also have free-text results and ranges (`result` and `range` are `varchar(255)`). | Trusting the flag. Recompute from the numeric value and range, and report disagreement. |
| D5 | Duplicate patient (S) | Patients 100 and 120 share name and DOB. 120 has no history. | "No history" for the wrong record. Check for name and DOB collisions first. |
| D6 | Missing DOB / provider | 1 patient without DOB (S). 2 without a provider, stored as `0` and `NULL` (N). | Wrong age reasoning. Treat `NULL`, `0` and `''` as unknown. |
| D7 | Mixed code systems (N) | Problems: 25 ICD-10, 2 ICD-9, 1 uncoded. All medication and allergy list rows are uncoded. | Misses ICD-9 rows. Parse the prefix, and fall back to the title. |
| D8 | Prescription dose/unit encoding (N + S) | 31 of 32 prescriptions have no `unit`. Stock row has `dosage='1'` with unit and interval as integer codes into `list_options`. | "Lisinopril 1". Resolve codes. Say "frequency not recorded". |
| D9 | Encounter reason and recency | 7 of 41 encounters have an empty reason (S). Stock encounters are dated 2014 (N). | Stale "last visit". Always output the date. |
| D10 | Vitals stored as strings with no units (N) | `bps` and `bpd` are `varchar`. Weight and height have no unit column. | String comparison bugs. Cast, and label units as assumed. |
| D11 | UUID registry (found in the audit, then fixed in the seeder) | The first seeding left 13 of 16 patients and most child rows out of `uuid_registry`, with v1 UUIDs. The seeder now writes v4 UUIDs and registers every row. | Rows inserted outside OpenEMR's code can be missing from FHIR. Check that a cited resource resolves before citing its UUID. |

Schema notes: 54 of 132 `patient_data` columns, 41 of 49 `prescriptions` columns and 27 of 29 `form_vitals` columns are nullable. Many enum-like fields are free text with `''` defaults, so NULL and empty mean the same thing.

## 5. Compliance and regulatory audit (HIPAA)

| # | Sev | Finding | Evidence | Implication and mitigation |
|---|-----|---------|----------|----------------------------|
| C1 | High | Reads of patient records are not audited | `audit_events_query` is empty. `patient-record-select` has 0 events, `http-request-select` 41, `other-delete` 990. SELECTs on unknown tables return early (`EventAuditLogger.php:440-444, 500-505`). | No 164.312(b) trail for agent reads. The agent writes its own audit record per tool call, and the site turns on `audit_events_query`. |
| C2 | High | `api_log` stores PHI in plaintext | `api_log_option=2`. `LogTablesSink.php:70-83` stores `request_body` and `response`, and `ApiResponseLoggerListener.php:77-83` copies the response into the request body. | The log needs chart-level protection and retention. Set `api_log_option=1` for the agent's client, and enable log encryption. |
| C3 | High | An LLM provider is a business associate | Identifiers sit in `patient_data` (`fname`, `lname`, `street`, `phone_*`, `email`, `DOB`, `ss`, `pubpid`), plus free-text notes. | Needs a BAA covering permitted use, no training, safeguards, breach reporting, subcontractors, return or destruction, and zero retention. Send only allow-listed fields. Observability tools that store prompts need a BAA or self-hosting. |
| C4 | Medium | Weak tamper evidence | Unkeyed SHA3-512 checksums in `log_comment_encrypt`, stored in the same DB. No `log_validator` table exists. | The agent audit log is append-only with a keyed HMAC chain, shipped outside the DB. |
| C5 | Medium | ATNA and syslog forwarding off | `enable_atna_audit` empty. `AtnaSink` exists but is unconfigured. | Forward to a SIEM, or enable ATNA. |
| C6 | Medium | No retention policy | No retention or purge settings in `library/globals.inc.php`. Soft delete flags only. `backup_log_dir=/tmp`. HIPAA requires 6 years for audit and policy documents (164.316(b)(2)). | The agent needs its own TTL for prompts and traces, and a delete path. |
| C7 | Medium | No breach-notification tooling | No breach code under `src/`. | Keep a runbook (164.404-410, 60 days). The agent audit log must show which patients were affected. |
| C8 | Medium | Disclosure tracking is manual | `extended_log` has 0 rows. `recordDisclosure` exists (`EventAuditLogger.php:567-590`). | Treatment and operations disclosures under a BAA are generally exempt from accounting (164.528). Logging the LLM recipient is still good practice. |
| C9 | Medium | Minimum necessary and break-glass are partial | `BreakglassChecker.php:39-58`. 0 `sensitivities` rows in `list_options`. | Run as the real user, apply ACL and sensitivity checks before building the prompt, and never use a god-mode account. |
| C10 | Mixed | Encryption is mixed | AES-256-CBC `CryptoGen` is on for documents. DB TLS is not enforced (`is_client_ssl_enabled` blank). Audit log, `api_log` and `patient_data` are plaintext. | TLS 1.2 or higher with certificate verification for every LLM and OpenEMR call. API keys in a secret manager. |

Controls map:

| Rule | Requirement | Agent control |
|------|-------------|---------------|
| 164.312(a) | Access control, unique user ID | Run as the user, per-patient allow-list, no shared service account |
| 164.312(b) | Audit controls | Per-call record: user, patient, tool, fields, model, correlation ID |
| 164.312(c) | Integrity | Keyed HMAC chain, append-only |
| 164.312(d) | Authentication | Short-lived OAuth2 token per session |
| 164.312(e) | Transmission security | TLS everywhere, certificate verification |
| 164.308 | Risk analysis, BAAs | Documented risk analysis, signed BAA |
| 164.404-410 | Breach notification | Runbook, queryable agent logs |
| 164.502(b) | Minimum necessary | Field allow-list, context cap |
| 164.514 | De-identification | Safe Harbor or limited data set filter. Free text is not reliably de-identified by regex. |

## 6. What the audit changes in the plan

| Finding | Plan change |
|---------|-------------|
| S3 no per-patient authorization | Gateway allow-list per physician, plus eval cases for cross-patient requests |
| C1, C2 reads and logs | The agent has its own audit log, and `api_log_option=1` for its client |
| D1 two medication stores | One `get_medications` tool that merges both and reports conflicts |
| D2, D3 absence of data | Verification layer requires a "not documented" label with dates and ages |
| D4 flags vs values | Rule layer recomputes flags from value and range |
| D5 duplicates | Name and DOB collision check before chart lookup |
| D11 UUID registry | Citations only use ids that resolve |
| C3 BAA | Provider choice and observability hosting are decided with the BAA in mind |
| P1, P3 latency | Parallel fetch, per-patient snapshot cache, capped results |
| S10 free-text content | Chart text is data, never instructions |

## 7. Limits of this audit

- The dataset has 16 patients, and 13 are synthetic with seeded defects. Defect counts are illustrative.
- Performance numbers come from a dev stack with Xdebug. They show relative cost, not production capacity.
- Not covered: dynamic SQLi/XSS/CSRF testing, non-core modules, the patient portal, and the full legacy tree for injection.
- The deployed instance has not been audited separately. It uses the `deploy/` stack, which closes S1, S2 and S9 by construction.
