"""Workers: stored-fact reads are patient-scoped and support-filtered; retrieval maps to ok or empty."""
from app.config import Settings
from app.w2.facts import RunContext
from app.w2.rag import build_retriever
from app.w2.workers import EvidenceRetrieverWorker, IntakeExtractorWorker, wants_evidence
from contracts.w2 import DocType, GraphState, WorkerStatus
from tests.w2_helpers import OTHER, PATIENT, fx, make_ingestor, stub


async def _ingest(tmp_path, doc_id=1, name="lab_report.pdf", doc_type=DocType.LAB_PDF, vlm=None):
    ing, store = make_ingestor(tmp_path, vlm=vlm)
    await ing.ingest(PATIENT, doc_id, doc_type, fx(name))
    return ing, store


async def test_no_documents_is_empty_not_ok(tmp_path):  # guards: "no documents" reported as success
    _, store = make_ingestor(tmp_path)
    ctx = RunContext(PATIENT, "what changed")
    r = await IntakeExtractorWorker(store, ctx).run()
    assert r.status is WorkerStatus.EMPTY and r.error_code == "no_documents" and not ctx.facts


async def test_reads_supported_lab_facts_with_stable_ids(tmp_path):  # guards: uncitable or unstable fact ids
    _, store = await _ingest(tmp_path)
    ctx = RunContext(PATIENT, "what changed")
    r = await IntakeExtractorWorker(store, ctx).run()
    assert r.status is WorkerStatus.OK and r.refs == [f.fact_id for f in ctx.facts]
    assert all(f.fact_id.startswith("doc:1#") and f.citation.bbox is not None for f in ctx.facts)


async def test_other_patients_documents_are_never_read(tmp_path):  # guards: cross-patient leakage
    _, store = await _ingest(tmp_path)
    ctx = RunContext(OTHER, "what changed")
    r = await IntakeExtractorWorker(store, ctx).run()
    assert r.status is WorkerStatus.EMPTY and not ctx.facts


async def test_unsupported_rows_are_excluded_but_counted(tmp_path):  # guards: unsupported values shaping an answer
    _, store = await _ingest(tmp_path, vlm=stub(lab="vlm_lab_unsupported.json"))
    ctx = RunContext(PATIENT, "what changed")
    await IntakeExtractorWorker(store, ctx).run()
    assert ctx.unsupported >= 1
    assert all(f.citation.quote_or_value for f in ctx.facts)


async def test_failed_extraction_is_a_failed_result(tmp_path):  # guards: a broken document read as "nothing found"
    from tests.w2_helpers import fxt
    from app.w2.vlm import StubVlm
    bad = StubVlm({DocType.LAB_PDF: fxt("vlm_malformed.txt"), DocType.INTAKE_FORM: fxt("vlm_intake_good.json")})
    _, store = await _ingest(tmp_path, vlm=bad)
    ctx = RunContext(PATIENT, "what changed")
    r = await IntakeExtractorWorker(store, ctx).run()
    assert r.status is WorkerStatus.FAILED and r.error_code == "extraction_failed" and ctx.failed_docs == 1


async def test_intake_demographics_never_reach_facts(tmp_path):  # guards: identifiers going to the model
    ing, store = make_ingestor(tmp_path)
    await ing.ingest(PATIENT, 2, DocType.INTAKE_FORM, fx("intake_form.pdf"))
    ctx = RunContext(PATIENT, "what should I pay attention to")
    await IntakeExtractorWorker(store, ctx).run()
    assert ctx.facts and {f.label for f in ctx.facts} <= {"chief_concern", "current_medications", "allergies",
                                                          "family_history"}


async def test_retriever_ok_and_empty(tmp_path):  # guards: empty evidence reported as ok
    retr = build_retriever(Settings(data_dir=str(tmp_path)), offline=True)
    ctx = RunContext(PATIENT, "How often should A1c be tested in diabetes?")
    r = await EvidenceRetrieverWorker(retr, ctx).run()
    assert r.status is WorkerStatus.OK and r.refs and ctx.snippets
    off = RunContext(PATIENT, "best pizza topping in chicago")
    r2 = await EvidenceRetrieverWorker(retr, off).run()
    assert r2.status is WorkerStatus.EMPTY and r2.error_code == "no_evidence" and not off.snippets


def test_wants_evidence_routing_input():  # guards: needless retrieval, or none when guidance is asked
    assert wants_evidence(RunContext(PATIENT, "What is the guideline for A1c frequency?"), GraphState())
    assert not wants_evidence(RunContext(PATIENT, "list the allergies"), GraphState())


async def test_question_about_an_unstored_report_kind_reads_no_facts(tmp_path):  # guards: lab/intake facts standing in for a genetic report
    _, store = await _ingest(tmp_path)
    ctx = RunContext(PATIENT, "What did the genetic testing report show?")
    r = await IntakeExtractorWorker(store, ctx).run()
    assert r.status is WorkerStatus.EMPTY and r.error_code == "document_kind_not_stored"
    assert not ctx.facts and ctx.missing_kind == "genetic"


async def test_unstored_kind_alongside_a_stored_topic_still_reads_documents(tmp_path):  # guards: over-blocking mixed questions
    _, store = await _ingest(tmp_path)
    ctx = RunContext(PATIENT, "Compare the genetic report with the latest lab results")
    r = await IntakeExtractorWorker(store, ctx).run()
    assert r.status is WorkerStatus.OK and ctx.facts and ctx.missing_kind == "genetic"


async def test_ordinary_questions_have_no_missing_kind(tmp_path):  # guards: false positives on normal chart questions
    _, store = await _ingest(tmp_path)
    ctx = RunContext(PATIENT, "What changed in the latest labs?")
    await IntakeExtractorWorker(store, ctx).run()
    assert ctx.missing_kind is None and ctx.facts
