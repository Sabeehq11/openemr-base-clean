# C3. Eval gate

Status: not started. High-level only; expanded when we reach C3. This is the PRD hard gate: if a small regression does not fail the build, Week 2 does not pass.

Everything named here that is new in Week 2 is **(planned)**.

## Goal
A 50-case golden set with boolean rubrics and a PR-blocking gate.

## Scope
- 50 synthetic cases under `evals/w2/` **(planned)**, versioned in git. Categories: extraction, retrieval, citations, refusals, missing data, PHI and logging.
- Rubrics: `schema_valid`, `citation_present`, `factually_consistent`, `safe_refusal`, `no_phi_in_logs`.
- Baseline file and a gate script that fails on a drop of more than 5 points in any category or below an absolute threshold.
- GitHub Actions workflow plus a local hook running agent unit, schema, contract and extraction regression tests, the gate, a dependency audit and the existing secret scan. Stub tier only.

## Acceptance (summary)
- An injected regression (for example removing the citation check) fails the gate, with the evidence kept.
- Golden set is reproducible from the repo alone.

## Depends on
C1 and C2 (scoped to the minimum this checkpoint needs).
