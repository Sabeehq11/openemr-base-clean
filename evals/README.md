# Evals

Every case names the failure mode it guards against (`guards`) and belongs to one category: invariant, boundary,
conflict, authorization, adversarial, failure_injection. Audit findings D1 to D9 each have at least one case.

D10 (vitals units) and D11 (UUID registry) are not covered: no vitals tool exists in this build, and D11 was fixed in
the seeder. D5 (duplicate patient warning) is a known gap, recorded by `bnd-duplicate-patient-D5-not-covered`.
That case asserts the gap exists, so it fails the day someone implements the check and forces the case to be rewritten.

## Tiers

| Tier | Command | Model | Cost |
|---|---|---|---|
| stub | `PYTHONPATH=agent agent/.venv/bin/python -m evals.run` (also runs inside `pytest`) | scripted, deterministic | none |
| live | `EVAL_MAX_USD=1.00 PYTHONPATH=agent agent/.venv/bin/python -m evals.run --tier live` | OpenRouter Claude Sonnet | capped, stops before the next case |

The stub tier runs the real gateway, authorization check, tools, loop, verifier, audit chain and trace store. Only the
FHIR server (synthetic charts in `charts.py`) and the model are replaced. It proves the *guards* work whatever the
model says, by scripting bad model output (invented numbers, diagnoses, injected instructions) and checking that
nothing unsafe reaches the user.

The live tier checks the same invariants against real model output. It needs `OPENROUTER_API_KEY` in `agent/.env`.
Without it the run exits 2 and writes nothing, so a missing key is never reported as a pass.

Results: `results/stub-latest.md|json`, `results/live-latest.md|json`.
