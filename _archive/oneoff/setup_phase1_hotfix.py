# -*- coding: utf-8 -*-
r'''
setup_phase1_hotfix.py - Sprint 1 热修
  1. 重写 tests/test_data_loader.py：真实数据断言改为结构不变量（边完整性/无自环/DAG）
  2. 写入 pytest.ini：消除 pytest-asyncio 弃用警告
  3. 诊断输出：pep 每册 topics 数、跨文件重复 id、按源边保留统计
  4. 定位两个 meta 脚本的弯引号行号（仅报告，不自动修改）
'''
import glob
import json
from collections import Counter, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# 用 chr 构造弯引号，避免本脚本自身被后续自检标记为污染
CURLY = "".join(chr(c) for c in (0x201C, 0x201D, 0x2018, 0x2019))

PYTEST_INI = """[pytest]
asyncio_mode = strict
asyncio_default_fixture_loop_scope = function
"""

TEST_LOADER = r'''# -*- coding: utf-8 -*-
"""data_loader 单元测试：合成数据验证逻辑 + 真实数据结构不变量（缺失则跳过）"""
import os
from collections import deque

import pytest

import paths
from backend import data_loader

HAS_MARBLE = os.path.isdir(paths.MARBLE_DATA_DIR)
HAS_PEP = os.path.isdir(paths.PEP_DATA_DIR)


def is_dag(g) -> bool:
    """Kahn 拓扑排序：全部节点可出队即为无环有向图"""
    indeg = {k: 0 for k in g.nodes}
    for e in g.edges:
        indeg[e.topic_key] += 1
    q = deque(k for k, d in indeg.items() if d == 0)
    seen = 0
    while q:
        k = q.popleft()
        seen += 1
        for nxt in g.adj_out.get(k, []):
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                q.append(nxt)
    return seen == len(g.nodes)


def test_mini_merge_filter_and_edges(mini_sources):
    g = data_loader.build_graph_from_dirs(mini_sources,
                                          subjects=["Mathematics"])
    assert set(g.nodes.keys()) == {
        "marble:mt_A", "marble:mt_B", "pep:mt_ch1_001", "pep:mt_ch2_001",
    }
    keys = [(e.prereq_key, e.topic_key) for e in g.edges]
    assert ("marble:mt_A", "marble:mt_B") in keys
    assert ("pep:mt_ch1_001", "pep:mt_ch2_001") in keys
    assert ("marble:mt_C", "marble:mt_B") not in keys
    assert ("marble:mt_A", "pep:mt_ch2_001") not in keys
    assert len(g.edges) == 2


def test_mini_stats(mini_sources):
    g = data_loader.build_graph_from_dirs(mini_sources,
                                          subjects=["Mathematics"])
    s = data_loader.stats(g)
    assert s["total_nodes"] == 4
    assert s["total_edges"] == 2
    assert s["by_source"] == {"marble": 2, "pep": 2}
    assert s["by_subject"] == {"Mathematics": 4}


def test_mini_grade_and_adjacency(mini_sources):
    g = data_loader.build_graph_from_dirs(mini_sources,
                                          subjects=["Mathematics"])
    assert g.nodes["pep:mt_ch2_001"].grade == 8
    assert g.adj_out["marble:mt_A"] == ["marble:mt_B"]
    assert g.adj_in["marble:mt_B"] == ["marble:mt_A"]


def test_no_self_loop(mini_sources):
    g = data_loader.build_graph_from_dirs(mini_sources,
                                          subjects=["Mathematics"])
    added = g.add_edge(
        data_loader.Edge(prereq_key="marble:mt_A", topic_key="marble:mt_A"))
    assert added is False


@pytest.mark.skipif(not HAS_MARBLE, reason="marble-data 未克隆")
def test_real_marble_subjects_filtered():
    topics, _ = data_loader._marble_part(
        paths.MARBLE_DATA_DIR, ["English", "Mathematics"])
    assert len(topics) > 700
    assert all(t["subject"] in ("English", "Mathematics") for t in topics)


@pytest.mark.skipif(not HAS_PEP, reason="pep-data 未克隆")
def test_real_pep_books():
    parts = data_loader._pep_parts(paths.PEP_DATA_DIR)
    total = sum(len(t) for t, _ in parts)
    assert 100 <= total <= 200
    g = data_loader.build_graph_from_dirs(subjects=["Mathematics"])
    pep_keys = [k for k in g.nodes if k.startswith("pep:")]
    assert len(pep_keys) >= 100


@pytest.mark.skipif(not (HAS_MARBLE and HAS_PEP),
                    reason="需要两个数据仓库齐备")
def test_real_graph_invariants():
    g = data_loader.get_graph()
    s = data_loader.stats(g)
    assert s["total_nodes"] > 900
    assert s["total_edges"] > 1000
    for e in g.edges:
        assert e.prereq_key in g.nodes
        assert e.topic_key in g.nodes
        assert e.prereq_key != e.topic_key
    assert is_dag(g)
    pep_edges = [e for e in g.edges
                 if e.prereq_key.startswith("pep:")
                 and e.topic_key.startswith("pep:")]
    assert len(pep_edges) > 100
'''


def fix_tests():
    print("[1/5] 重写 tests/test_data_loader.py ...")
    p = ROOT / "tests" / "test_data_loader.py"
    p.write_text(TEST_LOADER, encoding="utf-8")
    print("  [OK]   已覆盖（测试文件归我们管，直接覆盖）")


def write_pytest_ini():
    print("[2/5] 写入 pytest.ini ...")
    p = ROOT / "pytest.ini"
    if p.exists():
        print("  [SKIP] 已存在")
        return
    p.write_text(PYTEST_INI, encoding="utf-8")
    print("  [OK]   pytest.ini")


def diagnose_pep():
    print("[3/5] pep-data 诊断 ...")
    pattern = str(ROOT / "pep-data" / "data" / "**" / "topics.json")
    files = sorted(glob.glob(pattern, recursive=True))
    if not files:
        print("  [WARN] 未找到 pep-data/data 下的 topics.json")
        return
    ids = Counter()
    for fp in files:
        try:
            with open(fp, "r", encoding="utf-8") as f:
                ts = json.load(f).get("topics", [])
        except Exception as exc:
            print("  [ERR] " + fp + " -> " + str(exc))
            continue
        ids.update(str(t.get("id", "")) for t in ts)
        rel = str(Path(fp).relative_to(ROOT))
        print("  " + rel + " : " + str(len(ts)) + " topics")
    total, unique = sum(ids.values()), len(ids)
    print("  合计(含重复)=" + str(total) + "  唯一id=" + str(unique))
    dups = {k: v for k, v in ids.items() if v > 1}
    if dups:
        sample = dict(list(dups.items())[:10])
        print("  [WARN] 重复id " + str(len(dups)) + " 个, 示例: "
              + json.dumps(sample, ensure_ascii=False))
    else:
        print("  [PASS] 无跨文件重复 id")


def diagnose_edges():
    print("[4/5] 真实图谱按源统计 ...")
    from backend import data_loader as dl
    g = dl.get_graph()
    marble_e = sum(1 for e in g.edges if e.prereq_key.startswith("marble:"))
    pep_e = sum(1 for e in g.edges if e.prereq_key.startswith("pep:"))
    print("  marble 保留边=" + str(marble_e) + "（原始全学科 3221）")
    print("  pep    保留边=" + str(pep_e))
    raw = ROOT / "marble-data" / "data" / "dependencies.json"
    if raw.is_file():
        with open(raw, "r", encoding="utf-8") as f:
            n = len(json.load(f).get("dependencies", []))
        print("  marble 原始边=" + str(n) + "，被学科过滤裁剪 "
              + str(n - marble_e) + " 条属预期行为")


def locate_curly_quotes():
    print("[5/5] meta 脚本弯引号定位（仅报告，不修改）...")
    for name in ("setup_dirs.py", "setup_phase1.py"):
        p = ROOT / name
        if not p.is_file():
            continue
        lines = p.read_text(encoding="utf-8").splitlines()
        hits = [i + 1 for i, ln in enumerate(lines) if any(c in ln for c in CURLY)]
        if not hits:
            print("  " + name + " 无弯引号")
            continue
        preview = ", ".join(str(h) for h in hits[:5])
        more = " ...等共 " + str(len(hits)) + " 行" if len(hits) > 5 else ""
        print("  " + name + " 行号: " + preview + more)
        for h in hits[:3]:
            print("    L" + str(h) + ": " + lines[h - 1].strip()[:60])


if __name__ == "__main__":
    print("项目根: " + str(ROOT))
    fix_tests()
    write_pytest_ini()
    diagnose_pep()
    diagnose_edges()
    locate_curly_quotes()
    print("=" * 56)
    print("热修完成。下一步:")
    print("  python -m pytest tests/ -v")
