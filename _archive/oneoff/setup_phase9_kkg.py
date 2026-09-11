# -*- coding: utf-8 -*-
r'''
setup_phase9_kkg.py - Sprint 9: 接入 K12-KGraph 高中数学(补齐高中数学缺口)
  数据源: k12kgraph-hf/K12-KGraph/subject_specific_KG/math.json (人教版23册合并图谱)
  过滤: 节点 id 前缀 math_bx1/bx2/xzxbx1/2/3_rjb_ = 高中5册; 仅 Concept/Skill
  方向(已用真实数据验证): prerequisites_for source=前置; is_a 翻转(父类为先)
  key: "kkg:"+原id; book="高中·必修一"等; 与现有 k12/marble/pep 零冲突, 不清库
  接线: routes 学段归属 kkg->高中; 前端来源标签; 缓存版本 v=11
  测试: +3 条(mini方向/翻转/丢弃 + 开关 + 真实对账), 预期 69 passed
'''
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CURLY = "".join(chr(c) for c in (0x201C, 0x201D, 0x2018, 0x2019))
BAD_BACKTICK = chr(96) * 3

FILES = {}

FILES["backend/data_loader.py"] = r'''# -*- coding: utf-8 -*-
"""四源图谱加载: marble(小学英语) + pep(初中数学, 默认关) + k12(全学段) + kkg(高中数学)。

关键设计:
- 节点键: marble="marble:"+id; pep="pep:"+册名+":"+id; k12="k12:"+UUID; kkg="kkg:"+原id
- k12 关系: prerequisite/leads-to 当前置, related 丢弃; 方向自检(根少者胜)
- kkg(教材图谱): prerequisites_for source=前置(已验证); is_a 翻转(父类为先);
  relates_to 丢弃; 仅 Concept/Skill; 按 id 前缀过滤高中 5 册, 与 k12 零重叠
- 生产默认: marble=英语, pep 关, k12 三学段, kkg 开(文件存在时)
- 加载后 find_cycles 自检, 有环仅告警(k12/kkg 均为 LLM 辅助推断数据)
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

DEFAULT_MARBLE_SUBJECTS = ["English"]

K12_EXCLUDE = {
    "小学": {"体育", "思政", "美术"},
    "初中": {"体育", "俄语", "艺术", "美术"},
    "高中": {"体育", "科学", "美术", "艺术",
             "历史", "地理", "思政", "音乐", "通用"},
}
K12_GRADE = {"小学": 3, "初中": 7, "高中": 10}

# K12-KGraph 高中数学册: id前缀 -> (展示册名, 年级)
KKG_HS_BOOKS = {
    "math_bx1_rjb": ("高中·必修一", 10),
    "math_bx2_rjb": ("高中·必修二", 10),
    "math_xzxbx1_rjb": ("高中·选择性必修一", 11),
    "math_xzxbx2_rjb": ("高中·选择性必修二", 11),
    "math_xzxbx3_rjb": ("高中·选择性必修三", 12),
}
KKG_CANDIDATES = (
    "k12kgraph-hf/K12-KGraph/subject_specific_KG/math.json",
    "k12kgraph-hf/subject_specific_KG/math.json",
)


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
    rels = [r for r in rels
            if r.get("relation_type") in ("prerequisite", "leads-to")]
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


def _kkg_math_path(base_dir: str = "") -> str:
    root = base_dir if base_dir else paths.project_root()
    for rel in KKG_CANDIDATES:
        p = os.path.join(root, rel)
        if os.path.isfile(p):
            return p
    return ""


def _kkg_prefix_of(node_id: str) -> str:
    for prefix in KKG_HS_BOOKS:
        if node_id.startswith(prefix + "_"):
            return prefix
    return ""


def _kkg_extract(path: str) -> dict:
    """从 k12kgraph math.json 抽取高中数学 Concept/Skill 与前置边。

    方向语义(经 demo 数据人工验证): prerequisites_for 的 source 是前置;
    is_a 的 source 是子类 target 是父类, 翻转后父类为前置。
    返回 {topics: id->Topic, edges: [(pre_id, nxt_id, type)], related: int}。
    """
    d = _read_json(path)
    if not isinstance(d, dict) or "nodes" not in d or "edges" not in d:
        keys = sorted(d.keys()) if isinstance(d, dict) else ["<非dict>"]
        raise ValueError("k12kgraph math.json 结构异常, 顶层键: " + str(keys[:10]))
    topics: Dict[str, Topic] = {}
    for n in d.get("nodes", []):
        nid = str(n.get("id", ""))
        prefix = _kkg_prefix_of(nid)
        if not prefix:
            continue
        if str(n.get("label", "")) not in ("Concept", "Skill"):
            continue
        label, grade = KKG_HS_BOOKS[prefix]
        props = n.get("properties") or {}
        desc = str(props.get("definition") or props.get("description") or "")
        topics[nid] = Topic(
            key="kkg:" + nid, id=nid, source="kkg", book=label,
            name=str(n.get("name", "")), type=str(n.get("label", "")),
            subject="数学", domain="高中数学", grade=grade, description=desc)
    edges: List[Tuple[str, str, str]] = []
    related = 0
    for e in d.get("edges", []):
        et = str(e.get("type", ""))
        if et == "relates_to":
            related += 1
            continue
        if et not in ("prerequisites_for", "is_a"):
            continue
        s_id, t_id = str(e.get("source", "")), str(e.get("target", ""))
        pre_id, nxt_id = ((s_id, t_id) if et == "prerequisites_for"
                          else (t_id, s_id))
        edges.append((pre_id, nxt_id, et))
    return {"topics": topics, "edges": edges, "related": related}


def build_graph(marble_topics: List[dict], marble_deps: List[dict],
                pep_books: List[Tuple[str, List[dict], List[dict]]],
                subjects: Optional[List[str]] = None,
                k12_groups: Optional[List[dict]] = None,
                kkg_pack: Optional[dict] = None) -> Graph:
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

    if kkg_pack:
        for topic in kkg_pack["topics"].values():
            register(topic)
        for pre_id, nxt_id, et in kkg_pack["edges"]:
            if (pre_id not in kkg_pack["topics"]
                    or nxt_id not in kkg_pack["topics"]):
                g.note_drop("kkg_out_of_scope")
                continue
            g.add_edge(Edge(prereq_key="kkg:" + pre_id,
                            topic_key="kkg:" + nxt_id, strength=et))
        if kkg_pack["related"]:
            g.dropped["kkg_related"] = (g.dropped.get("kkg_related", 0)
                                        + kkg_pack["related"])
    return g


def build_graph_from_dirs(base_dir=None, subjects: Optional[List[str]] = None,
                          bands: Optional[List[str]] = None,
                          load_marble: bool = True, load_pep: bool = False,
                          load_k12: bool = True, load_kkg: bool = True) -> Graph:
    root = str(base_dir) if base_dir else paths.project_root()
    marble_topics: List[dict] = []
    marble_deps: List[dict] = []
    pep: List[Tuple[str, List[dict], List[dict]]] = []
    groups: List[dict] = []
    kkg_pack: Optional[dict] = None
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
    if load_kkg:
        kkg_path = _kkg_math_path(root)
        if kkg_path:
            kkg_pack = _kkg_extract(kkg_path)
            logger.info("k12kgraph 高中数学: %d 节点 / %d 前置边(含范围外)",
                        len(kkg_pack["topics"]), len(kkg_pack["edges"]))
    return build_graph(marble_topics, marble_deps, pep,
                       subjects if subjects is not None
                       else (DEFAULT_MARBLE_SUBJECTS if load_marble else None),
                       groups, kkg_pack)


def find_cycles(g: Graph, limit: int = 5) -> List[List[str]]:
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
                    logger.warning("图谱存在环(推断数据), 示例: %s",
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
    print("kkg 高中数学节点=" +
          str(sum(1 for n in graph.nodes.values() if n.source == "kkg")) +
          " kkg边=" + str(sum(1 for e in graph.edges
                              if e.prereq_key.startswith("kkg:"))))
    print("cycles=" + str(len(find_cycles(graph))))
'''

FILES["backend/routes/graph.py"] = r'''# -*- coding: utf-8 -*-
"""图谱路由: /api/graph(支持band学段子图) + /api/graph/recommend。

band 语义: 空或"全部"=全图; 小学/初中/高中=该学段子图。
学段归属: k12->book(学段名), marble->小学, pep->初中, kkg->高中。
- 子图边界在学段内计算; 子图 GraphService 按需构建缓存(键绑定全图 id)
- 未知 band 回退全图
"""
import logging
from typing import Dict

from fastapi import APIRouter

from backend import data_loader, graph_service, schemas, state

logger = logging.getLogger("app.api.graph")
router = APIRouter(tags=["graph"])

VALID_BANDS = ("小学", "初中", "高中")
_sub_cache: Dict[str, tuple] = {}


def _band_of(topic: data_loader.Topic) -> str:
    if topic.source == "k12":
        return topic.book
    if topic.source == "kkg":
        return "高中"
    if topic.source == "marble":
        return "小学"
    if topic.source == "pep":
        return "初中"
    return ""


def _band_service(band: str) -> graph_service.GraphService:
    full = state.get_graph_service()
    hit = _sub_cache.get(band)
    if hit and hit[0] == id(full.graph):
        return hit[1]
    g = data_loader.Graph()
    for key, t in full.graph.nodes.items():
        if _band_of(t) == band:
            g.add_node(t)
    for e in full.graph.edges:
        if e.prereq_key in g.nodes and e.topic_key in g.nodes:
            g.add_edge(e)
    sub = graph_service.GraphService(g)
    _sub_cache[band] = (id(full.graph), sub)
    logger.info("学段子图[%s]: %d 节点 / %d 边", band,
                len(g.nodes), len(g.edges))
    return sub


def _pick_service(band: str) -> graph_service.GraphService:
    if band in VALID_BANDS:
        return _band_service(band)
    return state.get_graph_service()


@router.get("/graph")
def read_graph(band: str = ""):
    gs = _pick_service(band)
    ps = state.get_progress()
    progress = ps.get_all()
    boundary = gs.compute_boundary(progress)
    payload = gs.serialize(progress, boundary)
    logger.debug("graph 响应[band=%s]: nodes=%d edges=%d boundary=%d",
                 band or "全部", len(payload["nodes"]),
                 len(payload["edges"]), len(payload["boundary"]))
    return schemas.ok(payload)


@router.get("/graph/recommend")
def recommend_path(k: int = 5, band: str = ""):
    k = max(1, min(10, k))
    gs = _pick_service(band)
    ps = state.get_progress()
    steps = gs.recommend_paths(ps.get_all(), k)
    logger.debug("路径推荐[band=%s]: %d 步", band or "全部", len(steps))
    return schemas.ok({"steps": steps})
'''

FILES["tests/test_data_loader.py"] = r'''# -*- coding: utf-8 -*-
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
        d = data_loader._read_json(path)
        exp_nodes += len(d.get("knowledge_points", []))
        for r in d.get("relations", []):
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

    print("[2/3] 前端补丁: 来源标签 + 缓存版本 v=11")
    main_js = ROOT / "web" / "js" / "main.js"
    src = main_js.read_text(encoding="utf-8")
    old = ('    var src = n.source === "pep" ? "人教初中 · " + n.book\n'
           '      : n.source === "marble" ? "Marble 小学"\n'
           '      : "K12 · " + (n.book || "");')
    new = ('    var src = n.source === "pep" ? "人教初中 · " + n.book\n'
           '      : n.source === "marble" ? "Marble 小学"\n'
           '      : n.source === "kkg" ? "人教教材图谱 · " + (n.book || "高中")\n'
           '      : "K12 · " + (n.book || "");')
    if old in src:
        main_js.write_text(src.replace(old, new), encoding="utf-8")
        print("  [OK] 来源标签已支持 kkg")
    else:
        print("  [WARN] 未找到标签代码(不影响功能, 仅弹窗来源显示), 跳过")
    html_p = ROOT / "web" / "index.html"
    html = html_p.read_text(encoding="utf-8")
    import re
    for name in ("api.js", "sim.js", "render.js", "ui.js", "main.js"):
        html = re.sub(r'src="/js/' + name + r'(\?v=\d+)?"',
                      'src="/js/' + name + '?v=11"', html)
    html_p.write_text(html, encoding="utf-8")
    print("  [OK] 缓存版本 -> v=11")

    print("[3/3] 验收")
    print("  python -m pytest tests/ -q        (预期 69 passed)")
    print("  python -m backend.data_loader     (看 kkg 高中数学节点/边数)")
    print("  重启服务, 高中视图应出现 数学 按钮(与语数团合并)")
    print("=" * 56)


if __name__ == "__main__":
    main()
