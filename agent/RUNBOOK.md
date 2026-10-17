# On-call runbook

Synthetic-data demo system. The same steps apply to a real deployment.

Where to look:

- Dashboard: `http://127.0.0.1:8400/dashboard` (HTTP Basic, any user, password = `DASHBOARD_TOKEN` from `agent/.env`).
  Disabled (404) when the token is unset. Not published through the tunnel.
- Alerts: `docker exec agent-agent-1 python -m app.cli alerts` (exit 0 clear, 2 firing). Run it from cron or a loop.
- Logs: `docker logs agent-agent-1`. Every line carries the `correlation_id`; the same id is in the audit chain and trace.
- Audit chain: `docker exec agent-agent-1 python -m app.cli verify-audit` (exit 3 = tampered).

Alert thresholds come from `app/metrics.py` and need at least 5 samples in the window, so one slow request never pages.

## high_latency_p95
**Fires:** p95 request latency above 25 s over the last 5 minutes (measured healthy real-model p95 is about 19 s; see docs/week1/PERFORMANCE.md).
**Patient impact:** clinicians wait too long for a brief. The loop caps at 20 s so nothing hangs forever.

1. Open the dashboard. Look at the Tools table and the p95 numbers. Is one tool slow, or is it the LLM?
2. LLM slow: check `https://status.openrouter.ai`. If the provider is degraded, set `LLM_PROVIDER=unavailable`
   in `agent/.env` and `docker compose -f agent/docker-compose.yml up -d`. Users then get the chart-data-only mode
   instead of waiting. Revert when the provider recovers.
3. FHIR tool slow: check OpenEMR (`curl -sk https://localhost:9300/apis/default/fhir/metadata`, `docker stats`).
   Restart the OpenEMR container if it is starved; the agent retries once and fails closed on timeout.
4. Record cause and fix in the incident notes.

## high_error_rate
**Fires:** more than 5% of requests ended in `error` over 10 minutes. Denials and degraded answers are not errors.
**Patient impact:** clinicians see "could not produce a verified answer". Nothing unverified is ever shown.

1. `docker logs --since 15m agent-agent-1 | rg '"level": "(ERROR|WARNING)"'` and group by `msg`.
2. Mostly `failed` after an LLM answer that does not parse: the model is returning malformed output. Check
   `OPENROUTER_MODEL` was not changed, then switch to `LLM_PROVIDER=unavailable` until it is fixed.
3. Mostly `gateway_rejected` in the audit rows with `why=bad_signature`: the module and agent secrets differ.
   Make `signing_secret` in `config.local.php` equal `MODULE_SIGNING_SECRET`. Repeated `replay` or
   `stale_timestamp`: check the clock on the host and in the container; do not widen the 60 s window.
4. Many `unauthorized` tool errors: the OAuth client or scopes changed. Re-check `agent/AUTHZ_PROOF.md`.

## high_tool_failure_rate
**Fires:** more than 10% of tool calls failed over 10 minutes.
**Patient impact:** answers are missing sources; the answer states which source was unavailable.

1. Dashboard Tools table: which tool, and how many retries.
2. All tools failing: OpenEMR is down or the token issuer is broken. `/ready` shows it (`checks.openemr`).
3. One tool failing: a FHIR resource changed shape (`bad_response`). Reproduce with
   `OE_LIVE=1 python -m pytest tests/test_tools_live.py`, fix the mapper, add a regression test.
4. Failures are retried once automatically; sustained failures mean the cause is persistent, not transient.

## w2_extraction_failure_rate
**Fires:** more than 20% of document extractions ended `failed` over 10 minutes (needs 5 or more; rejected and denied uploads do not count).
**Patient impact:** uploaded documents are kept but show no extracted data; nothing is written to the chart from a failed extraction.

1. Dashboard, Week 2 section: failure rate, ingest p95 and the outcome counts. Logs: `rg '"msg": "w2_extract"'` and group by `status` (`vlm_error`, `schema_invalid`, `no_text`).
2. Mostly `vlm_error`: the provider or key. Check `https://status.openrouter.ai`, `OPENROUTER_API_KEY`, and `/ready` (`w2_checks`). Documents are kept; extraction can be retried by re-uploading the same file (a duplicate reuses the stored document).
3. Mostly `schema_invalid`: the model output stopped matching the strict schema. Check `W2_VLM_MODEL` was not changed, reproduce on a synthetic fixture, never log the content, then add a regression case.
4. Mostly one document type: a layout the renderer cannot read. Check the renderer names in the `w2_startup` event.

## w2_retrieval_latency_p95
**Fires:** p95 of the `w2/retrieve` span above 3 s over 5 minutes (needs 5 or more).
**Patient impact:** guideline evidence arrives late. Chat still answers; the 30 s worker timeout bounds it.

1. Open the trace for a slow request by its `correlation_id`: the `embed` and `rerank` child spans show which stage is slow.
2. `rerank` slow: provider degraded. After one retry the retriever skips the rerank (`w2_rerank status=skipped`) and uses the fused order. Check `/ready` (`reranker` reports `degraded` after a failed call).
3. `embed` slow: same for the embedder; on failure retrieval is sparse-only. Embedding vectors are cached in `data/w2_embed_cache.json`, so only new text pays.
4. Neither: check CPU on the host (BM25 is in process).

## w2_eval_regression
**Fires:** a rubric in the latest golden-set result is more than 5 points below `evals/w2/baseline.json`.
**Patient impact:** none directly; the quality gate that protects extraction, citation and refusal behavior has slipped.
**Where it runs:** this alert reads `evals/results/w2-golden-latest.json` and `evals/w2/baseline.json`, which are repo files. The deployed agent image does not contain them, so there the dashboard shows `n/a (CI)` and the alert cannot fire. Enforcement is the CI gate (`w2-eval-gate.yml`), which fails the pull request. The alert is for a source checkout or dev stack.

1. Dashboard, Eval gate table: which rubric and category regressed. CI (`w2-eval-gate.yml`) fails the same change, so find the commit that went out without it.
2. Reproduce with `python -m evals.run` (stub, no network). Fix the cause; do not lower the baseline to make the alert quiet.
3. An intentional change: update the baseline in the same commit and say why in the commit message.

## Week 2 degraded (`/ready` status `degraded`)
`/ready` returns 200 with `status: degraded` and a failed entry in `w2_checks`. Week 1 chat is unaffected.

- `document_store` failed: `w2.db` cannot be opened. Check the data volume and disk space; restore from backup if corrupt (W2_ARCHITECTURE.md section 10). Week 2 requests return an unavailable error until then.
- `vector_index` failed: the guideline corpus did not load or the last embedding call failed (retrieval is sparse-only). Restore the provider; no data loss.
- `reranker` failed: key missing or the last rerank call failed; the fused order is used and flagged. Set `OPENROUTER_API_KEY` or wait for the provider.
- These checks never call a provider, so a state clears only after the next real request succeeds.

## Audit chain broken
`verify-audit` exits 3, the dashboard says BROKEN, and `/ready` reports `audit_chain` not ok.

1. Treat as a security incident. Stop the agent so no more rows chain onto a bad record.
2. Copy `data/audit.db` aside untouched. Find the first bad row (printed by the command).
3. Compare with the latest export made by `export-audit` (store exports outside the container). Rows after the last
   export and before the bad row are still trustworthy. Report the window that cannot be trusted.

## Routine
- Weekly: `python -m app.cli export-audit /path/outside/container/audit-YYYYMMDD.jsonl` and keep it somewhere the agent
  cannot write. The chain detects edits; only an external copy detects deletion of the newest rows.
- The dashboard shows the audit row count, so an unexpected drop is visible.
