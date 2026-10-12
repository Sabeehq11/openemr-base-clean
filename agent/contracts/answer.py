"""What the model must return: a list of claims, each tied to the records that support it."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ClaimKind(str, Enum):
    FACT = "fact"
    ABSENCE = "absence"
    FLAG = "flag"


class Claim(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    text: str = Field(min_length=1, max_length=600)
    source_ids: list[str] = Field(default_factory=list, max_length=20)
    kind: ClaimKind = ClaimKind.FACT


class AnswerDraft(BaseModel):
    """Unverified model output. Never shown to a user; only the verifier's result is."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    claims: list[Claim] = Field(max_length=40)


class VerifiedClaim(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    text: str
    source_ids: list[str]
    kind: ClaimKind
    rule_based: bool = False  # True when produced by the deterministic rules layer, not the model


class VerifiedAnswer(BaseModel):
    """The only answer shape a user ever sees."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    claims: list[VerifiedClaim]
    dropped_count: int = 0
    notes: list[str] = Field(default_factory=list)
