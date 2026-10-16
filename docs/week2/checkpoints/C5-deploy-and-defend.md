# C5. Deploy, performance, defend

Status: **built; two items wait on you** (the paid real-model run, and applying the deploy script to the live stack). Everything else was verified with stubs, mocks and temporary copies. No paid call was made.

## Goal
A reachable deployment of the Week 2 flow, click-to-source with a bounding-box overlay, the measurement and recovery evidence, and the defense material.

## What was built
| Piece | File |
|---|---|
| Page endpoint `POST /v1/w2/documents/{id}/page` (signed, patient-authorized, sha256-checked, nothing stored) | `agent/app/w2/api.py`, `ingest.py`, `docs/week2/openapi.yaml`, `agent/tests/test_w2_page_unit.py` |
| Module route with session, ACL and ownership checks; images direct, PDFs via the agent | `oe-module-copilot/public/source.php`, `src/SourceProxy.php`, `tests/Tests/Isolated/Modules/Copilot/SourceProxyIsolatedTest.php` |
| Viewer with overlay, pure `bboxToPercent`, text fallback; chat citations and upload rows | `public/assets/copilot-source.js`, `copilot.js`, `copilot-upload.js`, `copilot.css`, `tests/js/copilot-source.nodecheck.js` |
| Idempotent, non-destructive deploy script (dry run by default) | `scripts/copilot/deploy_w2.sh`, `agent/tests/test_w2_deploy_script_unit.py` |
| One Playwright spec for upload, source click and overlay position | `tests/playwright/specs/w2-click-to-source.spec.ts` |
| Measurement harness, stub and gated real tiers | `scripts/w2/measure.py`, `agent/tests/test_w2_measure_unit.py`, `docs/week2/PERFORMANCE.md` |
| Backup and restore drill on temporary copies | `scripts/w2/backup_drill.py`, `agent/tests/test_w2_backup_drill_unit.py`, `docs/week2/BACKUP.md` |
| Demo script and defense notes | `docs/week2/DEMO_SCRIPT.md` |
| Bruno request for the page endpoint | `docs/api-collection/copilot/w2/07-page.bru` |

## Safeguards, and how each is enforced
1. **Backup drill on temporary copies only.** Every write goes through `inside()` (real-path check against the drill's temp root, the source and the data dir). The source opens read-only. Tests prove a populated source is byte-identical afterward, an absent source is never created, and symlink escapes are refused.
2. **No paid run without your approval.** The real tier needs `--approved-budget uploads=N,chats=M,usd=X` and `W2_MEASURE_APPROVED=approve-<N>u-<M>c-<X>usd`. Approval is checked before any client is built. Hard caps are 10 uploads, 10 chats, $5.00. A run stops at the first provider error and at the spend cap, read from the target's own `traces.db`. Tests use a fake provider.
3. **Deployment is idempotent and non-destructive.** Dry run is the default; `--apply` is explicit. It never runs `down -v`, `unharden` or `seed_demo_patients.py` by default, never re-rotates credentials, and asserts the agent stays on `127.0.0.1:8400`. Tested with stubbed commands (second run is a no-op, seeding is refused when data exists). Dry run against the live stack today: only `docker compose up -d` and the tunnel start would change; patients 13, documents 5, `w2_documents` 5 before and after.

## Verification (targeted)
- Agent tests, `-m "not live"`: 870 passed, 1 skipped, 30 deselected. This includes the page, OpenAPI contract, PHI scan, measure, drill and deploy tests.
- Eval gate (`python -m evals.gate`): PASS.
- JS source viewer check: 11 of 11 pass (includes the full-page fallback box).
- Not run: the PHP isolated tests and the Playwright spec. There is no PHP on this host, and the spec needs the stack through the tunnel.

## Open items for you
1. **Approve or decline the real-model run.** Suggested: 10 uploads and 10 chats, cap $1.50, then give the token `approve-10u-10c-1.50usd`. Needs a separate agent instance with the real VLM and its own `DATA_DIR`.
2. **Apply the deploy script.** `scripts/copilot/deploy_w2.sh --apply` after reading the dry run. Then run the Playwright spec once with `BASE_URL` set to the tunnel URL.
3. Record the demo using `DEMO_SCRIPT.md`.

## Acceptance
- A grader can run the Week 2 flow locally and from the API collection: met. On the deployed app: pending item 2.
- Click-to-source with a bounding-box overlay: built and unit checked; the live browser check is pending item 2.
- Reports exist: `PERFORMANCE.md` (stub measured, real pending) and `BACKUP.md` (the 2026-10-15 run used a synthetic copy of the unused host file; the 2026-10-16 run drilled a read-only `.backup` of the `agent_agent_data` volume, 5 documents, with the live database passing `PRAGMA integrity_check` before and after).
