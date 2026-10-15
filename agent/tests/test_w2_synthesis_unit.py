"""Stage B verifier and synthesis: only cited, in-block, number-faithful, non-advice claims survive."""
import json

from app.config import Settings
from app.llm import LLMError, LLMResponse, StubLLM, Usage
from app.w2.facts import RunContext
from app.w2.rag import build_retriever
from app.w2.synthesis import build_blocks, synthesise
from app.w2.verify import verify_stage_b
from app.w2.workers import EvidenceRetrieverWorker, IntakeExtractorWorker
from contracts.w2 import DocType, SourceType, W2Draft, W2DraftClaim
from tests.w2_helpers import PATIENT, fx, make_ingestor


async def ctx_with_data(tmp_path):
    ing, store = make_ingestor(tmp_path)
    await ing.ingest(PATIENT, 1, DocType.LAB_PDF, fx("lab_report.pdf"))
    ctx = RunContext(PATIENT, "How often should A1c be tested in diabetes?")
    await IntakeExtractorWorker(store, ctx).run()
    await EvidenceRetrieverWorker(build_retriever(Settings(data_dir=str(tmp_path)), offline=True), ctx).run()
    return ctx


def draft(*claims):
    return W2Draft(claims=[W2DraftClaim(text=t, source_ids=list(s)) for t, s in claims])


def reply(*claims):
    body = {"claims": [{"text": t, "source_ids": list(s)} for t, s in claims]}
    return LLMResponse(text=json.dumps(body), usage=Usage(10, 5, 0.001))


async def test_good_claims_land_in_their_own_groups(tmp_path):  # guards: record facts and guidance blended
    c = await ctx_with_data(tmp_path)
    chunk = c.snippets[0].chunk_id
    docs, guides, rej = verify_stage_b(draft(("Hemoglobin A1c is 7.9 %.", ["doc:1#0"]),
                                             ("A1c is checked at least twice a year when at goal.", [chunk])),
                                       c.facts, c.snippets)
    assert len(docs) == 1 and len(guides) == 1 and not rej
    assert docs[0].citations[0].source_type is SourceType.EXTRACTED and docs[0].citations[0].bbox
    assert guides[0].citations[0].source_type is SourceType.GUIDELINE


async def test_uncited_claim_dropped(tmp_path):  # guards: an unsupported statement reaching the user
    c = await ctx_with_data(tmp_path)
    docs, _, rej = verify_stage_b(draft(("The patient is doing well.", [])), c.facts, c.snippets)
    assert not docs and rej == {"uncited": 1}


async def test_chart_or_invented_id_dropped(tmp_path):  # guards: Stage B writing chart claims
    c = await ctx_with_data(tmp_path)
    docs, guides, rej = verify_stage_b(draft(("Metformin 500 mg.", ["MedicationRequest/m1"]),
                                             ("Made up.", ["doc:9#9"])), c.facts, c.snippets)
    assert not docs and not guides and rej == {"unknown_source": 2}


async def test_wrong_block_citation_dropped(tmp_path):  # guards: a guideline claim citing a record fact or the reverse
    c = await ctx_with_data(tmp_path)
    _, _, rej = verify_stage_b(draft(("A1c is 7.9 % and guidance applies.", ["doc:1#0", c.snippets[0].chunk_id])),
                               c.facts, c.snippets)
    assert rej == {"mixed_groups": 1}


async def test_number_mismatch_dropped(tmp_path):  # guards: a value that is not in the cited fact
    c = await ctx_with_data(tmp_path)
    docs, _, rej = verify_stage_b(draft(("Hemoglobin A1c is 9.4 %.", ["doc:1#0"])), c.facts, c.snippets)
    assert not docs and rej == {"number_mismatch": 1}


async def test_advice_phrasing_dropped(tmp_path):  # guards: Week 1 refusals not holding on the new path
    c = await ctx_with_data(tmp_path)
    docs, _, rej = verify_stage_b(draft(("You should start metformin 500 mg daily.", ["doc:1#0"])),
                                  c.facts, c.snippets)
    assert not docs and sum(rej.values()) == 1


async def test_blocks_are_labelled_and_exclude_identifiers(tmp_path):  # guards: unlabelled blocks, identifiers to model
    c = await ctx_with_data(tmp_path)
    text = build_blocks(c.facts, c.snippets)
    assert "[PATIENT DOCUMENT FACTS]" in text and "[GUIDELINE EVIDENCE]" in text and PATIENT not in text


async def test_synthesise_verifies_model_output(tmp_path):  # guards: model output skipping the verifier
    c = await ctx_with_data(tmp_path)
    llm = StubLLM([reply(("Hemoglobin A1c is 7.9 %.", ["doc:1#0"]), ("Glucose is 500 mg/dL.", ["doc:1#2"]))])
    docs, guides, rej, unsynth = await synthesise(llm, c.facts, c.snippets)
    assert len(docs) == 1 and rej == {"number_mismatch": 1} and not unsynth
    assert "[PATIENT DOCUMENT FACTS]" in llm.calls[0][1]["content"]


async def test_model_down_falls_back_to_cited_facts(tmp_path):  # guards: no answer, or an uncited one, on outage
    c = await ctx_with_data(tmp_path)
    docs, guides, _, unsynth = await synthesise(StubLLM([LLMError("down")]), c.facts, c.snippets)
    assert unsynth and len(docs) == len(c.facts) and all(d.citations for d in docs + guides)


async def test_unparseable_twice_falls_back(tmp_path):  # guards: a bad model reply blocking the answer
    c = await ctx_with_data(tmp_path)
    junk = LLMResponse(text="not json", usage=Usage())
    _, _, _, unsynth = await synthesise(StubLLM([junk, junk]), c.facts, c.snippets)
    assert unsynth


async def test_nothing_to_synthesise_makes_no_model_call():  # guards: a model call with no evidence
    llm = StubLLM([])
    assert await synthesise(llm, [], []) == ([], [], {}, False)
    assert llm.calls == []


def _med_fact():
    """An intake medication exactly as stored: value 'Metformin 500 mg twice daily', quote 'Medication: ...'."""
    from app.w2.facts import DocFact
    from contracts.w2 import Citation
    cite = Citation(source_type=SourceType.EXTRACTED, source_id="Document/2005", page_or_section="1",
                    field_or_chunk_id="current_medications", quote_or_value="Medication: Metformin 500 mg twice daily",
                    bbox=(0.1, 0.1, 0.5, 0.2))
    return DocFact("doc:2005#6", 2005, "current_medications", "Metformin 500 mg twice daily", cite)


def test_restating_a_recorded_dose_is_kept_but_added_advice_is_not():
    fact = _med_fact()
    draft = W2Draft(claims=[
        W2DraftClaim(text="The intake form lists Metformin 500 mg twice daily.", source_ids=[fact.fact_id]),
        W2DraftClaim(text="Increase Metformin 500 mg twice daily to a higher dose.", source_ids=[fact.fact_id]),
    ])
    docs, guides, rejected = verify_stage_b(draft, [fact], [])
    assert [c.text for c in docs] == ["The intake form lists Metformin 500 mg twice daily."]
    assert rejected == {"output_rules": 1} and not guides
