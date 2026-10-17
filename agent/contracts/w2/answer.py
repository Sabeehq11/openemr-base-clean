"""Week 2 answer contracts. The chart group is the unchanged Week 1 `VerifiedAnswer`; Week 2 adds two groups beside it."""
from __future__ import annotations

from typing import Annotated

from pydantic import Field, model_validator

from contracts import VerifiedAnswer

from .common import Citation, SourceType, W2Model


class W2DraftClaim(W2Model):
    """Unverified model output for Stage B. The model names source ids; the server turns them into citations."""

    text: Annotated[str, Field(min_length=1, max_length=600)]
    source_ids: Annotated[list[str], Field(max_length=10)] = []


class W2Draft(W2Model):
    claims: Annotated[list[W2DraftClaim], Field(max_length=30)]


class W2Claim(W2Model):
    text: Annotated[str, Field(min_length=1, max_length=600)]
    citations: Annotated[list[Citation], Field(min_length=1, max_length=10)]


class W2VerifiedAnswer(W2Model):
    """The only Week 2 answer shape a user sees. `chart` is Stage A's result, carried through as is."""

    chart: VerifiedAnswer
    document_claims: list[W2Claim] = []
    guideline_claims: list[W2Claim] = []
    dropped_count: int = 0
    rejections: dict[str, int] = {}  # reason code -> count; never claim text
    notes: list[str] = []
    unsynthesised: bool = False  # True when the model was unavailable and facts are shown as recorded

    @model_validator(mode="after")
    def _groups_match_source_type(self) -> "W2VerifiedAnswer":
        for group, kind in ((self.document_claims, SourceType.EXTRACTED), (self.guideline_claims, SourceType.GUIDELINE)):
            if any(c.source_type is not kind for claim in group for c in claim.citations):
                raise ValueError("a claim group may only hold citations of its own source type")
        return self
