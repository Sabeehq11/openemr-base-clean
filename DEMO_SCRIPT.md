# MVP demo video script (3 to 5 minutes)

1. **0:00 Problem (20 s).** The 90-second window between rooms. Target user: a PCP with 20 patients a day (USERS.md).
2. **0:20 Running system (40 s).** Show the deployed URL, log in, open a seeded patient (Maria Alvarez), point out the medications and labs spread across screens.
3. **1:00 Audit (90 s).** Open AUDIT.md and read the summary. Show two findings live:
   - Patient 2: Lisinopril inactive in the medication list but active in prescriptions (SQL or UI).
   - Reads are not audited: `select event,count(*) from log group by event` shows `patient-record-select` at 0.
4. **2:30 Architecture (75 s).** Walk the diagram in ARCHITECTURE.md: module, agent service, FHIR with the user's token, verifier, audit log. Name the trust boundaries and the verification limits.
5. **3:45 Proof it is testable (30 s).** Run `cd tests/playwright && npm test` and show the passing tests, plus `scripts/setup-local.sh`.
6. **4:15 Next (30 s).** Early submission: build the tools, verifier, evals, Langfuse dashboard.

---

# Checkpoint 2 demo script (5 to 7 minutes, synthetic data)

Prep: stack up, `agent/.env` set, `curl localhost:8400/ready` says ready. Log in as `copilot_doc_a` in one window.

1. **0:00 Claim (20 s).** A read-only co-pilot that cites every fact and refuses what it cannot prove.
2. **0:20 Brief (60 s).** Open one of Dr. Alder's patients. Open the dock, ask for the pre-visit brief. Point out the streamed answer, the citation chips and the flags (recomputed by rules, not trusted from the chart). Click a chip to show the source record.
3. **1:20 Follow-up (30 s).** Ask "what changed since the last visit?" Note it uses the cached snapshot, and that switching patient resets the conversation.
4. **1:50 Authorization (75 s).** In the same session ask about a patient of Dr. Birch. The dock answers with the generic denial. Say why: OpenEMR itself would have returned the chart (`agent/AUTHZ_PROOF.md`), so the agent checks CareTeam membership with the user's token on every request. Log in as the nurse: also denied.
5. **3:05 Refusals (30 s).** Ask for a diagnosis, then for a dose. Both refused. Ask it to ignore its rules: the chart text is treated as data.
6. **3:35 Degraded mode (30 s).** With `LLM_PROVIDER=unavailable` the dock still shows the raw problems, medications, allergies and labs.
7. **4:05 Observability (75 s).** Open `/dashboard` (Basic auth): requests, p50/p95, tool failures, verification pass rate, tokens and cost. Run `python -m app.cli alerts` and `verify-audit`. Show the three alert rules in `agent/RUNBOOK.md`.
8. **5:20 Evidence (45 s).** `PYTHONPATH=agent agent/.venv/bin/python -m evals.run` (47/47, tagged by failure mode), `./scripts/e2e_copilot.sh` (16 Playwright specs), and the Bruno collection.
9. **6:05 Limits and next (45 s).** Care team is the access list; no write tools; live-model evals are on demand. Next: load tests and the cost analysis.
