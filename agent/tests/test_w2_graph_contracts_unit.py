"""Supervisor-worker and answer contracts: silent shape changes and wrong-group citations must fail loudly."""
import pytest
from pydantic import ValidationError

from contracts import VerifiedAnswer, VerifiedClaim
from contracts.w2 import (Citation, GraphState, Handoff, HandoffReason, Node, SourceType, W2Claim, W2VerifiedAnswer,
                          WorkerResult, WorkerStatus)


def _cite(kind: SourceType) -> Citation:
    bbox = (0.1, 0.1, 0.2, 0.2) if kind is SourceType.EXTRACTED else None
    return Citation(source_type=kind, source_id="Document/1", page_or_section="1", field_or_chunk_id="f",
                    quote_or_value="q", bbox=bbox)


def test_handoff_rejects_unknown_field():  # guards: free-text or extra data slipping into a handoff
    with pytest.raises(ValidationError):
        Handoff(from_node=Node.SUPERVISOR, to_node=Node.EVIDENCE_RETRIEVER, reason=HandoffReason.EVIDENCE_NEEDED,
                correlation_id="c1", step=1, question="what is the A1c")


def test_handoff_rejects_bad_enum():  # guards: an unlisted node or reason becoming a silent route
    with pytest.raises(ValidationError):
        Handoff(from_node="nowhere", to_node=Node.SUPERVISOR, reason=HandoffReason.READY, correlation_id="c1", step=0)


def test_worker_result_rejects_bad_status_and_extra():  # guards: a worker inventing a status
    with pytest.raises(ValidationError):
        WorkerResult(worker=Node.INTAKE_EXTRACTOR, status="maybe")
    with pytest.raises(ValidationError):
        WorkerResult(worker=Node.INTAKE_EXTRACTOR, status=WorkerStatus.OK, text="raw")


def test_graph_state_is_frozen():  # guards: routing state mutated mid-run
    s = GraphState()
    with pytest.raises(ValidationError):
        s.step = 3


def test_group_rejects_wrong_source_type():  # guards: a guideline citation listed as a patient-document claim
    with pytest.raises(ValidationError):
        W2VerifiedAnswer(chart=VerifiedAnswer(claims=[]),
                         document_claims=[W2Claim(text="x", citations=[_cite(SourceType.GUIDELINE)])])


def test_claim_needs_a_citation():  # guards: an uncited claim reaching the answer shape
    with pytest.raises(ValidationError):
        W2Claim(text="x", citations=[])


def test_chart_group_is_the_same_object():  # guards: the chart group being rebuilt or rewritten
    chart = VerifiedAnswer(claims=[VerifiedClaim(text="A1c 8.1", source_ids=["Observation/1"], kind="fact")])
    ans = W2VerifiedAnswer(chart=chart)
    assert ans.chart is chart
