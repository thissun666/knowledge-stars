# -*- coding: utf-8 -*-
r'''
setup_phase1_hotfix3.py - Sprint 1 定版修复
  依据 hotfix2 诊断结论:
    - pep 存在 12 对跨册撞车 id(8-down 与 9-up 章节号重复), 按裸 id 合并产生环
    - marble 数据为干净 DAG, 无需处理
  本脚本:
    1. 重写 backend/data_loader.py: pep 按册命名空间 + same-then-unique 跨册解析
    2. 重写 tests/conftest.py: 复刻真实布局的合成数据(含撞车 id 复现)
    3. 重写 tests/test_data_loader.py: 断言全部锚定诊断实测数字
  预期定版数字: nodes=958(marble 789 + pep 169), edges=1772(marble 1537 + pep 235)
'''
import sys
from pathlib import Path

ROOT = Path(__FILE__) if False else Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

CURLY = "".join(chr(c) for c in (0x201C, 0x201D, 0x2018, 0x2019))
BAD_BACKTICK = chr(96) * 3

FILES = {}

FILES["backend/data_loader.py"] = r'''# -*- coding: utf-8 -*-
"""双源图谱加载: Marble(小学, 按学科过滤) + pep(初中数学, 按册命名空间)。

关键设计:
  - marble 节点键 = "marble:" + id; pep 节点键 = "pep:" + 册名 + ":" + id
  - pep 必须按册隔离: 真实数据存在 12 对同 id 不同章节的撞车
    (如 mt_ch21_001 在 8-down 是四边形概念, 在 9-up 是一元二次方程概念),
    按裸 id 合并会产生交叉环(诊断已证实), 这是热修 3 的核心改动
  - pep 跨册前置解析策略 same-then-unique:
      1) 同册命中优先
      2) 跨册唯一命中则解析
      3) 缺失或歧义(命中 2 册以上)则丢弃, 计数到 Graph.dropped
  - marble 边只在 marble 内解析; 两套数据各自独立, 跨源引用一律丢弃
  - 根目录 data/topics.json 依 README 语义视为 7-up 册(64 个知识点)
  - get_graph() 线程安全惰性单例; 加载后 find_cycles 自检, 有环仅告警不中断
"""
import json
import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import paths
from backend import config

logger = logging.getLogger("app.data_loader")


@dataclass
class Topic:
    key: str
    id: str
    source: str
    book: str
    name: str
    type: str
    subject: str
    domain: str
    grade: int
    description: str = ""
    evidence: List[str] = field(default_factory=list)


@dataclass
class Edge:
    prereq_key: str
    topic_key: str
    strength: str = "hard"
    reason: str = ""


@dataclass
class Graph:
    nodes: Dict[str, Topic] = field(default_factory=dict)
    edges: List[Edge] = field(default_factory=list)
    adj_out: Dict[str, List[str]] = field(default_factory=dict)
    adj_in: Dict[str, List[str]] = field(default_factory=dict)
    dropped: Dict[str, int] = field(default_factory=dict)

    def add_node(self, t: Topic) -> None:
        self.nodes.setdefault(t.key, t)

    def note_drop(self, reason: str) -> None:
        self.dropped[reason] = self.dropped.get(reason, 0) + 1

    def add_edge(self, e: Edge) -> bool:
        if e.prereq_key == e.topic_key:
            self.note_drop("self_loop")
            return False
        if e.prereq_key not in self.nodes or e.topic_key not in self.nodes:
            self.note_drop("dangling")
            return False
        if e.topic_key in self.adj_out.get(e.prereq_key, []):
            self.note_drop("duplicate")
            return False
        self.edges.append(e)
        self.adj_out.setdefault(e.prereq_key, []).append(e.topic_key)
        self.adj_in.setdefault(e.topic_key, []).append(e.prereq_key)
        return True


def _read_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _deps_of(path: str) -> List[dict]:
    if os.path.isfile(path):
        return _read_json(path).get("dependencies", [])
    return []


def _to_topic(t: dict, source: str, book: str) -> Topic:
    try:
        grade = max(1, int(t.get("ageRangeEnd")) - 5)
    except (TypeError, ValueError):
        grade = 0
    tid = str(t.get("id", ""))
    key = source + ":" + book + ":" + tid if book else source + ":" + tid
    return Topic(
        key=key,
        id=tid,
        source=source,
        book=book,
        name=str(t.get("name", "")),
        type=str(t.get("type", "")),
        subject=str(t.get("subject", "")),
        domain=str(t.get("domain", "")),
        grade=grade,
        description=str(t.get("description", "")),
        evidence=list(t.get("evidence") or []),
    )


def _marble_part(marble_dir: str,
                 subjects: List[str]) -> Tuple[List[dict], List[dict]]:
    data_dir = os.path.join(marble_dir, "data")
    tp = os.path.join(data_dir, "topics.json")
    dp = os.path.join(data_dir, "dependencies.json")
    if not os.path.isfile(tp):
        return [], []
    raw = _read_json(tp).get("topics", [])
    allowed = set(subjects) if subjects else None
    topics = [t for t in raw if allowed is None or t.get("subject") in allowed]
    return topics, _deps_of(dp)


def _pep_books(pep_dir: str) -> List[Tuple[str, List[dict], List[dict]]]:
    """扫描 pep-data/data: 根 topics.json 视为 7-up 册, 子目录各为一册。"""
    data_dir = os.path.join(pep_dir, "data")
    books: List[Tuple[str, List[dict], List[dict]]] = []
    if not os.path.isdir(data_dir):
        return books
    root_tp = os.path.join(data_dir, "topics.json")
    if os.path.isfile(root_tp):
        books.append(("7-up", _read_json(root_tp).get("topics", []),
                      _deps_of(os.path.join(data_dir, "dependencies.json"))))
    for name in sorted(os.listdir(data_dir)):
        sub = os.path.join(data_dir, name)
        tp = os.path.join(sub, "topics.json")
        if os.path.isdir(sub) and os.path.isfile(tp):
            books.append((name, _read_json(tp).get("topics", []),
                          _deps_of(os.path.join(sub, "dependencies.json"))))
    return books


def build_graph(marble_topics: List[dict], marble_deps: List[dict],
                pep_books: List[Tuple[str, List[dict], List[dict]]],
                subjects: Optional[List[str]] = None) -> Graph:
    g = Graph()
    allowed = set(subjects) if subjects else None
    index: Dict[Tuple[str, str], List[str]] = {}

    def register(topic: Topic) -> None:
        g.add_node(topic)
        index.setdefault((topic.source, topic.id), []).append(topic.key)

    for t in marble_topics:
        if allowed is not None and t.get("subject") not in allowed:
            g.note_drop("filtered_subject")
            continue
        register(_to_topic(t, "marble", ""))

    for book, topics, _ in pep_books:
        for t in topics:
            register(_to_topic(t, "pep", book))

    def resolve(source: str, book: str,
                tid: str) -> Tuple[Optional[str], Optional[str]]:
        if source == "pep" and book:
            same = source + ":" + book + ":" + tid
            if same in g.nodes:
                return same, None
        hits = index.get((source, tid), [])
        if len(hits) == 1:
            return hits[0], None
        if not hits:
            return None, "missing"
        return None, "ambiguous"

    def wire(source: str, book: str, dep: dict) -> None:
        pre, why = resolve(source, book, str(dep.get("prerequisiteId", "")))
        if pre is None:
            g.note_drop(why or "unknown")
            return
        nxt, why = resolve(source, book, str(dep.get("topicId", "")))
        if nxt is None:
            g.note_drop(why or "unknown")
            return
        g.add_edge(Edge(prereq_key=pre, topic_key=nxt,
                        strength=str(dep.get("strength", "hard")),
                        reason=str(dep.get("reason", ""))))

    for d in marble_deps:
        wire("marble", "", d)
    for book, _, deps in pep_books:
        for d in deps:
            wire("pep", book, d)
    return g


def build_graph_from_dirs(base_dir=None,
                          subjects: Optional[List[str]] = None) -> Graph:
    root = str(base_dir) if base_dir else paths.project_root()
    mt, md = _marble_part(os.path.join(root, "marble-data"),
                          subjects or config.SUBJECTS)
    pb = _pep_books(os.path.join(root, "pep-data"))
    return build_graph(mt, md, pb, subjects)


def find_cycles(g: Graph, limit: int = 5) -> List[List[str]]:
    """DFS 找环, 返回最多 limit 条环路径(空列表 = DAG 成立)。"""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {k: WHITE for k in g.nodes}
    parent: Dict[str, str] = {}
    cycles: List[List[str]] = []
    for start in g.nodes:
        if color[start] != WHITE:
            continue
        color[start] = GRAY
        stack = [(start, iter(g.adj_out.get(start, [])))]
        while stack:
            node, it = stack[-1]
            advanced = False
            for nxt in it:
                if color[nxt] == WHITE:
                    color[nxt] = GRAY
                    parent[nxt] = node
                    stack.append((nxt, iter(g.adj_out.get(nxt, []))))
                    advanced = True
                    break
                elif color[nxt] == GRAY:
                    cyc = [node]
                    cur = node
                    while cur != nxt:
                        cur = parent.get(cur, nxt)
                        cyc.append(cur)
                    cyc.reverse()
                    cycles.append(cyc)
                    if len(cycles) >= limit:
                        return cycles
            if not advanced:
                color[node] = BLACK
                stack.pop()
    return cycles


def stats(g: Graph) -> dict:
    by_source: Dict[str, int] = {}
    by_subject: Dict[str, int] = {}
    for n in g.nodes.values():
        by_source[n.source] = by_source.get(n.source, 0) + 1
        by_subject[n.subject] = by_subject.get(n.subject, 0) + 1
    return {
        "total_nodes": len(g.nodes),
        "total_edges": len(g.edges),
        "by_source": by_source,
        "by_subject": by_subject,
    }


_graph: Optional[Graph] = None
_lock = threading.Lock()


def get_graph() -> Graph:
    global _graph
    if _graph is None:
        with _lock:
            if _graph is None:
                _graph = build_graph_from_dirs()
                cyc = find_cycles(_graph)
                if cyc:
                    logger.warning("图谱存在 %d 个环, 示例: %s",
                                   len(cyc), " -> ".join(cyc[0]))
    return _graph


if __name__ == "__main__":
    graph = get_graph()
    s = stats(graph)
    print("nodes=" + str(s["total_nodes"]) + " edges=" + str(s["total_edges"]))
    print("by_source=" + json.dumps(s["by_source"], ensure_ascii=False))
    print("by_subject=" + json.dumps(s["by_subject"], ensure_ascii=False))
    print("dropped=" + json.dumps(graph.dropped, ensure_ascii=False))
    print("cycles=" + str(len(find_cycles(graph))))
'''

FILES["tests/conftest.py"] = r'''# -*- coding: utf-8 -*-
"""公共 fixture: 复刻真实仓库布局的微型双源数据, 含跨册撞车 id 复现。"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _topic(tid, name, age_end, subject="Mathematics", domain="d"):
    return {"id": tid, "type": "CONCEPTUAL", "subject": subject,
            "domain": domain, "name": name, "description": "",
            "ageRangeStart": age_end - 1, "ageRangeEnd": age_end}


@pytest.fixture
def mini_sources(tmp_path: Path) -> Path:
    marble = tmp_path / "marble-data" / "data"
    _write(marble / "topics.json", {"topics": [
        _topic("mt_A", "A", 8),
        _topic("mt_B", "B", 9),
        _topic("mt_C", "C", 9, subject="Science"),
    ]})
    _write(marble / "dependencies.json", {"dependencies": [
        {"topicId": "mt_B", "prerequisiteId": "mt_A",
         "strength": "hard", "reason": "keep"},
        {"topicId": "mt_B", "prerequisiteId": "mt_C",
         "strength": "soft", "reason": "C 被学科过滤, 边应丢弃"},
    ]})
    pep = tmp_path / "pep-data" / "data"
    # 根目录 = 7-up 册(与真实仓库布局一致)
    _write(pep / "topics.json", {"topics": [
        _topic("mt_ch1_001", "正负数", 12, domain="有理数"),
    ]})
    # 7-down: 跨册唯一依赖 + 歧义依赖 + 跨源依赖
    _write(pep / "7-down" / "topics.json", {"topics": [
        _topic("mt_ch5_001", "对顶角", 13, domain="相交线与平行线"),
    ]})
    _write(pep / "7-down" / "dependencies.json", {"dependencies": [
        {"topicId": "mt_ch5_001", "prerequisiteId": "mt_ch1_001",
         "strength": "hard", "reason": "跨册唯一命中, 应解析"},
        {"topicId": "mt_ch5_001", "prerequisiteId": "mt_ch21_001",
         "strength": "soft", "reason": "两册命中, 歧义应丢弃"},
        {"topicId": "mt_ch5_001", "prerequisiteId": "mt_A",
         "strength": "soft", "reason": "pep 内缺失, 应丢弃"},
    ]})
    # 8-down 与 9-up 撞 id: 同一 id 是两个不同知识点(复刻真实数据缺陷)
    _write(pep / "8-down" / "topics.json", {"topics": [
        _topic("mt_ch21_001", "四边形与多边形的概念", 13, domain="四边形"),
        _topic("mt_ch21_002", "平行四边形的性质与判定", 13, domain="四边形"),
    ]})
    _write(pep / "8-down" / "dependencies.json", {"dependencies": [
        {"topicId": "mt_ch21_002", "prerequisiteId": "mt_ch21_001",
         "strength": "hard", "reason": "同册优先"},
    ]})
    _write(pep / "9-up" / "topics.json", {"topics": [
        _topic("mt_ch21_001", "一元二次方程的概念", 14, domain="一元二次方程"),
        _topic("mt_ch22_001", "配方法解一元二次方程", 14, domain="一元二次方程"),
    ]})
    _write(pep / "9-up" / "dependencies.json", {"dependencies": [
        {"topicId": "mt_ch22_001", "prerequisiteId": "mt_ch21_001",
         "strength": "hard", "reason": "同册优先"},
    ]})
    return tmp_path
'''

FILES["tests/test_data_loader.py"] = r'''# -*- coding: utf-8 -*-
"""data_loader 测试: 合成数据(含撞车 id 复现) + 真实数据定版数字锚定。"""
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
    assert g.dropped.get("filtered_subject") == 1   # marble mt_C
    assert g.dropped.get("ambiguous") == 1          # ch21_001 两册命中
    assert g.dropped.get("missing") == 2            # mt_C(被滤) + mt_A(跨源)
    assert g.dropped.get("self_loop", 0) == 0


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
    assert not g.dropped.get("missing")


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
'''


def write_files():
    print("[1/3] 覆盖交付文件（自有文件, 直接覆盖）...")
    for rel, content in FILES.items():
        p = ROOT / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        print("  [FIX]  " + rel)


def self_check():
    print("[2/3] 自检报告")
    bad_b, bad_q, meta_q = [], [], []
    total = 0
    for rel, content in FILES.items():
        total += 1
        if BAD_BACKTICK in content:
            bad_b.append(rel)
        if any(c in content for c in CURLY):
            bad_q.append(rel)
    for p in ROOT.rglob("*.py"):
        rel = str(p.relative_to(ROOT))
        parts = set(p.parts)
        if parts & {"venv", "marble-data", "pep-data", "__pycache__"}:
            continue
        total += 1
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        if BAD_BACKTICK in text:
            bad_b.append(rel)
        if any(c in text for c in CURLY):
            if p.name.startswith("setup_"):
                meta_q.append(rel)
            else:
                bad_q.append(rel)
    print("  扫描文件数: " + str(total))
    if bad_b:
        print("  [FAIL] 三反引号污染: " + ", ".join(sorted(set(bad_b))))
    else:
        print("  [PASS] 无三反引号污染")
    if bad_q:
        print("  [FAIL] 运行时文件弯引号: " + ", ".join(sorted(set(bad_q))))
    else:
        print("  [PASS] 运行时文件无弯引号")
    if meta_q:
        print("  [NOTE] meta 脚本弯引号(自检代码自身, 可忽略): "
              + ", ".join(sorted(set(meta_q))))


def next_steps():
    print("[3/3] 下一步命令")
    print("  python -m pytest tests/ -v")
    print("  python -m backend.data_loader")
    print("=" * 56)


if __name__ == "__main__":
    print("项目根: " + str(ROOT))
    write_files()
    self_check()
    next_steps()
