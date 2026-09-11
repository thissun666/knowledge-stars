# -*- coding: utf-8 -*-
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
