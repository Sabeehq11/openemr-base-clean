# MVP demo video script (3 to 5 minutes)

1. **0:00 Problem (20 s).** The 90-second window between rooms. Target user: a PCP with 20 patients a day (USERS.md).
2. **0:20 Running system (40 s).** Show the deployed URL, log in, open a seeded patient (Maria Alvarez), point out the medications and labs spread across screens.
3. **1:00 Audit (90 s).** Open AUDIT.md and read the summary. Show two findings live:
   - Patient 2: Lisinopril inactive in the medication list but active in prescriptions (SQL or UI).
   - Reads are not audited: `select event,count(*) from log group by event` shows `patient-record-select` at 0.
4. **2:30 Architecture (75 s).** Walk the diagram in ARCHITECTURE.md: module, agent service, FHIR with the user's token, verifier, audit log. Name the trust boundaries and the verification limits.
5. **3:45 Proof it is testable (30 s).** Run `cd tests/playwright && npm test` and show the passing tests, plus `scripts/setup-local.sh`.
6. **4:15 Next (30 s).** Early submission: build the tools, verifier, evals, Langfuse dashboard.
