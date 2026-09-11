# -*- coding: utf-8 -*-
r'''
setup_phase7.py - Sprint 7 打磨包(活性 + 弹窗 + 亮点可视化)
  1. 星点闪烁层: 958 点各自相位呼吸, 收敛后星图不再"死气沉沉"(零成本)
  2. 弹窗宽幅化: 讲解/练习/纠错/路径 max 880px, 正文 14px/1.75
  3. 亮点台面化:
     - 顶栏[继续学习]: 图谱推荐第一站 -> 飞过去 -> 开详情(核心卖点前置)
     - 讲解/练习弹窗顶部"图谱衔接"行(确定性渲染, 非AI表演)
     - 讲解 prompt 要求开头衔接已掌握前置
测试: 56 不变(prompt 微调不破坏断言)
'''
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

CURLY = "".join(chr(c) for c in (0x201C, 0x201D, 0x2018, 0x2019))
BAD_BACKTICK = chr(96) * 3

FILES = {}

FILES["backend/ai_service.py"] = r'''# -*- coding: utf-8 -*-
"""glm-4-flash 客户端: OpenAI 兼容接口, 超时+有限重试+显式降级。

  - call_llm 支持单轮(prompt, system)与多轮(messages=[...])
  - 出题/批改/纠错/追问 prompt 注入前置掌握情况(差异化核心)
  - 讲解 prompt 要求开头自然衔接已掌握前置(图谱上下文肉眼可见)
"""
import logging
import re
import time
from typing import List, Optional, Tuple

import httpx

from backend import config

logger = logging.getLogger("app.ai")

TIMEOUT = 45.0
MAX_RETRY = 2

SUBJECT_CN = {"Mathematics": "数学", "English": "英语"}

_PLACEHOLDERS = {"", "略", "changeme", "todo",
                 "your-api-key-here", "yourapikey"}


class AIError(RuntimeError):
    """AI 调用失败(未配置/网络/响应异常), 由路由层转 code=6。"""


def is_configured() -> bool:
    key = (config.AI_API_KEY or "").strip().lower()
    if key.startswith("your-api-key"):
        return False
    return key not in _PLACEHOLDERS


def call_llm(content: str = "", system: str = "",
             messages: Optional[List[dict]] = None) -> str:
    if not is_configured():
        raise AIError("AI_API_KEY 未配置, 请在 .env 填入智谱 Key 后重启服务")
    if messages is None:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": content})
    body = {"model": config.AI_MODEL, "messages": messages,
            "temperature": 0.6}
    headers = {"Authorization": "Bearer " + config.AI_API_KEY.strip()}
    url = config.AI_BASE_URL.rstrip("/") + "/chat/completions"
    last: Optional[Exception] = None
    for attempt in range(MAX_RETRY + 1):
        try:
            resp = httpx.post(url, json=body, headers=headers,
                              timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            if content and content.strip():
                return content.strip()
            raise ValueError("空响应")
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            last = exc
            logger.warning("LLM 调用失败(第 %d 次): %s", attempt + 1, exc)
            if attempt < MAX_RETRY:
                time.sleep(0.6 * (attempt + 1))
    raise AIError("AI 调用失败: " + str(last))


def _prereq_lines(prereq_status: List[Tuple[str, bool]]) -> str:
    if not prereq_status:
        return "无(根知识点)"
    return "、".join(
        n + "(" + ("已掌握" if ok else "未掌握") + ")"
        for n, ok in prereq_status)


def build_explain_prompt(name: str, subject: str, domain: str,
                         grade: int, description: str,
                         prereq_names: List[str]) -> str:
    subj = SUBJECT_CN.get(subject, subject)
    g = grade if grade > 0 else 6
    pre = "、".join(prereq_names) if prereq_names else "无(根知识点)"
    return (
        "请为学生讲解知识点, 用简体中文。\n"
        "学科: " + subj + "\n年级: " + str(g) + "\n领域: " + domain + "\n"
        "知识点: " + name + "\n内容描述: " + (description or "无") +
        "\n前置知识: " + pre + "\n"
        "要求: 如果前置知识里标注了学生已掌握的内容, 开头先用一句话"
        "自然衔接它(例如 借助你学过的xx), 再进入正文。\n"
        "输出 Markdown, 依次包含四个小节(用 ## 开头):\n"
        "## 是什么\n## 生活中的例子\n## 常见误区\n## 小练习\n"
        "小练习只出 1 道并附答案与解析。"
        "总字数不超过 400 字, 语言贴近 " + str(g) + " 年级学生, 多用比喻。")


def build_translate_prompt(name: str, domain: str,
                           description: str) -> str:
    return (
        "将下面的中小学知识点信息翻译成简体中文。\n"
        "输出格式严格为两行, 不要输出任何其他内容:\n"
        "名称: <知识点中文名, 简洁准确, 不超过 15 个字>\n"
        "描述: <描述译文, 通俗易懂>\n\n"
        "知识点名: " + name + "\n领域: " + domain +
        "\n描述: " + (description or "无"))


def build_mistake_prompt(name: str, subject: str, domain: str, grade: int,
                         description: str,
                         prereq_status: List[Tuple[str, bool]],
                         question: str, my_answer: str) -> str:
    subj = SUBJECT_CN.get(subject, subject)
    g = grade if grade > 0 else 6
    return (
        "学生做错了与知识点相关的题, 请针对性纠错, 用简体中文。\n"
        "知识点: " + name + "(学科:" + subj + ", 年级" + str(g) +
        ", 领域:" + domain + ")\n"
        "内容描述: " + (description or "无") + "\n"
        "前置知识掌握情况: " + _prereq_lines(prereq_status) + "\n\n"
        "题目: " + question + "\n学生的答案/做法: " + my_answer + "\n\n"
        "输出 Markdown, 依次四个小节(用 ## 开头):\n"
        "## 错在哪一步\n## 正确思路\n## 需要回去补的基础\n"
        "## 巩固练习(1道, 附答案)\n"
        "要求: 已掌握的前置不要重讲; 未掌握的前置要明确指出要回去补; "
        "总字数300以内, 贴合" + str(g) + "年级认知。")


def build_ask_system(name: str, subject: str, domain: str, grade: int,
                     prereq_status: List[Tuple[str, bool]]) -> str:
    subj = SUBJECT_CN.get(subject, subject)
    g = grade if grade > 0 else 6
    return (
        "你是一名耐心的中小学一对一辅导老师。学生正在学习《" + name + "》"
        "(学科:" + subj + ", 年级" + str(g) + ", 领域:" + domain + ")。"
        "学生已看过该知识点的讲解, 现在就同一知识点继续追问。"
        "前置知识掌握情况: " + _prereq_lines(prereq_status) + "。"
        "回答规则: 简体中文; 先直接回答再简短解释; 贴合" + str(g) +
        "年级认知, 多用比喻; 不确定时诚实说明; 单次回答200字以内。")


def build_practice_prompt(name: str, subject: str, domain: str,
                          grade: int, description: str,
                          prereq_names: List[str]) -> str:
    subj = SUBJECT_CN.get(subject, subject)
    g = grade if grade > 0 else 6
    pre = "、".join(prereq_names) if prereq_names else "无(根知识点)"
    return (
        "为下面的知识点出 1 道练习题, 用简体中文。\n"
        "学科: " + subj + "\n年级: " + str(g) + "\n领域: " + domain + "\n"
        "知识点: " + name + "\n内容描述: " + (description or "无") +
        "\n前置知识: " + pre + "\n\n"
        "要求:\n"
        "1. 只输出题目本身, 不要输出答案、解析或任何提示\n"
        "2. 贴合" + str(g) + "年级认知, 尽量结合生活场景\n"
        "3. 答案应简短(一个数/一个词/一句话), 便于学生键盘输入\n"
        "4. 直接输出题目, 不要加 题目: 之类的前缀")


def build_grade_prompt(name: str, subject: str, domain: str, grade: int,
                       description: str,
                       prereq_status: List[Tuple[str, bool]],
                       question: str, student_answer: str) -> str:
    subj = SUBJECT_CN.get(subject, subject)
    g = grade if grade > 0 else 6
    return (
        "批改学生的作答, 用简体中文。\n"
        "知识点: " + name + "(学科:" + subj + ", 年级" + str(g) +
        ", 领域:" + domain + ")\n"
        "内容描述: " + (description or "无") + "\n"
        "前置知识掌握情况: " + _prereq_lines(prereq_status) + "\n\n"
        "题目: " + question + "\n学生的作答: " + student_answer + "\n\n"
        "先自己解题, 再对照学生作答判分。输出格式严格为:\n"
        "判定: 正确 或 判定: 错误\n"
        "正确答案: <正确答案>\n"
        "解析: <判分理由; 若错误, 指出错在哪一步、建议回去补什么, 200字以内>")


def parse_judgement(text: str) -> Optional[bool]:
    m = re.search(r"判定[:：]\s*(正确|错误)", text)
    if m:
        return m.group(1) == "正确"
    return None


def parse_translate(text: str,
                    fallback_name: str,
                    fallback_desc: str) -> Tuple[str, str]:
    name, desc = fallback_name, fallback_desc
    m1 = re.search(r"名称[:：]\s*(.+)", text)
    if m1:
        name = m1.group(1).strip() or name
    m2 = re.search(r"描述[:：]\s*(.+)", text, re.S)
    if m2:
        desc = m2.group(1).strip() or desc
    elif text.strip():
        desc = text.strip()
    return name, desc
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
    root.onclick = function (e) {
      if (e.target === root) closeModal();
    };
    var box = document.createElement("div");
    box.className = "modal" + (opts.wide ? " wide" : "");
    var h = document.createElement("h3");
    if (opts.titleHTML) {
      h.innerHTML = opts.titleHTML;
    } else {
      h.textContent = opts.title || "";
    }
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

FILES["web/js/render.js"] = r'''(function () {
  "use strict";
  var BG = "#0f1526";
  var NODE_COLOR = { mastered: "#34c77b", learning: "#f5a623", locked: "#566179" };
  var TAU = Math.PI * 2;

  function ok(v) { return typeof v === "number" && isFinite(v); }

  function draw(ctx, view, model, o) {
    if (!ok(view.k) || view.k <= 0) {
      view.k = 1; view.x = o.width / 2; view.y = o.height / 2;
    }
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
        if (!ok(e._na.x) || !ok(e._na.y) || !ok(e._nb.x) || !ok(e._nb.y)) {
          continue;
        }
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
      if (!ok(n.x) || !ok(n.y)) continue;
      var sx = n.x * k + view.x, sy = n.y * k + view.y;
      if (sx < -40 || sx > o.width + 40 || sy < -40 || sy > o.height + 40) continue;
      var st = model.progress[n.key] || "locked";
      var d = dim(n);
      var tw = Math.sin(o.now * 0.0015 + i * 2.399963);
      var r = 6 + tw * 0.7;
      ctx.globalAlpha = d ? 0.15 : (0.85 + 0.15 * tw);
      ctx.beginPath();
      ctx.arc(n.x, n.y, r, 0, TAU);
      ctx.fillStyle = NODE_COLOR[st] || NODE_COLOR.locked;
      ctx.fill();
      if (d) continue;
      if (model.boundarySet.has(n.key)) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, 10 + tw * 0.5, 0, TAU);
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
      if (!ok(n.x) || !ok(n.y)) continue;
      if (n.x < minX) minX = n.x;
      if (n.y < minY) minY = n.y;
      if (n.x > maxX) maxX = n.x;
      if (n.y > maxY) maxY = n.y;
    }
    if (minX > maxX || minY > maxY) return { x: 0, y: 0, k: 1 };
    var pad = 70;
    var bw = (maxX - minX) || 1, bh = (maxY - minY) || 1;
    var k = Math.min((w - pad * 2) / bw, (h - pad * 2) / bh);
    k = Math.max(0.05, Math.min(2.2, k));
    var x = w / 2 - (minX + bw / 2) * k, y = h / 2 - (minY + bh / 2) * k;
    if (!ok(x) || !ok(y) || !ok(k) || k <= 0) return { x: 0, y: 0, k: 1 };
    return { k: k, x: x, y: y };
  }

  function toWorld(view, sx, sy) {
    return { x: (sx - view.x) / view.k, y: (sy - view.y) / view.k };
  }

  window.Renderer = { draw: draw, fit: fit, toWorld: toWorld };
})();
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
button, input, textarea { font-family: inherit; }
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
#btnContinue { background: var(--accent); border-color: var(--accent);
  color: #fff; font-weight: 600; }
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
.modal { width: 480px; max-width: 92vw; background: var(--panel);
  border: 1px solid var(--line); border-radius: 12px; padding: 18px 20px;
  box-shadow: 0 12px 44px rgba(0,0,0,0.5); max-height: 86vh; overflow: auto; }
.modal.wide { width: min(880px, 94vw); }
.modal h3 { margin: 0 0 10px; font-size: 16px; line-height: 1.4; }
.meta { color: var(--muted); font-size: 12px; margin-bottom: 10px; }
.graphline { font-size: 12px; color: var(--muted);
  background: rgba(52,199,123,0.08); border: 1px dashed rgba(52,199,123,0.35);
  border-radius: 6px; padding: 6px 10px; margin-bottom: 10px; }
.graphline b { color: var(--green); font-weight: 600; }
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
.btn.ai { background: linear-gradient(135deg, #5b6cff, #2f6dff);
  border-color: #5b6cff; color: #fff; font-weight: 600; }
.btn.warn { background: #6e3a3a; border-color: #96504f; color: #ffe; }
.badge-cn { display: inline-block; margin-left: 8px; font-size: 11px;
  color: var(--green); border: 1px solid var(--green);
  border-radius: 4px; padding: 0 5px; vertical-align: 2px; }
.md h4 { margin: 12px 0 6px; color: var(--gold); font-size: 14px; }
.md p { margin: 6px 0; font-size: 13px; }
.modal.wide .md p, .modal.wide .md li { font-size: 14px; line-height: 1.75; }
.md ul { margin: 4px 0; padding-left: 4px; }
.md li { margin: 3px 0 3px 14px; font-size: 13px; }
.md code { background: #0d1322; padding: 1px 5px; border-radius: 4px;
  font-size: 12px; }
.ta { width: 100%; min-height: 56px; background: #0d1322; color: var(--text);
  border: 1px solid var(--line); border-radius: 6px; padding: 8px 10px;
  font-size: 13px; line-height: 1.5; margin-bottom: 8px; resize: vertical;
  outline: none; }
.ta:focus { border-color: var(--accent); }
.ask-q { background: #1b2743; border-left: 3px solid var(--accent);
  padding: 6px 10px; border-radius: 6px; font-size: 13px;
  margin: 8px 0 4px; }
.ask-a { font-size: 13px; margin: 4px 0 10px; color: var(--text); }
.ask-row { display: flex; gap: 8px; margin-top: 10px; }
.ask-row input { flex: 1; background: #0d1322; border: 1px solid var(--line);
  color: var(--text); border-radius: 6px; padding: 7px 10px; outline: none; }
.ask-row input:focus { border-color: var(--accent); }
.step { display: flex; justify-content: space-between; align-items: center;
  gap: 10px; padding: 9px 4px; border-bottom: 1px solid var(--line);
  font-size: 13px; }
.step .why { color: var(--muted); font-size: 12px; }
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

FILES["web/js/main.js"] = r'''(function () {
  "use strict";
  var canvas, ctx, W = 0, H = 0, dpr = 1;
  var view = { x: 0, y: 0, k: 1 };
  var model = null;
  var alpha = 0;
  var fitQueue = [];
  var drag = null, hoverKey = null, selectedKey = null;
  var pulses = {};
  var translated = {};
  var filters = { subject: "", onlyBoundary: false };
  var LBL = { mastered: "已掌握", learning: "学习中", locked: "待学习" };
  var SUBJECT_CN = { "Mathematics": "数学", "English": "英语" };
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

  function load() {
    var el = document.getElementById("loading");
    el.classList.remove("hidden");
    el.textContent = "正在加载星图...";
    el.onclick = null;
    API.getGraph().then(function (d) {
      model = buildModel(d);
      Sim.create(model.nodes, W, H);
      fitView();
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
    var name = node ? nodeLabel(node) : key;
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
    var src = n.source === "pep" ? "人教初中 · " + n.book : "Marble 小学";
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
        " · " + esc(cnDomain(n.domain)) + " · 年级 " + (n.grade > 0 ? n.grade : "-") +
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
    API.recommend(5).then(function (d) {
      var rows = [], i;
      for (i = 0; i < d.steps.length; i++) {
        var s = d.steps[i];
        rows.push("<div class='step'><div><b>" + s.order + ". " +
          esc(s.name) + "</b><div class='why'>" + esc(cnSubject(s.subject)) +
          " · " + esc(cnDomain(s.domain)) + " · 年级 " + s.grade +
          " · " + esc(s.reason) + "</div></div>" +
          "<button class='btn' data-loc='" + s.key + "'>定位</button></div>");
      }
      UI.modal({
        wide: true,
        title: "推荐学习路径",
        bodyHTML: "<div class='meta'>由图谱算法直出(解锁杠杆+年级排序), " +
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
    API.recommend(1).then(function (d) {
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
    try {
      Renderer.draw(ctx, view, model || EMPTY, {
        width: W, height: H, dpr: dpr, pulses: pulses, now: now,
        hoverKey: hoverKey, selectedKey: selectedKey
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


def write_files():
    print("[1/3] 写入交付文件(直接落盘)...")
    for rel, content in FILES.items():
        p = ROOT / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        existed = p.exists()
        p.write_text(content, encoding="utf-8")
        print("  [" + ("OVERWRITE" if existed else "NEW") + "] " + rel)


def self_check():
    print("[2/3] 自检报告")
    bad = []
    for rel, content in FILES.items():
        if BAD_BACKTICK in content or any(c in content for c in CURLY):
            bad.append(rel)
    print("  [PASS] 无三反引号/弯引号污染" if not bad
          else "  [FAIL] " + ", ".join(bad))


def next_steps():
    print("[3/3] 验收")
    print("  python -m pytest tests/ -q          (预期 56 passed)")
    print("  重启服务 + 浏览器 Ctrl+F5:")
    print("  1. 收敛后星点持续微闪(活的星空), 布局仍稳定可读")
    print("  2. 顶栏[继续学习](蓝色高亮): 一键飞到图谱推荐节点+开详情+toast说明理由")
    print("  3. AI讲解/练习弹窗: 顶部绿色虚线框[图谱衔接](已掌握/薄弱前置)")
    print("  4. 讲解正文 14px 宽幅阅读; 讲解开头会衔接你已掌握的前置")
    print("  5. 纠错/路径弹窗同为宽幅")
    print("=" * 56)


if __name__ == "__main__":
    print("项目根: " + str(ROOT))
    write_files()
    self_check()
    next_steps()
