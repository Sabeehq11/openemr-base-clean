"""Conversation state: bound to one patient and one user, plus a short-lived snapshot of fetched tool results.

The snapshot only saves FHIR round trips. It never replaces authorization: ToolRunner re-checks the decision before
it will hand back a cached envelope.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from contracts import ToolEnvelope, ToolName

SNAPSHOT_TTL_S = 300
MAX_CONVERSATIONS = 500
CONVERSATION_IDLE_S = 3600


class Snapshot:
    def __init__(self, ttl_s: float = SNAPSHOT_TTL_S, clock=time.time) -> None:
        self._ttl, self._clock = ttl_s, clock
        self._items: dict[tuple, tuple[float, ToolEnvelope]] = {}

    @staticmethod
    def key(tool: ToolName, args: dict) -> tuple:
        return (tool.value, tuple(sorted((k, str(v)) for k, v in args.items())))

    def get(self, tool: ToolName, args: dict) -> ToolEnvelope | None:
        hit = self._items.get(self.key(tool, args))
        if hit is None or self._clock() - hit[0] > self._ttl:
            return None
        return hit[1]

    def put(self, tool: ToolName, args: dict, env: ToolEnvelope) -> None:
        self._items[self.key(tool, args)] = (self._clock(), env)

    def fresh(self) -> list[ToolEnvelope]:
        now = self._clock()
        return [e for t, e in self._items.values() if now - t <= self._ttl]


@dataclass
class Conversation:
    id: str
    user_uuid: str
    patient_uuid: str
    snapshot: Snapshot
    messages: list[dict] = field(default_factory=list)
    touched: float = 0.0


class ConversationStore:
    """In-memory. A conversation is only returned to the same user for the same patient; anything else is new."""

    def __init__(self, clock=time.time) -> None:
        self._clock, self._items = clock, {}

    def open(self, conversation_id: str | None, user_uuid: str, patient_uuid: str) -> Conversation:
        now = self._clock()
        self._items = {k: c for k, c in self._items.items() if now - c.touched < CONVERSATION_IDLE_S}
        c = self._items.get(conversation_id or "")
        if c is not None and c.user_uuid == user_uuid and c.patient_uuid == patient_uuid:
            c.touched = now
            return c
        if len(self._items) >= MAX_CONVERSATIONS:
            oldest = min(self._items.values(), key=lambda x: x.touched)
            del self._items[oldest.id]
        c = Conversation(id=str(uuid.uuid4()), user_uuid=user_uuid, patient_uuid=patient_uuid,
                         snapshot=Snapshot(clock=self._clock), touched=now)
        self._items[c.id] = c
        return c
