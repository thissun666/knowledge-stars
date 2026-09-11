# -*- coding: utf-8 -*-
r'''
setup_phase8_layout.py - Sprint 8.5: 科目裁剪 + 学科合并 + 分团星图
  1. K12_EXCLUDE 更新:
     高中只留 语文/数学/英语/物理/化学/生物/日语 (去 历史/地理/思政/音乐/通用)
     初中补排美术(之前漏排); 小学保持语文数学
  2. 学科名合并: English->英语, Mathematics->数学, chips 不再重复
  3. sim.js 重写: 网格斥力(支持2万点) + 分团引力
     main.js 加载时按学科团初始化位置(语数一团/英日一团/理化生一团/政史地一团)
测试: 66 不变(真实数据对账动态计算, 排除清单变化自动适应)
'''
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
- k12 科目排除清单: 按学段裁剪(用户指定)
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

DEFAULT_MARBLE_SUBJECTS = ["English"]

# k12 科目排除清单(按学段): 高中只留语数英物化生+日语; 初中去美术等; 小学只留语数
K12_EXCLUDE = {
    "小学": {"体育", "思政", "美术"},
    "初中": {"体育", "俄语", "艺术", "美术"},
    "高中": {"体育", "科学", "美术", "艺术",
             "历史", "地理", "思政", "音乐", "通用"},
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


def build_graph(marble_topics: List[dict], marble_deps: List[dict],
                pep_books: List[Tuple[str, List[dict], List[dict]]],
                subjects: Optional[List[str]] = None,
                k12_groups: Optional[List[dict]] = None) -> Graph:
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

FILES["web/js/sim.js"] = r'''(function () {
  "use strict";
  var CELL = 46, REST = 52;
  var CW = 800, CH = 600;

  function finite(v) { return typeof v === "number" && isFinite(v); }

  function create(nodes, w, h) {
    CW = w || 800; CH = h || 600;
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      if (!finite(n.x) || !finite(n.y)) {
        var a = Math.random() * Math.PI * 2;
        var r = Math.sqrt(Math.random()) * Math.min(CW, CH) * 0.35;
        n.x = CW / 2 + Math.cos(a) * r;
        n.y = CH / 2 + Math.sin(a) * r;
      }
      if (!finite(n.vx)) n.vx = 0;
      if (!finite(n.vy)) n.vy = 0;
    }
  }

  function tick(nodes, edges, alpha) {
    if (!nodes.length) return;
    var i, j, n;
    var grid = {};
    var cs = CELL;
    for (i = 0; i < nodes.length; i++) {
      n = nodes[i];
      var k = Math.floor(n.x / cs) + "," + Math.floor(n.y / cs);
      (grid[k] || (grid[k] = [])).push(n);
    }
    var R2 = CELL * CELL;
    for (i = 0; i < nodes.length; i++) {
      var a = nodes[i];
      var gx = Math.floor(a.x / cs), gy = Math.floor(a.y / cs);
      for (var ox = -1; ox <= 1; ox++) {
        for (var oy = -1; oy <= 1; oy++) {
          var cell = grid[(gx + ox) + "," + (gy + oy)];
          if (!cell) continue;
          for (j = 0; j < cell.length; j++) {
            var b = cell[j];
            if (b === a) continue;
            var dx = a.x - b.x, dy = a.y - b.y;
            var d2 = dx * dx + dy * dy;
            if (d2 >= R2 || d2 < 0.01) continue;
            var d = Math.sqrt(d2);
            var f = ((CELL - d) / CELL) * 1.6 * alpha / d;
            var fx = dx * f, fy = dy * f;
            a.vx += fx; a.vy += fy;
            b.vx -= fx; b.vy -= fy;
          }
        }
      }
    }
    for (i = 0; i < edges.length; i++) {
      var e = edges[i];
      var p = e._na, q = e._nb;
      var ddx = q.x - p.x, ddy = q.y - p.y;
      var dist = Math.sqrt(ddx * ddx + ddy * ddy) || 0.01;
      var fs = (dist - REST) / dist * 0.06 * alpha;
      var sx = ddx * fs, sy = ddy * fs;
      p.vx += sx; p.vy += sy;
      q.vx -= sx; q.vy -= sy;
    }
    var maxV = 10;
    for (i = 0; i < nodes.length; i++) {
      n = nodes[i];
      if (finite(n._gx)) {
        n.vx += (n._gx - n.x) * 0.02 * alpha;
        n.vy += (n._gy - n.y) * 0.02 * alpha;
      }
      n.vx += (CW / 2 - n.x) * 0.0012 * alpha;
      n.vy += (CH / 2 - n.y) * 0.0012 * alpha;
      n.vx *= 0.82;
      n.vy *= 0.82;
      var sp = Math.sqrt(n.vx * n.vx + n.vy * n.vy);
      if (sp > maxV) { n.vx = n.vx / sp * maxV; n.vy = n.vy / sp * maxV; }
      n.x += n.vx;
      n.y += n.vy;
    }
  }

  window.Sim = { create: create, tick: tick };
})();
'''

FILES["web/js/main.js"] = r'''(function () {
  "use strict";
  var canvas, ctx, W = 0, H = 0, dpr = 1;
  var view = { x: 0, y: 0, k: 1 };
  var model = null;
  var alpha = 0;
  var fitQueue = [];
  var drag = null, hoverKey = null, selectedKey = null;
  var pulses = {};
  var edgePulses = [];
  var translated = {};
  var currentBand = "";
  var filters = { subject: "", onlyBoundary: false };
  var LBL = { mastered: "已掌握", learning: "学习中", locked: "待学习" };
  var SUBJECT_CN = {
    "Mathematics": "数学", "English": "英语",
    "数学": "数学", "语文": "语文", "英语": "英语", "物理": "物理",
    "化学": "化学", "生物": "生物", "历史": "历史", "地理": "地理",
    "思政": "思政", "日语": "日语", "美术": "美术", "音乐": "音乐",
    "通用": "通用技术"
  };
  var CLUSTER_OF = {
    "语文": "语数", "数学": "语数",
    "英语": "英日", "日语": "英日",
    "物理": "理化生", "化学": "理化生", "生物": "理化生",
    "思政": "政史地", "历史": "政史地", "地理": "政史地"
  };
  var DOMAIN_CN = {
    "Geometry": "几何",
    "Multiplication & Division": "乘除法",
    "Multiplication and Division": "乘除法",
    "Addition & Subtraction": "加减法",
    "Addition and Subtraction": "加减法",
    "Fractions": "分数",
    "Fractions, Decimals & Percentages": "分数小数与百分比",
    "Decimals": "小数",
    "Percentages": "百分比",
    "Measurement": "测量",
    "Place Value": "位值",
    "Number & Place Value": "数与位值",
    "Number and Place Value": "数与位值",
    "Position & Direction": "位置与方向",
    "Position and Direction": "位置与方向",
    "Statistics": "统计",
    "Algebra": "代数",
    "Ratio & Proportion": "比与比例",
    "Time": "时间",
    "Money": "货币",
    "Counting": "数数",
    "Patterns": "规律"
  };
  var EMPTY = {
    nodes: [], edges: [], progress: {}, boundarySet: new Set(),
    dimFn: function () { return false; }
  };

  function cnSubject(s) { return SUBJECT_CN[s] || s; }
  function cnDomain(d) { return DOMAIN_CN[d] || d; }
  function canonSubj(s) { return SUBJECT_CN[s] || s; }

  function esc(s) {
    return String(s).split("&").join("&amp;")
      .split("<").join("&lt;").split(">").join("&gt;");
  }

  function inlineMd(s) {
    return s.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
      .replace(/`([^`]+)`/g, "<code>$1</code>");
  }

  function mdToHtml(src) {
    var lines = esc(src).split("\n");
    var out = [], inList = false, i;
    function closeList() {
      if (inList) { out.push("</ul>"); inList = false; }
    }
    for (i = 0; i < lines.length; i++) {
      var line = lines[i];
      if (/^#{1,4}\s+/.test(line)) {
        closeList();
        out.push("<h4>" + inlineMd(line.replace(/^#{1,4}\s+/, "")) + "</h4>");
      } else if (/^[-*]\s+/.test(line)) {
        if (!inList) { out.push("<ul>"); inList = true; }
        out.push("<li>" + inlineMd(line.replace(/^[-*]\s+/, "")) + "</li>");
      } else if (line.trim()) {
        closeList();
        out.push("<p>" + inlineMd(line) + "</p>");
      }
    }
    closeList();
    return out.join("");
  }

  function statusOf(key) { return model.progress[key] || "locked"; }

  function nodeLabel(n) {
    return (translated[n.key] ? translated[n.key].name : null) || n.name;
  }

  function graphLineHTML(key) {
    var pre = model.adjIn[key] || [];
    if (!pre.length) {
      return "<div class='graphline'>图谱衔接 · 根知识点, 无前置, 可直接开学</div>";
    }
    var okN = [], missN = [], i;
    for (i = 0; i < pre.length; i++) {
      var pn = model.byKey[pre[i]];
      var label = pn ? nodeLabel(pn) : pre[i];
      if (model.progress[pre[i]] === "mastered") okN.push(label);
      else missN.push(label);
    }
    var parts = ["已接上你掌握的: <b>" + esc(okN.join("、") || "无") + "</b>"];
    if (missN.length) {
      parts.push("薄弱前置: <b style='color:#f5a623'>" +
        esc(missN.join("、")) + "</b>(讲解会照顾到)");
    }
    return "<div class='graphline'>图谱衔接 · " + parts.join(" · ") + "</div>";
  }

  function buildModel(d) {
    var nodes = d.nodes || [];
    var byKey = {}, idx = {}, adjIn = {}, i;
    for (i = 0; i < nodes.length; i++) {
      nodes[i]._cs = canonSubj(nodes[i].subject);
      byKey[nodes[i].key] = nodes[i];
      idx[nodes[i].key] = i;
    }
    var edges = [];
    for (i = 0; i < (d.edges || []).length; i++) {
      var e = d.edges[i];
      var a = idx[e.prereq], b = idx[e.topic];
      if (a === undefined || b === undefined) continue;
      edges.push({
        prereq: e.prereq, topic: e.topic,
        _a: a, _b: b, _na: nodes[a], _nb: nodes[b]
      });
      if (!adjIn[e.topic]) adjIn[e.topic] = [];
      adjIn[e.topic].push(e.prereq);
    }
    var m = {
      nodes: nodes, edges: edges, byKey: byKey, adjIn: adjIn,
      progress: d.progress || {}, boundarySet: new Set(d.boundary || [])
    };
    m.dimFn = function (n) {
      if (filters.subject && n._cs !== filters.subject) return true;
      if (filters.onlyBoundary && !m.boundarySet.has(n.key)) return true;
      return false;
    };
    return m;
  }

  function seedClusters() {
    if (!model) return;
    var groups = {}, i;
    for (i = 0; i < model.nodes.length; i++) {
      var n = model.nodes[i];
      var g = CLUSTER_OF[n._cs] || n._cs || "其他";
      (groups[g] || (groups[g] = [])).push(n);
    }
    var names = Object.keys(groups);
    var cx = W / 2, cy = H / 2;
    var R = names.length > 1 ? Math.min(W, H) * 0.32 : 0;
    for (i = 0; i < names.length; i++) {
      var ang = (i / names.length) * Math.PI * 2 - Math.PI / 2;
      var gx = cx + Math.cos(ang) * R;
      var gy = cy + Math.sin(ang) * R;
      var members = groups[names[i]];
      var spread = 10 + 6 * Math.sqrt(members.length);
      for (var j = 0; j < members.length; j++) {
        var m = members[j];
        var a2 = Math.random() * Math.PI * 2;
        var r2 = Math.sqrt(Math.random()) * spread;
        m.x = gx + Math.cos(a2) * r2;
        m.y = gy + Math.sin(a2) * r2;
        m._gx = gx;
        m._gy = gy;
      }
    }
  }

  function rebuildSubjectChips() {
    if (!model) return;
    var seen = {}, list = [], i;
    for (i = 0; i < model.nodes.length; i++) {
      var s = model.nodes[i]._cs;
      if (s && !seen[s]) { seen[s] = 1; list.push(s); }
    }
    list.sort(function (a, b) { return a.localeCompare(b, "zh"); });
    var html = "<button class='chip subj active' data-subject=''>全部学科</button>";
    for (i = 0; i < list.length; i++) {
      html += "<button class='chip subj' data-subject='" +
        esc(list[i]) + "'>" + esc(list[i]) + "</button>";
    }
    var box = document.getElementById("subjectChips");
    box.innerHTML = html;
    var chips = box.querySelectorAll(".chip.subj");
    for (i = 0; i < chips.length; i++) {
      chips[i].onclick = (function (btn) {
        return function () {
          for (var j = 0; j < chips.length; j++) {
            chips[j].classList.remove("active");
          }
          btn.classList.add("active");
          filters.subject = btn.getAttribute("data-subject") || "";
        };
      })(chips[i]);
    }
  }

  function load() {
    var el = document.getElementById("loading");
    el.classList.remove("hidden");
    el.textContent = "正在加载星图...";
    el.onclick = null;
    API.getGraph(currentBand).then(function (d) {
      model = buildModel(d);
      filters.subject = "";
      seedClusters();
      Sim.create(model.nodes, W, H);
      fitView();
      fitQueue = [0.5, 0.2, 0.08];
      alpha = 1;
      el.classList.add("hidden");
      rebuildSubjectChips();
      updateStats();
      UI.toast((currentBand || "全部学段") + " · " + model.nodes.length +
        " 个知识点 · " + model.edges.length + " 条前置关系", "ok");
    }).catch(function (err) {
      el.textContent = "加载失败: " + err.message + "(点击重试)";
      el.onclick = function () { load(); };
    });
  }

  function updateStats() {
    if (!model) return;
    var m = 0, l = 0, key;
    for (key in model.progress) {
      if (model.progress[key] === "mastered") m++;
      else if (model.progress[key] === "learning") l++;
    }
    var total = model.nodes.length;
    document.getElementById("statsBar").innerHTML =
      "共 <b>" + total + "</b> · 已掌握 <b style='color:#34c77b'>" + m +
      "</b> · 学习中 <b style='color:#f5a623'>" + l +
      "</b> · 待学习 <b>" + (total - m - l) +
      "</b> · 当前边界 <b style='color:#ffd166'>" + model.boundarySet.size + "</b>";
  }

  function setStatus(key, status) {
    if (!model) return;
    var node = model.byKey[key];
    var name = node ? nodeLabel(node) : key;
    API.setStatus(key, status).then(function (data) {
      model.progress[key] = status;
      model.boundarySet = new Set(data.boundary);
      var now = performance.now(), i, j;
      var stagger = 0;
      for (i = 0; i < data.newly_unlocked.length; i++) {
        var uk = data.newly_unlocked[i];
        var depart = now + stagger;
        var routed = false;
        for (j = 0; j < model.edges.length; j++) {
          var e2 = model.edges[j];
          if (e2.prereq === key && e2.topic === uk) {
            edgePulses.push({ from: e2._na, to: e2._nb, t0: depart });
            routed = true;
          }
        }
        pulses[uk] = routed ? (depart + 900) : now;
        if (routed) stagger += 120;
      }
      updateStats();
      var msg = LBL[status] + ": " + name;
      if (data.newly_unlocked.length) {
        msg += " · 解锁 " + data.newly_unlocked.length + " 个新知识点";
      }
      UI.toast(msg, "ok");
    }).catch(function (err) {
      UI.toast("更新失败: " + err.message, "err");
    });
  }

  function openDetail(key) {
    if (!model) return;
    var n = model.byKey[key];
    if (!n) return;
    var ov = translated[key];
    var title = nodeLabel(n);
    var desc = ov ? ov.description : (n.description || "暂无描述");
    var pre = model.adjIn[key] || [];
    var preNames = [], i;
    for (i = 0; i < pre.length && i < 6; i++) {
      var pn = model.byKey[pre[i]];
      preNames.push(pn ? nodeLabel(pn) : pre[i]);
    }
    var preText = pre.length
      ? preNames.join("、") + (pre.length > 6 ? " 等, 共 " + pre.length + " 个" : "")
      : "无(根知识点)";
    var src = n.source === "pep" ? "人教初中 · " + n.book
      : n.source === "marble" ? "Marble 小学"
      : "K12 · " + (n.book || "");
    var st = statusOf(key);
    var order = ["mastered", "learning", "locked"];
    var btns = "";
    for (i = 0; i < order.length; i++) {
      btns += "<button class='btn " + order[i] +
        (order[i] === st ? " cur" : "") + "' data-status='" + order[i] + "'>" +
        LBL[order[i]] + "</button>";
    }
    var cnBadge = ov ? "<span class='badge-cn'>已译</span>" : "";
    var transBtn = (n.source === "marble" && !ov)
      ? "<button class='btn' data-act='translate'>翻译本卡</button>" : "";
    UI.modal({
      wide: true,
      titleHTML: esc(title) + cnBadge,
      bodyHTML: "<div class='meta'>[" + esc(src) + "] " + esc(n._cs) +
        " · " + esc(cnDomain(n.domain)) + " · " +
        (n.source === "k12" ? esc(n.book) : "年级 " + (n.grade > 0 ? n.grade : "-")) +
        (model.boundarySet.has(key)
          ? " · <span style='color:#ffd166'>当前边界, 现在可学</span>" : "") +
        "</div><div class='desc'>" + esc(desc) +
        "</div><div class='pre'>前置知识: " + esc(preText) +
        "</div><div class='acts'>" + btns +
        "<button class='btn ai' data-act='explain'>AI 中文讲解</button>" +
        "<button class='btn warn' data-act='mistake'>我错了, 帮我纠错</button>" +
        transBtn + "</div>",
      onOpen: function (box) {
        var bs = box.querySelectorAll("[data-status]");
        for (var j = 0; j < bs.length; j++) {
          bs[j].onclick = (function (st2) {
            return function () {
              UI.closeModal();
              setStatus(key, st2);
            };
          })(bs[j].getAttribute("data-status"));
        }
        var ex = box.querySelector("[data-act='explain']");
        if (ex) ex.onclick = function () { openExplain(key); };
        var mk = box.querySelector("[data-act='mistake']");
        if (mk) mk.onclick = function () { openMistake(key); };
        var tr = box.querySelector("[data-act='translate']");
        if (tr) tr.onclick = function () { doTranslate(key); };
      }
    });
  }

  function openExplain(key) {
    if (!model) return;
    var n = model.byKey[key];
    var title = nodeLabel(n);
    var chatHist = [];
    var asking = false;
    UI.modal({
      wide: true,
      title: "AI 讲解 · " + title,
      bodyHTML: graphLineHTML(key) +
        "<div class='md' id='mdBox'>" +
        "<div class='snap-empty'>正在生成讲解, 首次约几秒...</div></div>" +
        "<div id='askList'></div>" +
        "<div class='ask-row'><input id='askInput' type='text' " +
        "placeholder='没懂? 就这个知识点继续追问(带上下文)'>" +
        "<button class='btn' id='askSend'>追问</button></div>" +
        "<div class='acts' style='margin-top:10px'>" +
        "<button class='btn' id='mBack'>返回详情</button>" +
        "<button class='btn' id='mPractice'>来一题练习</button>" +
        "<button class='btn ai' id='mRegen'>重新生成</button></div>",
      onOpen: function (box) {
        var mdBox = box.querySelector("#mdBox");
        var askList = box.querySelector("#askList");
        var askInput = box.querySelector("#askInput");
        function fill(promise) {
          mdBox.innerHTML = "<div class='snap-empty'>生成中...</div>";
          promise.then(function (d) {
            mdBox.innerHTML = mdToHtml(d.content);
            chatHist = [{ role: "assistant", content: d.content }];
            if (d.cached) {
              mdBox.insertAdjacentHTML("beforeend",
                "<p style='color:#8a94a6;font-size:11px'>来自缓存 · " +
                "点击重新生成可获取新版本</p>");
            }
          }).catch(function (err) {
            mdBox.innerHTML = "<div class='snap-empty'>" +
              esc(err.message) + "</div>";
            UI.toast(err.message, "err");
          });
        }
        function sendAsk() {
          var q = askInput.value.trim();
          if (!q || asking) return;
          asking = true;
          var hist = chatHist.slice(-8);
          chatHist.push({ role: "user", content: q });
          askList.insertAdjacentHTML("beforeend",
            "<div class='ask-q'>问: " + esc(q) + "</div>" +
            "<div class='ask-a' data-pending>思考中...</div>");
          askInput.value = "";
          API.ask(key, q, hist).then(function (d) {
            chatHist.push({ role: "assistant", content: d.content });
            var p = askList.querySelector("[data-pending]");
            if (p) {
              p.removeAttribute("data-pending");
              p.innerHTML = mdToHtml(d.content);
            }
            asking = false;
          }).catch(function (err) {
            var p = askList.querySelector("[data-pending]");
            if (p) {
              p.removeAttribute("data-pending");
              p.textContent = "失败: " + err.message;
            }
            asking = false;
            UI.toast(err.message, "err");
          });
        }
        box.querySelector("#askSend").onclick = sendAsk;
        askInput.addEventListener("keydown", function (e) {
          if (e.key === "Enter") sendAsk();
        });
        fill(API.explain(key, false));
        box.querySelector("#mRegen").onclick = function () {
          fill(API.explain(key, true));
        };
        box.querySelector("#mBack").onclick = function () {
          openDetail(key);
        };
        box.querySelector("#mPractice").onclick = function () {
          openPractice(key);
        };
      }
    });
  }

  function openPractice(key) {
    if (!model) return;
    var n = model.byKey[key];
    var title = nodeLabel(n);
    var lastQ = "";
    UI.modal({
      wide: true,
      title: "练习 · " + title,
      bodyHTML: graphLineHTML(key) +
        "<div id='pq' class='ask-q'>正在出题...</div>" +
        "<textarea id='pa' class='ta' style='margin-top:10px' " +
        "placeholder='在这里输入你的答案'></textarea>" +
        "<div class='acts'><button class='btn' id='pNew'>换一题</button>" +
        "<button class='btn ai' id='pGo'>提交判分</button></div>" +
        "<div class='md' id='pOut'></div>",
      onOpen: function (box) {
        var qBox = box.querySelector("#pq");
        var aBox = box.querySelector("#pa");
        var out = box.querySelector("#pOut");
        function newQuestion() {
          out.innerHTML = "";
          aBox.value = "";
          qBox.textContent = "正在出题...";
          API.practice(key).then(function (d) {
            lastQ = d.question;
            qBox.textContent = d.question;
          }).catch(function (err) {
            qBox.textContent = "出题失败: " + err.message;
            UI.toast(err.message, "err");
          });
        }
        box.querySelector("#pNew").onclick = newQuestion;
        box.querySelector("#pGo").onclick = function () {
          var a = aBox.value.trim();
          if (!lastQ) { UI.toast("题目还没出来, 稍等或点换一题", "err"); return; }
          if (!a) { UI.toast("先写下你的答案", "err"); return; }
          out.innerHTML = "<div class='snap-empty'>批改中...</div>";
          API.grade(key, lastQ, a).then(function (d) {
            out.innerHTML = mdToHtml(d.content);
            if (d.correct === true) {
              out.insertAdjacentHTML("beforeend",
                "<div class='acts'><button class='btn mastered' id='pMaster'>" +
                "很棒, 标记为已掌握</button></div>");
              var mb = out.querySelector("#pMaster");
              if (mb) mb.onclick = function () {
                UI.closeModal();
                setStatus(key, "mastered");
              };
            } else if (d.correct === false) {
              out.insertAdjacentHTML("beforeend",
                "<div class='acts'><button class='btn warn' id='pFix'>" +
                "没懂, 带着这道题去纠错</button></div>");
              var fb = out.querySelector("#pFix");
              if (fb) fb.onclick = function () {
                UI.closeModal();
                openMistake(key, lastQ, a);
              };
            }
          }).catch(function (err) {
            out.innerHTML = "<div class='snap-empty'>" +
              esc(err.message) + "</div>";
            UI.toast(err.message, "err");
          });
        };
        newQuestion();
      }
    });
  }

  function openMistake(key, prefillQ, prefillA) {
    if (!model) return;
    var n = model.byKey[key];
    var title = nodeLabel(n);
    UI.modal({
      wide: true,
      title: "错题纠错 · " + title,
      bodyHTML: "<div class='meta'>AI 会结合你的前置掌握情况定位错误, " +
        "把题目和你当时的做法贴进来:</div>" +
        "<textarea id='mkQ' class='ta' " +
        "placeholder='题目(必填), 例如: 小明有3个苹果每个100克, 一共多少克?'></textarea>" +
        "<textarea id='mkA' class='ta' " +
        "placeholder='你的答案/做法(必填), 卡在哪一步也可以写'></textarea>" +
        "<div class='acts'><button class='btn ai' id='mkGo'>生成纠错</button></div>" +
        "<div class='md' id='mkOut'></div>",
      onOpen: function (box) {
        var qBox = box.querySelector("#mkQ");
        var aBox = box.querySelector("#mkA");
        if (prefillQ) qBox.value = prefillQ;
        if (prefillA) aBox.value = prefillA;
        box.querySelector("#mkGo").onclick = function () {
          var q = qBox.value.trim();
          var a = aBox.value.trim();
          var out = box.querySelector("#mkOut");
          if (!q || !a) {
            UI.toast("题目和你的做法都要填", "err");
            return;
          }
          out.innerHTML = "<div class='snap-empty'>分析中...</div>";
          API.mistake(key, q, a).then(function (d) {
            out.innerHTML = mdToHtml(d.content);
          }).catch(function (err) {
            out.innerHTML = "<div class='snap-empty'>" +
              esc(err.message) + "</div>";
            UI.toast(err.message, "err");
          });
        };
      }
    });
  }

  function openPath() {
    API.recommend(5, currentBand).then(function (d) {
      var rows = [], i;
      for (i = 0; i < d.steps.length; i++) {
        var s = d.steps[i];
        rows.push("<div class='step'><div><b>" + s.order + ". " +
          esc(s.name) + "</b><div class='why'>" + esc(cnSubject(s.subject)) +
          " · " + esc(cnDomain(s.domain)) + " · " +
          esc(s.reason) + "</div></div>" +
          "<button class='btn' data-loc='" + s.key + "'>定位</button></div>");
      }
      UI.modal({
        wide: true,
        title: "推荐学习路径" + (currentBand ? " · " + currentBand : ""),
        bodyHTML: "<div class='meta'>由图谱算法直出(解锁杠杆排序), " +
          "非AI杜撰 · 掌握一个后再打开, 列表自动更新</div>" +
          (rows.length ? rows.join("")
            : "<div class='snap-empty'>当前没有可推荐节点(全部掌握或数据为空)</div>"),
        onOpen: function (box) {
          var bs = box.querySelectorAll("[data-loc]");
          for (var j = 0; j < bs.length; j++) {
            bs[j].onclick = (function (key) {
              return function () {
                UI.closeModal();
                gotoNode(key);
              };
            })(bs[j].getAttribute("data-loc"));
          }
        }
      });
    }).catch(function (err) {
      UI.toast("获取推荐失败: " + err.message, "err");
    });
  }

  function continueLearning() {
    API.recommend(1, currentBand).then(function (d) {
      if (!d.steps.length) {
        UI.toast("暂无可推荐节点: 全部掌握或数据为空", "err");
        return;
      }
      var s = d.steps[0];
      gotoNode(s.key);
      openDetail(s.key);
      UI.toast("图谱建议现在学: " + s.name + " · " + s.reason, "ok");
    }).catch(function (err) {
      UI.toast("获取推荐失败: " + err.message, "err");
    });
  }

  function doTranslate(key) {
    API.translate(key).then(function (d) {
      translated[key] = { name: d.name, description: d.description };
      openDetail(key);
      UI.toast(d.cached ? "已翻译(缓存)" : "翻译完成", "ok");
    }).catch(function (err) {
      UI.toast("翻译失败: " + err.message, "err");
    });
  }

  function doRestore(sid) {
    API.restore(sid).then(function (rd) {
      return API.getProgress().then(function (pd) {
        model.progress = pd.progress;
        model.boundarySet = new Set(rd.boundary);
        updateStats();
        UI.toast("已恢复 " + rd.restored + " 条进度记录", "ok");
      });
    }).catch(function (err) {
      UI.toast("恢复失败: " + err.message, "err");
    });
  }

  function openSnapshots() {
    if (!model) return;
    API.listSnapshots().then(function (d) {
      var list = d.snapshots || [];
      var rows = [];
      for (var i = 0; i < list.length; i++) {
        rows.push("<div class='snap-item'><span>#" + list[i].id + " · " +
          esc(list[i].label || "无备注") + " · " + esc(list[i].created_at) +
          "</span><button class='btn' data-sid='" + list[i].id +
          "'>恢复</button></div>");
      }
      UI.modal({
        title: "恢复快照",
        bodyHTML: rows.length ? rows.join("")
          : "<div class='snap-empty'>暂无快照, 点击上方保存快照按钮创建</div>",
        onOpen: function (box) {
          var bs = box.querySelectorAll("[data-sid]");
          for (var j = 0; j < bs.length; j++) {
            bs[j].onclick = (function (sid) {
              return function () {
                UI.confirm("恢复快照",
                  "将覆盖当前全部进度, 回滚到快照 #" + sid + ", 确认?",
                  function () { doRestore(sid); });
              };
            })(bs[j].getAttribute("data-sid"));
          }
        }
      });
    }).catch(function (err) {
      UI.toast("获取快照失败: " + err.message, "err");
    });
  }

  function pick(sx, sy) {
    if (!model) return null;
    var w = Renderer.toWorld(view, sx, sy);
    var th = Math.max(8, 12 / view.k);
    var best = null, bd = th * th;
    for (var i = 0; i < model.nodes.length; i++) {
      var n = model.nodes[i];
      var dx = n.x - w.x, dy = n.y - w.y;
      var d2 = dx * dx + dy * dy;
      if (d2 < bd) { bd = d2; best = n; }
    }
    return best;
  }

  function showTip(n, x, y) {
    var t = document.getElementById("tooltip");
    t.classList.remove("hidden");
    t.innerHTML = "<b>" + esc(nodeLabel(n)) + "</b><br>" +
      LBL[statusOf(n.key)] +
      (model.boundarySet.has(n.key)
        ? " · <span style='color:#ffd166'>现在可学</span>" : "");
    var tx = x + 16, ty = y + 16;
    if (tx > W - 250) tx = x - 260;
    t.style.left = tx + "px";
    t.style.top = ty + "px";
  }

  function hideTip() {
    document.getElementById("tooltip").classList.add("hidden");
  }

  function zoomAt(sx, sy, f) {
    var k2 = view.k * f;
    if (!isFinite(k2) || k2 <= 0) return;
    k2 = Math.max(0.05, Math.min(3, k2));
    view.x = sx - (sx - view.x) * (k2 / view.k);
    view.y = sy - (sy - view.y) * (k2 / view.k);
    view.k = k2;
  }

  function fitView() {
    if (!model) return;
    var v = Renderer.fit(model.nodes, W, H);
    if (isFinite(v.x) && isFinite(v.y) && isFinite(v.k) && v.k > 0) {
      view = v;
    }
  }

  function gotoNode(key) {
    hideSearchList();
    var n = model && model.byKey[key];
    if (!n) return;
    if (view.k < 1.1) view.k = 1.1;
    view.x = W / 2 - n.x * view.k;
    view.y = H / 2 - n.y * view.k;
    selectedKey = key;
    pulses[key] = performance.now();
    hideTip();
  }

  function hideSearchList() {
    var list = document.getElementById("searchList");
    list.classList.add("hidden");
    list.innerHTML = "";
  }

  function wireCanvas() {
    canvas.addEventListener("mousedown", function (e) {
      if (e.button !== 0 || !model) return;
      var hit = pick(e.offsetX, e.offsetY);
      var wpt = Renderer.toWorld(view, e.offsetX, e.offsetY);
      drag = {
        mode: hit ? "node" : "pan",
        key: hit ? hit.key : null,
        dx: hit ? hit.x - wpt.x : 0,
        dy: hit ? hit.y - wpt.y : 0,
        lastX: e.offsetX, lastY: e.offsetY,
        startX: e.offsetX, startY: e.offsetY, moved: false
      };
      if (drag.mode === "node") canvas.classList.add("dragging");
    });
    canvas.addEventListener("mousemove", function (e) {
      if (drag) {
        var mdx = Math.abs(e.offsetX - drag.startX) +
                  Math.abs(e.offsetY - drag.startY);
        if (mdx > 4) drag.moved = true;
        if (drag.mode === "pan") {
          view.x += e.offsetX - drag.lastX;
          view.y += e.offsetY - drag.lastY;
          drag.lastX = e.offsetX;
          drag.lastY = e.offsetY;
        } else if (drag.key && model.byKey[drag.key]) {
          var w = Renderer.toWorld(view, e.offsetX, e.offsetY);
          var n = model.byKey[drag.key];
          n.x = w.x + drag.dx;
          n.y = w.y + drag.dy;
          alpha = Math.max(alpha, 0.25);
        }
        hideTip();
        return;
      }
      var h = pick(e.offsetX, e.offsetY);
      hoverKey = h ? h.key : null;
      canvas.style.cursor = h ? "pointer" : "grab";
      if (h) showTip(h, e.offsetX, e.offsetY); else hideTip();
    });
    window.addEventListener("mouseup", function () {
      if (!drag) return;
      if (!drag.moved && drag.key) openDetail(drag.key);
      drag = null;
      canvas.classList.remove("dragging");
    });
    canvas.addEventListener("mouseleave", function () {
      hoverKey = null;
      hideTip();
    });
    canvas.addEventListener("wheel", function (e) {
      e.preventDefault();
      zoomAt(e.offsetX, e.offsetY, e.deltaY < 0 ? 1.12 : 1 / 1.12);
    }, { passive: false });
    canvas.addEventListener("dblclick", function () { fitView(); });
  }

  function wireToolbar() {
    var bands = document.querySelectorAll("#bandChips .chip.band");
    for (var i = 0; i < bands.length; i++) {
      bands[i].onclick = (function (btn) {
        return function () {
          if (btn.classList.contains("active")) return;
          for (var j = 0; j < bands.length; j++) {
            bands[j].classList.remove("active");
          }
          btn.classList.add("active");
          currentBand = btn.getAttribute("data-band") || "";
          load();
        };
      })(bands[i]);
    }
    document.getElementById("onlyBoundary").onchange = function () {
      filters.onlyBoundary = this.checked;
    };
    document.getElementById("btnContinue").onclick = continueLearning;
    document.getElementById("btnPath").onclick = openPath;
    document.getElementById("btnSnapshot").onclick = function () {
      API.snapshot("手动 " + new Date().toLocaleString()).then(function (d) {
        UI.toast("快照已保存 #" + d.snapshot_id, "ok");
      }).catch(function (err) {
        UI.toast("快照失败: " + err.message, "err");
      });
    };
    document.getElementById("btnRestore").onclick = openSnapshots;
    document.getElementById("zoomIn").onclick = function () {
      zoomAt(W / 2, H / 2, 1.25);
    };
    document.getElementById("zoomOut").onclick = function () {
      zoomAt(W / 2, H / 2, 1 / 1.25);
    };
    document.getElementById("zoomFit").onclick = fitView;
  }

  function wireSearch() {
    var input = document.getElementById("searchInput");
    var list = document.getElementById("searchList");
    function renderList(q) {
      if (!q || !model) { hideSearchList(); return; }
      var hits = [];
      for (var i = 0; i < model.nodes.length && hits.length < 8; i++) {
        var label = nodeLabel(model.nodes[i]).toLowerCase();
        if (label.indexOf(q) >= 0 ||
            model.nodes[i].name.toLowerCase().indexOf(q) >= 0) {
          hits.push(model.nodes[i]);
        }
      }
      if (!hits.length) { hideSearchList(); return; }
      var html = "";
      for (i = 0; i < hits.length; i++) {
        html += "<div class='search-item' data-key='" + hits[i].key + "'>" +
          esc(nodeLabel(hits[i])) + "</div>";
      }
      list.innerHTML = html;
      list.classList.remove("hidden");
      var items = list.querySelectorAll(".search-item");
      for (i = 0; i < items.length; i++) {
        items[i].onmousedown = (function (key) {
          return function () { gotoNode(key); };
        })(items[i].getAttribute("data-key"));
      }
    }
    input.addEventListener("input", function () {
      renderList(input.value.trim().toLowerCase());
    });
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        var first = list.querySelector(".search-item");
        if (first) gotoNode(first.getAttribute("data-key"));
      } else if (e.key === "Escape") {
        hideSearchList();
        input.blur();
      }
    });
    document.addEventListener("mousedown", function (e) {
      if (e.target !== input && !list.contains(e.target)) hideSearchList();
    });
  }

  function frame() {
    if (model && alpha > 0.004) {
      try {
        Sim.tick(model.nodes, model.edges, alpha);
      } catch (err) {
        console.error("sim tick error:", err);
        alpha = 0;
      }
      alpha *= 0.986;
      if (fitQueue.length && alpha < fitQueue[0]) {
        fitView();
        fitQueue.shift();
      }
    }
    var now = performance.now(), key;
    for (key in pulses) {
      if (now - pulses[key] > 1400) delete pulses[key];
    }
    edgePulses = edgePulses.filter(function (ep) {
      return now - ep.t0 < 2200;
    });
    try {
      Renderer.draw(ctx, view, model || EMPTY, {
        width: W, height: H, dpr: dpr, pulses: pulses, now: now,
        hoverKey: hoverKey, selectedKey: selectedKey,
        edgePulses: edgePulses
      });
    } catch (err) {
      console.error("render error:", err);
    }
    requestAnimationFrame(frame);
  }

  function onResize() {
    var stage = document.getElementById("stage");
    W = stage.clientWidth;
    H = stage.clientHeight;
    dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.floor(W * dpr));
    canvas.height = Math.max(1, Math.floor(H * dpr));
  }

  function boot() {
    canvas = document.getElementById("canvas");
    ctx = canvas.getContext("2d");
    onResize();
    window.addEventListener("resize", onResize);
    wireToolbar();
    wireCanvas();
    wireSearch();
    requestAnimationFrame(frame);
    load();
  }

  window.App = { boot: boot };
})();

App.boot();
'''


def main():
    print("项目根: " + str(ROOT))
    bad = [rel for rel, c in FILES.items()
           if BAD_BACKTICK in c or any(x in c for x in CURLY)]
    print("[1/2] 自检: " + ("[PASS] 无污染" if not bad else "[FAIL] " + str(bad)))
    if bad:
        return
    for rel, content in FILES.items():
        p = ROOT / rel
        p.write_text(content, encoding="utf-8")
        print("  [OVERWRITE] " + rel)
    print("[2/2] 验收")
    print("  python -m pytest tests/ -q     (预期 66 passed)")
    print("  python -m backend.data_loader  (确认高中科目已裁剪)")
    print("  重启服务 + 浏览器 Ctrl+F5:")
    print("  1. 点高中: 节点数应明显小于21562(约7000), 学科chips只剩语数英物化生日语")
    print("  2. 星图呈多团分布: 语数一团/英日一团/理化生一团(高中无政史地则不出现)")
    print("  3. 全部视图: 4-5团环绕, 不再是一个大圆饼")
    print("  4. 英语按钮只剩一个(marble+k12合并)")
    print("=" * 56)


if __name__ == "__main__":
    main()
