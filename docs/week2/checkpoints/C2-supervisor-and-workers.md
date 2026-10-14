# C2. Supervisor and two workers

Status: not started. High-level only; expanded when we reach C2.

Everything named here that is new in Week 2 is **(planned)**.

## Goal
One supervisor and two workers (intake-extractor, evidence-retriever) with explicit, logged handoffs, wrapping the Week 1 `AgentLoop` rather than rewriting it.

## Scope
- Supervisor state machine **(planned)** with typed `Handoff` objects and a step cap and deadline in the style of `agent/app/loop.py`.
- Answer synthesis that keeps chart or extracted facts and guideline evidence in separate labelled blocks; citation `source_type` of `chart | extracted | guideline`.
- Additive verifier changes for extracted-fact and guideline-chunk citations. Week 1 refusals stay unchanged.

## Acceptance (summary)
- Supervisor-worker contract test and routing cases pass.
- Every handoff is a logged event, and a trace is reconstructable from the correlation ID.
- Week 1 tests and evals still pass.

## Depends on
C1.
