# -*- coding: utf-8 -*-
r'''
setup_phase8_data.py - Sprint 8 数据源切换(核心数据层)
  1. 新增 k12-knowledge-points 加载器:
     - split/ 按学段x科目文件, 节点 key = "k12:"+UUID
     - 科目排除清单(用户指定), related 关系丢弃计数
     - 方向自检: 比较两种朝向的根节点数, 取根少者(入口=基础概念), 日志打印判定
     - 学段映射 grade: 小学3/初中7/高中10; Topic.book = 学段名
  2. 生产默认加载: marble(仅小学英语) + k12(三学段, 排除科目); pep 默认关闭
     (k12 初中数学覆盖原 pep, 保留 _pep_books 供测试与将来启用)
  3. build_graph_from_dirs 增加开关: load_marble/load_pep/load_k12/bands
  4. 测试更新: 旧 mini 测试显式开 pep 关 k12 保持原语义;
     新增 k12 mini 测试(方向判定/related丢弃/科目排除) + 真实数据对账
  5. 清库: 删除旧进度 DB(key 体系全换, 开发期数据无意义)
注意: 本轮后 /api/graph 返回全量(~2万节点), 星图会重;
     学段切换(按band返回子图)需要下一轮接线, 届时解决。
'''
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CURLY = "".join(chr(c) for c in (0x201C, 0x201D, 0x2018, 0x2019))
BAD_BACKTICK = chr(96) * 3

FILES = {}

FILES["backend/data_loader.py"] = r'''# -*- coding: utf-8 -*-
"""三源图谱加载: marble(小学英语) + pep(初中数学, 默认关) + k12(全学段)。

关键设计:
- marble 节点键 = "marble:"+id; pep = "pep:"+册名+":"+id(撞车 id 按册隔离);
  k12 = "k12:"+UUID(全局唯一)
- k12 关系语义: prerequisite/leads-to 均当地前置边; related 弱相关丢弃计数
- k12 方向自检: from/to 朝向未知, 比较两种朝向下的根节点数(无前置入口),
  基础概念入口应更少, 取根少朝向; 平局取自然朝向(from=前置); 日志打印判定
- k12 科目排除清单: 初中 体育/俄语/艺术; 小学 体育/思政/美术; 高中 体育/科学/美术/艺术
- k12 关系不跨科目不跨学段(README 语义), 按文件独立解析, 悬空计数
- 生产默认: marble=英语, pep 关(被 k12 初中数学覆盖), k12 三学段全开
- 加载后 find_cycles 自检, 有环仅告警不中断(k12 为 LLM 推断数据, 可能成环)
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

# 生产默认: marble 只保留小学英语(k12 小学英语缺失, 由 marble 补)
DEFAULT_MARBLE_SUBJECTS = ["English"]

# k12 科目排除清单(按学段)
K12_EXCLUDE = {
    "小学": {"体育", "思政", "美术"},
    "初中": {"体育", "俄语", "艺术"},
    "高中": {"体育", "科学", "美术", "艺术"},
}
K12_GRADE = {"小学": 3, "初中": 7, "高中": 10}


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
        key=key, id=tid, source=source, book=book,
        name=str(t.get("name", "")), type=str(t.get("type", "")),
        subject=str(t.get("subject", "")), domain=str(t.get("domain", "")),
        grade=grade, description=str(t.get("description", "")),
        evidence=list(t.get("evidence") or []),
    )


def _marble_part(marble_dir: str, subjects: List[str]) -> Tuple[List[dict], List[dict]]:
    data_dir = os.path.join(marble_dir, "data")
    tp = os.path.join(data_dir, "topics.json")
    dp = os.path.join(data_dir, "dependencies.json")
    if not os.path.isfile(tp):
        return [], []
    raw = _read_json(tp).get("topics", [])
    allowed = set(subjects)
    topics = [t for t in raw if t.get("subject") in allowed]
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


def _k12_dir(base_dir: str = "") -> str:
    root = base_dir if base_dir else paths.project_root()
    return os.path.join(root, "k12-data")


def _k12_split_files(base_dir: str = "", bands: Optional[List[str]] = None
                     ) -> List[Tuple[str, str, str]]:
    """返回 (学段, 科目, 文件路径), 应用科目排除清单与学段过滤。"""
    split = os.path.join(_k12_dir(base_dir), "split")
    out: List[Tuple[str, str, str]] = []
    if not os.path.isdir(split):
        return out
    allowed_bands = set(bands) if bands else None
    for band in sorted(os.listdir(split)):
        bdir = os.path.join(split, band)
        if not os.path.isdir(bdir):
            continue
        if allowed_bands is not None and band not in allowed_bands:
            continue
        for fname in sorted(os.listdir(bdir)):
            if not fname.endswith(".json") or fname == "index.json":
                continue
            subject = fname[:-5]
            if subject in K12_EXCLUDE.get(band, set()):
                continue
            out.append((band, subject, os.path.join(bdir, fname)))
    return out


def _to_k12_topic(t: dict, band: str) -> Topic:
    tid = str(t.get("id", ""))
    return Topic(
        key="k12:" + tid, id=tid, source="k12", book=band,
        name=str(t.get("canonical_name", "")), type="",
        subject=str(t.get("subject", "")), domain=str(t.get("subject", "")),
        grade=K12_GRADE.get(band, 0),
        description=str(t.get("summary", "")),
    )


def _k12_should_flip(ids: set, rels: List[dict]) -> Tuple[bool, int, int]:
    """方向自检: 比较两种朝向的根节点数(无前置入口), 基础概念入口应更少。

    返回 (是否翻转, 自然朝向根数, 翻转朝向根数)。平局取自然朝向(from=前置)。
    """
    rels = [r for r in rels if r.get("relation_type") in ("prerequisite", "leads-to")]
    indeg_nat: Dict[str, int] = {}
    indeg_flip: Dict[str, int] = {}
    for r in rels:
        f, t = str(r.get("from_kp_id", "")), str(r.get("to_kp_id", ""))
        if f not in ids or t not in ids:
            continue
        indeg_nat[t] = indeg_nat.get(t, 0) + 1
        indeg_flip[f] = indeg_flip.get(f, 0) + 1
    roots_nat = sum(1 for i in ids if not indeg_nat.get(i))
    roots_flip = sum(1 for i in ids if not indeg_flip.get(i))
    return roots_flip < roots_nat, roots_nat, roots_flip


def build_graph(marble_topics: List[dict], marble_deps: List[dict],
                pep_books: List[Tuple[str, List[dict], List[dict]]],
                subjects: Optional[List[str]] = None,
                k12_groups: Optional[List[dict]] = None) -> Graph:
    """k12_groups: [{band, topics, relations}, ...]"""
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

    k12_ids: set = set()
    k12_rels: List[dict] = []
    for grp in (k12_groups or []):
        for t in grp["topics"]:
            topic = _to_k12_topic(t, grp["band"])
            register(topic)
            k12_ids.add(topic.id)
        k12_rels.extend(grp["relations"])

    if k12_ids:
        flip, rn, rf = _k12_should_flip(k12_ids, k12_rels)
        logger.info("k12 方向自检: 自然朝向根=%d, 翻转朝向根=%d -> 采用%s",
                    rn, rf, "翻转" if flip else "自然朝向(from=前置)")

    def resolve(source: str, book: str, tid: str) -> Tuple[Optional[str], Optional[str]]:
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

    for r in k12_rels:
        rt = str(r.get("relation_type", ""))
        if rt == "related":
            g.note_drop("k12_related")
            continue
        if rt not in ("prerequisite", "leads-to"):
            g.note_drop("k12_unknown_type")
            continue
        fid, tid2 = str(r.get("from_kp_id", "")), str(r.get("to_kp_id", ""))
        pre_id, nxt_id = (tid2, fid) if flip else (fid, tid2)
        pre = "k12:" + pre_id
        nxt = "k12:" + nxt_id
        if pre not in g.nodes or nxt not in g.nodes:
            g.note_drop("k12_dangling")
            continue
        g.add_edge(Edge(prereq_key=pre, topic_key=nxt, strength=rt))
    return g


def build_graph_from_dirs(base_dir=None, subjects: Optional[List[str]] = None,
                          bands: Optional[List[str]] = None,
                          load_marble: bool = True, load_pep: bool = False,
                          load_k12: bool = True) -> Graph:
    root = str(base_dir) if base_dir else paths.project_root()
    marble_topics: List[dict] = []
    marble_deps: List[dict] = []
    pep: List[Tuple[str, List[dict], List[dict]]] = []
    groups: List[dict] = []
    if load_marble:
        mt, md = _marble_part(os.path.join(root, "marble-data"),
                              subjects if subjects is not None
                              else DEFAULT_MARBLE_SUBJECTS)
        marble_topics, marble_deps = mt, md
    if load_pep:
        pep = _pep_books(os.path.join(root, "pep-data"))
    if load_k12:
        for band, _subject, path in _k12_split_files(root, bands):
            d = _read_json(path)
            groups.append({"band": band, "topics": d.get("knowledge_points", []),
                           "relations": d.get("relations", [])})
    return build_graph(marble_topics, marble_deps, pep,
                       subjects if subjects is not None
                       else (DEFAULT_MARBLE_SUBJECTS if load_marble else None),
                       groups)


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
                    logger.warning("图谱存在环(k12 为推断数据), 示例: %s",
                                   " -> ".join(cyc[0]))
                s = stats(_graph)
                logger.info("图谱加载: %s, dropped=%s",
                            json.dumps(s["by_source"], ensure_ascii=False),
                            json.dumps(_graph.dropped, ensure_ascii=False))
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

FILES["tests/test_data_loader.py"] = r'''# -*- coding: utf-8 -*-
"""data_loader 测试: 合成数据(mini 双源 + mini k12) + 真实数据对账。

phase8 起:
- 旧 mini 双源测试显式 load_pep=True, load_k12=False, 保持原语义
- 新增 k12 mini 测试: 方向自检/related 丢弃/科目排除/学段 grade
- 真实数据测试改为结构化对账(k12 数字从数据文件动态计算, 不硬编码)
"""
import os
import json
import pytest
import paths
from backend import data_loader

HAS_MARBLE = os.path.isdir(paths.MARBLE_DATA_DIR)
HAS_PEP = os.path.isdir(paths.PEP_DATA_DIR)
HAS_K12 = os.path.isdir(os.path.join(paths.project_root(), "k12-data", "split"))


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
    assert g.nodes["k12:k1"].name == "加法"
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
    # 数据以 to=前置 的反向语义书写: B,C 依赖 A 但写成 from=B to=A 形式
    # 自然朝向: B->A, C->A (根=2); 翻转: A->B, A->C (根=1) -> 应自动翻转
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


@pytest.mark.skipif(not (HAS_MARBLE and HAS_K12),
                    reason="需要 marble 与 k12 数据齐备")
def test_real_graph_invariants():
    """生产默认图: marble 英语 + k12 三学段(排除科目) + pep 关闭。对账数字动态计算。"""
    exp_nodes, exp_prereq, exp_related = 0, 0, 0
    for band, subject, path in data_loader._k12_split_files():
        d = data_loader._read_json(path)
        exp_nodes += len(d.get("knowledge_points", []))
        for r in d.get("relations", []):
            if r.get("relation_type") == "related":
                exp_related += 1
            elif r.get("relation_type") in ("prerequisite", "leads-to"):
                exp_prereq += 1
    g = data_loader.get_graph()
    s = data_loader.stats(g)
    assert s["by_source"].get("marble") == 286  # 仅小学英语
    assert "pep" not in s["by_source"]
    assert s["by_source"].get("k12") == exp_nodes
    assert g.dropped.get("k12_related") == exp_related
    assert g.dropped.get("k12_dangling", 0) == 0
    for e in g.edges:
        assert e.prereq_key in g.nodes
        assert e.topic_key in g.nodes
        assert e.prereq_key != e.topic_key
    cyc = data_loader.find_cycles(g)
    print("k12 真实数据环数(仅报告): " + str(len(cyc)))
    assert s["total_nodes"] == 286 + exp_nodes
'''

FILES["tests/conftest.py"] = r'''# -*- coding: utf-8 -*-
"""公共 fixture: 微型双源数据(含撞车 id 复现) + 服务/客户端注入。

phase8: build_graph_from_dirs 显式 load_pep=True, load_k12=False,
保持 mini 双源语义不变; 生产默认(marble英语+k12)不受影响。
"""
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
        {"topicId": "mt_B", "prerequisiteId": "mt_A", "strength": "hard",
         "reason": "keep"},
        {"topicId": "mt_B", "prerequisiteId": "mt_C", "strength": "soft",
         "reason": "C 被学科过滤, 边应丢弃"},
    ]})
    pep = tmp_path / "pep-data" / "data"
    _write(pep / "topics.json", {"topics": [
        _topic("mt_ch1_001", "正负数", 12, domain="有理数"),
    ]})
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


@pytest.fixture
def mini_graph(mini_sources):
    from backend import data_loader
    return data_loader.build_graph_from_dirs(
        mini_sources, subjects=["Mathematics"],
        load_marble=True, load_pep=True, load_k12=False)


@pytest.fixture
def svc(mini_graph):
    from backend import graph_service
    return graph_service.GraphService(mini_graph)


@pytest.fixture
def progress(tmp_path):
    from backend import progress_service
    p = progress_service.ProgressService(str(tmp_path / "progress.db"))
    yield p
    p.close()


@pytest.fixture
def client(mini_sources, tmp_path):
    from backend import data_loader, graph_service, progress_service, state
    from backend.main import app
    from fastapi.testclient import TestClient
    g = graph_service.GraphService(
        data_loader.build_graph_from_dirs(
            mini_sources, subjects=["Mathematics"],
            load_marble=True, load_pep=True, load_k12=False))
    p = progress_service.ProgressService(str(tmp_path / "api.db"))
    state.init(g, p)
    c = TestClient(app)
    yield c
    state.reset()
    p.close()
'''


def main():
    print("项目根: " + str(ROOT))
    bad = [rel for rel, c in FILES.items()
           if BAD_BACKTICK in c or any(x in c for x in CURLY)]
    print("[1/3] 自检: " + ("[PASS] 无污染" if not bad else "[FAIL] " + str(bad)))
    if bad:
        return
    for rel, content in FILES.items():
        p = ROOT / rel
        p.write_text(content, encoding="utf-8")
        print("  [OVERWRITE] " + rel)
    print("[2/3] 清库: 删除旧进度 DB(开发期数据, key 体系已全换)")
    try:
        sys.path.insert(0, str(ROOT))
        import paths
        db = paths.DB_FILE
        if os.path.isfile(db):
            os.remove(db)
            print("  [DELETED] " + str(db))
        else:
            print("  [SKIP] 不存在: " + str(db))
    except Exception as exc:
        print("  [WARN] 清库失败(手动删除即可): " + str(exc))
    print("[3/3] 验收")
    print("  python -m pytest tests/ -q")
    print("  python -m backend.data_loader   (看真实规模/方向判定/环数报告)")
    print("=" * 56)
    print("注意: 本轮后 /api/graph 返回全量约2万节点, 星图会很重,")
    print("  这是预期中的中间状态; 学段切换接线在下一轮(需3个契约文件)。")


if __name__ == "__main__":
    main()
