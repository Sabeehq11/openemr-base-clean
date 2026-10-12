"""Pydantic v2 contracts shared by tools, the agent loop and the verifier.

Every record carries a `source_id` ("Resource/uuid"). The verifier resolves claims against these ids.
All models are frozen: a record the tools returned cannot be mutated before it is verified.
"""
from .records import (  # noqa: F401
    Allergy,
    ChangeItem,
    ChangesSince,
    Encounter,
    LabResult,
    Medication,
    PatientSummary,
    Problem,
    ToolEnvelope,
    ToolError,
    ToolName,
)
from .answer import AnswerDraft, Claim, ClaimKind, VerifiedAnswer, VerifiedClaim  # noqa: E402,F401
