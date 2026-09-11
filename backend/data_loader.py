# -*- coding: utf-8 -*-
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

# 非科目文件: 切分工具可能留下的元数据, 不参与图谱加载
_K12_META_FILES = {"relations"}

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
    difficulty: float = 0.5


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
            if subject in _K12_META_FILES:
                continue
            if subject in K12_EXCLUDE.get(band, set()):
                continue
            out.append((band, subject, os.path.join(bdir, fname)))
    return out


def _k12_load_split(path: str) -> Tuple[List[dict], List[dict]]:
    """读取 k12 分片: 兼容 {knowledge_points, relations} 与裸数组两种形态。"""
    d = _read_json(path)
    if isinstance(d, dict):
        return (d.get("knowledge_points") or [],
                d.get("relations") or [])
    if isinstance(d, list):
        logger.warning("k12 分片为裸数组(缺少 relations): %s", path)
        return d, []
    logger.warning("k12 分片结构未知, 跳过: %s", path)
    return [], []


def _k12_difficulty(t: dict) -> float:
    """Sprint11.5: annotations.difficulty.value(0~1), 缺失/非法回退0.5。"""
    raw = t.get("annotations")
    try:
        ann = json.loads(raw) if isinstance(raw, str) else (raw or {})
        v = (ann.get("difficulty") or {}).get("value")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return max(0.0, min(1.0, float(v)))
    except (ValueError, TypeError):
        pass
    return 0.5


def _to_k12_topic(t: dict, band: str) -> Topic:
    tid = str(t.get("id", ""))
    return Topic(
        key="k12:" + tid, id=tid, source="k12", book=band,
        name=str(t.get("canonical_name", "")), type="",
        subject=str(t.get("subject", "")), domain=str(t.get("subject", "")),
        grade=K12_GRADE.get(band, 0),
        description=str(t.get("summary", "")),
        difficulty=_k12_difficulty(t),
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
            kps, rels = _k12_load_split(path)
            groups.append({"band": band, "topics": kps,
                           "relations": rels})
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


def _load_inferred_links(g: Graph, path: str = "") -> int:
    r"""Sprint11: 可选装载 data/inferred_links.json (prerequisites 推导缝合边,
    build_inferred_links.py 产物, 构建时已过 SCC 剔环)。
    文件缺失/损坏静默降级为 0 条; 逐条走 add_edge 守卫(自环/悬空/去重)。"""
    from pathlib import Path as _P
    p = _P(path) if path else (
        _P(paths.DATA_DIR) / "inferred_links.json")
    if not p.exists():
        logger.info("推断边文件缺失, 跳过: %s", str(p))
        return 0
    try:
        entries = json.loads(p.read_text(encoding="utf-8")).get("edges") or []
    except (ValueError, OSError) as exc:
        logger.warning("推断边文件损坏, 跳过: %s", exc)
        return 0
    added = 0
    for ent in entries:
        if g.add_edge(Edge(prereq_key=str(ent.get("from", "")),
                           topic_key=str(ent.get("to", "")),
                           strength="inferred",
                           reason="ai-prereq:" + str(ent.get("via", "")))):
            added += 1
    logger.info("推断边装载: %d/%d", added, len(entries))
    return added



def _load_phase2_links(g: Graph) -> int:
    r"""Sprint11阶段2: 可选装载 data/phase2_links.json (LLM消歧前置缝合边,
    build_phase2_edges.py 产物, 构建时已过分科目地板+SCC 剔环)。
    文件缺失/损坏静默降级为 0 条; 逐条走 add_edge 守卫(自环/悬空/去重)。"""
    import json as _json
    from pathlib import Path as _P
    p = _P(paths.DATA_DIR) / "phase2_links.json"
    if not p.exists():
        logger.info("phase2边文件缺失, 跳过: %s", str(p))
        return 0
    try:
        entries = _json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        logger.warning("phase2边文件损坏, 跳过: %s", exc)
        return 0
    if not isinstance(entries, list):
        return 0
    added = 0
    for ent in entries:
        if g.add_edge(Edge(prereq_key=str(ent.get("from", "")),
                           topic_key=str(ent.get("to", "")),
                           strength="inferred",
                           reason="phase2")):
            added += 1
    logger.info("phase2边装载: %d/%d", added, len(entries))
    return added



def get_graph() -> Graph:
    global _graph
    if _graph is None:
        with _lock:
            if _graph is None:
                _graph = build_graph_from_dirs()
                _load_inferred_links(_graph)
                _load_phase2_links(_graph)
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
