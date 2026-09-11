# -*- coding: utf-8 -*-
r'''
setup_phase2.py - Sprint 2 一键交付脚本
交付文件:
  backend/schemas.py          统一响应 {code, data, msg}
  backend/graph_service.py    拓扑排序/祖先后代/边界节点(全量+增量)
  backend/progress_service.py SQLite WAL/参数化/快照回滚
  backend/state.py            服务单例(惰性初始化, 测试可注入)
  backend/main.py             FastAPI 入口 + 静态托管 + 统一校验错误
  backend/routes/graph.py     GET /api/graph
  backend/routes/progress.py  GET/POST /api/progress + 快照/恢复
  tests/conftest.py           扩充: svc/progress/client fixture
  tests/test_graph_service.py 5 条
  tests/test_progress_service.py 7 条
  tests/test_api.py           6 条
测试隔离: 合成图谱 + 临时 DB, 不触真实 data/student_progress.db, 无 lifespan
'''
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

CURLY = "".join(chr(c) for c in (0x201C, 0x201D, 0x2018, 0x2019))
BAD_BACKTICK = chr(96) * 3

FILES = {}

FILES["backend/schemas.py"] = r'''# -*- coding: utf-8 -*-
"""统一响应封装: 所有 API 一律返回 {code, data, msg}。

code 约定:
  0   成功
  1   通用业务错误
  2   未知知识点 topic_key
  3   非法状态值 status
  422 请求参数校验失败(由 main.py 异常处理器转换, HTTP 仍为 200)
"""


def ok(data=None, msg: str = "ok") -> dict:
    return {"code": 0, "data": data, "msg": msg}


def err(msg: str = "error", code: int = 1) -> dict:
    return {"code": code, "data": None, "msg": msg}
'''

FILES["backend/graph_service.py"] = r'''# -*- coding: utf-8 -*-
"""图谱服务: 拓扑排序、祖先/后代链(记忆化)、边界节点(全量+增量)。

边界节点定义: 自身未掌握 且 所有前置均已掌握(无前置的根节点天然满足)。
增量策略: 状态变更后仅重估 变更节点及其全部后代, 避免每次全图重算。
"""
import logging
from collections import deque
from typing import Dict, List, Optional, Set, Tuple

from backend import data_loader

logger = logging.getLogger("app.graph")


class GraphService:
    def __init__(self, graph: data_loader.Graph):
        self.graph = graph
        self._topo: Optional[List[str]] = None
        self._anc: Dict[str, Set[str]] = {}
        self._desc: Dict[str, Set[str]] = {}
        self._boundary: Optional[Set[str]] = None

    # ---------- 拓扑 ----------
    def topo_order(self) -> List[str]:
        if self._topo is None:
            indeg = {k: 0 for k in self.graph.nodes}
            for e in self.graph.edges:
                indeg[e.topic_key] += 1
            q = deque(sorted(k for k, d in indeg.items() if d == 0))
            order: List[str] = []
            while q:
                k = q.popleft()
                order.append(k)
                for m in sorted(self.graph.adj_out.get(k, [])):
                    indeg[m] -= 1
                    if indeg[m] == 0:
                        q.append(m)
            self._topo = order
            logger.debug("拓扑排序完成: %d/%d 节点", len(order),
                         len(self.graph.nodes))
        return self._topo

    def is_dag(self) -> bool:
        return len(self.topo_order()) == len(self.graph.nodes)

    # ---------- 祖先/后代(迭代 DFS, 记忆化) ----------
    def descendants(self, key: str) -> Set[str]:
        if key not in self._desc:
            seen: Set[str] = set()
            stack = [key]
            while stack:
                cur = stack.pop()
                for nxt in self.graph.adj_out.get(cur, ()):
                    if nxt not in seen:
                        seen.add(nxt)
                        stack.append(nxt)
            self._desc[key] = seen
        return self._desc[key]

    def ancestors(self, key: str) -> Set[str]:
        if key not in self._anc:
            seen: Set[str] = set()
            stack = [key]
            while stack:
                cur = stack.pop()
                for pre in self.graph.adj_in.get(cur, ()):
                    if pre not in seen:
                        seen.add(pre)
                        stack.append(pre)
            self._anc[key] = seen
        return self._anc[key]

    # ---------- 边界节点 ----------
    def is_boundary(self, key: str, progress: Dict[str, str]) -> bool:
        if progress.get(key) == "mastered":
            return False
        pres = self.graph.adj_in.get(key, [])
        return all(progress.get(p) == "mastered" for p in pres)

    def compute_boundary(self, progress: Dict[str, str]) -> Set[str]:
        b = {k for k in self.graph.nodes if self.is_boundary(k, progress)}
        self._boundary = set(b)
        logger.debug("全量边界重算: %d 个", len(b))
        return b

    def apply_change(self, progress: Dict[str, str],
                     changed_keys: List[str]) -> Tuple[Set[str], List[str], List[str]]:
        """增量重估: 只评估 changed_keys 及其后代。
        返回 (新边界集合, 新进入边界的节点, 退出边界的节点)。"""
        if self._boundary is None:
            self.compute_boundary(progress)
        before = set(self._boundary or set())
        candidates: Set[str] = set()
        for c in changed_keys:
            candidates.add(c)
            candidates |= self.descendants(c)
        for c in candidates:
            if c in self.graph.nodes and self.is_boundary(c, progress):
                self._boundary.add(c)
            else:
                self._boundary.discard(c)
        newly = sorted(self._boundary - before)
        lost = sorted(before - self._boundary)
        if newly or lost:
            logger.debug("边界增量: + %s - %s", newly, lost)
        return set(self._boundary), newly, lost

    # ---------- 序列化 ----------
    def serialize(self, progress: Dict[str, str],
                  boundary: Set[str]) -> dict:
        nodes = []
        for key in sorted(self.graph.nodes):
            t = self.graph.nodes[key]
            nodes.append({
                "key": t.key, "id": t.id, "source": t.source,
                "book": t.book, "name": t.name, "type": t.type,
                "subject": t.subject, "domain": t.domain,
                "grade": t.grade, "description": t.description,
            })
        edges = [{"prereq": e.prereq_key, "topic": e.topic_key,
                  "strength": e.strength} for e in self.graph.edges]
        return {
            "nodes": nodes,
            "edges": edges,
            "progress": progress,
            "boundary": sorted(boundary),
            "stats": data_loader.stats(self.graph),
        }
'''

FILES["backend/progress_service.py"] = r'''# -*- coding: utf-8 -*-
"""学生进度服务: SQLite WAL + 参数化 SQL + 快照回滚。

  - 单连接 + 线程锁: FastAPI 多线程下写安全, WAL 允许读不阻塞
  - 快照表: 整库状态 JSON 化存档, 支持一键恢复(替代 Git 自动 commit)
  - 所有 SQL 一律 ? 参数化, 禁止字符串拼接
"""
import json
import logging
import sqlite3
import threading
from datetime import datetime
from typing import Dict, List

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

    def close(self) -> None:
        with self._lock:
            self._conn.close()
'''

FILES["backend/state.py"] = r'''# -*- coding: utf-8 -*-
"""服务单例: 惰性初始化(首次请求才加载数据/建连), 无 lifespan 副作用。

测试通过 init() 注入合成图谱与临时 DB, 用 reset() 清理, 与生产完全解耦。
"""
import threading
from typing import Optional

import paths

_lock = threading.Lock()
_graph_service = None
_progress = None


def init(graph_service_obj, progress_obj) -> None:
    global _graph_service, _progress
    _graph_service = graph_service_obj
    _progress = progress_obj


def reset() -> None:
    global _graph_service, _progress
    _graph_service = None
    _progress = None


def get_graph_service():
    global _graph_service
    if _graph_service is None:
        with _lock:
            if _graph_service is None:
                from backend import data_loader, graph_service
                _graph_service = graph_service.GraphService(
                    data_loader.get_graph())
    return _graph_service


def get_progress():
    global _progress
    if _progress is None:
        with _lock:
            if _progress is None:
                from backend import progress_service
                _progress = progress_service.ProgressService(paths.DB_FILE)
    return _progress
'''

FILES["backend/routes/graph.py"] = r'''# -*- coding: utf-8 -*-
"""GET /api/graph: 一次性返回节点/边/进度/边界/统计, 前端首屏数据源。"""
import logging

from fastapi import APIRouter

from backend import schemas, state

logger = logging.getLogger("app.api.graph")
router = APIRouter(tags=["graph"])


@router.get("/graph")
def read_graph():
    gs = state.get_graph_service()
    ps = state.get_progress()
    progress = ps.get_all()
    boundary = gs.compute_boundary(progress)
    payload = gs.serialize(progress, boundary)
    logger.debug("graph 响应: nodes=%d edges=%d boundary=%d",
                 len(payload["nodes"]), len(payload["edges"]),
                 len(payload["boundary"]))
    return schemas.ok(payload)
'''

FILES["backend/routes/progress.py"] = r'''# -*- coding: utf-8 -*-
"""进度读写 + 快照/恢复。业务错误以统一格式 code!=0 返回(HTTP 200)。"""
import logging
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from backend import progress_service, schemas, state

logger = logging.getLogger("app.api.progress")
router = APIRouter(tags=["progress"])


class ProgressIn(BaseModel):
    topic_key: str
    status: str
    student_name: str = progress_service.DEFAULT_STUDENT


class SnapshotIn(BaseModel):
    label: str = ""


class RestoreIn(BaseModel):
    snapshot_id: int


@router.get("/progress")
def read_progress():
    gs = state.get_graph_service()
    ps = state.get_progress()
    progress = ps.get_all()
    boundary = gs.compute_boundary(progress)
    return schemas.ok({"progress": progress, "counts": ps.counts(),
                       "boundary": sorted(boundary)})


@router.post("/progress")
def update_progress(body: ProgressIn):
    gs = state.get_graph_service()
    ps = state.get_progress()
    if body.topic_key not in gs.graph.nodes:
        return schemas.err("未知知识点: " + body.topic_key, code=2)
    if body.status not in progress_service.VALID_STATUSES:
        return schemas.err("非法状态: " + body.status, code=3)
    record = ps.set_status(body.topic_key, body.status, body.student_name)
    progress = ps.get_all()
    boundary, newly, lost = gs.apply_change(progress, [body.topic_key])
    return schemas.ok({
        "record": record,
        "boundary": sorted(boundary),
        "newly_unlocked": newly,
        "no_longer_boundary": lost,
        "counts": ps.counts(),
    })


@router.post("/progress/snapshot")
def create_snapshot(body: SnapshotIn):
    ps = state.get_progress()
    sid = ps.snapshot(body.label)
    return schemas.ok({"snapshot_id": sid})


@router.get("/progress/snapshots")
def list_snapshots():
    ps = state.get_progress()
    return schemas.ok({"snapshots": ps.list_snapshots()})


@router.post("/progress/restore")
def restore_snapshot(body: RestoreIn):
    gs = state.get_graph_service()
    ps = state.get_progress()
    try:
        n = ps.restore(body.snapshot_id)
    except ValueError as exc:
        return schemas.err(str(exc), code=1)
    progress = ps.get_all()
    boundary = gs.compute_boundary(progress)  # 全量重算, 复位增量缓存
    return schemas.ok({"restored": n, "boundary": sorted(boundary),
                       "counts": ps.counts()})
'''

FILES["backend/main.py"] = r'''# -*- coding: utf-8 -*-
"""FastAPI 入口: 路由 + 静态托管同源(免 CORS/代理), 无 lifespan 副作用。

服务单例在 backend/state 中惰性初始化, 首次请求才加载数据,
因此 TestClient/uvicorn 启动均无后台线程, 规避竞态(Skill 第 8 条)。
"""
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import paths
from backend import schemas
from backend.routes import graph as graph_routes
from backend.routes import progress as progress_routes

from logging_setup import setup_logging
from backend import config

logger = setup_logging(config.LOG_LEVEL)

app = FastAPI(title="知识点星图", version="0.2.0")

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

# 静态托管放最后: 未命中 API 的路径回落到 web/, index.html 作为首页
app.mount("/", StaticFiles(directory=paths.WEB_DIR, html=True), name="web")

logger.info("应用已装配: web=%s", paths.WEB_DIR)
'''

FILES["tests/conftest.py"] = r'''# -*- coding: utf-8 -*-
"""公共 fixture: 微型双源数据(含撞车 id 复现) + 服务/客户端注入。"""
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
    return data_loader.build_graph_from_dirs(mini_sources,
                                             subjects=["Mathematics"])


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
        data_loader.build_graph_from_dirs(mini_sources,
                                          subjects=["Mathematics"]))
    p = progress_service.ProgressService(str(tmp_path / "api.db"))
    state.init(g, p)
    # 不使用 with 上下文, 彻底不触发 lifespan(项目也未注册 lifespan)
    c = TestClient(app)
    yield c
    state.reset()
    p.close()
'''

FILES["tests/test_graph_service.py"] = r'''# -*- coding: utf-8 -*-
"""graph_service 测试: 拓扑/祖先后代/边界全量与增量的一致性。"""


def test_topo_and_dag(svc):
    order = svc.topo_order()
    assert len(order) == len(svc.graph.nodes)
    assert svc.is_dag()
    idx = {k: i for i, k in enumerate(order)}
    for e in svc.graph.edges:
        assert idx[e.prereq_key] < idx[e.topic_key]


def test_initial_boundary_is_roots(svc):
    b = svc.compute_boundary({})
    assert b == {"marble:mt_A", "pep:7-up:mt_ch1_001",
                 "pep:8-down:mt_ch21_001", "pep:9-up:mt_ch21_001"}


def test_ancestors_descendants(svc):
    assert svc.descendants("pep:7-up:mt_ch1_001") == {"pep:7-down:mt_ch5_001"}
    assert svc.ancestors("pep:7-down:mt_ch5_001") == {"pep:7-up:mt_ch1_001"}
    assert svc.descendants("marble:mt_A") == {"marble:mt_B"}
    assert svc.ancestors("marble:mt_A") == set()


def test_delta_matches_full(svc):
    prog = {"pep:7-up:mt_ch1_001": "mastered"}
    svc.compute_boundary({})  # 先缓存变更前状态
    boundary, newly, lost = svc.apply_change(prog, ["pep:7-up:mt_ch1_001"])
    full = svc.compute_boundary(prog)  # 全量对账
    assert boundary == full
    assert newly == ["pep:7-down:mt_ch5_001"]
    assert lost == ["pep:7-up:mt_ch1_001"]


def test_learning_is_boundary_candidate(svc):
    b = svc.compute_boundary({"marble:mt_A": "learning"})
    assert "marble:mt_A" in b
'''

FILES["tests/test_progress_service.py"] = r'''# -*- coding: utf-8 -*-
"""progress_service 测试: 参数化读写/WAL/快照回滚/持久化, 全部临时库。"""
import pytest

from backend.progress_service import ProgressService


def test_set_get_and_validation(progress):
    rec = progress.set_status("marble:mt_A", "mastered")
    assert rec["status"] == "mastered"
    assert progress.get_all() == {"marble:mt_A": "mastered"}
    with pytest.raises(ValueError):
        progress.set_status("marble:mt_A", "genius")


def test_upsert_overwrites(progress):
    progress.set_status("marble:mt_A", "learning")
    progress.set_status("marble:mt_A", "mastered")
    assert progress.get_all() == {"marble:mt_A": "mastered"}


def test_wal_mode(progress):
    mode = progress._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert str(mode).lower() == "wal"


def test_counts(progress):
    progress.set_status("a", "mastered")
    progress.set_status("b", "learning")
    c = progress.counts()
    assert c["total"] == 2
    assert c["by_status"]["mastered"] == 1
    assert c["by_status"]["learning"] == 1
    assert c["by_status"]["locked"] == 0


def test_snapshot_and_restore(progress):
    progress.set_status("a", "mastered")
    sid = progress.snapshot("v1")
    progress.set_status("a", "locked")
    progress.set_status("b", "mastered")
    n = progress.restore(sid)
    assert n == 1
    assert progress.get_all() == {"a": "mastered"}


def test_restore_unknown_snapshot(progress):
    with pytest.raises(ValueError):
        progress.restore(999)


def test_persistence_reopen(tmp_path):
    db = str(tmp_path / "p.db")
    p1 = ProgressService(db)
    p1.set_status("k", "mastered")
    p1.close()
    p2 = ProgressService(db)
    assert p2.get_all() == {"k": "mastered"}
    p2.close()
'''

FILES["tests/test_api.py"] = r'''# -*- coding: utf-8 -*-
"""API 集成测试: 统一响应格式 + 进度流 + 快照恢复, 合成数据零外部依赖。"""

ROOTS = {"marble:mt_A", "pep:7-up:mt_ch1_001",
         "pep:8-down:mt_ch21_001", "pep:9-up:mt_ch21_001"}


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
    assert d["stats"]["total_nodes"] == 8
    assert d["stats"]["total_edges"] == 4


def test_progress_update_flow(client):
    r = client.post("/api/progress", json={
        "topic_key": "pep:7-up:mt_ch1_001",
        "status": "mastered"}).json()
    assert r["code"] == 0
    assert r["data"]["newly_unlocked"] == ["pep:7-down:mt_ch5_001"]
    assert r["data"]["no_longer_boundary"] == ["pep:7-up:mt_ch1_001"]
    r2 = client.get("/api/progress").json()
    assert r2["data"]["progress"]["pep:7-up:mt_ch1_001"] == "mastered"
    assert r2["data"]["counts"]["by_status"]["mastered"] == 1


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
    assert r["code"] == 0
    sid = r["data"]["snapshot_id"]
    client.post("/api/progress", json={
        "topic_key": "marble:mt_B", "status": "mastered"})
    r2 = client.post("/api/progress/restore",
                     json={"snapshot_id": sid}).json()
    assert r2["code"] == 0
    r3 = client.get("/api/progress").json()
    assert r3["data"]["progress"] == {"marble:mt_A": "mastered"}
    assert set(r3["data"]["boundary"]) == ROOTS
'''


def write_files():
    print("[1/4] 写入交付文件（不存在才写, --force 可覆盖）...")
    import sys as _sys
    force = "--force" in _sys.argv
    ok_n, skip_n = 0, 0
    for rel, content in FILES.items():
        p = ROOT / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists() and not force:
            print("  [SKIP] " + rel + "（已存在）")
            skip_n += 1
            continue
        p.write_text(content, encoding="utf-8")
        print("  [OK]   " + rel)
        ok_n += 1
    print("  合计: 写入 " + str(ok_n) + ", 跳过 " + str(skip_n))


def self_check():
    print("[2/4] 自检报告")
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
    print("  [PASS] 无三反引号污染" if not bad_b
          else "  [FAIL] 三反引号污染: " + ", ".join(sorted(set(bad_b))))
    print("  [PASS] 运行时文件无弯引号" if not bad_q
          else "  [FAIL] 运行时文件弯引号: " + ", ".join(sorted(set(bad_q))))
    if meta_q:
        print("  [NOTE] meta 脚本弯引号(自检代码自身, 可忽略): "
              + ", ".join(sorted(set(meta_q))))
    cases = sum(content.count("def test_") for content in FILES.values())
    print("  本轮新增测试用例: " + str(cases))


def next_steps():
    print("[3/4] 验收命令")
    print("  python -m pytest tests/ -v")
    print("  uvicorn backend.main:app --reload --port 8000")
    print("[4/4] 服务启动后冒烟(另开终端)")
    print("  curl http://127.0.0.1:8000/api/health")
    print("  curl -s http://127.0.0.1:8000/api/graph | python -c \"import sys,json; d=json.load(sys.stdin); print(d['code'], d['data']['stats'])\"")
    print("  curl -s -X POST http://127.0.0.1:8000/api/progress -H \"Content-Type: application/json\" -d \"{\\\"topic_key\\\":\\\"pep:7-up:mt_ch1_001\\\",\\\"status\\\":\\\"mastered\\\"}\"")
    print("=" * 56)


if __name__ == "__main__":
    print("项目根: " + str(ROOT))
    write_files()
    self_check()
    next_steps()
