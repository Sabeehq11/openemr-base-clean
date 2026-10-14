# USERS.md: Target User and Use Cases

This is the source of truth for `ARCHITECTURE.md`. Every agent capability must point back to a use case ID (UC-n) below.

## Target user

**Dr. Dana Whitfield, primary care physician (internal medicine) in a small ambulatory clinic.** She sees about 20 patients per day in 20-minute slots, most of them established patients with chronic conditions (type 2 diabetes, hypertension, CKD, hyperlipidemia, AFib). She works from one workstation per exam room, logged into OpenEMR with her own account. She is the provider of record for her panel.

Not the target: nurses, residents, front-desk staff, and patients. The agent is built for a single physician, reading only that physician's own panel. Other roles are an authorization test case (see "Refusals"), not a supported persona.

## Her workflow today

| When | What she does | Pain |
|------|---------------|------|
| 7:45 AM, before clinic | Skims the schedule. Opens a few charts she is worried about. | There is no time to open all 20. Most patients are walked into cold. |
| Between rooms (about 90 s) | Opens the chart, clicks through demographics, problems, meds, last note, labs. | The facts are spread across 5 or more screens. Medications live in two places (the med list and prescriptions). Labs are in a separate report view. |
| In the room | Talks to the patient, reconciles meds from memory, orders labs. | She misses stale labs and changes since the last visit. |
| After the visit | Documents, then handles the in-basket. | Not in scope for this agent. |

## Moment the agent enters her day

The 30 seconds before she opens a chart: she has just finished with room 3 and the next patient is waiting in room 4. She has the patient's name from the schedule. She types or taps a question into the Co-Pilot panel docked next to the chart, reads a short answer with source links, and then walks in. The answer is a briefing, not a decision. She verifies anything surprising by clicking the cited source record.

## Use cases

### UC-1: 90-second pre-visit brief
*Between rooms, for the patient she is about to see, summarize who they are and what matters today.*

- **Input:** the patient already selected in the chart (patient context comes from the OpenEMR session, not from free text).
- **Output:** 5 to 8 lines: age/sex, active problems, current medications, allergies, last visit date and reason, and anything flagged (UC-2 and UC-3 items). Each line carries a source link (table and record id).
- **Latency target:** first useful tokens in 3 s or less, complete answer in 10 s or less.
- **Why an agent, not a dashboard:** a dashboard shows every field with equal weight. The brief has to pick what matters for *today's* visit reason and reconcile two medication sources into one honest statement ("on Lisinopril per prescriptions, but marked inactive in the med list"). That is selection plus synthesis across sources. A fixed layout cannot condition on the visit reason, and it cannot say what it is unsure of.

### UC-2: What changed since the last visit
*"What's new since I last saw her?"*

- **Output:** new or changed diagnoses, medication starts/stops/dose changes, new lab results (with flags), and new encounters or outside documents since the last encounter date.
- **Why an agent:** "since the last visit" is relative to a date the physician does not have to supply, and the answer crosses 4 tables (lists, prescriptions, procedure results, encounters). A sorted list would force her to find the last visit and diff the tables herself. This requires tool chaining: find the last encounter date, then query each source with that date.

### UC-3: Labs and medication safety flags
*"Anything overdue or concerning in her labs or meds?"*

- **Output:** abnormal or out-of-range results, labs that are stale (for example, no A1c in more than 6 months for a diabetic patient), and rule-based flags: a documented allergy that conflicts with an active medication, a missing allergy record ("no allergy documented", which is not the same as "no known allergies"), and duplicate medication entries.
- **Why an agent:** the physician needs the *absence* of data called out ("no A1c on file"). A chart view only shows what exists. The flags come from deterministic rules; the agent's job is to run them for the right patient and explain the result in plain language with sources.

### UC-4: Follow-up questions about the same patient
*"What was her last A1c, and what was it before that?" "Is she on any ACE inhibitors?"*

- **Output:** a short factual answer with citations, or an explicit "not found in the record".
- **Why an agent:** this is the only use case that needs **multi-turn conversation**, because follow-ups reference the previous answer ("before that"). It is justified by questions the physician only thinks of after reading the brief. If a follow-up cannot be answered from the record, the agent says so instead of guessing.

## Capabilities map (used by ARCHITECTURE.md)

| Capability | Needed by |
|------------|-----------|
| Patient-scoped read tools (demographics, problems, meds, allergies, labs, encounters) | UC-1, UC-2, UC-3, UC-4 |
| Tool chaining (last encounter date, then per-source queries) | UC-2 |
| Multi-turn conversation state (one patient per conversation) | UC-4 |
| Rule checks (allergy vs medication, lab staleness) | UC-3 |
| Source attribution on every claim | all |

If a capability does not appear in this table, it should not be built.

## Refusals (what the agent must not do)

- No diagnosis, treatment recommendations, or prescribing. It reports what is in the chart and flags rule-based items for the physician to judge.
- No writes to the chart. Read-only.
- No data for patients outside the authenticated physician's access. The agent answers "I can't access that patient" without confirming the patient exists.
- No answers from general medical knowledge presented as patient facts. Any claim about the patient must cite a record.
- No cross-patient questions ("which of my patients have...") in the MVP. Panel-level queries are a later extension and need their own authorization design.
- Instructions found *inside* patient records (free-text notes) are treated as data, never as commands.

## Tolerance for error

A wrong or invented medication, allergy, or lab value is worse than no answer. Dr. Whitfield tolerates "I could not find X" and a slower answer. She does not tolerate a confident wrong statement. This drives the verification design: unsupported claims are dropped or labelled, not softened.
