"""Agent-side derived store: data_dir/w2.db. Derived and rebuildable; OpenEMR stays authoritative.

Every query is scoped by patient UUID, so a document id from another patient finds nothing. A document row and all
of its fact rows are written in one transaction, so a failed extraction never leaves partial facts.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from contracts.w2 import (
    DocStatus,
    DocType,
    DocumentRecord,
    ErrorCode,
    IntakeFormExtraction,
    LabPdfExtraction,
    RenderMode,
)

from . import EXTRACTOR_VERSION

SCHEMA = """
CREATE TABLE IF NOT EXISTS w2_documents (
  document_id INTEGER NOT NULL, patient_uuid TEXT NOT NULL, doc_type TEXT NOT NULL, sha256 TEXT NOT NULL,
  status TEXT NOT NULL, error_code TEXT, render_mode TEXT, renderer TEXT, model TEXT,
  extractor_version TEXT NOT NULL, created_at TEXT NOT NULL, collection_date TEXT,
  PRIMARY KEY (patient_uuid, document_id), UNIQUE (patient_uuid, sha256)
);
CREATE TABLE IF NOT EXISTS w2_facts (
  fact_id INTEGER PRIMARY KEY AUTOINCREMENT, patient_uuid TEXT NOT NULL, document_id INTEGER NOT NULL,
  kind TEXT NOT NULL, ord INTEGER NOT NULL, payload TEXT NOT NULL, support TEXT NOT NULL, record_uuid TEXT
);
CREATE INDEX IF NOT EXISTS w2_facts_doc ON w2_facts (patient_uuid, document_id);
CREATE TABLE IF NOT EXISTS w2_citations (
  fact_id INTEGER NOT NULL, patient_uuid TEXT NOT NULL, source_id TEXT NOT NULL, page TEXT NOT NULL,
  quote TEXT NOT NULL, bbox TEXT
);
"""

LAB_KIND = "lab_row"
LIST_KINDS = ("current_medications", "allergies", "family_history")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class W2Store:
    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        with self._conn() as c:
            c.executescript(SCHEMA)

    def ping(self) -> None:
        """Raises if the database cannot be opened or queried. Used by /ready."""
        with self._conn() as c:
            c.execute("SELECT 1").fetchone()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self._path, timeout=10)
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def find_by_hash(self, patient_uuid: str, sha256: str) -> DocumentRecord | None:
        with self._conn() as c:
            row = c.execute("SELECT document_id FROM w2_documents WHERE patient_uuid=? AND sha256=?",
                            (patient_uuid, sha256)).fetchone()
        return self.get(patient_uuid, row[0]) if row else None

    def rebind(self, patient_uuid: str, old_id: int, new_id: int) -> DocumentRecord | None:
        """Move one patient's extracted document to the id OpenEMR assigned this upload. Facts and citations move with
        it; the hash stays, so a later upload of the same bytes still finds this extraction. No model call."""
        if old_id == new_id:
            return self.get(patient_uuid, old_id)
        with self._lock, self._conn() as c:
            owner = c.execute("SELECT sha256 FROM w2_documents WHERE patient_uuid=? AND document_id=?",
                              (patient_uuid, old_id)).fetchone()
            if owner is None:
                return None
            if c.execute("SELECT 1 FROM w2_documents WHERE patient_uuid=? AND document_id=?",
                         (patient_uuid, new_id)).fetchone():
                return None  # the caller asked for an id this patient already has; do not merge two documents
            c.execute("UPDATE w2_documents SET document_id=? WHERE patient_uuid=? AND document_id=?",
                      (new_id, patient_uuid, old_id))
            c.execute("UPDATE w2_facts SET document_id=? WHERE patient_uuid=? AND document_id=?",
                      (new_id, patient_uuid, old_id))
            c.execute("UPDATE w2_citations SET source_id=? WHERE patient_uuid=? AND source_id=?",
                      (f"Document/{new_id}", patient_uuid, f"Document/{old_id}"))
            for fact_id, payload in c.execute("SELECT fact_id, payload FROM w2_facts WHERE patient_uuid=? AND document_id=?",
                                              (patient_uuid, new_id)):
                c.execute("UPDATE w2_facts SET payload=? WHERE fact_id=?",
                          (_retarget_payload(payload, old_id, new_id), fact_id))
        return self.get(patient_uuid, new_id)

    def list_document_ids(self, patient_uuid: str, limit: int = 5) -> list[int]:
        """Newest documents first, for one patient only. Read-only; used by the intake-extractor worker."""
        with self._conn() as c:
            rows = c.execute("SELECT document_id FROM w2_documents WHERE patient_uuid=? "
                             "ORDER BY created_at DESC, document_id DESC LIMIT ?", (patient_uuid, limit)).fetchall()
        return [r[0] for r in rows]

    def get(self, patient_uuid: str, document_id: int) -> DocumentRecord | None:
        with self._conn() as c:
            d = c.execute(
                "SELECT doc_type,sha256,status,error_code,render_mode,renderer,model,extractor_version,created_at,"
                "collection_date FROM w2_documents WHERE patient_uuid=? AND document_id=?",
                (patient_uuid, document_id)).fetchone()
            if d is None:
                return None
            facts = c.execute(
                "SELECT kind,payload,record_uuid FROM w2_facts WHERE patient_uuid=? AND document_id=? ORDER BY ord",
                (patient_uuid, document_id)).fetchall()
        doc_type = DocType(d[0])
        links = {str(i): r for i, (_k, _p, r) in enumerate(facts) if r}
        lab = intake = None
        if d[2] == DocStatus.EXTRACTED.value:
            if doc_type is DocType.LAB_PDF:
                lab = LabPdfExtraction(document_id=document_id, collection_date=d[9] or None,
                                       rows=[json.loads(p) for k, p, _r in facts if k == LAB_KIND])
            else:
                intake = IntakeFormExtraction(document_id=document_id, **_intake_kwargs(facts))
        return DocumentRecord(
            document_id=document_id, patient_uuid=patient_uuid, doc_type=doc_type, sha256=d[1],
            status=DocStatus(d[2]), error_code=ErrorCode(d[3]) if d[3] else None,
            render_mode=RenderMode(d[4]) if d[4] else None, renderer=d[5], model=d[6], extractor_version=d[7],
            created_at=d[8], lab=lab, intake=intake, links=links)

    def save_failed(self, patient_uuid: str, document_id: int, doc_type: DocType, sha256: str, code: ErrorCode,
                    render_mode: RenderMode | None = None, renderer: str | None = None,
                    model: str | None = None) -> DocumentRecord:
        """Keep the document, record why extraction failed, store no facts."""
        with self._lock, self._conn() as c:
            self._clear(c, patient_uuid, document_id)
            c.execute(
                "INSERT OR REPLACE INTO w2_documents (document_id,patient_uuid,doc_type,sha256,status,error_code,"
                "render_mode,renderer,model,extractor_version,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (document_id, patient_uuid, doc_type.value, sha256, DocStatus.EXTRACTION_FAILED.value, code.value,
                 render_mode.value if render_mode else None, renderer, model, EXTRACTOR_VERSION, _now()))
        rec = self.get(patient_uuid, document_id)
        if rec is None:
            raise RuntimeError("document row missing after save")
        return rec

    def save_extracted(self, patient_uuid: str, document_id: int, doc_type: DocType, sha256: str,
                       facts: list[tuple[str, dict, str, dict | None]], collection_date: str | None,
                       render_mode: RenderMode, renderer: str | None, model: str) -> DocumentRecord:
        """One transaction: the document row and every fact row (kind, payload, support, citation) or nothing."""
        with self._lock, self._conn() as c:
            self._clear(c, patient_uuid, document_id)
            c.execute(
                "INSERT OR REPLACE INTO w2_documents (document_id,patient_uuid,doc_type,sha256,status,error_code,"
                "render_mode,renderer,model,extractor_version,created_at,collection_date) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (document_id, patient_uuid, doc_type.value, sha256, DocStatus.EXTRACTED.value, None,
                 render_mode.value, renderer, model, EXTRACTOR_VERSION, _now(), collection_date))
            for ordinal, (kind, payload, support, cite) in enumerate(facts):
                cur = c.execute(
                    "INSERT INTO w2_facts (patient_uuid,document_id,kind,ord,payload,support) VALUES (?,?,?,?,?,?)",
                    (patient_uuid, document_id, kind, ordinal, json.dumps(payload), support))
                if cite:
                    c.execute("INSERT INTO w2_citations (fact_id,patient_uuid,source_id,page,quote,bbox) "
                              "VALUES (?,?,?,?,?,?)",
                              (cur.lastrowid, patient_uuid, cite["source_id"], cite["page_or_section"],
                               cite["quote_or_value"], json.dumps(cite.get("bbox"))))
        rec = self.get(patient_uuid, document_id)
        if rec is None:
            raise RuntimeError("document row missing after save")
        return rec

    def save_links(self, patient_uuid: str, document_id: int, links: dict[int, str]) -> int:
        """Record the OpenEMR record uuid written for each lab row (by row index). Returns rows updated."""
        n = 0
        with self._lock, self._conn() as c:
            for ordinal, record_uuid in links.items():
                cur = c.execute(
                    "UPDATE w2_facts SET record_uuid=? WHERE patient_uuid=? AND document_id=? AND ord=? AND kind=? "
                    "AND support='supported' AND record_uuid IS NULL",
                    (record_uuid, patient_uuid, document_id, ordinal, LAB_KIND))
                n += cur.rowcount
        return n

    @staticmethod
    def _clear(c: sqlite3.Connection, patient_uuid: str, document_id: int) -> None:
        c.execute("DELETE FROM w2_citations WHERE patient_uuid=? AND fact_id IN "
                  "(SELECT fact_id FROM w2_facts WHERE patient_uuid=? AND document_id=?)",
                  (patient_uuid, patient_uuid, document_id))
        c.execute("DELETE FROM w2_facts WHERE patient_uuid=? AND document_id=?", (patient_uuid, document_id))


def _retarget_payload(payload: str, old_id: int, new_id: int) -> str:
    """Citation source ids are stored inside the fact payload. Point them at the document id this upload owns."""
    old, new = f"Document/{old_id}", f"Document/{new_id}"
    data = json.loads(payload)

    def walk(node):
        if isinstance(node, dict):
            if node.get("source_id") == old:
                node["source_id"] = new
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return json.dumps(data)


def _intake_kwargs(facts: list[tuple[str, str, str | None]]) -> dict:
    out: dict = {"demographics": {}, "current_medications": [], "allergies": [], "family_history": []}
    for kind, payload, _r in facts:
        value = json.loads(payload)
        if kind in LIST_KINDS:
            out[kind].append(value)
        elif kind == "chief_concern":
            out[kind] = value
        else:
            out["demographics"][kind] = value
    return out
