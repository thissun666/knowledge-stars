# -*- coding: utf-8 -*-
r"""Sprint11: 推断边装载契约 - 小图fixture全路径 + 全图冒烟"""
import json

from backend import data_loader
from backend.data_loader import Graph, Topic, _load_inferred_links


def _mk_node(key):
    return Topic(key=key, id=key, source="t", book="", name=key,
                 type="kp", subject="数学", domain="", grade=7)


def _mk_graph():
    g = Graph()
    for k in ("a", "b", "c"):
        g.add_node(_mk_node(k))
    return g


def _write(tmp_path, payload):
    p = tmp_path / "inferred_links.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(p)


def test_basic_load():
    g = _mk_graph()
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        p = str(Path(td) / "inferred_links.json")
        Path(p).write_text(json.dumps({"edges": [
            {"from": "a", "to": "b", "via": "exact", "prereq_name": "x"},
            {"from": "b", "to": "c", "via": "contains", "prereq_name": "y"},
        ]}, ensure_ascii=False), encoding="utf-8")
        n = _load_inferred_links(g, p)
    assert n == 2
    assert g.adj_out["a"] == ["b"]
    assert g.adj_in["c"] == ["b"]
    assert g.edges[0].strength == "inferred"
    assert g.edges[0].reason.startswith("ai-prereq:")


def test_dedup_against_existing():
    g = _mk_graph()
    assert g.add_edge(type("E", (), {"prereq_key": "a", "topic_key": "b",
                                     "strength": "hard", "reason": ""})())
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        p = str(Path(td) / "f.json")
        Path(p).write_text(json.dumps({"edges": [
            {"from": "a", "to": "b", "via": "exact"},
            {"from": "b", "to": "c", "via": "exact"},
        ]}), encoding="utf-8")
        n = _load_inferred_links(g, p)
    assert n == 1
    assert g.dropped.get("duplicate") == 1


def test_self_loop_and_dangling_rejected():
    g = _mk_graph()
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        p = str(Path(td) / "f.json")
        Path(p).write_text(json.dumps({"edges": [
            {"from": "a", "to": "a", "via": "exact"},
            {"from": "a", "to": "ghost", "via": "exact"},
        ]}), encoding="utf-8")
        n = _load_inferred_links(g, p)
    assert n == 0
    assert g.dropped.get("self_loop") == 1
    assert g.dropped.get("dangling") == 1


def test_missing_file_tolerated(tmp_path):
    g = _mk_graph()
    assert _load_inferred_links(g, str(tmp_path / "nope.json")) == 0


def test_corrupt_file_tolerated(tmp_path):
    g = _mk_graph()
    p = tmp_path / "bad.json"
    p.write_text("{not json!!!", encoding="utf-8")
    assert _load_inferred_links(g, str(p)) == 0


def test_full_graph_has_inferred():
    g = data_loader.get_graph()
    n = sum(1 for e in g.edges if e.strength == "inferred")
    assert n >= 4000, "全图推断边应 >=4000, 实际 " + str(n)
