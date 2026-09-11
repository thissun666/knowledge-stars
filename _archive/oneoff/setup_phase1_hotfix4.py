# -*- coding: utf-8 -*-
r'''
setup_phase1_hotfix4.py - Sprint 1 测试断言修正
loader 冻结不动(958/1772/cycles=0 已验证正确), 仅修正两处断言:
  1. filtered_subject 只在直接调用 build_graph 时触发(学科过滤发生在
     _marble_part, build_graph_from_dirs 路径下不会出现), 补一个直调测试
  2. missing 的语义是"引用不存在节点的边", 含 marble 引用被学科过滤的
     节点; 改为结构化对账: missing == marble原始边数 - marble保留边数,
     等式成立即证明 pep 侧零悬空引用
'''
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

CURLY = "".join(chr(c) for c in (0x201C, 0x201D, 0x2018, 0x2019))
BAD_BACKTICK = chr(96) * 3

TEST_LOADER = r'''# -*- coding: utf-8 -*-
"""data_loader 测试: 合成数据(含撞车 id 复现) + 真实数据定版数字锚定。

dropped 计数器语义(hotfix4 定稿):
  - filtered_subject: 仅直接调用 build_graph 传入未过滤数据时触发。
    build_graph_from_dirs 路径下, marble 学科过滤发生在 _marble_part,
    被滤节点不会进入 build_graph, 故该计数器不出现
  - missing: 引用了不存在节点的边。含两类: marble 边引用被学科过滤的
    节点(预期裁剪), 以及真正的悬空/跨源引用
  - ambiguous: pep 撞车 id 的跨册歧义引用(真实数据 2 条)
"""
import os

import pytest

import paths
from backend import data_loader

HAS_MARBLE = os.path.isdir(paths.MARBLE_DATA_DIR)
HAS_PEP = os.path.isdir(paths.PEP_DATA_DIR)


def build(mini_sources):
    return data_loader.build_graph_from_dirs(mini_sources,
                                             subjects=["Mathematics"])


def test_mini_nodes_and_collision(mini_sources):
    g = build(mini_sources)
    assert set(g.nodes) == {
        "marble:mt_A", "marble:mt_B",
        "pep:7-up:mt_ch1_001", "pep:7-down:mt_ch5_001",
        "pep:8-down:mt_ch21_001", "pep:8-down:mt_ch21_002",
        "pep:9-up:mt_ch21_001", "pep:9-up:mt_ch22_001",
    }
    # 撞车 id 按册隔离, 是两个不同知识点
    assert g.nodes["pep:8-down:mt_ch21_001"].name == "四边形与多边形的概念"
    assert g.nodes["pep:9-up:mt_ch21_001"].name == "一元二次方程的概念"


def test_mini_edges_same_book_priority(mini_sources):
    g = build(mini_sources)
    keys = [(e.prereq_key, e.topic_key) for e in g.edges]
    assert ("marble:mt_A", "marble:mt_B") in keys
    # 跨册唯一命中应解析(7-down 依赖 7-up 根册)
    assert ("pep:7-up:mt_ch1_001", "pep:7-down:mt_ch5_001") in keys
    # 撞车 id 各册同册优先
    assert ("pep:8-down:mt_ch21_001", "pep:8-down:mt_ch21_002") in keys
    assert ("pep:9-up:mt_ch21_001", "pep:9-up:mt_ch22_001") in keys
    # 撞车边不许跨册串线
    assert ("pep:8-down:mt_ch21_001", "pep:9-up:mt_ch22_001") not in keys
    assert ("pep:9-up:mt_ch21_001", "pep:8-down:mt_ch21_002") not in keys
    assert len(g.edges) == 4


def test_mini_dropped_counters(mini_sources):
    g = build(mini_sources)
    # marble 学科过滤发生在 _marble_part, 不进入 Graph.dropped
    assert "filtered_subject" not in g.dropped
    assert g.dropped.get("ambiguous") == 1          # 7-down 引用撞车 id
    assert g.dropped.get("missing") == 2            # mt_C(被滤) + mt_A(跨源)


def test_build_graph_direct_filters_and_counts():
    # 直接调用 build_graph(不经 _marble_part)时, 防御分支生效
    raw = [{"id": "mt_X", "subject": "Science", "name": "X"},
           {"id": "mt_Y", "subject": "Mathematics", "name": "Y"}]
    g = data_loader.build_graph(raw, [], [], subjects=["Mathematics"])
    assert set(g.nodes) == {"marble:mt_Y"}
    assert g.dropped.get("filtered_subject") == 1


def test_mini_grade(mini_sources):
    g = build(mini_sources)
    assert g.nodes["pep:7-up:mt_ch1_001"].grade == 7   # ageEnd 12 - 5
    assert g.nodes["marble:mt_A"].grade == 3           # ageEnd 8 - 5


def test_no_self_loop(mini_sources):
    g = build(mini_sources)
    added = g.add_edge(data_loader.Edge(
        prereq_key="marble:mt_A", topic_key="marble:mt_A"))
    assert added is False
    assert g.dropped.get("self_loop") == 1


def test_find_cycles_clean(mini_sources):
    assert data_loader.find_cycles(build(mini_sources)) == []


@pytest.mark.skipif(not HAS_MARBLE, reason="marble-data 未克隆")
def test_real_marble_subjects_filtered():
    topics, _ = data_loader._marble_part(
        paths.MARBLE_DATA_DIR, ["English", "Mathematics"])
    assert len(topics) == 789  # 数学 503 + 英语 286, 锚定当前数据版本
    assert all(t["subject"] in ("English", "Mathematics") for t in topics)


@pytest.mark.skipif(not HAS_PEP, reason="pep-data 未克隆")
def test_real_pep_namespaced():
    books = data_loader._pep_books(paths.PEP_DATA_DIR)
    assert len(books) == 6
    assert sum(len(tp) for _, tp, _ in books) == 169
    g = data_loader.build_graph_from_dirs(subjects=["Mathematics"])
    pep_keys = [k for k in g.nodes if k.startswith("pep:")]
    # 12 对撞车 id 按册拆开后还原 169 个知识点
    assert len(pep_keys) == 169
    assert "pep:8-down:mt_ch21_001" in g.nodes
    assert "pep:9-up:mt_ch21_001" in g.nodes
    pep_edges = [e for e in g.edges if e.prereq_key.startswith("pep:")]
    assert len(pep_edges) == 235  # README 声称 237, 歧义丢弃 2 条
    assert g.dropped.get("ambiguous") == 2
    # 结构化对账: missing 全部来自 marble 引用被学科过滤的节点。
    # 等式成立即证明 pep 侧零悬空引用(若有, missing 必然偏大)
    _, md = data_loader._marble_part(str(paths.MARBLE_DATA_DIR),
                                     ["Mathematics"])
    kept_marble = sum(1 for e in g.edges
                      if e.prereq_key.startswith("marble:"))
    assert g.dropped.get("missing", 0) == len(md) - kept_marble


@pytest.mark.skipif(not (HAS_MARBLE and HAS_PEP),
                    reason="需要两个数据仓库齐备")
def test_real_graph_invariants():
    g = data_loader.get_graph()
    s = data_loader.stats(g)
    assert s["by_source"] == {"marble": 789, "pep": 169}
    assert s["total_nodes"] == 958
    assert s["total_edges"] == 1772  # marble 1537 + pep 235
    for e in g.edges:
        assert e.prereq_key in g.nodes
        assert e.topic_key in g.nodes
        assert e.prereq_key != e.topic_key
    assert data_loader.find_cycles(g) == []
    # 默认过滤(En+Math)下的对账: missing = 3221 - 1537 = 1684
    _, md = data_loader._marble_part(str(paths.MARBLE_DATA_DIR),
                                     ["English", "Mathematics"])
    kept = sum(1 for e in g.edges if e.prereq_key.startswith("marble:"))
    assert g.dropped.get("missing", 0) == len(md) - kept
'''


def write_file():
    print("[1/3] 覆盖 tests/test_data_loader.py ...")
    p = ROOT / "tests" / "test_data_loader.py"
    p.write_text(TEST_LOADER, encoding="utf-8")
    print("  [FIX]  已覆盖")


def self_check():
    print("[2/3] 自检报告")
    ok = True
    if BAD_BACKTICK in TEST_LOADER:
        print("  [FAIL] 三反引号污染")
        ok = False
    if any(c in TEST_LOADER for c in CURLY):
        print("  [FAIL] 弯引号污染")
        ok = False
    if ok:
        print("  [PASS] 交付内容无三反引号/弯引号污染")
    print("  测试用例数: " + str(TEST_LOADER.count("def test_")))


def next_steps():
    print("[3/3] 下一步命令")
    print("  python -m pytest tests/ -v")
    print("=" * 56)


if __name__ == "__main__":
    print("项目根: " + str(ROOT))
    write_file()
    self_check()
    next_steps()
