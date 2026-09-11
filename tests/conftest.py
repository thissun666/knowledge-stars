# -*- coding: utf-8 -*-
"""公共 fixture: 微型双源数据(含撞车 id 复现) + 服务/客户端注入。

phase8: build_graph_from_dirs 显式 load_pep=True, load_k12=False,
保持 mini 双源语义不变; 生产默认(marble英语+k12)不受影响。
"""
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
        {"topicId": "mt_B", "prerequisiteId": "mt_A", "strength": "hard",
         "reason": "keep"},
        {"topicId": "mt_B", "prerequisiteId": "mt_C", "strength": "soft",
         "reason": "C 被学科过滤, 边应丢弃"},
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
    return data_loader.build_graph_from_dirs(
        mini_sources, subjects=["Mathematics"],
        load_marble=True, load_pep=True, load_k12=False)


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
        data_loader.build_graph_from_dirs(
            mini_sources, subjects=["Mathematics"],
            load_marble=True, load_pep=True, load_k12=False))
    p = progress_service.ProgressService(str(tmp_path / "api.db"))
    state.init(g, p)
    c = TestClient(app)
    yield c
    state.reset()
    p.close()
