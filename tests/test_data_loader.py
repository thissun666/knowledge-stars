# -*- coding: utf-8 -*-
"""data_loader 测试: 合成数据(mini 双源 + mini k12 + mini kkg) + 真实数据对账。"""
import os
import json
import pytest
import paths
from backend import data_loader

HAS_MARBLE = os.path.isdir(paths.MARBLE_DATA_DIR)
HAS_PEP = os.path.isdir(paths.PEP_DATA_DIR)
HAS_K12 = os.path.isdir(os.path.join(paths.project_root(), "k12-data", "split"))
HAS_KKG = bool(data_loader._kkg_math_path())


def build(mini_sources):
    return data_loader.build_graph_from_dirs(
        mini_sources, subjects=["Mathematics"],
        load_marble=True, load_pep=True, load_k12=False)


def _kp(kid, name, band="小学", subject="数学"):
    return {"id": kid, "canonical_name": name, "subject": subject,
            "grade_band": band, "curriculum_system": "x", "confidence": 0.9,
            "aliases": "[]", "annotations": "{}", "summary": name + "的摘要",
            "title": name}


def _rel(f, t, rt="prerequisite"):
    return {"id": "r_" + f + t, "from_kp_id": f, "to_kp_id": t,
            "relation_type": rt, "source_evidence": "", "from_name": f,
            "to_name": t}


def _write_k12(root, band, subject, kps, rels):
    d = root / "k12-data" / "split" / band
    d.mkdir(parents=True, exist_ok=True)
    (d / (subject + ".json")).write_text(
        json.dumps({"grade_band": band, "subject": subject,
                    "knowledge_points": kps, "relations": rels},
                   ensure_ascii=False), encoding="utf-8")


def _write_kkg(root, nodes, edges):
    d = root / "k12kgraph-hf" / "K12-KGraph" / "subject_specific_KG"
    d.mkdir(parents=True, exist_ok=True)
    (d / "math.json").write_text(
        json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False),
        encoding="utf-8")


def test_mini_nodes_and_collision(mini_sources):
    g = build(mini_sources)
    assert set(g.nodes) == {
        "marble:mt_A", "marble:mt_B",
        "pep:7-up:mt_ch1_001", "pep:7-down:mt_ch5_001",
        "pep:8-down:mt_ch21_001", "pep:8-down:mt_ch21_002",
        "pep:9-up:mt_ch21_001", "pep:9-up:mt_ch22_001",
    }
    assert g.nodes["pep:8-down:mt_ch21_001"].name == "四边形与多边形的概念"
    assert g.nodes["pep:9-up:mt_ch21_001"].name == "一元二次方程的概念"


def test_mini_edges_same_book_priority(mini_sources):
    g = build(mini_sources)
    keys = [(e.prereq_key, e.topic_key) for e in g.edges]
    assert ("marble:mt_A", "marble:mt_B") in keys
    assert ("pep:7-up:mt_ch1_001", "pep:7-down:mt_ch5_001") in keys
    assert ("pep:8-down:mt_ch21_001", "pep:8-down:mt_ch21_002") in keys
    assert ("pep:9-up:mt_ch21_001", "pep:9-up:mt_ch22_001") in keys
    assert ("pep:8-down:mt_ch21_001", "pep:9-up:mt_ch22_001") not in keys
    assert len(g.edges) == 4


def test_mini_dropped_counters(mini_sources):
    g = build(mini_sources)
    assert "filtered_subject" not in g.dropped
    assert g.dropped.get("ambiguous") == 1
    assert g.dropped.get("missing") == 2


def test_build_graph_direct_filters_and_counts():
    raw = [{"id": "mt_X", "subject": "Science", "name": "X"},
           {"id": "mt_Y", "subject": "Mathematics", "name": "Y"}]
    g = data_loader.build_graph(raw, [], [], subjects=["Mathematics"])
    assert set(g.nodes) == {"marble:mt_Y"}
    assert g.dropped.get("filtered_subject") == 1


def test_mini_grade(mini_sources):
    g = build(mini_sources)
    assert g.nodes["pep:7-up:mt_ch1_001"].grade == 7
    assert g.nodes["marble:mt_A"].grade == 3


def test_no_self_loop(mini_sources):
    g = build(mini_sources)
    added = g.add_edge(data_loader.Edge(
        prereq_key="marble:mt_A", topic_key="marble:mt_A"))
    assert added is False
    assert g.dropped.get("self_loop") == 1


def test_find_cycles_clean(mini_sources):
    assert data_loader.find_cycles(build(mini_sources)) == []


def test_k12_mini_basic(tmp_path):
    _write_k12(tmp_path, "小学", "数学",
               [_kp("k1", "加法"), _kp("k2", "乘法"), _kp("k3", "四则运算")],
               [_rel("k1", "k2"), _rel("k2", "k3"), _rel("k1", "k3", "related")])
    g = data_loader.build_graph_from_dirs(str(tmp_path), load_marble=False,
                                          load_pep=False, load_k12=True)
    assert set(g.nodes) == {"k12:k1", "k12:k2", "k12:k3"}
    keys = [(e.prereq_key, e.topic_key) for e in g.edges]
    assert ("k12:k1", "k12:k2") in keys
    assert ("k12:k2", "k12:k3") in keys
    assert len(g.edges) == 2
    assert g.dropped.get("k12_related") == 1
    assert g.nodes["k12:k1"].grade == 3
    assert g.nodes["k12:k1"].book == "小学"
    assert data_loader.find_cycles(g) == []


def test_k12_subject_excluded(tmp_path):
    _write_k12(tmp_path, "小学", "数学", [_kp("k1", "加法")], [])
    _write_k12(tmp_path, "小学", "思政", [_kp("k9", "道德")], [])
    _write_k12(tmp_path, "初中", "俄语", [_kp("k8", "俄语入门")], [])
    g = data_loader.build_graph_from_dirs(str(tmp_path), load_marble=False,
                                          load_pep=False, load_k12=True)
    assert set(g.nodes) == {"k12:k1"}


def test_k12_bands_filter(tmp_path):
    _write_k12(tmp_path, "小学", "数学", [_kp("k1", "加法")], [])
    _write_k12(tmp_path, "高中", "物理", [_kp("k5", "力学")], [])
    g = data_loader.build_graph_from_dirs(str(tmp_path), load_marble=False,
                                          load_pep=False, load_k12=True,
                                          bands=["高中"])
    assert set(g.nodes) == {"k12:k5"}
    assert g.nodes["k12:k5"].grade == 10


def test_k12_direction_flip(tmp_path):
    _write_k12(tmp_path, "小学", "数学",
               [_kp("kA", "加法"), _kp("kB", "乘法"), _kp("kC", "减法")],
               [_rel("kB", "kA"), _rel("kC", "kA")])
    g = data_loader.build_graph_from_dirs(str(tmp_path), load_marble=False,
                                          load_pep=False, load_k12=True)
    keys = [(e.prereq_key, e.topic_key) for e in g.edges]
    assert ("k12:kA", "k12:kB") in keys
    assert ("k12:kA", "k12:kC") in keys
    assert data_loader.find_cycles(g) == []


def test_k12_dangling(tmp_path):
    _write_k12(tmp_path, "小学", "数学", [_kp("k1", "加法")],
               [_rel("k1", "kX"), _rel("kX", "k1")])
    g = data_loader.build_graph_from_dirs(str(tmp_path), load_marble=False,
                                          load_pep=False, load_k12=True)
    assert set(g.nodes) == {"k12:k1"}
    assert g.dropped.get("k12_dangling") == 2
    assert g.edges == []


def test_kkg_mini(tmp_path):
    nodes = [
        {"id": "math_bx1_rjb_cpt1", "label": "Concept", "name": "集合",
         "properties": {"definition": "集合的定义"}},
        {"id": "math_bx1_rjb_cpt2", "label": "Concept", "name": "子集",
         "properties": {"definition": "子集的定义"}},
        {"id": "math_bx1_rjb_cpt3", "label": "Concept", "name": "基本初等函数",
         "properties": {}},
        {"id": "math_bx1_rjb_cpt4", "label": "Concept", "name": "指数函数",
         "properties": {}},
        {"id": "math_bx1_rjb_skl1", "label": "Skill", "name": "求定义域",
         "properties": {"description": "会求函数定义域"}},
        {"id": "math_bx1_rjb_exe1", "label": "Exercise", "name": "题目",
         "properties": {}},
        {"id": "math_bx1_rjb_ch1", "label": "Chapter", "name": "第一章",
         "properties": {}},
        {"id": "math_7a_rjb_cpt1", "label": "Concept", "name": "有理数",
         "properties": {}},
    ]
    edges = [
        {"source": "math_bx1_rjb_cpt1", "target": "math_bx1_rjb_cpt2",
         "type": "prerequisites_for", "properties": {}},
        {"source": "math_bx1_rjb_cpt2", "target": "math_bx1_rjb_skl1",
         "type": "prerequisites_for", "properties": {}},
        {"source": "math_bx1_rjb_cpt4", "target": "math_bx1_rjb_cpt3",
         "type": "is_a", "properties": {}},
        {"source": "math_bx1_rjb_cpt1", "target": "math_bx1_rjb_cpt4",
         "type": "relates_to", "properties": {}},
        {"source": "math_bx1_rjb_cpt1", "target": "math_7a_rjb_cpt1",
         "type": "prerequisites_for", "properties": {}},
        {"source": "math_bx1_rjb_exe1", "target": "math_bx1_rjb_cpt1",
         "type": "tests_concept", "properties": {}},
    ]
    _write_kkg(tmp_path, nodes, edges)
    g = data_loader.build_graph_from_dirs(str(tmp_path), load_marble=False,
                                          load_pep=False, load_k12=True,
                                          load_kkg=True)
    assert set(g.nodes) == {
        "kkg:math_bx1_rjb_cpt1", "kkg:math_bx1_rjb_cpt2",
        "kkg:math_bx1_rjb_cpt3", "kkg:math_bx1_rjb_cpt4",
        "kkg:math_bx1_rjb_skl1"}
    keys = [(e.prereq_key, e.topic_key) for e in g.edges]
    assert ("kkg:math_bx1_rjb_cpt1", "kkg:math_bx1_rjb_cpt2") in keys
    assert ("kkg:math_bx1_rjb_cpt2", "kkg:math_bx1_rjb_skl1") in keys
    assert ("kkg:math_bx1_rjb_cpt3", "kkg:math_bx1_rjb_cpt4") in keys
    assert len(keys) == 3
    assert g.dropped.get("kkg_related") == 1
    assert g.dropped.get("kkg_out_of_scope") == 1
    t = g.nodes["kkg:math_bx1_rjb_cpt1"]
    assert t.source == "kkg" and t.subject == "数学"
    assert t.book == "高中·必修一" and t.grade == 10
    assert t.description == "集合的定义"
    assert data_loader.find_cycles(g) == []


def test_kkg_flag_off(tmp_path):
    _write_kkg(tmp_path,
               [{"id": "math_bx1_rjb_cpt1", "label": "Concept",
                 "name": "集合", "properties": {}}], [])
    g = data_loader.build_graph_from_dirs(str(tmp_path), load_marble=False,
                                          load_pep=False, load_k12=True,
                                          load_kkg=False)
    assert not any(k.startswith("kkg:") for k in g.nodes)


@pytest.mark.skipif(not HAS_MARBLE, reason="marble-data 未克隆")
def test_real_marble_subjects_filtered():
    topics, _ = data_loader._marble_part(
        paths.MARBLE_DATA_DIR, ["English", "Mathematics"])
    assert len(topics) == 789
    assert all(t["subject"] in ("English", "Mathematics") for t in topics)


@pytest.mark.skipif(not HAS_PEP, reason="pep-data 未克隆")
def test_real_pep_namespaced():
    books = data_loader._pep_books(paths.PEP_DATA_DIR)
    assert len(books) == 6
    assert sum(len(tp) for _, tp, _ in books) == 169
    g = data_loader.build_graph_from_dirs(
        subjects=["Mathematics"], load_marble=True, load_pep=True,
        load_k12=False)
    pep_keys = [k for k in g.nodes if k.startswith("pep:")]
    assert len(pep_keys) == 169
    pep_edges = [e for e in g.edges if e.prereq_key.startswith("pep:")]
    assert len(pep_edges) == 235
    assert g.dropped.get("ambiguous") == 2
    _, md = data_loader._marble_part(str(paths.MARBLE_DATA_DIR), ["Mathematics"])
    kept_marble = sum(1 for e in g.edges if e.prereq_key.startswith("marble:"))
    assert g.dropped.get("missing", 0) == len(md) - kept_marble


@pytest.mark.skipif(not HAS_KKG, reason="k12kgraph-hf 未下载")
def test_real_kkg_reconciliation():
    pack = data_loader._kkg_extract(data_loader._kkg_math_path())
    exp_out = sum(1 for pre, nxt, _t in pack["edges"]
                  if pre not in pack["topics"] or nxt not in pack["topics"])
    g = data_loader.build_graph_from_dirs(load_marble=False, load_pep=False,
                                          load_k12=False, load_kkg=True)
    assert len(g.nodes) == len(pack["topics"])
    assert all(k.startswith("kkg:") for k in g.nodes)
    assert g.dropped.get("kkg_related") == pack["related"]
    assert g.dropped.get("kkg_out_of_scope") == exp_out
    cyc = data_loader.find_cycles(g)
    print("kkg 高中数学 环数(仅报告): " + str(len(cyc)))


@pytest.mark.skipif(not (HAS_MARBLE and HAS_K12),
                    reason="需要 marble 与 k12 数据齐备")
def test_real_graph_invariants():
    """生产默认图对账: marble 英语 + k12 三学段 + kkg 高中数学(文件存在时)。"""
    exp_nodes, exp_prereq, exp_related = 0, 0, 0
    for band, subject, path in data_loader._k12_split_files():
        kps, rels = data_loader._k12_load_split(path)
        exp_nodes += len(kps)
        for r in rels:
            if r.get("relation_type") == "related":
                exp_related += 1
            elif r.get("relation_type") in ("prerequisite", "leads-to"):
                exp_prereq += 1
    exp_kkg = len(data_loader._kkg_extract(
        data_loader._kkg_math_path())["topics"]) if HAS_KKG else 0
    g = data_loader.get_graph()
    s = data_loader.stats(g)
    assert s["by_source"].get("marble") == 286
    assert "pep" not in s["by_source"]
    assert s["by_source"].get("k12") == exp_nodes
    assert s["by_source"].get("kkg", 0) == exp_kkg
    assert g.dropped.get("k12_related") == exp_related
    assert g.dropped.get("k12_dangling", 0) == 0
    for e in g.edges:
        assert e.prereq_key in g.nodes
        assert e.topic_key in g.nodes
        assert e.prereq_key != e.topic_key
    assert s["total_nodes"] == 286 + exp_nodes + exp_kkg
