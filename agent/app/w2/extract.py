"""Raw VLM text -> validated, cited extraction. A failed parse is a failed extraction with zero rows."""
from __future__ import annotations

import re
from datetime import date

from pydantic import ValidationError

from app.rules import recompute_flag
from contracts import LabResult
from contracts.w2 import (
    AbnormalFlag,
    Demographics,
    IntakeField,
    IntakeFormExtraction,
    LabPdfExtraction,
    LabRow,
    RawIntakeOutput,
    RawLabOutput,
    Support,
)
from contracts.w2.extraction import RawIntakeField

from .loinc import supported_loinc
from .render import TextLine
from .support import NATIVE_CONFIDENCE_CAP, ExtractionError, build_citation, is_supported, parse_json

_RANGE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)\s*$")
_NUM = re.compile(r"^\s*-?\d+(?:\.\d+)?\s*$")
_FLAGS = {f.value for f in AbnormalFlag}


def parse_number(text: str) -> float | None:
    return float(text) if _NUM.match(text) else None


def parse_range(text: str | None) -> tuple[float | None, float | None]:
    m = _RANGE.match(text or "")
    return (float(m.group(1)), float(m.group(2))) if m else (None, None)


def recomputed_flag(value: str, unit: str | None, reference_range: str | None, model_flag: str | None) -> AbnormalFlag:
    """Flag from value and range by the Week 1 rules layer. The model's flag is only a fallback for 'critical' text
    and is never trusted over a computable result."""
    low, high = parse_range(reference_range)
    num = parse_number(value)
    lab = LabResult(source_id="x", test="x", loinc=None, value=num, unit=unit, ref_low=low, ref_high=high,
                    flag_reported=None, collected=None)
    calc = recompute_flag(lab)
    if calc is not None:
        return AbnormalFlag(calc)
    return AbnormalFlag.UNKNOWN


def _parse_date(text: str | None) -> date | None:
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None


def extract_lab(raw: str, document_id: int, text_layer: str = "", native: bool = False,
                lines: tuple[TextLine, ...] = ()) -> LabPdfExtraction:
    try:
        out = RawLabOutput.model_validate(parse_json(raw))
    except ValidationError:
        raise ExtractionError("schema_invalid") from None
    rows: list[LabRow] = []
    for i, r in enumerate(out.rows):
        cite = build_citation(document_id, r.evidence, f"rows[{i}]", lines)
        ok = is_supported(r.value, r.evidence, cite, text_layer, native) and parse_number(r.value) is not None
        conf = min(max(r.confidence, 0.0), 1.0)
        if native:
            conf = min(conf, NATIVE_CONFIDENCE_CAP)
        # A code is kept only for a supported row, and only when the printed name and method back it (loinc.py).
        loinc = supported_loinc(r.loinc, r.test_name, r.evidence.quote) if ok else None
        try:
            rows.append(LabRow(
                test_name=r.test_name, value=r.value, unit=r.unit, reference_range=r.reference_range,
                abnormal_flag=recomputed_flag(r.value, r.unit, r.reference_range, r.abnormal_flag),
                loinc=loinc, citation=cite if ok else None, confidence=conf,
                support=Support.SUPPORTED if ok else Support.UNSUPPORTED))
        except ValidationError:
            raise ExtractionError("schema_invalid") from None
    return LabPdfExtraction(document_id=document_id, collection_date=_parse_date(out.collection_date), rows=rows)


def _field(f: RawIntakeField, document_id: int, name: str, text_layer: str, native: bool,
           lines: tuple[TextLine, ...] = ()) -> IntakeField:
    cite = build_citation(document_id, f.evidence, name, lines)
    ok = is_supported(f.value, f.evidence, cite, text_layer, native)
    conf = min(max(f.confidence, 0.0), 1.0)
    return IntakeField(value=f.value, citation=cite if ok else None,
                       confidence=min(conf, NATIVE_CONFIDENCE_CAP) if native else conf,
                       support=Support.SUPPORTED if ok else Support.UNSUPPORTED)


def extract_intake(raw: str, document_id: int, text_layer: str = "", native: bool = False,
                   lines: tuple[TextLine, ...] = ()) -> IntakeFormExtraction:
    try:
        out = RawIntakeOutput.model_validate(parse_json(raw))

        def one(f: RawIntakeField | None, name: str) -> IntakeField | None:
            return _field(f, document_id, name, text_layer, native, lines) if f else None

        def many(items: list[RawIntakeField], name: str) -> list[IntakeField]:
            return [_field(f, document_id, f"{name}[{i}]", text_layer, native, lines) for i, f in enumerate(items)]

        demo = Demographics(**{k: one(getattr(out, k), k) for k in
                               ("first_name", "last_name", "date_of_birth", "sex", "phone", "email", "address")})
        return IntakeFormExtraction(
            document_id=document_id, demographics=demo, chief_concern=one(out.chief_concern, "chief_concern"),
            current_medications=many(out.current_medications, "current_medications"),
            allergies=many(out.allergies, "allergies"), family_history=many(out.family_history, "family_history"))
    except ValidationError:
        raise ExtractionError("schema_invalid") from None
