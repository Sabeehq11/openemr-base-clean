import sqlite3

import pytest

from app.audit import ChainedAuditSink


def make(tmp_path, key="k" * 32):
    return ChainedAuditSink(str(tmp_path / "a.db"), key, clock=lambda: 1_800_000_000.0)


def fill(sink, n=5):
    for i in range(n):
        sink.record("tool_call", tool="get_labs", patient_uuid="p", user_uuid="u", outcome="ok", records=i)


def test_empty_chain_is_valid(tmp_path):
    assert make(tmp_path).verify().ok


def test_intact_chain_verifies(tmp_path):
    s = make(tmp_path)
    fill(s)
    r = s.verify()
    assert r.ok and r.rows == 5


def test_edited_row_is_detected_at_that_row(tmp_path):
    s = make(tmp_path)
    fill(s)
    s._db.execute("UPDATE audit SET body = replace(body, '\"records\":2', '\"records\":9') WHERE seq = 3")
    s._db.commit()
    r = s.verify()
    assert not r.ok and r.first_bad_seq == 3


def test_deleted_row_is_detected(tmp_path):
    s = make(tmp_path)
    fill(s)
    s._db.execute("DELETE FROM audit WHERE seq = 2")
    s._db.commit()
    r = s.verify()
    assert not r.ok and r.first_bad_seq == 3


def test_reordered_rows_are_detected(tmp_path):
    s = make(tmp_path)
    fill(s)
    s._db.execute("UPDATE audit SET seq = 99 WHERE seq = 2")
    s._db.commit()
    assert not s.verify().ok


def test_rewriting_the_whole_chain_needs_the_key(tmp_path):
    s = make(tmp_path)
    fill(s, 3)
    forged = make(tmp_path, key="x" * 32)  # attacker without the key
    assert not forged.verify().ok


def test_truncating_the_tail_is_visible_via_row_count(tmp_path):
    s = make(tmp_path)
    fill(s, 4)
    s._db.execute("DELETE FROM audit WHERE seq = 4")
    s._db.commit()
    assert s.verify().rows == 3  # the chain is valid but shorter; the dashboard shows the count so shrinkage is visible


def test_only_allowed_identifier_fields_are_stored(tmp_path):
    s = make(tmp_path)
    s.record("tool_call", tool="get_labs", patient_uuid="p", token="Bearer abc.def", diagnosis="secret", value="12.1")
    body = s._db.execute("SELECT body FROM audit").fetchone()[0]
    assert "abc.def" not in body and "secret" not in body and "12.1" not in body
    assert '"tool":"get_labs"' in body


def test_survives_restart_and_continues_the_chain(tmp_path):
    s = make(tmp_path)
    fill(s, 2)
    s2 = make(tmp_path)
    fill(s2, 2)
    assert s2.verify().rows == 4 and s2.verify().ok


def test_empty_key_is_refused(tmp_path):
    with pytest.raises(ValueError):
        ChainedAuditSink(str(tmp_path / "x.db"), "")


def test_sqlite_file_is_the_only_state(tmp_path):
    s = make(tmp_path)
    fill(s, 1)
    assert sqlite3.connect(str(tmp_path / "a.db")).execute("select count(*) from audit").fetchone()[0] == 1
