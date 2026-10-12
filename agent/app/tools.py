"""The six read-only tools. Nothing here can write, and nothing runs without a fresh allow decision.

Each call: authorize(token, patient) -> FHIR read with the user's token -> map -> envelope with source_ids.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from typing import Awaitable, Callable

from contracts import (
    ChangeItem,
    ToolEnvelope,
    ToolError,
    ToolName,
)

from . import mappers
from .authz import AuthzDecision, PatientAuthorizer
from .fhir_client import FhirClient
from .gateway import AuditSink
from .logging_setup import correlation_id_var

_UUID_CHARS = set("0123456789abcdef-")

LISTS_NOTE = (
    "Medications come from FHIR MedicationRequest (the prescriptions store). Medications entered only on the "
    "chart's medication list (lists store) are not exposed by FHIR; none exist in the seeded data."
)


class DeniedError(ToolError):
    def __init__(self, decision: AuthzDecision) -> None:
        super().__init__("unauthorized", "You do not have access to this patient.")
        self.decision = decision


def _valid_uuid(value: str) -> bool:
    return 32 <= len(value) <= 36 and set(value.lower()) <= _UUID_CHARS


class ToolRunner:
    def __init__(self, authorizer: PatientAuthorizer, fhir: FhirClient,
                 today: Callable[[], date] = lambda: datetime.now(timezone.utc).date(),
                 audit: AuditSink | None = None) -> None:
        self._authz = authorizer
        self._fhir = fhir
        self._today = today
        self._audit = audit
        self._dispatch: dict[ToolName, Callable[..., Awaitable[ToolEnvelope]]] = {
            ToolName.GET_PATIENT_SUMMARY: self.get_patient_summary,
            ToolName.GET_PROBLEMS: self.get_problems,
            ToolName.GET_MEDICATIONS: self.get_medications,
            ToolName.GET_ALLERGIES: self.get_allergies,
            ToolName.GET_LABS: self.get_labs,
            ToolName.GET_CHANGES_SINCE: self.get_changes_since,
        }

    async def run(self, tool: ToolName, token: str | None, patient_uuid: str, claimed_user: str | None = None,
                  **args: object) -> ToolEnvelope:
        """Single entry point. The decision is made here, on every call, and bound to this patient."""
        decision = await self.check(tool, token, patient_uuid, claimed_user)
        try:
            env = await self._dispatch[tool](token, patient_uuid, **args)
        except ToolError as e:
            self._record("tool_error", tool, patient_uuid, decision.user_uuid, e.code)
            raise
        self._record("tool_call", tool, patient_uuid, decision.user_uuid, "ok", records=len(env.records))
        return env

    async def check(self, tool: ToolName, token: str | None, patient_uuid: str,
                    claimed_user: str | None = None) -> AuthzDecision:
        """The per-call decision. Also used before serving a cached snapshot, so a cache hit is never a bypass."""
        if not _valid_uuid(patient_uuid):
            raise ToolError("invalid_input", "invalid patient identifier")
        decision = await self._authz.authorize(token, patient_uuid, claimed_user)
        if not decision.allowed or decision.patient_uuid != patient_uuid or token is None:
            self._record("tool_denied", tool, patient_uuid, decision.user_uuid, decision.reason.value)
            raise DeniedError(decision)
        return decision

    def _record(self, event: str, tool: ToolName, patient: str, user: str | None, outcome: str, **extra: object) -> None:
        if self._audit is not None:
            self._audit.record(event, tool=tool.value, patient_uuid=patient, user_uuid=user, outcome=outcome,
                               correlation_id=correlation_id_var.get(), **extra)

    def _envelope(self, tool: ToolName, patient: str, records: list, notes: list[str] | None = None) -> ToolEnvelope:
        return ToolEnvelope(
            tool=tool, patient_uuid=patient, retrieved_at=datetime.now(timezone.utc), records=records,
            empty=not records, source_ids=[r.source_id for r in records], notes=notes or [],
        )

    async def get_patient_summary(self, token: str, patient: str) -> ToolEnvelope:
        res = await self._fhir.read("Patient", patient, token)
        return self._envelope(ToolName.GET_PATIENT_SUMMARY, patient, [mappers.patient_summary(res, self._today())])

    async def get_problems(self, token: str, patient: str) -> ToolEnvelope:
        rows = await self._fhir.search("Condition", token, {"patient": patient})
        return self._envelope(ToolName.GET_PROBLEMS, patient, [mappers.problem(r) for r in rows])

    async def get_medications(self, token: str, patient: str) -> ToolEnvelope:
        rows = await self._fhir.search("MedicationRequest", token, {"patient": patient})
        return self._envelope(ToolName.GET_MEDICATIONS, patient, [mappers.medication(r) for r in rows], [LISTS_NOTE])

    async def get_allergies(self, token: str, patient: str) -> ToolEnvelope:
        rows = await self._fhir.search("AllergyIntolerance", token, {"patient": patient})
        return self._envelope(ToolName.GET_ALLERGIES, patient, [mappers.allergy(r) for r in rows])

    async def get_labs(self, token: str, patient: str) -> ToolEnvelope:
        rows = await self._fhir.search("Observation", token, {"patient": patient, "category": "laboratory"})
        labs = sorted((mappers.lab(r) for r in rows), key=lambda x: x.collected or date.min, reverse=True)
        return self._envelope(ToolName.GET_LABS, patient, labs)

    async def get_changes_since(self, token: str, patient: str, since: date | str | None = None) -> ToolEnvelope:
        if isinstance(since, str):
            try:
                since = date.fromisoformat(since)
            except ValueError as e:
                raise ToolError("invalid_input", "since must be an ISO date (YYYY-MM-DD)") from e
        if not isinstance(since, date):
            raise ToolError("invalid_input", "since is required")
        enc, labs, meds, probs = await asyncio.gather(
            self._fhir.search("Encounter", token, {"patient": patient}),
            self._fhir.search("Observation", token, {"patient": patient, "category": "laboratory"}),
            self._fhir.search("MedicationRequest", token, {"patient": patient}),
            self._fhir.search("Condition", token, {"patient": patient}),
        )
        items: list[ChangeItem] = []
        for r in enc:
            e = mappers.encounter(r)
            if e.date and e.date >= since:
                items.append(ChangeItem(source_id=e.source_id, resource_type="Encounter", changed_on=e.date,
                                        summary=f"Visit: {e.reason or e.kind or 'no reason recorded'}"))
        for r in labs:
            lab = mappers.lab(r)
            if lab.collected and lab.collected >= since:
                items.append(ChangeItem(source_id=lab.source_id, resource_type="Observation", changed_on=lab.collected,
                                        summary=f"{lab.test}: {lab.value} {lab.unit or ''}".strip()))
        for r in meds:
            m = mappers.medication(r)
            if m.authored_on and m.authored_on >= since:
                items.append(ChangeItem(source_id=m.source_id, resource_type="MedicationRequest",
                                        changed_on=m.authored_on, summary=f"Medication on record: {m.name} {m.dose_text or ''}".strip()))
        for r in probs:
            p = mappers.problem(r)
            if p.onset and p.onset >= since:
                items.append(ChangeItem(source_id=p.source_id, resource_type="Condition", changed_on=p.onset,
                                        summary=f"Problem added: {p.title}"))
        items.sort(key=lambda i: i.changed_on or date.min, reverse=True)
        return self._envelope(ToolName.GET_CHANGES_SINCE, patient, items,
                              [f"Window starts {since.isoformat()}; dates are clinical dates, not edit times."])
