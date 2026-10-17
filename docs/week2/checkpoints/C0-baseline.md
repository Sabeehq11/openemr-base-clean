<!-- Detailed C0 plan, copied from the approved plan. Status: executing on branch week2-agentforge. -->

# Week 2 C0: Baseline and Architecture Defense

## Verified Git state (corrects the earlier roadmap)

Read-only checks show the earlier "about 30 uncommitted files" came from a stale snapshot:
- The working tree is **clean**. `main` is at `2e1ed62`, in sync with `origin/main`, with no stashes and no tags.
- The "modified" files were committed in `2e1ed62` (docs moved to `docs/week1/`, scripts to `scripts/copilot/`).
- `agent/.env` and `agent/data/` are git-ignored. No secrets are tracked.
- No git hooks are installed and `gh` is not installed locally.
- No GitHub workflow touches `agent/` or `evals/`, so the PRD's PR-blocking gate does not exist yet.

Docs versus reality (record these in the baseline, do not guess):
- Docs say **56** stub evals. The latest run (`evals/results/stub-latest.json`) says **60/60**, and the registry has 60 stub cases.
- Docs say "321 passed", which includes live tests. The agent has about 319 test functions, so the offline pass and skip counts must be measured.
- The local venv is Python 3.10 and the container is 3.12. Record both.

The old roadmap gap "uncommitted changes" and the step "commit pending changes" are dropped. C0 starts from a clean tree.

## Plan structure (what gets created when we execute)

- `docs/week2/ROADMAP.md`: the full Week 2 roadmap, reference only, corrected for the facts above, with a status line per checkpoint.
- `docs/week2/BASELINE.md`: the recorded Week 1 baseline (C0 output).
- `docs/week2/checkpoints/C0-baseline.md`: this plan, detailed.
- `docs/week2/checkpoints/C1..C5-*.md`: short high-level stubs, expanded only when we reach each one.
- `W2_ARCHITECTURE.md` at repo root (required by the PRD).

Method: one long-lived branch, `week2-agentforge`, created from `main` at the start of C0. Each checkpoint lands as its own verified commit (or short commit series) on that branch, and a checkpoint's commit is made only after that checkpoint's verification checks pass. No per-checkpoint branches or pull requests. C0's commit message is `docs(w2): C0 baseline, roadmap and architecture` (Conventional Commits, per `CLAUDE.md`). When and how the branch merges to `main` is decided after C0 and is not part of C0. The PRD puts Early Submission at Thursday 11:59 PM, so C0 is time-boxed to about 2 hours.

Existing versus planned (labeling rule for every C0 doc): a path, endpoint, flag, env var or file that exists at `w1-baseline` is written plainly. Anything Week 2 will add, such as `agent/app/w2/`, `agent/contracts/w2/`, `/v1/w2/chat`, `W2_ENABLED`, `w2_facts`, `evals/w2/` or the CI workflow, is marked **(planned)** at first mention in each section and listed in a "Planned Week 2 surface" table in `W2_ARCHITECTURE.md`. Planned items are never presented as runnable.

## C0 scope (docs and records only)

**C0.1 Roadmap reference.** Save the roadmap to `docs/week2/ROADMAP.md` and create the C0 to C5 plan files. Only C0 is detailed.

**C0.2 Baseline record.**
- Tag `w1-baseline` at the commit that is `HEAD` when C0 starts.
- Run offline suites only: `cd agent && .venv/bin/python -m pytest` (live tests stay skipped without `OE_LIVE=1`) and `PYTHONPATH=agent agent/.venv/bin/python -m evals.run`.
- The eval run rewrites tracked `evals/results/stub-latest.*`. Restore them with `git checkout` unless the content changed for a real reason, so the baseline stays docs-only.
- Write `docs/week2/BASELINE.md`: commit SHA, date, Python and dependency versions, exact pytest pass/skip counts, eval counts per category, and pointers to the existing p50/p95 numbers in `docs/week1/PERFORMANCE.md` and cost numbers in `docs/week1/AI_COST_ANALYSIS.md`. Do not re-run load tests.
- Optional, only if the local OpenEMR stack is already up: one `OE_LIVE=1` run, recorded separately and labelled live.

**C0.3 `W2_ARCHITECTURE.md` v1 (the 4-hour defense doc).** Sections:
1. Week 1 baseline versus Week 2 behavior, and the isolation rules (new code under `agent/app/w2/` and `agent/contracts/w2/`, `W2_ENABLED` default off, `/v1/chat` untouched, supervisor wraps `AgentLoop`).
2. Document ingestion flow and typed contracts: `Citation`, `LabPdfExtraction`, `IntakeFormExtraction`, `Handoff`.
3. Worker graph (supervisor, intake-extractor, evidence-retriever) and the handoff decision rules.
4. RAG design: corpus, chunking, BM25 plus dense with rank fusion, Cohere Rerank, offline stub for CI.
5. Eval design: 50 cases, categories, boolean rubrics (`schema_valid`, `citation_present`, `factually_consistent`, `safe_refusal`, `no_phi_in_logs`), gate thresholds.
6. Data model and authority per artifact (extracted lab observations, intake facts, guideline chunks, citations, golden set): owner, lineage, access control, validation rules. Any change to a Week 1 schema carries a migration note.
7. Security and PHI: threat list (prompt injection through documents, VLM hallucination, PHI in logs and traces, upload abuse), scrubbing approach, and the plan for the CI PHI check.
8. Testing strategy table: what is unit-tested, integration-tested, golden-evaluated, and not tested and why. Every test names the failure mode it guards.
9. Failure modes: ingestion failure, schema violation, empty retrieval, routing error. Each states how to spot it in logs and the recovery action.
10. Backup and recovery with RPO and RTO estimates, and the Week 1 debt list.
11. Open decisions: write path to OpenEMR (the agent's OAuth client is read-only, so `user/document.write` or a module-side PHP write is a spike in C1), and the dense embedding source.

**C0.4 README split.** Add "Week 1 baseline" and "Week 2 multimodal" sections to the README. List every environment variable, its default and which flow needs it. Week 2 flow instructions are marked "planned" until built, so graders are not misled.

**C0.5 Housekeeping.** Correct the stale "56" mentions only where the baseline proves a different figure, and note the change in `BASELINE.md`. No application code, tests, CI or config change.

## C0 tests and acceptance criteria

Verification (no new code means no new tests; checks are on the record):
- Baseline reproducibility: a second run of the same two commands gives the same counts as `BASELINE.md`.
- `git status` is clean after the baseline run, apart from the new docs.
- `git diff --stat w1-baseline` on `week2-agentforge` shows only `docs/week2/**`, `W2_ARCHITECTURE.md` and `README.md`, checked before the C0 commit is made.
- Every PRD "document" requirement maps to a section in `W2_ARCHITECTURE.md`: schemas, RAG and eval design, security, data model, testing strategy, failure modes, backup with RPO/RTO, PHI audit approach.
- Every **existing** file path, endpoint, flag and env var named in the C0 docs resolves (a quick `rg` or `ls` pass against `w1-baseline`). Future Week 2 items are not required to resolve, but each must carry the **(planned)** label or appear in the "Planned Week 2 surface" table. A check lists any unlabeled reference that does not resolve as a failure.
- `BASELINE.md` states which numbers were measured today versus quoted from Week 1 docs.
- A reader can tell from the README which flow, flag and service is Week 1 and which is Week 2.

Acceptance:
- `w1-baseline` tag exists and points at a clean, green commit.
- `BASELINE.md` has exact offline pytest and eval numbers with versions.
- `W2_ARCHITECTURE.md` v1 covers all ten sections, with open decisions listed explicitly.
- `docs/week2/ROADMAP.md` and the C0 to C5 plan files exist; C1 to C5 stay high-level.
- Week 1 behavior is provably unchanged: no files under `agent/`, `evals/`, `interface/` or `deploy/` differ from `w1-baseline`.

Rollback: C0 is docs only, so reverting the C0 commit restores the exact Week 1 state (or reset `week2-agentforge` to `w1-baseline`). The tag is local until pushed.

## Risks specific to C0

- **Offline tests may fail on this machine** (Python 3.10 versus 3.12, a missing dependency). Record the failures as the baseline and fix only the environment, never the tests.
- **Eval run dirties tracked result files.** Restore them or the baseline commit is not docs-only.
- **Doc drift.** Numbers in docs are stale (56 versus 60). Only measured numbers go in new docs.
- **Scope creep into C1.** Contracts and code stay out of C0.

## Later checkpoints (high-level only)

- **C1 MVP: ingest, extract, retrieve.** Contracts, upload and `attach_and_extract`, `w2_facts` storage, guideline corpus with hybrid retrieval and rerank, synthetic fixtures. Opens with the OpenEMR write-path spike.
- **C2 Supervisor and two workers.** Typed handoffs, intake-extractor, evidence-retriever, additive verifier changes for extracted and guideline citations.
- **C3 Eval gate.** 50-case golden set, boolean rubrics, baseline and gate script, PR-blocking GitHub workflow plus local hook, a demonstrated injected regression.
- **C4 Observability and contracts.** Correlation IDs and parent spans, W2 logs, dashboard metrics, alerts, `/ready` with degraded status, OpenAPI, Bruno updates, PHI CI check.
- **C5 Deploy and defend.** Deployment, click-to-source UI with bbox overlay, performance and cost report, backup drill, demo video.

Each is expanded into its own detailed plan only when we reach it. Because C3 is the PRD's hard gate and the clock is tight, C1 and C2 plans will be scoped to the minimum that C3 needs.
