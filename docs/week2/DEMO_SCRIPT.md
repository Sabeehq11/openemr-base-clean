# Week 2 demo script (3 to 5 minutes, synthetic data)

The recording is yours. This is the outline and the exact things to show. Prep: stack up, `scripts/copilot/deploy_w2.sh --dry-run` shows nothing left to do, `curl localhost:8400/ready` says `ready` or `degraded`, log in as `copilot_doc_a` (through the tunnel URL if you are showing the deployed app; see the README "Deployed" section for today's URL).

| Time | Show | Say |
|---|---|---|
| 0:00 (20 s) | Open one of Dr. Alder's patients, open the dock | A co-pilot that reads the chart and now also the paperwork that arrives as PDFs, and cites every claim |
| 0:20 (50 s) | Upload `agent/tests/fixtures/w2/lab_report.pdf` as a lab PDF. The result lists each test with value, flag and a citation | Extraction is a closed schema; a value without a source location is dropped. The module, not the agent, writes OpenEMR rows. Upload it again: no second record (idempotent on file hash) |
| 1:10 (40 s) | Click **View source** on a result line | The page image appears with the box drawn at the cited location. Server text is shown as text only. If the image cannot load, the quote and page remain |
| 1:50 (50 s) | Ask: "What changed, and what do the guidelines say about A1c frequency?" | Chart facts, extracted-document facts and guideline evidence arrive as separate labelled groups. Click a document chip: the same viewer |
| 2:40 (30 s) | Ask about a patient of Dr. Birch | Generic denial. Authorization is the user's own token on every call; the upload route has the same rule |
| 3:10 (40 s) | `scripts/w2/gate_demo.sh`, or open `evals/results/regression-evidence-2026-10-14.md` | Three injected faults (citation, safety, PHI) each fail the gate with exit 1; the files are restored byte for byte. This is the PR-blocking check |
| 3:50 (40 s) | `/dashboard` Week 2 section, then rebuild one trace from its correlation id (`SELECT ... FROM spans WHERE trace_id IN (...)`) | Ingestion and retrieval p95 against the SLOs, extraction pass rate, routing, three alerts. `n/a (CI)` for the eval alert in the image is expected |
| 4:30 (30 s) | `docs/week2/PERFORMANCE.md` and `BACKUP.md` | Stub numbers are labelled stub. The real-model run is pending approval and says so. The restore drill ran on a temp copy of synthetic data (the host `agent/data/w2.db` was empty); the running agent's volume was not drilled |

## Defense notes

- **Why a hand-written supervisor.** One verifier on every path, typed handoffs, a 6-step and 40 s cap, and no framework to audit. The graph is small enough to read in one file, and every handoff is a log line.
- **Why the tunnel.** It reuses what Week 1 already hardened (`tunnel.sh` runs the security gate and exposes only OpenEMR). The agent stays on `127.0.0.1:8400` and is reached only by the module with the signed-request secret. Limit: a quick-tunnel URL changes on every start, so the README records the date and the steps, not a permanent link.
- **Why page images are rendered on demand.** The agent keeps no document bytes. The module fetches them in the user's session, after its own ownership checks, and the agent verifies the bytes hash to the extracted document before rendering.
- **Known limits, said plainly.**
  - The real-model run has not happened. Only stub numbers exist. The SLOs are targets until it does.
  - With about 10 samples in that run, p95 is the near-maximum.
  - The eval-regression alert shows `n/a (CI)` in the deployed image; CI is what enforces it.
  - The quick-tunnel URL is not permanent. The optional VPS path in `deploy/` is untested.
  - The PHP isolated tests for `source.php` need the OpenEMR container; they were written but not run on a host without PHP.
  - Backup numbers come from a 12-document synthetic laptop copy, not from the 5 documents in the agent's volume. No backup scheduler is deployed.
  - OpenEMR retention gaps (Week 1 finding C6) are unchanged.
