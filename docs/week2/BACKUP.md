# Week 2 backup and recovery drill

Design and table of what is protected: `W2_ARCHITECTURE.md` section 10. This file records what a drill actually measured.

## What was drilled, and what was not

| Data | Drilled? | Why |
|---|---|---|
| `w2.db` (documents, facts, citations) | Yes, on an isolated temporary copy of **synthetic data** (see "Which file was the source") | Derived, rebuildable, safe to wreck on a copy. The running agent's own store was **not** drilled |
| Rebuild with no backup (re-extraction) | Yes, stub model, temp copy | Free and deterministic; real-model time is in `PERFORMANCE.md` when that run is approved |
| Agent starting from a restored copy | Yes, a throwaway process on port 8497 | Never the live container |
| Golden set and eval gate from the repo alone | Yes, in a temp directory | Proves the evidence rebuilds without local state |
| OpenEMR database and documents volume | **No** | Operator-owned and the only irreplaceable data. Documented, not drilled |
| `audit.db`, `traces.db` | No | Same `.backup` mechanism as `w2.db`; not repeated |

## Safeguards (enforced in `scripts/w2/backup_drill.py`)

- The source file is opened read-only (`mode=ro`) and copied once with SQLite's online backup API into a fresh temp directory.
- Every delete, corruption and restore goes through `inside()`, which resolves real paths and refuses anything outside the
  temp root, the source file, or the live data directory. Symlink escapes are refused.
- The live agent is never called. cwd is the temp root, so `agent/.env` is never loaded by the throwaway agent.
- If the source file does not exist or is empty, the COPY is filled from committed synthetic fixtures. The source path is never created.
- The default `--source` is the host file `agent/data/w2.db`. That is **not** the running agent's store (see below).
- The temp directory is removed at the end. The report records `live_source_unchanged` (sha256 before and after; the key name is kept from the code and covers whatever `--source` points at) and
  `temp_dir_removed`.
- Tests (`agent/tests/test_w2_backup_drill_unit.py`): paths outside the root, the source, the data dir and symlink escapes
  are refused; a populated source stays byte-identical and nothing else appears beside it; an absent source is never created.

## Run

```
agent/.venv/bin/python scripts/w2/backup_drill.py            # report on stdout; add --out file.json
```

## Measured (2026-10-15, local machine, stub model)

The source was the host file `agent/data/w2.db`, which had 0 rows, so the copy was seeded with 12 synthetic documents
(6 lab, 6 intake): 90 facts, 90 citations, 92 KiB. That host file's hash was unchanged. Every figure below is for this
synthetic copy, not for the demo patients' extractions.

### Which file was the source

Two different `w2.db` files exist, and the earlier wording ("the live `w2.db` was empty") confused them:

| File | Used by | Rows in `w2_documents` |
|---|---|---|
| `agent/data/w2.db` on the host checkout (dated 2026-10-14) | Nothing running. A leftover from local runs (`DATA_DIR=./data` in `agent/.env`) | 0 (read read-only, 2026-10-15) |
| `/srv/agent/data/w2.db` inside the `agent_data` Docker volume | The running agent container (`docker-compose.yml` sets `DATA_DIR=/srv/agent/data`) | 5 as reported by `deploy_w2.sh` and the C4/C5 checkpoints. **Not re-read for this report** |

So there is no contradiction: C4 and `deploy_w2.sh` counted the container's volume (5 documents); this drill read the
unused host file (0 rows). The drill therefore says nothing about the 5 real extractions, and the live store was never
opened, copied or changed by it.

## Measured (2026-10-16, the running agent's volume)

The source this time was a SQLite `.backup` of `/srv/agent/data/w2.db` inside the `agent_agent_data` Docker volume, taken
by a throwaway container that mounted the volume read-only. The live agent container was not entered for the copy and was
not restarted (same start time before and after).

The live file hash was deliberately not compared. The store opens SQLite without setting a journal mode, so ordinary WAL
checkpointing rewrites `w2.db` and changes its hash even when the drill touched nothing. Health was checked instead:
`PRAGMA integrity_check` on the live database returned `ok` both before and after, and `w2_documents` stayed at 5.

| Check | Result |
|---|---|
| Live `PRAGMA integrity_check` before and after | `ok` both times; document count 5 both times |
| Snapshot `PRAGMA integrity_check` and document count | `ok`, 5, matching the live count taken just before the copy |
| `backup_drill.py` on the temp copy (`--skip-gate`) | Exit 0. `source_rows`: 5 documents, 30 facts, 25 citations. `seeded_in_copy` absent, so nothing synthetic was added |
| Restore after delete and after corruption | Both `restore_verified` true; restore 0.6 ms each |
| Throwaway agent over the restored copy | `/ready` answered, `document_store` ok, 746 ms from process start |
| Agent container | Not restarted; no WAL files were present, so the copy could not miss uncheckpointed rows |

No row contents, quotes or patient identifiers are recorded here. The temp copy and the report were deleted after the
checks.

| Step | Result |
|---|---|
| Copy source file read-only | 0.9 ms |
| Snapshot (SQLite `.backup`) | 0.8 ms |
| Disaster A, file deleted: restore from snapshot | 0.7 ms; counts, content digest and `integrity_check` equal to the snapshot |
| Disaster B, file overwritten with random bytes (integrity check reports a failure) | restore 0.7 ms; same verification, passed |
| Throwaway agent over the restored copy | `/ready` answered, `document_store` ok, 697 ms from process start |
| Rebuild with no backup, 12 documents, stub model | 18 ms per document, 12 of 12 documents back |
| Golden set and gate from a copy of the repository's files only | PASS, 3.0 s |

3 documents were written after the snapshot in each drill; they are gone after restore, which is the RPO window made visible.

### Measured RPO and RTO against the design targets

| Target | Design | Measured | Notes |
|---|---|---|---|
| RTO, agent service from a `w2.db` backup | 1 hour | **Under 2 seconds** for file restore plus agent start (this drill), plus operator time to fetch the copy | Demo-size store, local disk. Operator time dominates in practice |
| RTO, derived facts with no backup | 2 to 4 hours | **Stub: 18 ms per document, about 0.2 s for 12.** Real: bounded by VLM latency per document | The real figure is in `PERFORMANCE.md` once the approved real run exists. Until then it is VLM time times document count, and it is not measured |
| RPO, derived and audit data | 24 hours | **Equals the backup interval.** Everything written after the last snapshot is lost (3 of 15 documents in the drill) | The drill proves the loss window exists, not that nightly copies are scheduled. No scheduler is deployed by this project |
| RPO, patient record | OpenEMR host policy | Not measured | Operator-owned |
| RTO, Week 1 chat with W2 storage down | 0 | Not re-measured; covered by the C4 degraded-readiness test and the shared-path check | |

## Limits stated plainly

- Numbers come from a 12-document, 92 KiB store on a laptop disk. They show the mechanism works and how fast it is
  without contention. They do not predict a large store or a network volume.
- The "nightly copy" is a recommendation. Nothing in the repository schedules it, and the tunnel demo runs no backup job.
- Restoring `w2.db` alone does not restore `audit.db`. The audit chain proves integrity only for the file it lives in;
  restoring an older copy loses later entries. Keep the audit key separately, as section 10 says.
- A restored `w2.db` can be older than the documents in OpenEMR. Documents uploaded after the snapshot have no extraction
  until they are re-uploaded or re-extracted.
- OpenEMR retention gaps (Week 1 audit finding C6) are unchanged.
