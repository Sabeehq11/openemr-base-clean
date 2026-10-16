"""The backup drill must only ever write inside its own temp directory, and leave a live source byte-identical."""
import importlib.util
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("w2_drill", Path(__file__).resolve().parents[2] / "scripts" / "w2" / "backup_drill.py")
d = importlib.util.module_from_spec(SPEC)
sys.modules["w2_drill"] = d
SPEC.loader.exec_module(d)


def test_paths_outside_the_temp_root_are_refused(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    assert d.inside(root, root / "a" / "w2.db")
    for bad in (tmp_path / "other.db", root, root / ".." / "x.db", Path("/etc/hosts")):
        with pytest.raises(d.UnsafePath):
            d.inside(root, bad)


def test_the_source_and_the_data_dir_are_refused_even_if_inside_the_root(tmp_path):
    root = tmp_path / "root"
    (root / "data").mkdir(parents=True)
    src = root / "data" / "w2.db"
    with pytest.raises(d.UnsafePath):
        d.inside(root, src, forbidden=(src,))
    with pytest.raises(d.UnsafePath):
        d.inside(root, src, forbidden=(root / "data",))


def test_symlink_escape_is_refused(tmp_path):
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "link").symlink_to(outside)
    with pytest.raises(d.UnsafePath):
        d.inside(root, root / "link" / "w2.db")


def test_a_populated_live_source_is_byte_identical_after_the_drill(tmp_path):
    live = tmp_path / "live" / "w2.db"
    live.parent.mkdir()
    d.populate(live, 4)
    before = d.sha256_file(live)
    rep = d.drill(live, docs=4, data_dir=live.parent, run_agent=False, run_gate=False)
    assert d.sha256_file(live) == before and rep["live_source_unchanged"] is True
    assert rep["source_rows"]["w2_documents"] == 4 and "seeded_in_copy" not in rep  # copied, not reseeded
    assert all(rep["restore_verified"].values()) and rep["temp_dir_removed"]
    assert rep["after_disaster"] == {"deleted": "missing", "corrupted": "failed:DatabaseError"}
    assert sorted(p.name for p in live.parent.iterdir()) == ["w2.db"]  # nothing else was written beside it


def test_an_absent_source_is_never_created(tmp_path):
    live = tmp_path / "nope" / "w2.db"
    rep = d.drill(live, docs=2, data_dir=live.parent, run_agent=False, run_gate=False)
    assert not live.exists() and not live.parent.exists() and rep["seeded_in_copy"] == 2
