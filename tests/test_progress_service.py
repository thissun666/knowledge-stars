# -*- coding: utf-8 -*-
"""progress_service 测试: 参数化读写/WAL/快照回滚/持久化, 全部临时库。"""
import pytest

from backend.progress_service import ProgressService


def test_set_get_and_validation(progress):
    rec = progress.set_status("marble:mt_A", "mastered")
    assert rec["status"] == "mastered"
    assert progress.get_all() == {"marble:mt_A": "mastered"}
    with pytest.raises(ValueError):
        progress.set_status("marble:mt_A", "genius")


def test_upsert_overwrites(progress):
    progress.set_status("marble:mt_A", "learning")
    progress.set_status("marble:mt_A", "mastered")
    assert progress.get_all() == {"marble:mt_A": "mastered"}


def test_wal_mode(progress):
    mode = progress._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert str(mode).lower() == "wal"


def test_counts(progress):
    progress.set_status("a", "mastered")
    progress.set_status("b", "learning")
    c = progress.counts()
    assert c["total"] == 2
    assert c["by_status"]["mastered"] == 1
    assert c["by_status"]["learning"] == 1
    assert c["by_status"]["locked"] == 0


def test_snapshot_and_restore(progress):
    progress.set_status("a", "mastered")
    sid = progress.snapshot("v1")
    progress.set_status("a", "locked")
    progress.set_status("b", "mastered")
    n = progress.restore(sid)
    assert n == 1
    assert progress.get_all() == {"a": "mastered"}


def test_restore_unknown_snapshot(progress):
    with pytest.raises(ValueError):
        progress.restore(999)


def test_persistence_reopen(tmp_path):
    db = str(tmp_path / "p.db")
    p1 = ProgressService(db)
    p1.set_status("k", "mastered")
    p1.close()
    p2 = ProgressService(db)
    assert p2.get_all() == {"k": "mastered"}
    p2.close()
