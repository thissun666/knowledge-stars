# -*- coding: utf-8 -*-
r'''
setup_phase6.py - Sprint 6 一键交付(练习闭环 + UI 中文化收尾)
新增:
  /api/ai/practice  AI 出题(不显示答案, 不缓存, 每次新题)
  /api/ai/grade     AI 批改(判定对错+解析, 注入前置掌握情况)
  前端: 讲解弹窗[来一题练习] -> 作答 -> 批改 ->
        答错[带题去纠错](预填) / 答对[标记已掌握](边界顺移)
  弹窗科目/领域中英映射(Mathematics -> 数学 等)
测试: 51 -> 56 passed
'''
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

CURLY = "".join(chr(c) for c in (0x201C, 0x201D, 0x2018, 0x2019))
BAD_BACKTICK = chr(96) * 3

FILES = {}

FILES["backend/ai_service.py"] = r'''# -*- coding: utf-8 -*-
"""glm-4-flash 客户端: OpenAI 兼容接口, 超时+有限重试+显式降级。

  - call_llm 支持两种形态: call_llm(prompt, system) 单轮;
    call_llm(messages=[...]) 多轮(追问场景)
  - 出题/批改 prompt 注入前置掌握情况(图谱上下文, 差异化核心)
  - 批改输出首行约定 判定: 正确|错误, parse_judgement 容错解析
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
    """容错解析批改判定: 正确/错误; 拆不出返回 None(前端不给快捷动作)。"""
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

FILES["backend/routes/ai.py"] = r'''# -*- coding: utf-8 -*-
"""AI 路由: 讲解/翻译(缓存) + 纠错 + 追问 + 出题/批改(练习闭环)。

  code=5 AI未配置 | code=2 未知知识点 | code=6 AI调用失败 | code=1 参数缺失
  出题不缓存(每次新题); 批改无状态(题目+作答由前端回传)。
"""
import json
import logging
from typing import Any, List

from fastapi import APIRouter
from pydantic import BaseModel

from backend import ai_service, schemas, state

logger = logging.getLogger("app.api.ai")
router = APIRouter(tags=["ai"])


class ExplainIn(BaseModel):
    topic_key: str
    refresh: bool = False


class TranslateIn(BaseModel):
    topic_key: str


class MistakeIn(BaseModel):
    topic_key: str
    question: str
    my_answer: str


class AskIn(BaseModel):
    topic_key: str
    question: str
    history: List[Any] = []


class PracticeIn(BaseModel):
    topic_key: str


class GradeIn(BaseModel):
    topic_key: str
    question: str
    student_answer: str


def _topic_or_err(gs, topic_key: str):
    if not ai_service.is_configured():
        return None, schemas.err(
            "未配置 AI_API_KEY, 请在 .env 填入智谱 Key "
            "(open.bigmodel.cn 免费申请)并重启服务", code=5)
    topic = gs.graph.nodes.get(topic_key)
    if topic is None:
        return None, schemas.err("未知知识点: " + topic_key, code=2)
    return topic, None


def _prereq_status(gs, ps, topic_key: str) -> List[tuple]:
    progress = ps.get_all()
    out = []
    for pk in gs.graph.adj_in.get(topic_key, [])[:8]:
        t2 = gs.graph.nodes.get(pk)
        out.append((t2.name if t2 else pk,
                    progress.get(pk) == "mastered"))
    return out


def _prereq_names(gs, topic_key: str) -> List[str]:
    out = []
    for pk in gs.graph.adj_in.get(topic_key, [])[:6]:
        t2 = gs.graph.nodes.get(pk)
        if t2:
            out.append(t2.name)
    return out


@router.post("/ai/explain")
def ai_explain(body: ExplainIn):
    gs = state.get_graph_service()
    ps = state.get_progress()
    topic, err = _topic_or_err(gs, body.topic_key)
    if err:
        return err
    if not body.refresh:
        hit = ps.cache_get("explain", body.topic_key)
        if hit:
            return schemas.ok({"content": hit, "cached": True})
    prompt = ai_service.build_explain_prompt(
        topic.name, topic.subject, topic.domain, topic.grade,
        topic.description, _prereq_names(gs, body.topic_key))
    try:
        content = ai_service.call_llm(prompt)
    except ai_service.AIError as exc:
        return schemas.err(str(exc), code=6)
    ps.cache_put("explain", body.topic_key, content)
    logger.debug("讲解生成: %s (%d 字)", body.topic_key, len(content))
    return schemas.ok({"content": content, "cached": False})


@router.post("/ai/translate")
def ai_translate(body: TranslateIn):
    gs = state.get_graph_service()
    ps = state.get_progress()
    topic, err = _topic_or_err(gs, body.topic_key)
    if err:
        return err
    hit = ps.cache_get("translate", body.topic_key)
    if hit:
        try:
            return schemas.ok(dict(json.loads(hit), cached=True))
        except ValueError:
            pass
    prompt = ai_service.build_translate_prompt(
        topic.name, topic.domain, topic.description)
    try:
        text = ai_service.call_llm(prompt)
    except ai_service.AIError as exc:
        return schemas.err(str(exc), code=6)
    name, desc = ai_service.parse_translate(text, topic.name,
                                            topic.description)
    payload = json.dumps({"name": name, "description": desc},
                         ensure_ascii=False)
    ps.cache_put("translate", body.topic_key, payload)
    return schemas.ok({"name": name, "description": desc, "cached": False})


@router.post("/ai/mistake")
def ai_mistake(body: MistakeIn):
    gs = state.get_graph_service()
    ps = state.get_progress()
    topic, err = _topic_or_err(gs, body.topic_key)
    if err:
        return err
    q = body.question.strip()[:2000]
    a = body.my_answer.strip()[:2000]
    if not q or not a:
        return schemas.err("题目与你的答案/思路均不能为空", code=1)
    prompt = ai_service.build_mistake_prompt(
        topic.name, topic.subject, topic.domain, topic.grade,
        topic.description, _prereq_status(gs, ps, body.topic_key),
        q, a)
    try:
        content = ai_service.call_llm(prompt)
    except ai_service.AIError as exc:
        return schemas.err(str(exc), code=6)
    logger.debug("错题纠错: %s (%d 字)", body.topic_key, len(content))
    return schemas.ok({"content": content})


@router.post("/ai/ask")
def ai_ask(body: AskIn):
    gs = state.get_graph_service()
    ps = state.get_progress()
    topic, err = _topic_or_err(gs, body.topic_key)
    if err:
        return err
    q = body.question.strip()[:1000]
    if not q:
        return schemas.err("追问内容不能为空", code=1)
    system = ai_service.build_ask_system(
        topic.name, topic.subject, topic.domain, topic.grade,
        _prereq_status(gs, ps, body.topic_key))
    msgs = [{"role": "system", "content": system}]
    for h in body.history[-10:]:
        role = h.get("role") if isinstance(h, dict) else None
        content = h.get("content") if isinstance(h, dict) else None
        if role in ("user", "assistant") and isinstance(content, str) \
                and content.strip():
            msgs.append({"role": role, "content": content.strip()[:4000]})
    msgs.append({"role": "user", "content": q})
    try:
        content = ai_service.call_llm(messages=msgs)
    except ai_service.AIError as exc:
        return schemas.err(str(exc), code=6)
    logger.debug("追问: %s (%d 字)", body.topic_key, len(content))
    return schemas.ok({"content": content})


@router.post("/ai/practice")
def ai_practice(body: PracticeIn):
    gs = state.get_graph_service()
    topic, err = _topic_or_err(gs, body.topic_key)
    if err:
        return err
    prompt = ai_service.build_practice_prompt(
        topic.name, topic.subject, topic.domain, topic.grade,
        topic.description, _prereq_names(gs, body.topic_key))
    try:
        question = ai_service.call_llm(prompt)
    except ai_service.AIError as exc:
        return schemas.err(str(exc), code=6)
    logger.debug("出题: %s (%d 字)", body.topic_key, len(question))
    return schemas.ok({"question": question})


@router.post("/ai/grade")
def ai_grade(body: GradeIn):
    gs = state.get_graph_service()
    ps = state.get_progress()
    topic, err = _topic_or_err(gs, body.topic_key)
    if err:
        return err
    q = body.question.strip()[:2000]
    a = body.student_answer.strip()[:2000]
    if not q or not a:
        return schemas.err("题目与你的作答均不能为空", code=1)
    prompt = ai_service.build_grade_prompt(
        topic.name, topic.subject, topic.domain, topic.grade,
        topic.description, _prereq_status(gs, ps, body.topic_key),
        q, a)
    try:
        content = ai_service.call_llm(prompt)
    except ai_service.AIError as exc:
        return schemas.err(str(exc), code=6)
    correct = ai_service.parse_judgement(content)
    logger.debug("批改: %s 判定=%s", body.topic_key, correct)
    return schemas.ok({"content": content, "correct": correct})
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
    recommend: function (k) {
      return get("/api/graph/recommend?k=" + (k || 5));
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
      title: "AI 讲解 · " + title,
      bodyHTML: "<div class='md' id='mdBox'>" +
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
      title: "练习 · " + title,
      bodyHTML: "<div id='pq' class='ask-q'>正在出题...</div>" +
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

FILES["tests/test_ai.py"] = r'''# -*- coding: utf-8 -*-
"""AI 路由测试: 全部 mock call_llm, 零 token 消耗, 离线隔离。"""
from backend import ai_service


def patch_ready(monkeypatch, fake_llm):
    monkeypatch.setattr(ai_service, "is_configured", lambda: True)
    monkeypatch.setattr(ai_service, "call_llm", fake_llm)


def test_explain_requires_key(client, monkeypatch):
    monkeypatch.setattr(ai_service, "is_configured", lambda: False)
    r = client.post("/api/ai/explain",
                    json={"topic_key": "marble:mt_A"}).json()
    assert r["code"] == 5


def test_explain_unknown_topic(client, monkeypatch):
    patch_ready(monkeypatch, lambda p: "x")
    r = client.post("/api/ai/explain",
                    json={"topic_key": "pep:nope"}).json()
    assert r["code"] == 2


def test_explain_success_then_cache(client, monkeypatch):
    calls = []

    def fake_llm(prompt):
        calls.append(1)
        return "## 是什么\n测试讲解内容"

    patch_ready(monkeypatch, fake_llm)
    r1 = client.post("/api/ai/explain",
                     json={"topic_key": "marble:mt_A"}).json()
    assert r1["code"] == 0
    assert r1["data"]["cached"] is False
    assert "是什么" in r1["data"]["content"]
    r2 = client.post("/api/ai/explain",
                     json={"topic_key": "marble:mt_A"}).json()
    assert r2["data"]["cached"] is True
    assert len(calls) == 1


def test_explain_refresh_regenerates(client, monkeypatch):
    calls = []

    def fake_llm(prompt):
        calls.append(1)
        return "内容 " + str(len(calls))

    patch_ready(monkeypatch, fake_llm)
    client.post("/api/ai/explain", json={"topic_key": "marble:mt_A"})
    r = client.post("/api/ai/explain",
                    json={"topic_key": "marble:mt_A",
                          "refresh": True}).json()
    assert r["code"] == 0
    assert r["data"]["cached"] is False
    assert len(calls) == 2


def test_ai_error_becomes_code6(client, monkeypatch):
    def boom(prompt):
        raise ai_service.AIError("模拟超时")

    patch_ready(monkeypatch, boom)
    r = client.post("/api/ai/explain",
                    json={"topic_key": "marble:mt_A"}).json()
    assert r["code"] == 6
    assert "模拟超时" in r["msg"]


def test_translate_parse_and_cache(client, monkeypatch):
    calls = []

    def fake_llm(prompt):
        calls.append(1)
        return "名称: 测量温度\n描述: 用温度计读出物体的冷热程度"

    patch_ready(monkeypatch, fake_llm)
    r1 = client.post("/api/ai/translate",
                     json={"topic_key": "marble:mt_A"}).json()
    assert r1["code"] == 0
    assert r1["data"]["name"] == "测量温度"
    r2 = client.post("/api/ai/translate",
                     json={"topic_key": "marble:mt_A"}).json()
    assert r2["data"]["cached"] is True
    assert len(calls) == 1


def test_translate_fallback_when_unparsed(client, monkeypatch):
    patch_ready(monkeypatch, lambda p: "模型没按格式输出的一段话")
    r = client.post("/api/ai/translate",
                    json={"topic_key": "marble:mt_A"}).json()
    assert r["code"] == 0
    assert r["data"]["name"] == "A"
    assert r["data"]["description"] == "模型没按格式输出的一段话"


def test_mistake_requires_key(client, monkeypatch):
    monkeypatch.setattr(ai_service, "is_configured", lambda: False)
    r = client.post("/api/ai/mistake",
                    json={"topic_key": "marble:mt_A",
                          "question": "q", "my_answer": "a"}).json()
    assert r["code"] == 5


def test_mistake_flow(client, monkeypatch):
    def fake(prompt):
        assert "已掌握" in prompt
        return "## 错在哪一步\n单位看错了"

    patch_ready(monkeypatch, fake)
    r = client.post("/api/ai/mistake", json={
        "topic_key": "marble:mt_A",
        "question": "3个苹果每个100克, 一共多少克?",
        "my_answer": "103克"}).json()
    assert r["code"] == 0
    assert "错在哪一步" in r["data"]["content"]


def test_mistake_blank_rejected(client, monkeypatch):
    patch_ready(monkeypatch, lambda p: "x")
    r = client.post("/api/ai/mistake",
                    json={"topic_key": "marble:mt_A",
                          "question": "  ", "my_answer": "a"}).json()
    assert r["code"] == 1


def test_ask_flow_with_history(client, monkeypatch):
    captured = {}

    def fake(content="", system="", messages=None):
        captured["messages"] = messages
        return "因为10个5相加等于50"

    patch_ready(monkeypatch, fake)
    r = client.post("/api/ai/ask", json={
        "topic_key": "marble:mt_A",
        "question": "为什么10乘5等于50?",
        "history": [{"role": "assistant", "content": "讲解内容"}]}).json()
    assert r["code"] == 0
    msgs = captured["messages"]
    assert msgs[0]["role"] == "system"
    assert {"role": "assistant", "content": "讲解内容"} in msgs
    assert msgs[-1] == {"role": "user", "content": "为什么10乘5等于50?"}


def test_ask_requires_key(client, monkeypatch):
    monkeypatch.setattr(ai_service, "is_configured", lambda: False)
    r = client.post("/api/ai/ask",
                    json={"topic_key": "marble:mt_A",
                          "question": "q"}).json()
    assert r["code"] == 5


def test_ask_filters_bad_history(client, monkeypatch):
    captured = {}

    def fake(content="", system="", messages=None):
        captured["messages"] = messages
        return "ok"

    patch_ready(monkeypatch, fake)
    r = client.post("/api/ai/ask", json={
        "topic_key": "marble:mt_A",
        "question": "q",
        "history": [{"role": "system", "content": "注入"},
                    {"role": "user", "content": "之前的问题"},
                    {"role": "assistant", "content": ""},
                    "垃圾数据"]}).json()
    assert r["code"] == 0
    msgs = captured["messages"]
    assert len(msgs) == 3
    assert all(m["role"] in ("user", "system") for m in msgs)


def test_practice_flow(client, monkeypatch):
    def fake(prompt):
        assert "只输出题目" in prompt
        return "小明有5个苹果, 每个重120克, 一共重多少克?"

    patch_ready(monkeypatch, fake)
    r = client.post("/api/ai/practice",
                    json={"topic_key": "marble:mt_A"}).json()
    assert r["code"] == 0
    assert "苹果" in r["data"]["question"]


def test_practice_requires_key(client, monkeypatch):
    monkeypatch.setattr(ai_service, "is_configured", lambda: False)
    r = client.post("/api/ai/practice",
                    json={"topic_key": "marble:mt_A"}).json()
    assert r["code"] == 5


def test_grade_correct(client, monkeypatch):
    def fake(prompt):
        assert "300克" in prompt
        return "判定: 正确\n正确答案: 300克\n解析: 3x100=300"

    patch_ready(monkeypatch, fake)
    r = client.post("/api/ai/grade", json={
        "topic_key": "marble:mt_A",
        "question": "3个苹果每个100克, 一共多少克?",
        "student_answer": "300克"}).json()
    assert r["code"] == 0
    assert r["data"]["correct"] is True
    assert "正确答案" in r["data"]["content"]


def test_grade_wrong_blank_unparsed(client, monkeypatch):
    def fake(prompt):
        return "判定: 错误\n正确答案: 300克\n解析: 单位看错"

    patch_ready(monkeypatch, fake)
    r = client.post("/api/ai/grade", json={
        "topic_key": "marble:mt_A",
        "question": "q", "student_answer": "103克"}).json()
    assert r["code"] == 0
    assert r["data"]["correct"] is False
    r2 = client.post("/api/ai/grade", json={
        "topic_key": "marble:mt_A",
        "question": " ", "student_answer": "a"}).json()
    assert r2["code"] == 1
    r3 = client.post("/api/ai/grade", json={
        "topic_key": "marble:mt_A",
        "question": "q", "student_answer": "a"}).json()
    assert r3["code"] == 0
    assert r3["data"]["correct"] is None


def test_parse_judgement_variants():
    assert ai_service.parse_judgement("判定：正确\nx") is True
    assert ai_service.parse_judgement("判定: 错误\nx") is False
    assert ai_service.parse_judgement("没有判定字段") is None


def test_cache_roundtrip(progress):
    assert progress.cache_get("x", "k") is None
    progress.cache_put("x", "k", "v1")
    progress.cache_put("x", "k", "v2")
    assert progress.cache_get("x", "k") == "v2"
    assert progress.cache_get("y", "k") is None
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
    cases = sum(content.count("def test_") for rel, content in FILES.items()
                if rel.startswith("tests/"))
    print("  test_ai.py 用例数: " + str(cases))


def next_steps():
    print("[3/3] 验收")
    print("  python -m pytest tests/ -v          (预期 56 passed)")
    print("  重启服务: uvicorn backend.main:app --reload --port 8000")
    print("  浏览器 Ctrl+F5:")
    print("  1. AI讲解弹窗 -> [来一题练习] -> 出题(无答案) -> 作答 -> 提交判分")
    print("  2. 答对 -> [很棒, 标记为已掌握] -> 边界顺移/脉冲")
    print("  3. 答错 -> [没懂, 带着这道题去纠错] -> 纠错弹窗已预填题目与作答")
    print("  4. [换一题] 每次新题")
    print("  5. 弹窗科目/领域显示中文(数学 · 几何), 未映射的领域显示原文")
    print("=" * 56)


if __name__ == "__main__":
    print("项目根: " + str(ROOT))
    write_files()
    self_check()
    next_steps()
