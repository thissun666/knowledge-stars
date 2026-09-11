# -*- coding: utf-8 -*-
"""graph_service 测试: 拓扑/祖先后代/边界全量与增量一致性 + 路径推荐。"""


def test_topo_and_dag(svc):
    order = svc.topo_order()
    assert len(order) == len(svc.graph.nodes)
    assert svc.is_dag()
    idx = {k: i for i, k in enumerate(order)}
    for e in svc.graph.edges:
        assert idx[e.prereq_key] < idx[e.topic_key]


def test_initial_boundary_is_roots(svc):
    b = svc.compute_boundary({})
    assert b == {"marble:mt_A", "pep:7-up:mt_ch1_001",
                 "pep:8-down:mt_ch21_001", "pep:9-up:mt_ch21_001"}


def test_ancestors_descendants(svc):
    assert svc.descendants("pep:7-up:mt_ch1_001") == {"pep:7-down:mt_ch5_001"}
    assert svc.ancestors("pep:7-down:mt_ch5_001") == {"pep:7-up:mt_ch1_001"}
    assert svc.descendants("marble:mt_A") == {"marble:mt_B"}
    assert svc.ancestors("marble:mt_A") == set()


def test_delta_matches_full(svc):
    prog = {"pep:7-up:mt_ch1_001": "mastered"}
    svc.compute_boundary({})  # 先缓存变更前状态
    boundary, newly, lost = svc.apply_change(prog, ["pep:7-up:mt_ch1_001"])
    full = svc.compute_boundary(prog)  # 全量对账
    assert boundary == full
    assert newly == ["pep:7-down:mt_ch5_001"]
    assert lost == ["pep:7-up:mt_ch1_001"]


def test_learning_is_boundary_candidate(svc):
    b = svc.compute_boundary({"marble:mt_A": "learning"})
    assert "marble:mt_A" in b


def test_recommend_paths_empty_progress(svc):
    # 新契约: 每科目一条。夹具全部同科目 -> 恰好 1 条(旧 top-k 语义已废弃)
    steps = svc.recommend_paths({}, k=3)
    assert len(steps) == 1
    subjects = [s["subject"] for s in steps]
    assert len(subjects) == len(set(subjects))
    assert steps[0]["key"] == "marble:mt_A"
    assert steps[0]["learning"] is False



def test_recommend_paths_after_mastered(svc):
    # 新契约: 该科目只出一个名额, 掌握 mt_A 后由杠杆更高的边界节点接任
    # (解锁传播由 test_recommend_per_subject.py::test_switch_after_mastery 覆盖)
    steps = svc.recommend_paths({"marble:mt_A": "mastered"})
    keys = [s["key"] for s in steps]
    assert "marble:mt_A" not in keys
    assert keys == ["pep:7-up:mt_ch1_001"]


