# -*- coding: utf-8 -*-
r'''
setup_phase4.py - Sprint 4 一键交付脚本(AI 教学 + 中文化)
交付文件:
  backend/ai_service.py        glm-4-flash 客户端(超时/重试/降级)
  backend/routes/ai.py         POST /api/ai/explain + /api/ai/translate
  tests/test_ai.py             8 条, 全部 mock LLM, 零 token 消耗
  backend/progress_service.py  覆盖: 新增 ai_cache 表 + cache_get/put
  backend/main.py              覆盖: 挂载 ai 路由
  web/js/api.js                覆盖: 新增 explain/translate
  web/js/main.js               覆盖: 讲解弹窗(md渲染)/翻译本卡/译文记忆
  web/css/style.css            覆盖: 追加 .md 与讲解按钮样式
业务约定: code=5 AI未配置, code=6 AI调用失败, 缓存命中返回 cached=true
'''
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FORCE = "--force" in sys.argv

CURLY = "".join(chr(c) for c in (0x201C, 0x201D, 0x2018, 0x2019))
BAD_BACKTICK = chr(96) * 3

FILES = {}

FILES["backend/ai_service.py"] = r'''# -*- coding: utf-8 -*-
"""glm-4-flash 客户端: OpenAI 兼容接口, 超时+有限重试+显式降级。

  - is_configured(): .env 的 AI_API_KEY 为空或仍是占位符时返回 False,
    路由层据此返回 code=5, 前端 toast 引导, 不抛异常
  - call_llm(): 仅对网络/响应结构异常重试 MAX_RETRY 次, 业务可控
  - 所有调用方必须捕获 AIError 并转成统一响应, 禁止裸抛到框架层
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


class AIError(RuntimeError):
    """AI 调用失败(未配置/网络/响应异常), 由路由层转 code=6。"""


def is_configured() -> bool:
    key = (config.AI_API_KEY or "").strip()
    return bool(key) and "your-api-key" not in key.lower()


def call_llm(prompt: str, system: str = "") -> str:
    if not is_configured():
        raise AIError("AI_API_KEY 未配置, 请在 .env 填入智谱 Key 后重启服务")
    messages: List[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
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
            content = resp.json()["choices"][0]["message"]["content"]
            if content and content.strip():
                return content.strip()
            raise ValueError("空响应")
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            last = exc
            logger.warning("LLM 调用失败(第 %d 次): %s", attempt + 1, exc)
            if attempt < MAX_RETRY:
                time.sleep(0.6 * (attempt + 1))
    raise AIError("AI 调用失败: " + str(last))


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


def parse_translate(text: str,
                    fallback_name: str,
                    fallback_desc: str) -> Tuple[str, str]:
    """容错解析: 匹配 名称:/描述: 两行; 失败则整段作描述兜底。"""
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
"""AI 路由: 讲解与翻译, 结果缓存到 ai_cache 表(持久化, 二次点击秒回)。

  code=5 AI 未配置 | code=2 未知知识点 | code=6 AI 调用失败
"""
import json
import logging

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


@router.post("/ai/explain")
def ai_explain(body: ExplainIn):
    gs = state.get_graph_service()
    ps = state.get_progress()
    if not ai_service.is_configured():
        return schemas.err("未配置 AI_API_KEY, 请在 .env 填入智谱 Key "
                           "(open.bigmodel.cn 免费申请)并重启服务", code=5)
    topic = gs.graph.nodes.get(body.topic_key)
    if topic is None:
        return schemas.err("未知知识点: " + body.topic_key, code=2)
    if not body.refresh:
        hit = ps.cache_get("explain", body.topic_key)
        if hit:
            return schemas.ok({"content": hit, "cached": True})
    pre_names = []
    for pk in gs.graph.adj_in.get(body.topic_key, [])[:6]:
        t2 = gs.graph.nodes.get(pk)
        if t2:
            pre_names.append(t2.name)
    prompt = ai_service.build_explain_prompt(
        topic.name, topic.subject, topic.domain, topic.grade,
        topic.description, pre_names)
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
    if not ai_service.is_configured():
        return schemas.err("未配置 AI_API_KEY, 请在 .env 填入智谱 Key "
                           "(open.bigmodel.cn 免费申请)并重启服务", code=5)
    topic = gs.graph.nodes.get(body.topic_key)
    if topic is None:
        return schemas.err("未知知识点: " + body.topic_key, code=2)
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
'''

FILES["backend/progress_service.py"] = r'''# -*- coding: utf-8 -*-
"""学生进度 + AI 缓存服务: SQLite WAL + 参数化 SQL + 快照回滚。

  - 单连接 + 线程锁: FastAPI 多线程下写安全, WAL 允许读不阻塞
  - 快照表: 整库状态 JSON 存档, 支持一键恢复
  - ai_cache 表: AI 讲解/翻译结果持久化, topic 维度, 二次点击零开销
  - 所有 SQL 一律 ? 参数化, 禁止字符串拼接
"""
import json
import logging
import sqlite3
import threading
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger("app.progress")

VALID_STATUSES = ("mastered", "learning", "locked")
DEFAULT_STUDENT = "小明"


class ProgressService:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()
        logger.debug("ProgressService 就绪: %s", db_path)

    def _create_tables(self) -> None:
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS topic_progress ("
                " topic_key TEXT PRIMARY KEY,"
                " status TEXT NOT NULL,"
                " student_name TEXT NOT NULL DEFAULT '" + DEFAULT_STUDENT + "',"
                " updated_at TEXT NOT NULL)")
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS snapshots ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " created_at TEXT NOT NULL,"
                " label TEXT NOT NULL DEFAULT '',"
                " payload TEXT NOT NULL)")
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS ai_cache ("
                " cache_key TEXT PRIMARY KEY,"
                " content TEXT NOT NULL,"
                " created_at TEXT NOT NULL)")
            self._conn.commit()

    def _now(self) -> str:
        return datetime.now().isoformat(timespec="seconds")

    def get_all(self) -> Dict[str, str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT topic_key, status FROM topic_progress").fetchall()
            return {r["topic_key"]: r["status"] for r in rows}

    def set_status(self, topic_key: str, status: str,
                   student_name: str = DEFAULT_STUDENT) -> dict:
        if status not in VALID_STATUSES:
            raise ValueError("非法状态: " + status)
        with self._lock:
            now = self._now()
            self._conn.execute(
                "INSERT INTO topic_progress"
                " (topic_key, status, student_name, updated_at)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(topic_key) DO UPDATE SET"
                " status=excluded.status,"
                " student_name=excluded.student_name,"
                " updated_at=excluded.updated_at",
                (topic_key, status, student_name, now))
            self._conn.commit()
        logger.debug("进度更新: %s -> %s", topic_key, status)
        return {"topic_key": topic_key, "status": status,
                "student_name": student_name, "updated_at": now}

    def counts(self) -> dict:
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) AS n FROM topic_progress"
                " GROUP BY status").fetchall()
        by_status = {s: 0 for s in VALID_STATUSES}
        for r in rows:
            by_status[r["status"]] = r["n"]
        return {"total": sum(by_status.values()), "by_status": by_status}

    # ---------- 快照 ----------
    def snapshot(self, label: str = "") -> int:
        with self._lock:
            rows = self._conn.execute(
                "SELECT topic_key, status, student_name, updated_at"
                " FROM topic_progress").fetchall()
            payload = json.dumps(
                {"statuses": {r["topic_key"]: {
                    "status": r["status"],
                    "student_name": r["student_name"],
                    "updated_at": r["updated_at"]} for r in rows}},
                ensure_ascii=False)
            cur = self._conn.execute(
                "INSERT INTO snapshots (created_at, label, payload)"
                " VALUES (?, ?, ?)", (self._now(), label, payload))
            self._conn.commit()
            logger.debug("快照创建 id=%d label=%s 条目=%d",
                         cur.lastrowid, label, len(rows))
            return int(cur.lastrowid)

    def list_snapshots(self) -> List[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, created_at, label FROM snapshots"
                " ORDER BY id DESC").fetchall()
        return [{"id": r["id"], "created_at": r["created_at"],
                 "label": r["label"]} for r in rows]

    def restore(self, snapshot_id: int) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM snapshots WHERE id=?",
                (snapshot_id,)).fetchone()
            if row is None:
                raise ValueError("快照不存在: " + str(snapshot_id))
            data = json.loads(row["payload"])
            self._conn.execute("DELETE FROM topic_progress")
            items = data.get("statuses", {})
            self._conn.executemany(
                "INSERT INTO topic_progress"
                " (topic_key, status, student_name, updated_at)"
                " VALUES (?, ?, ?, ?)",
                [(k, v.get("status", "locked"),
                  v.get("student_name", DEFAULT_STUDENT),
                  v.get("updated_at", "")) for k, v in items.items()])
            self._conn.commit()
            logger.debug("快照恢复 id=%d 条目=%d", snapshot_id, len(items))
            return len(items)

    # ---------- AI 缓存 ----------
    @staticmethod
    def _cache_key(kind: str, topic_key: str) -> str:
        return kind + "|" + topic_key

    def cache_get(self, kind: str, topic_key: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT content FROM ai_cache WHERE cache_key=?",
                (self._cache_key(kind, topic_key),)).fetchone()
        return row["content"] if row else None

    def cache_put(self, kind: str, topic_key: str, content: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO ai_cache"
                " (cache_key, content, created_at) VALUES (?, ?, ?)",
                (self._cache_key(kind, topic_key), content, self._now()))
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
'''

FILES["backend/main.py"] = r'''# -*- coding: utf-8 -*-
"""FastAPI 入口: 路由 + 静态托管同源(免 CORS/代理), 无 lifespan 副作用。

服务单例在 backend/state 中惰性初始化, 首次请求才加载数据,
因此 TestClient/uvicorn 启动均无后台线程, 规避竞态。
"""
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import paths
from backend import schemas
from backend.routes import ai as ai_routes
from backend.routes import graph as graph_routes
from backend.routes import progress as progress_routes

from logging_setup import setup_logging
from backend import config

logger = setup_logging(config.LOG_LEVEL)

app = FastAPI(title="知识点星图", version="0.4.0")

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc):
    """参数校验失败也走统一格式, 前端只认 code 字段。"""
    return JSONResponse(status_code=200,
                        content=schemas.err("参数校验失败", code=422))


@app.get("/api/health", tags=["meta"])
def health():
    return schemas.ok({"status": "up"})


app.include_router(graph_routes.router, prefix="/api")
app.include_router(progress_routes.router, prefix="/api")
app.include_router(ai_routes.router, prefix="/api")

# 静态托管放最后: 未命中 API 的路径回落到 web/, index.html 作为首页
app.mount("/", StaticFiles(directory=paths.WEB_DIR, html=True), name="web")

logger.info("应用已装配: web=%s", paths.WEB_DIR)
'''

FILES["tests/test_ai.py"] = r'''# -*- coding: utf-8 -*-
"""AI 路由测试: 全部 mock call_llm, 零 token 消耗, 离线隔离。"""


def patch_ready(monkeypatch, fake_llm):
    from backend import ai_service
    monkeypatch.setattr(ai_service, "is_configured", lambda: True)
    monkeypatch.setattr(ai_service, "call_llm", fake_llm)


def test_explain_requires_key(client, monkeypatch):
    from backend import ai_service
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
    assert len(calls) == 1  # 第二次命中缓存, 不再调 LLM


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
    from backend import ai_service

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
    assert "温度计" in r1["data"]["description"]
    r2 = client.post("/api/ai/translate",
                     json={"topic_key": "marble:mt_A"}).json()
    assert r2["data"]["cached"] is True
    assert len(calls) == 1


def test_translate_fallback_when_unparsed(client, monkeypatch):
    patch_ready(monkeypatch, lambda p: "模型没按格式输出的一段话")
    r = client.post("/api/ai/translate",
                    json={"topic_key": "marble:mt_A"}).json()
    assert r["code"] == 0
    assert r["data"]["name"] == "A"          # 回退原名
    assert r["data"]["description"] == "模型没按格式输出的一段话"


def test_cache_roundtrip(progress):
    assert progress.cache_get("x", "k") is None
    progress.cache_put("x", "k", "v1")
    progress.cache_put("x", "k", "v2")       # 覆盖写
    assert progress.cache_get("x", "k") == "v2"
    assert progress.cache_get("y", "k") is None  # kind 隔离
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
    }
  };
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
.modal { width: 480px; max-width: 92vw; background: var(--panel);
  border: 1px solid var(--line); border-radius: 12px; padding: 18px 20px;
  box-shadow: 0 12px 44px rgba(0,0,0,0.5); max-height: 86vh; overflow: auto; }
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
.btn.ai { background: linear-gradient(135deg, #5b6cff, #2f6dff);
  border-color: #5b6cff; color: #fff; font-weight: 600; }
.badge-cn { display: inline-block; margin-left: 8px; font-size: 11px;
  color: var(--green); border: 1px solid var(--green);
  border-radius: 4px; padding: 0 5px; vertical-align: 2px; }
.md h4 { margin: 12px 0 6px; color: var(--gold); font-size: 14px; }
.md p { margin: 6px 0; font-size: 13px; }
.md ul { margin: 4px 0; padding-left: 4px; }
.md li { margin: 3px 0 3px 14px; font-size: 13px; }
.md code { background: #0d1322; padding: 1px 5px; border-radius: 4px;
  font-size: 12px; }
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
  var EMPTY = {
    nodes: [], edges: [], progress: {}, boundarySet: new Set(),
    dimFn: function () { return false; }
  };

  function esc(s) {
    return String(s).split("&").join("&amp;")
      .split("<").join("&lt;").split(">").join("&gt;");
  }

  function inlineMd(s) {
    return s.split("**").join("<b>").split("<b>").join("<b>")
      .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
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
    var ov = translated[key];
    var title = ov ? ov.name : n.name;
    var desc = ov ? ov.description : (n.description || "暂无描述");
    var pre = model.adjIn[key] || [];
    var preNames = [], i;
    for (i = 0; i < pre.length && i < 6; i++) {
      var pn = model.byKey[pre[i]];
      preNames.push((translated[pre[i]] ? translated[pre[i]].name : null)
        || (pn ? pn.name : pre[i]));
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
      title: esc(title) + cnBadge,
      bodyHTML: "<div class='meta'>[" + esc(src) + "] " + esc(n.subject) +
        " · " + esc(n.domain) + " · 年级 " + (n.grade > 0 ? n.grade : "-") +
        (model.boundarySet.has(key)
          ? " · <span style='color:#ffd166'>当前边界, 现在可学</span>" : "") +
        "</div><div class='desc'>" + esc(desc) +
        "</div><div class='pre'>前置知识: " + esc(preText) +
        "</div><div class='acts'>" + btns +
        "<button class='btn ai' data-act='explain'>AI 中文讲解</button>" +
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
        var tr = box.querySelector("[data-act='translate']");
        if (tr) tr.onclick = function () { doTranslate(key); };
      }
    });
  }

  function openExplain(key) {
    if (!model) return;
    var n = model.byKey[key];
    var title = (translated[key] ? translated[key].name : n.name);
    UI.modal({
      title: "AI 讲解 · " + title,
      bodyHTML: "<div class='md' id='mdBox'>" +
        "<div class='snap-empty'>正在生成讲解, 首次约几秒...</div></div>" +
        "<div class='acts'><button class='btn' id='mBack'>返回详情</button>" +
        "<button class='btn ai' id='mRegen'>重新生成</button></div>",
      onOpen: function (box) {
        var mdBox = box.querySelector("#mdBox");
        function fill(promise) {
          mdBox.innerHTML = "<div class='snap-empty'>生成中...</div>";
          promise.then(function (d) {
            mdBox.innerHTML = mdToHtml(d.content);
            if (d.cached) {
              mdBox.insertAdjacentHTML("beforeend",
                "<p style='color:#8a94a6;font-size:11px'>来自缓存 · " +
                "点击重新生成可获取新版本</p>");
            }
          }).catch(function (err) {
            mdBox.innerHTML = "<div class='snap-empty'>" + esc(err.message) +
              "</div>";
            UI.toast(err.message, "err");
          });
        }
        fill(API.explain(key, false));
        box.querySelector("#mRegen").onclick = function () {
          fill(API.explain(key, true));
        };
        box.querySelector("#mBack").onclick = function () {
          openDetail(key);
        };
      }
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
    var name = translated[n.key] ? translated[n.key].name : n.name;
    t.classList.remove("hidden");
    t.innerHTML = "<b>" + esc(name) + "</b><br>" + LBL[statusOf(n.key)] +
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
    function nodeLabel(n) {
      return (translated[n.key] ? translated[n.key].name : null) || n.name;
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
        var label = nodeLabel(model.nodes[i]).toLowerCase();
        if (label.indexOf(q) >= 0 ||
            model.nodes[i].name.toLowerCase().indexOf(q) >= 0) {
          hits.push(model.nodes[i]);
        }
      }
      if (!hits.length) { hideList(); return; }
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


def write_files():
    print("[1/4] 写入交付文件(新建才提示, 覆盖项直接落盘)...")
    for rel, content in FILES.items():
        p = ROOT / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        existed = p.exists()
        p.write_text(content, encoding="utf-8")
        print("  [" + ("OVERWRITE" if existed else "NEW") + "] " + rel)


def self_check():
    print("[2/4] 自检报告")
    bad_b, bad_q = [], []
    for rel, content in FILES.items():
        if BAD_BACKTICK in content:
            bad_b.append(rel)
        if any(c in content for c in CURLY):
            bad_q.append(rel)
    print("  [PASS] 无三反引号污染" if not bad_b
          else "  [FAIL] 三反引号: " + ", ".join(bad_b))
    print("  [PASS] 无弯引号污染" if not bad_q
          else "  [FAIL] 弯引号: " + ", ".join(bad_q))
    cases = sum(content.count("def test_") for rel, content in FILES.items()
                if rel.startswith("tests/"))
    print("  本轮测试用例: " + str(cases))


def next_steps():
    print("[3/4] 验收")
    print("  python -m pytest tests/ -v          (预期 37 passed)")
    print("  确认 .env 的 AI_API_KEY 已填真实值后重启: uvicorn backend.main:app --reload --port 8000")
    print("[4/4] 浏览器验收(Ctrl+F5)")
    print("  1. 点任意英文节点 -> 弹窗新增 [AI 中文讲解] 与 [翻译本卡]")
    print("  2. 翻译本卡 -> 标题/描述变中文, 出现[已译]角标, 再开秒出(缓存)")
    print("  3. AI 中文讲解 -> 是什么/生活例子/常见误区/小练习 四节")
    print("  4. 重新生成 -> 强制新内容; 未填 Key 时给出引导提示")
    print("=" * 56)


if __name__ == "__main__":
    print("项目根: " + str(ROOT))
    write_files()
    self_check()
    next_steps()
