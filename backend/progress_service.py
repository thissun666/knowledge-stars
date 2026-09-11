# -*- coding: utf-8 -*-
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
