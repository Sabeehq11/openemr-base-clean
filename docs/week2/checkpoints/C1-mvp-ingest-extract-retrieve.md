# C1. MVP: ingest, extract, retrieve

Status: not started. High-level only; this file is expanded into a detailed plan when we reach C1.

Everything named here that is new in Week 2 is **(planned)**.

## Goal
Upload a lab PDF and an intake form for a patient, extract strict-schema JSON with citations, and retrieve one guideline snippet, locally.

## Scope
- Spike first: how Week 2 writes to OpenEMR (`user/document.write` for the agent's OAuth client versus a module-side PHP write). Decide and record in `W2_ARCHITECTURE.md`.
- Strict contracts **(planned)**: `Citation`, `LabPdfExtraction`, `IntakeFormExtraction`.
- Upload path through the module, `attach_and_extract` **(planned)**, idempotent on file hash plus patient, `w2_facts` **(planned)** store.
- Guideline corpus, hybrid retrieval and rerank with offline stubs, synthetic fixtures.

## Acceptance (summary)
- Local demo of upload, cited JSON and one guideline snippet.
- Week 1 pytest and stub evals still pass with `W2_ENABLED` **(planned)** at 0 and 1.

## Depends on
C0 complete.
