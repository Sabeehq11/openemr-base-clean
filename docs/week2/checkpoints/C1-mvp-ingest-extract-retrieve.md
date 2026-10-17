# C1. MVP: ingest, extract, retrieve

Status: **done** on branch `week2-agentforge`. Everything runs only with `W2_ENABLED=true`; with it off the agent is identical to `w1-baseline`.

## Goal
Upload a lab PDF and an intake form for a patient, extract strict-schema JSON with citations, and retrieve a guideline snippet, locally.

## What was built
- **Write path (decision):** module-side PHP. `public/upload.php` and `src/UploadProxy.php` take user and patient from the session, check CSRF and ACL, store the file with `Document::createDocument`, then send it to the agent with a signed `POST /v1/w2/documents`. Neither the chat model nor the agent's OAuth client gains a write path, so the Week 1 "no write tool" invariant holds (a new eval, `inv-w2-ingest-is-not-a-chat-tool`, checks that no upload or extract tool is in the chat tool list).
- **Contracts** (`agent/contracts/w2/`): frozen Pydantic models, `extra="forbid"`: `Citation` (bbox required, 0 to 1, for extracted fields), `LabRow`, `LabPdfExtraction`, `IntakeFormExtraction`, `DocumentRecord`, `GuidelineChunk`, `RetrievalResult`.
- **Extraction** (`agent/app/w2/`): `w2.db` store scoped by patient; render fallback chain PyMuPDF, pypdfium2, Poppler, native-PDF last resort; OpenRouter VLM client plus a stub; a malformed reply means `extraction_failed` and zero rows; a value missing from its quote becomes `unsupported`; a LOINC code is kept only when the printed name and method support it; the abnormal flag is recomputed by `app/rules.py`, not trusted from the model.
- **Chart integration:** `LabRecordWriter` writes `procedure_order` (history only) to `procedure_report` (unreviewed) to `procedure_result`, linked by `document_id`, in one transaction, insert-only, `supported` rows only. Week 1's `get_labs` reads them with no Week 1 change. The intake form is filed under Patient Information; its values stay `pending` in `w2.db` (deliberate deferral, `W2_ARCHITECTURE.md` section 11).
- **Idempotency:** (patient, SHA-256) in `w2.db`; OpenEMR `documents.hash` checked before upload; `(document_id, result_text)` checked before each lab insert.
- **Retrieval** (`agent/app/w2/rag/`): 5 corpus files in `agent/corpus/` (inside the Docker build context, so the image carries them), own BM25, hashed (CI) and hosted (cached) embedders, RRF (k=60), Cohere Rerank v3.5 through OpenRouter `/rerank` (same key as the VLM and embeddings) with an offline lexical fallback, explicit `empty` and `rerank_skipped`.
- **Authorize before write:** the module calls `POST /v1/w2/preflight` (signature, care-team check, size, type, page count; stores nothing) before it files any document in OpenEMR. A denial is 403 and nothing is written. If the agent refuses after the file was filed, the module soft-deletes only the document that request created (same flag OpenEMR's own delete sets); a pre-existing document is never touched.
- **API:** `POST /v1/w2/preflight`, `POST /v1/w2/documents`, `GET /v1/w2/documents/{id}`, `POST /v1/w2/documents/{id}/links`, `POST /v1/w2/retrieve`; each is gateway-verified, then patient-authorized, fail closed. Audit events carry ids and counts only.
- Bruno requests: `docs/api-collection/copilot/w2/`. Demo: `scripts/copilot/demo_c1.sh`.

## Verified
- Agent pytest and stub evals pass with `W2_ENABLED` at 0 and 1 (Week 1 tests unchanged; new W2 tests added). The invariant eval brings the eval count to 61.
- Isolated PHPUnit (the Copilot module tests and the full isolated suite) passes.
- Live provider run (spend-capped, synthetic data only, 2026-10-14), through the one `OPENROUTER_API_KEY`:
  - **VLM** (Sonnet 4.5, one call on `lab_report.pdf`): all 5 rows supported; values, units, flags and the collection date matched the fixture. The model's own boxes were wrong, so the stored citation box is the PDF's own text line, found by matching the quote against the text layer (it matched the printed line exactly on all 5 rows); with no text layer, or a quote that spans or repeats lines, the box is the whole page. The model proposed the direct-assay LDL code `18262-6` for a plain "LDL Cholesterol" row; the page does not say "direct", so `app/w2/loinc.py` stored no code and kept the value and citation.
  - **Hosted embeddings and Cohere Rerank v3.5 via `/rerank`:** all requests returned 200 and each query made one rerank call. The right passage ranked first for 8 of 8 queries (7 on-topic, 1 off-topic correctly `empty`); the offline lexical reranker got 7 of 8 and missed "How often should A1c be tested?". A deliberately invalid key returned 401, was not retried, and fell back to lexical with `rerank_skipped` set. Reranking cost about $0.001 per call.
- Not covered live: the intake form, scanned images and the native-PDF fallback (their citations use the whole-page box), and the relevance cutoff (0.15) was checked on only 8 queries. Re-run with `W2_USE_STUB_VLM=false` and a key set.

## Depends on
C0 complete. Next: C2 (supervisor and two workers).
