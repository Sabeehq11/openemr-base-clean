"""Stored-fact view for the answer path: which extracted facts the model may see, and the id each one is cited by.

Only `supported` facts with an extracted citation are shown to the model. `unsupported` ones are counted and stay
visible elsewhere, but are never used as the basis for an answer. Demographics never reach the model.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from contracts.w2 import Citation, DocumentRecord, IntakeFormExtraction, RetrievedSnippet, Support

INTAKE_LISTS = ("current_medications", "allergies", "family_history")
DEMOGRAPHIC_KEYS = ("first_name", "last_name", "date_of_birth", "sex", "phone", "email", "address")


@dataclass(frozen=True)
class DocFact:
    """One supported fact. `fact_id` is what the model and the verifier cite: doc:{document_id}#{ord}."""

    fact_id: str
    document_id: int
    label: str
    text: str
    citation: Citation


@dataclass
class RunContext:
    """Per-request scratch space between the workers and synthesis. Never logged."""

    patient_uuid: str
    question: str
    facts: list[DocFact] = field(default_factory=list)
    unsupported: int = 0
    failed_docs: int = 0
    missing_kind: str | None = None  # a kind of document the question names that this system never stores
    snippets: list[RetrievedSnippet] = field(default_factory=list)
    rerank_skipped: bool = False

    def test_names(self) -> list[str]:
        return [f.label.removeprefix("lab:") for f in self.facts if f.label.startswith("lab:")][:10]


def _lab_text(row) -> str:
    text = f"{row.test_name}: {row.value}" + (f" {row.unit}" if row.unit else "")
    if row.reference_range:
        text += f", reference range {row.reference_range}"
    if row.abnormal_flag.value != "unknown":
        text += f", flag {row.abnormal_flag.value}"
    return text


def _intake_fields(e: IntakeFormExtraction) -> list[tuple[str, object]]:
    """Non-null fields in the order the store wrote them, so the list index equals the stored `ord`."""
    out: list[tuple[str, object]] = []
    for k in DEMOGRAPHIC_KEYS:
        f = getattr(e.demographics, k)
        if f is not None:
            out.append(("demographics", f))
    if e.chief_concern is not None:
        out.append(("chief_concern", e.chief_concern))
    for k in INTAKE_LISTS:
        out.extend((k, f) for f in getattr(e, k))
    return out


def facts_from_record(rec: DocumentRecord) -> tuple[list[DocFact], int]:
    """Supported facts safe for the model, plus a count of unsupported ones."""
    facts: list[DocFact] = []
    unsupported = 0
    if rec.lab is not None:
        for i, row in enumerate(rec.lab.rows):
            if row.support is not Support.SUPPORTED or row.citation is None:
                unsupported += 1
                continue
            facts.append(DocFact(f"doc:{rec.document_id}#{i}", rec.document_id, f"lab:{row.test_name}",
                                 _lab_text(row), row.citation))
    elif rec.intake is not None:
        for i, (kind, f) in enumerate(_intake_fields(rec.intake)):
            if f.support is not Support.SUPPORTED or f.citation is None:
                unsupported += 1
                continue
            if kind == "demographics":
                continue
            facts.append(DocFact(f"doc:{rec.document_id}#{i}", rec.document_id, kind, f.value, f.citation))
    return facts, unsupported
