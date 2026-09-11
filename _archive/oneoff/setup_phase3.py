# -*- coding: utf-8 -*-
r'''
setup_phase3.py - Sprint 3 一键交付脚本(前端, 零外部依赖)
交付文件:
  web/index.html      页面骨架(顶栏/画布/图例/浮层)
  web/css/style.css   手写暗色主题
  web/js/api.js       fetch 封装(统一 code 判定)
  web/js/sim.js       力导向: 网格斥力 + 弹簧 + 领域/学段重心 + 降温
  web/js/render.js    Canvas 渲染: 边批绘制/节点三色/边界金环/脉冲/标签
  web/js/ui.js        Toast / Modal / Confirm
  web/js/main.js      装配: 数据加载/交互(缩放平移拖拽)/过滤/搜索/快照
自检: 三反引号/弯引号污染 + JS 括号平衡(逐文件)
运行前提: Sprint 2 后端已就绪(29 passed), uvicorn 服务中
'''
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FORCE = "--force" in sys.argv

CURLY = "".join(chr(c) for c in (0x201C, 0x201D, 0x2018, 0x2019))
BAD_BACKTICK = chr(96) * 3

FILES = {}

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
  <div class="chips" id="subjectChips">
    <button class="chip active" data-subject="">全部</button>
    <button class="chip" data-subject="Mathematics">数学</button>
    <button class="chip" data-subject="English">英语</button>
  </div>
  <label class="toggle"><input type="checkbox" id="onlyBoundary">只看边界</label>
  <div class="search">
    <input id="searchInput" type="text" placeholder="搜索知识点名, 回车定位" autocomplete="off">
    <div id="searchList" class="search-list hidden"></div>
  </div>
  <div class="btns">
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

FILES["web/css/style.css"] = r''':root {
  --bg: #0f1526; --panel: #171f33; --line: #2a3550;
  --text: #dfe6ff; --muted: #8a94a6;
  --green: #34c77b; --amber: #f5a623; --gray: #566179;
  --gold: #ffd166; --accent: #2f6dff;
}
* { box-sizing: border-box; }
html, body { margin: 0; height: 100%; background: var(--bg); color: var(--text);
  font: 14px/1.5 "Segoe UI", "Microsoft YaHei", Arial, sans-serif; }
body { display: flex; flex-direction: column; overflow: hidden; }
button { font-family: inherit; }
#topbar { display: flex; align-items: center; gap: 12px; padding: 9px 16px;
  background: var(--panel); border-bottom: 1px solid var(--line);
  flex-wrap: wrap; position: relative; z-index: 5; }
.brand { font-weight: 700; font-size: 16px; letter-spacing: 2px; margin-right: 6px; }
.chip { background: transparent; color: var(--muted); border: 1px solid var(--line);
  border-radius: 999px; padding: 4px 14px; cursor: pointer; font-size: 13px; }
.chip.active { background: var(--accent); border-color: var(--accent); color: #fff; }
.toggle { display: flex; align-items: center; gap: 5px; color: var(--muted);
  font-size: 13px; cursor: pointer; user-select: none; }
.search { position: relative; }
#searchInput { width: 220px; background: #0d1322; border: 1px solid var(--line);
  color: var(--text); border-radius: 6px; padding: 6px 10px; outline: none; }
#searchInput:focus { border-color: var(--accent); }
.search-list { position: absolute; top: 36px; left: 0; width: 300px;
  max-height: 280px; overflow: auto; background: var(--panel);
  border: 1px solid var(--line); border-radius: 8px; z-index: 30; }
.search-item { padding: 8px 12px; font-size: 13px; cursor: pointer;
  border-bottom: 1px solid var(--line); }
.search-item:last-child { border-bottom: none; }
.search-item:hover { background: #223055; }
.btns { display: flex; gap: 8px; }
.btns button, #zoomCtl button { background: #223055; color: var(--text);
  border: 1px solid var(--line); border-radius: 6px; padding: 6px 12px;
  cursor: pointer; font-size: 13px; }
.btns button:hover, #zoomCtl button:hover { filter: brightness(1.2); }
.stats { margin-left: auto; color: var(--muted); font-size: 13px; }
.stats b { color: var(--text); font-weight: 600; }
#stage { position: relative; flex: 1; }
#canvas { width: 100%; height: 100%; display: block; cursor: grab; }
#canvas.dragging { cursor: grabbing; }
#tooltip { position: absolute; pointer-events: none; background: rgba(13,19,34,0.95);
  border: 1px solid var(--line); border-radius: 6px; padding: 7px 11px;
  font-size: 12px; z-index: 10; max-width: 260px; }
#legend { position: absolute; left: 14px; bottom: 14px; display: flex; gap: 14px;
  align-items: center; background: rgba(23,31,51,0.88);
  border: 1px solid var(--line); border-radius: 8px; padding: 8px 14px;
  font-size: 12px; color: var(--muted); }
.dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%;
  margin-right: 5px; }
.c-mastered { background: var(--green); }
.c-learning { background: var(--amber); }
.c-locked { background: var(--gray); }
.ring { display: inline-block; width: 9px; height: 9px; border-radius: 50%;
  border: 2px solid var(--gold); margin-right: 5px; }
.hint { position: absolute; right: 14px; bottom: 14px; color: var(--muted);
  font-size: 12px; opacity: 0.85; }
#zoomCtl { position: absolute; right: 14px; top: 14px; display: flex;
  flex-direction: column; gap: 6px; }
#zoomCtl button { width: 38px; }
.overlay { position: absolute; inset: 0; display: flex; align-items: center;
  justify-content: center; background: rgba(15,21,38,0.75); z-index: 15;
  font-size: 16px; color: var(--muted); cursor: pointer; }
.hidden { display: none !important; }
#modalRoot { position: fixed; inset: 0; background: rgba(5,8,18,0.62);
  display: flex; align-items: center; justify-content: center; z-index: 40; }
.modal { width: 440px; max-width: 92vw; background: var(--panel);
  border: 1px solid var(--line); border-radius: 12px; padding: 18px 20px;
  box-shadow: 0 12px 44px rgba(0,0,0,0.5); }
.modal h3 { margin: 0 0 10px; font-size: 16px; line-height: 1.4; }
.meta { color: var(--muted); font-size: 12px; margin-bottom: 10px; }
.desc { font-size: 13px; max-height: 130px; overflow: auto; margin-bottom: 10px; }
.pre { font-size: 12px; color: var(--muted); max-height: 90px; overflow: auto;
  margin-bottom: 14px; }
.acts { display: flex; gap: 10px; justify-content: flex-end; flex-wrap: wrap; }
.btn { padding: 7px 14px; border-radius: 6px; border: 1px solid var(--line);
  background: #223055; color: var(--text); cursor: pointer; font-size: 13px; }
.btn.cur { box-shadow: 0 0 0 2px var(--text); }
.btn.mastered { background: var(--green); border-color: var(--green);
  color: #08240f; font-weight: 600; }
.btn.learning { background: var(--amber); border-color: var(--amber);
  color: #33230a; font-weight: 600; }
.btn.locked { background: #39445f; }
.snap-item { display: flex; justify-content: space-between; align-items: center;
  gap: 10px; padding: 9px 4px; border-bottom: 1px solid var(--line); font-size: 13px; }
.snap-item span { color: var(--muted); }
.snap-empty { color: var(--muted); text-align: center; padding: 18px 0; }
#toastRoot { position: fixed; top: 62px; right: 16px; z-index: 50;
  display: flex; flex-direction: column; gap: 8px; }
.toast { background: var(--panel); border: 1px solid var(--line);
  border-left: 4px solid var(--accent); border-radius: 8px; padding: 10px 14px;
  font-size: 13px; box-shadow: 0 6px 24px rgba(0,0,0,0.4);
  animation: slidein 0.22s ease; max-width: 320px; }
.toast.err { border-left-color: #e05656; }
.toast.ok { border-left-color: var(--green); }
@keyframes slidein { from { transform: translateX(24px); opacity: 0; }
  to { transform: none; opacity: 1; } }
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
    getGraph: function () { return get("/api/graph"); },
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
    }
  };
})();
'''

FILES["web/js/sim.js"] = r'''(function () {
  "use strict";
  var GOLDEN = Math.PI * (3 - Math.sqrt(5));
  var anchors = { marble: { x: 0, y: 0 }, pep: { x: 0, y: 0 } };
  var W = 1200, H = 800;

  function hashCode(s) {
    var h = 0, i;
    for (i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
    return (h >>> 0) / 4294967295;
  }

  function create(nodes, width, height) {
    W = width; H = height;
    anchors.marble.x = width * 0.32; anchors.marble.y = height * 0.5;
    anchors.pep.x = width * 0.74; anchors.pep.y = height * 0.5;
    var i;
    for (i = 0; i < nodes.length; i++) nodes[i]._idx = i;
    var sorted = nodes.slice().sort(function (a, b) {
      var ka = a.subject + "|" + a.domain + "|" + a.key;
      var kb = b.subject + "|" + b.domain + "|" + b.key;
      return ka < kb ? -1 : (ka > kb ? 1 : 0);
    });
    for (i = 0; i < sorted.length; i++) {
      var n = sorted[i];
      var a = anchors[n.source] || anchors.marble;
      var j = hashCode(n.key);
      var r = 14 * Math.sqrt(i) + 6;
      var ang = i * GOLDEN;
      n.x = a.x + Math.cos(ang) * r * (0.85 + j * 0.3);
      n.y = a.y + Math.sin(ang) * r * (0.85 + j * 0.3);
      n.vx = 0; n.vy = 0;
    }
  }

  function tick(nodes, edges, alphaT) {
    var CELL = 80, CUT2 = 6400;
    var grid = {};
    var i, j, n;
    for (i = 0; i < nodes.length; i++) {
      n = nodes[i];
      var ckey = Math.floor(n.x / CELL) + "," + Math.floor(n.y / CELL);
      if (!grid[ckey]) grid[ckey] = [];
      grid[ckey].push(n);
    }
    for (i = 0; i < nodes.length; i++) {
      n = nodes[i];
      var gx = Math.floor(n.x / CELL), gy = Math.floor(n.y / CELL);
      for (var ox = gx - 1; ox <= gx + 1; ox++) {
        for (var oy = gy - 1; oy <= gy + 1; oy++) {
          var bucket = grid[ox + "," + oy];
          if (!bucket) continue;
          for (j = 0; j < bucket.length; j++) {
            var m = bucket[j];
            if (m._idx <= n._idx) continue;
            var dx = n.x - m.x, dy = n.y - m.y;
            var d2 = dx * dx + dy * dy;
            if (d2 > CUT2 || d2 < 0.01) continue;
            var d = Math.sqrt(d2);
            var f = Math.min(2.5, 1600 / d2) * alphaT;
            var fx = dx / d * f, fy = dy / d * f;
            n.vx += fx; n.vy += fy;
            m.vx -= fx; m.vy -= fy;
          }
        }
      }
    }
    for (i = 0; i < edges.length; i++) {
      var e = edges[i];
      var a = nodes[e._a], b = nodes[e._b];
      var ddx = b.x - a.x, ddy = b.y - a.y;
      var dist = Math.sqrt(ddx * ddx + ddy * ddy) || 1;
      var fs = (dist - 75) * 0.018 * alphaT;
      var sx = ddx / dist * fs, sy = ddy / dist * fs;
      a.vx += sx; a.vy += sy;
      b.vx -= sx; b.vy -= sy;
    }
    var sums = {}, dk, s;
    for (i = 0; i < nodes.length; i++) {
      n = nodes[i];
      dk = n.subject + "|" + n.domain;
      s = sums[dk];
      if (!s) { s = sums[dk] = { x: 0, y: 0, c: 0 }; }
      s.x += n.x; s.y += n.y; s.c++;
    }
    var cx = W / 2, cy = H / 2;
    for (i = 0; i < nodes.length; i++) {
      n = nodes[i];
      s = sums[n.subject + "|" + n.domain];
      if (s) {
        n.vx += (s.x / s.c - n.x) * 0.004 * alphaT;
        n.vy += (s.y / s.c - n.y) * 0.004 * alphaT;
      }
      var an = anchors[n.source] || anchors.marble;
      n.vx += (an.x - n.x) * 0.002 * alphaT;
      n.vy += (an.y - n.y) * 0.002 * alphaT;
      n.vx += (cx - n.x) * 0.0012 * alphaT;
      n.vy += (cy - n.y) * 0.0012 * alphaT;
      n.vx *= 0.85;
      n.vy *= 0.85;
      n.x += n.vx;
      n.y += n.vy;
    }
  }

  window.Sim = { create: create, tick: tick };
})();
'''

FILES["web/js/render.js"] = r'''(function () {
  "use strict";
  var BG = "#0f1526";
  var NODE_COLOR = { mastered: "#34c77b", learning: "#f5a623", locked: "#566179" };
  var TAU = Math.PI * 2;

  function draw(ctx, view, model, o) {
    ctx.setTransform(o.dpr, 0, 0, o.dpr, 0, 0);
    ctx.fillStyle = BG;
    ctx.fillRect(0, 0, o.width, o.height);
    var k = view.k;
    ctx.translate(view.x, view.y);
    ctx.scale(k, k);
    var dim = model.dimFn;
    var i;
    ctx.lineWidth = 1 / k;
    for (var pass = 0; pass < 2; pass++) {
      ctx.strokeStyle = pass === 0 ? "rgba(148,163,220,0.22)"
                                   : "rgba(148,163,220,0.06)";
      ctx.beginPath();
      for (i = 0; i < model.edges.length; i++) {
        var e = model.edges[i];
        var dimmed = dim(e._na) || dim(e._nb);
        if (pass === 0 && dimmed) continue;
        if (pass === 1 && !dimmed) continue;
        ctx.moveTo(e._na.x, e._na.y);
        ctx.lineTo(e._nb.x, e._nb.y);
      }
      ctx.stroke();
    }
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    for (i = 0; i < model.nodes.length; i++) {
      var n = model.nodes[i];
      var sx = n.x * k + view.x, sy = n.y * k + view.y;
      if (sx < -40 || sx > o.width + 40 || sy < -40 || sy > o.height + 40) continue;
      var st = model.progress[n.key] || "locked";
      var d = dim(n);
      ctx.globalAlpha = d ? 0.15 : 1;
      ctx.beginPath();
      ctx.arc(n.x, n.y, 6, 0, TAU);
      ctx.fillStyle = NODE_COLOR[st];
      ctx.fill();
      if (d) continue;
      if (model.boundarySet.has(n.key)) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, 10, 0, TAU);
        ctx.strokeStyle = "#ffd166";
        ctx.lineWidth = 2 / k;
        ctx.stroke();
      }
      var p = o.pulses[n.key];
      if (p) {
        var age = (o.now - p) / 1000;
        if (age >= 0 && age < 1.2) {
          ctx.beginPath();
          ctx.arc(n.x, n.y, 6 + age * 26, 0, TAU);
          ctx.strokeStyle = "rgba(255,209,102," +
            (0.8 * (1 - age / 1.2)).toFixed(3) + ")";
          ctx.lineWidth = 2 / k;
          ctx.stroke();
        }
      }
      var focused = n.key === o.hoverKey || n.key === o.selectedKey;
      if (focused) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, 8.5, 0, TAU);
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = 1.5 / k;
        ctx.stroke();
      }
      if (focused || k >= 1.3) {
        ctx.fillStyle = "#d7defc";
        ctx.font = (12 / k).toFixed(2) + "px sans-serif";
        ctx.fillText(n.name, n.x, n.y - 17);
      }
    }
    ctx.globalAlpha = 1;
  }

  function fit(nodes, w, h) {
    if (!nodes.length) return { x: 0, y: 0, k: 1 };
    var minX = 1e9, minY = 1e9, maxX = -1e9, maxY = -1e9, i;
    for (i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      if (n.x < minX) minX = n.x;
      if (n.y < minY) minY = n.y;
      if (n.x > maxX) maxX = n.x;
      if (n.y > maxY) maxY = n.y;
    }
    var pad = 70;
    var bw = (maxX - minX) || 1, bh = (maxY - minY) || 1;
    var k = Math.min((w - pad * 2) / bw, (h - pad * 2) / bh);
    k = Math.max(0.05, Math.min(2.2, k));
    return { k: k, x: w / 2 - (minX + bw / 2) * k, y: h / 2 - (minY + bh / 2) * k };
  }

  function toWorld(view, sx, sy) {
    return { x: (sx - view.x) / view.k, y: (sy - view.y) / view.k };
  }

  window.Renderer = { draw: draw, fit: fit, toWorld: toWorld };
})();
'''

FILES["web/js/ui.js"] = r'''(function () {
  "use strict";
  function toast(msg, type) {
    var root = document.getElementById("toastRoot");
    var el = document.createElement("div");
    el.className = "toast " + (type || "");
    el.textContent = msg;
    root.appendChild(el);
    setTimeout(function () {
      if (el.parentNode) el.parentNode.removeChild(el);
    }, 2600);
  }

  function closeModal() {
    var root = document.getElementById("modalRoot");
    root.classList.add("hidden");
    root.innerHTML = "";
  }

  function modal(opts) {
    closeModal();
    var root = document.getElementById("modalRoot");
    root.classList.remove("hidden");
    var box = document.createElement("div");
    box.className = "modal";
    var h = document.createElement("h3");
    h.textContent = opts.title || "";
    box.appendChild(h);
    if (opts.bodyHTML) {
      var body = document.createElement("div");
      body.innerHTML = opts.bodyHTML;
      box.appendChild(body);
    }
    root.appendChild(box);
    if (opts.onOpen) opts.onOpen(box);
    return box;
  }

  function confirm(title, text, onOk) {
    modal({
      title: title,
      bodyHTML: "<p class='desc'>" + text + "</p>" +
        "<div class='acts'>" +
        "<button class='btn' id='mCancel'>取消</button>" +
        "<button class='btn mastered' id='mOk'>确认</button></div>",
      onOpen: function (box) {
        box.querySelector("#mCancel").onclick = closeModal;
        box.querySelector("#mOk").onclick = function () {
          closeModal();
          onOk();
        };
      }
    });
  }

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeModal();
  });

  window.UI = { toast: toast, modal: modal, closeModal: closeModal, confirm: confirm };
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
  var filters = { subject: "", onlyBoundary: false };
  var LBL = { mastered: "已掌握", learning: "学习中", locked: "待学习" };
  var EMPTY = {
    nodes: [], edges: [], progress: {}, boundarySet: new Set(),
    dimFn: function () { return false; }
  };

  function esc(s) {
    return String(s).split("&").join("&amp;")
      .split("<").join("&lt;").split(">").join("&gt;");
  }

  function statusOf(key) { return model.progress[key] || "locked"; }

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

  function load() {
    var el = document.getElementById("loading");
    el.classList.remove("hidden");
    el.textContent = "正在加载星图...";
    el.onclick = null;
    API.getGraph().then(function (d) {
      model = buildModel(d);
      Sim.create(model.nodes, W, H);
      view = Renderer.fit(model.nodes, W, H);
      fitQueue = [0.5, 0.2, 0.08];
      alpha = 1;
      el.classList.add("hidden");
      updateStats();
      UI.toast("已加载 " + model.nodes.length + " 个知识点 · " +
        model.edges.length + " 条前置关系", "ok");
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
    var name = node ? node.name : key;
    API.setStatus(key, status).then(function (data) {
      model.progress[key] = status;
      model.boundarySet = new Set(data.boundary);
      var now = performance.now(), i;
      for (i = 0; i < data.newly_unlocked.length; i++) {
        pulses[data.newly_unlocked[i]] = now;
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
    var pre = model.adjIn[key] || [];
    var preNames = [], i;
    for (i = 0; i < pre.length && i < 6; i++) {
      var pn = model.byKey[pre[i]];
      preNames.push(pn ? pn.name : pre[i]);
    }
    var preText = pre.length
      ? preNames.join("、") + (pre.length > 6 ? " 等, 共 " + pre.length + " 个" : "")
      : "无(根知识点)";
    var src = n.source === "pep" ? "人教初中 · " + n.book : "Marble 小学";
    var st = statusOf(key);
    var order = ["mastered", "learning", "locked"];
    var btns = "";
    for (i = 0; i < order.length; i++) {
      btns += "<button class='btn " + order[i] +
        (order[i] === st ? " cur" : "") + "' data-status='" + order[i] + "'>" +
        LBL[order[i]] + "</button>";
    }
    UI.modal({
      title: n.name,
      bodyHTML: "<div class='meta'>[" + esc(src) + "] " + esc(n.subject) +
        " · " + esc(n.domain) + " · 年级 " + (n.grade > 0 ? n.grade : "-") +
        (model.boundarySet.has(key)
          ? " · <span style='color:#ffd166'>当前边界, 现在可学</span>" : "") +
        "</div><div class='desc'>" + esc(n.description || "暂无描述") +
        "</div><div class='pre'>前置知识: " + esc(preText) +
        "</div><div class='acts'>" + btns + "</div>",
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
      }
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
    t.innerHTML = "<b>" + esc(n.name) + "</b><br>" + LBL[statusOf(n.key)] +
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
    var k2 = Math.max(0.05, Math.min(3, view.k * f));
    view.x = sx - (sx - view.x) * (k2 / view.k);
    view.y = sy - (sy - view.y) * (k2 / view.k);
    view.k = k2;
  }

  function fitView() {
    if (model) view = Renderer.fit(model.nodes, W, H);
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
    var chips = document.querySelectorAll("#subjectChips .chip");
    for (var i = 0; i < chips.length; i++) {
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
    document.getElementById("onlyBoundary").onchange = function () {
      filters.onlyBoundary = this.checked;
    };
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
    function hideList() {
      list.classList.add("hidden");
      list.innerHTML = "";
    }
    function gotoNode(key) {
      hideList();
      var n = model && model.byKey[key];
      if (!n) return;
      if (view.k < 1.1) view.k = 1.1;
      view.x = W / 2 - n.x * view.k;
      view.y = H / 2 - n.y * view.k;
      selectedKey = key;
      pulses[key] = performance.now();
      hideTip();
    }
    function renderList(q) {
      if (!q || !model) { hideList(); return; }
      var hits = [];
      for (var i = 0; i < model.nodes.length && hits.length < 8; i++) {
        if (model.nodes[i].name.toLowerCase().indexOf(q) >= 0) {
          hits.push(model.nodes[i]);
        }
      }
      if (!hits.length) { hideList(); return; }
      var html = "";
      for (i = 0; i < hits.length; i++) {
        html += "<div class='search-item' data-key='" + hits[i].key + "'>" +
          esc(hits[i].name) + "</div>";
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
        hideList();
        input.blur();
      }
    });
    document.addEventListener("mousedown", function (e) {
      if (e.target !== input && !list.contains(e.target)) hideList();
    });
  }

  function frame() {
    if (model && alpha > 0.004) {
      Sim.tick(model.nodes, model.edges, alpha);
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
    Renderer.draw(ctx, view, model || EMPTY, {
      width: W, height: H, dpr: dpr, pulses: pulses, now: now,
      hoverKey: hoverKey, selectedKey: selectedKey
    });
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


def js_balance_ok(src: str) -> bool:
    """粗校验: 去掉注释与字符串后括号应成对(仅捕捉低级语法损伤)。"""
    s = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    s = re.sub(r"(?m)//[^\n]*", " ", s)
    s = re.sub(r'"(?:\\.|[^"\\])*"', " ", s)
    pairs = {"(": ")", "[": "]", "{": "}"}
    stack = []
    for ch in s:
        if ch in pairs:
            stack.append(pairs[ch])
        elif ch in ")]}":
            if not stack or stack.pop() != ch:
                return False
    return not stack


def write_files():
    print("[1/4] 写入前端文件（不存在才写, --force 可覆盖）...")
    ok_n, skip_n = 0, 0
    for rel, content in FILES.items():
        p = ROOT / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists() and not FORCE:
            print("  [SKIP] " + rel + "（已存在）")
            skip_n += 1
            continue
        p.write_text(content, encoding="utf-8")
        print("  [OK]   " + rel)
        ok_n += 1
    print("  合计: 写入 " + str(ok_n) + ", 跳过 " + str(skip_n))


def self_check():
    print("[2/4] 自检报告")
    bad_b, bad_q, bal = [], [], []
    for rel, content in FILES.items():
        if BAD_BACKTICK in content:
            bad_b.append(rel)
        if any(c in content for c in CURLY):
            bad_q.append(rel)
        if rel.endswith(".js") and not js_balance_ok(content):
            bal.append(rel)
    print("  [PASS] 无三反引号污染" if not bad_b
          else "  [FAIL] 三反引号污染: " + ", ".join(bad_b))
    print("  [PASS] 无弯引号污染" if not bad_q
          else "  [FAIL] 弯引号污染: " + ", ".join(bad_q))
    print("  [PASS] JS 括号平衡 x" + str(len([f for f in FILES if f.endswith(".js")]))
          if not bal else "  [FAIL] JS 括号不平衡: " + ", ".join(bal))
    for p in sorted((ROOT / "web").rglob("*")):
        if p.is_file():
            print("  - web/" + str(p.relative_to(ROOT / "web")).replace("\\", "/")
                  + " (" + str(p.stat().st_size) + " B)")


def next_steps():
    print("[3/4] 后端回归(应仍为 29 passed)")
    print("  python -m pytest tests/ -q")
    print("[4/4] 浏览器验收(uvicorn 运行中则直接 Ctrl+F5 刷新)")
    print("  http://127.0.0.1:8000/")
    print("=" * 56)


if __name__ == "__main__":
    print("项目根: " + str(ROOT))
    write_files()
    self_check()
    next_steps()
