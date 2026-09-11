# -*- coding: utf-8 -*-
r'''
setup_phase8_wire.py - Sprint 8 接线: 学段切换
  1. /api/graph?band=小学|初中|高中  返回该学段子图(边界学段内精确计算)
     /api/graph/recommend?band=      推荐同样支持学段过滤
     - 子图在路由层构建并缓存, 缓存键含全图id(测试间自动失效)
     - band 归属: k12->book(学段名), marble->小学, pep->初中
     - 未知 band 回退全图(兼容旧调用)
  2. 前端: 顶栏 学段切换chips(全部/小学/初中/高中) + 切换重建星图
     + 学科chips按当前学段动态生成
  3. 测试: test_api.py 增加 3 条 band 测试(mini数据: marble=小学 pep=初中)
预期: 64 passed
'''
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CURLY = "".join(chr(c) for c in (0x201C, 0x201D, 0x2018, 0x2019))
BAD_BACKTICK = chr(96) * 3

FILES = {}

FILES["backend/routes/graph.py"] = r'''# -*- coding: utf-8 -*-
"""图谱路由: /api/graph(支持band学段子图) + /api/graph/recommend。

band 语义: 空或"全部"=全图; 小学/初中/高中=该学段子图。
- 子图边界在学段内计算(k12 关系不跨学段, 结果与全图一致且更省)
- 子图 GraphService 按需构建缓存, 缓存键绑定全图对象 id(图换则失效)
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

FILES["tests/test_api.py"] = r'''# -*- coding: utf-8 -*-
"""API 集成测试: 统一响应格式 + 进度流 + 快照恢复 + 路径推荐 + 学段子图。"""
ROOTS = {"marble:mt_A", "pep:7-up:mt_ch1_001",
         "pep:8-down:mt_ch21_001", "pep:9-up:mt_ch21_001"}
AFTER_A = (ROOTS - {"marble:mt_A"}) | {"marble:mt_B"}


def test_health(client):
    r = client.get("/api/health").json()
    assert r["code"] == 0
    assert r["data"]["status"] == "up"


def test_graph_shape(client):
    r = client.get("/api/graph").json()
    assert r["code"] == 0
    d = r["data"]
    assert len(d["nodes"]) == 8
    assert len(d["edges"]) == 4
    assert set(d["boundary"]) == ROOTS


def test_graph_band_primary(client):
    r = client.get("/api/graph", params={"band": "小学"}).json()
    assert r["code"] == 0
    d = r["data"]
    assert {n["key"] for n in d["nodes"]} == {"marble:mt_A", "marble:mt_B"}
    assert len(d["edges"]) == 1
    assert set(d["boundary"]) == {"marble:mt_A"}


def test_graph_band_middle(client):
    r = client.get("/api/graph", params={"band": "初中"}).json()
    d = r["data"]
    keys = {n["key"] for n in d["nodes"]}
    assert "pep:7-up:mt_ch1_001" in keys
    assert "marble:mt_A" not in keys
    assert len(keys) == 6
    assert set(d["boundary"]) == ROOTS - {"marble:mt_A"}


def test_graph_band_unknown_falls_back_full(client):
    r = client.get("/api/graph", params={"band": "幼儿园"}).json()
    assert len(r["data"]["nodes"]) == 8


def test_graph_band_progress_shared(client):
    client.post("/api/progress", json={
        "topic_key": "marble:mt_A", "status": "mastered"})
    r = client.get("/api/graph", params={"band": "小学"}).json()
    assert r["data"]["progress"] == {"marble:mt_A": "mastered"}
    assert set(r["data"]["boundary"]) == {"marble:mt_B"}


def test_graph_recommend(client):
    r = client.get("/api/graph/recommend").json()
    assert r["code"] == 0
    steps = r["data"]["steps"]
    assert 1 <= len(steps) <= 5
    assert "key" in steps[0] and "reason" in steps[0]
    assert steps[0]["order"] == 1
    assert set(s["key"] for s in steps) <= ROOTS


def test_graph_recommend_band(client):
    r = client.get("/api/graph/recommend", params={"band": "小学"}).json()
    steps = r["data"]["steps"]
    assert all(s["key"].startswith("marble:") for s in steps)


def test_progress_update_flow(client):
    r = client.post("/api/progress", json={
        "topic_key": "pep:7-up:mt_ch1_001",
        "status": "mastered"}).json()
    assert r["code"] == 0
    assert r["data"]["newly_unlocked"] == ["pep:7-down:mt_ch5_001"]
    assert r["data"]["no_longer_boundary"] == ["pep:7-up:mt_ch1_001"]


def test_progress_diff_on_second_update(client):
    r1 = client.post("/api/progress", json={
        "topic_key": "marble:mt_A", "status": "mastered"}).json()
    assert r1["data"]["newly_unlocked"] == ["marble:mt_B"]
    r2 = client.post("/api/progress", json={
        "topic_key": "marble:mt_B", "status": "mastered"}).json()
    assert r2["data"]["newly_unlocked"] == []
    assert r2["data"]["no_longer_boundary"] == ["marble:mt_B"]


def test_progress_rejects_bad_status(client):
    r = client.post("/api/progress", json={
        "topic_key": "marble:mt_A", "status": "genius"}).json()
    assert r["code"] == 3


def test_progress_rejects_unknown_topic(client):
    r = client.post("/api/progress", json={
        "topic_key": "pep:nope", "status": "mastered"}).json()
    assert r["code"] == 2


def test_snapshot_restore_flow(client):
    client.post("/api/progress", json={
        "topic_key": "marble:mt_A", "status": "mastered"})
    r = client.post("/api/progress/snapshot", json={"label": "t1"}).json()
    sid = r["data"]["snapshot_id"]
    client.post("/api/progress", json={
        "topic_key": "marble:mt_B", "status": "mastered"})
    r2 = client.post("/api/progress/restore",
                     json={"snapshot_id": sid}).json()
    assert r2["code"] == 0
    assert set(r2["data"]["boundary"]) == AFTER_A
    r3 = client.get("/api/progress").json()
    assert r3["data"]["progress"] == {"marble:mt_A": "mastered"}
    assert set(r3["data"]["boundary"]) == AFTER_A
'''

FILES["web/index.html"] = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>知识点星图</title>
<link rel="stylesheet" href="/css/style.css">
</head>
<body>
<header id="topbar">
  <div class="brand">知识点星图</div>
  <div class="chips" id="bandChips">
    <button class="chip band active" data-band="">全部</button>
    <button class="chip band" data-band="小学">小学</button>
    <button class="chip band" data-band="初中">初中</button>
    <button class="chip band" data-band="高中">高中</button>
  </div>
  <div class="chips" id="subjectChips"></div>
  <label class="toggle"><input type="checkbox" id="onlyBoundary">只看边界</label>
  <div class="search">
    <input id="searchInput" type="text" placeholder="搜索知识点名, 回车定位" autocomplete="off">
    <div id="searchList" class="search-list hidden"></div>
  </div>
  <div class="btns">
    <button id="btnContinue">继续学习</button>
    <button id="btnPath">推荐路径</button>
    <button id="btnSnapshot">保存快照</button>
    <button id="btnRestore">恢复快照</button>
  </div>
  <div class="stats" id="statsBar">加载中...</div>
</header>
<main id="stage">
  <canvas id="canvas"></canvas>
  <div id="tooltip" class="hidden"></div>
  <div id="legend">
    <span><i class="dot c-mastered"></i>已掌握</span>
    <span><i class="dot c-learning"></i>学习中</span>
    <span><i class="dot c-locked"></i>待学习</span>
    <span><i class="ring"></i>当前边界(现在可学)</span>
  </div>
  <div class="hint">滚轮缩放 | 拖空白平移 | 拖节点移动 | 点节点详情 | 双击空白复位</div>
  <div id="zoomCtl">
    <button id="zoomIn">+</button>
    <button id="zoomOut">-</button>
    <button id="zoomFit">复位</button>
  </div>
  <div id="loading" class="overlay">正在加载星图...</div>
</main>
<div id="modalRoot" class="hidden"></div>
<div id="toastRoot"></div>
<script src="/js/api.js"></script>
<script src="/js/sim.js"></script>
<script src="/js/render.js"></script>
<script src="/js/ui.js"></script>
<script src="/js/main.js"></script>
</body>
</html>
'''

FILES["web/js/api.js"] = r'''(function () {
  "use strict";
  function handle(res) {
    return res.json().then(function (j) {
      if (!j || typeof j.code === "undefined") {
        throw new Error("响应格式异常");
      }
      if (j.code !== 0) {
        throw new Error(j.msg || ("业务错误 " + j.code));
      }
      return j.data;
    });
  }
  function get(url) {
    return fetch(url).then(handle);
  }
  function post(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(handle);
  }
  window.API = {
    getGraph: function (band) {
      return get("/api/graph" + (band ? "?band=" + encodeURIComponent(band) : ""));
    },
    getProgress: function () { return get("/api/progress"); },
    setStatus: function (topicKey, status) {
      return post("/api/progress", { topic_key: topicKey, status: status });
    },
    snapshot: function (label) {
      return post("/api/progress/snapshot", { label: label });
    },
    listSnapshots: function () { return get("/api/progress/snapshots"); },
    restore: function (id) {
      return post("/api/progress/restore", { snapshot_id: id });
    },
    explain: function (topicKey, refresh) {
      return post("/api/ai/explain",
        { topic_key: topicKey, refresh: !!refresh });
    },
    translate: function (topicKey) {
      return post("/api/ai/translate", { topic_key: topicKey });
    },
    mistake: function (topicKey, question, myAnswer) {
      return post("/api/ai/mistake",
        { topic_key: topicKey, question: question, my_answer: myAnswer });
    },
    ask: function (topicKey, question, history) {
      return post("/api/ai/ask",
        { topic_key: topicKey, question: question, history: history });
    },
    recommend: function (k, band) {
      var q = "?k=" + (k || 5) +
        (band ? "&band=" + encodeURIComponent(band) : "");
      return get("/api/graph/recommend" + q);
    },
    practice: function (topicKey) {
      return post("/api/ai/practice", { topic_key: topicKey });
    },
    grade: function (topicKey, question, studentAnswer) {
      return post("/api/ai/grade",
        { topic_key: topicKey, question: question,
          student_answer: studentAnswer });
    }
  };
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
      if (filters.subject && n.subject !== filters.subject) return true;
      if (filters.onlyBoundary && !m.boundarySet.has(n.key)) return true;
      return false;
    };
    return m;
  }

  function rebuildSubjectChips() {
    if (!model) return;
    var seen = {}, list = [], i;
    for (i = 0; i < model.nodes.length; i++) {
      var s = model.nodes[i].subject;
      if (s && !seen[s]) { seen[s] = 1; list.push(s); }
    }
    list.sort(function (a, b) {
      return cnSubject(a).localeCompare(cnSubject(b), "zh");
    });
    var html = "<button class='chip subj active' data-subject=''>全部学科</button>";
    for (i = 0; i < list.length; i++) {
      html += "<button class='chip subj' data-subject='" +
        esc(list[i]) + "'>" + esc(cnSubject(list[i])) + "</button>";
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
      bodyHTML: "<div class='meta'>[" + esc(src) + "] " + esc(cnSubject(n.subject)) +
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
    print("  python -m pytest tests/ -q          (预期 64 passed)")
    print("  重启服务 + 浏览器 Ctrl+F5:")
    print("  1. 顶栏学段chips: 默认全部 -> 点高中/初中/小学, 星图重建, 学科chips随之刷新")
    print("  2. [继续学习]/[推荐路径] 跟随当前学段")
    print("  3. 学段内标记掌握 -> 解锁/传导动画 -> 边界顺移")
    print("  4. 单学段(约7千-1万节点)渲染应明显流畅于全图")
    print("=" * 56)


if __name__ == "__main__":
    main()
