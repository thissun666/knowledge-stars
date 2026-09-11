# -*- coding: utf-8 -*-
"""图谱路由: /api/graph(支持band学段子图) + /api/graph/recommend。

band 语义: 空或"全部"=全图; 小学/初中/高中=该学段子图。
学段归属: k12->book(学段名), marble->小学, pep->初中, kkg->高中。
- 子图边界在学段内计算; 子图 GraphService 按需构建缓存(键绑定全图 id)
- 未知 band 回退全图
"""
import json
import logging
from typing import Dict

from fastapi import APIRouter

from backend import data_loader, graph_service, schemas, state

logger = logging.getLogger("app.api.graph")
router = APIRouter(tags=["graph"])

VALID_BANDS = ("小学", "初中", "高中")
_sub_cache: Dict[str, tuple] = {}


def _translations_for(keys, ps) -> Dict[str, dict]:
    """收集已缓存中文翻译, 仅限当前payload节点(坏JSON容错)。"""
    out: Dict[str, dict] = {}
    for key in keys:
        hit = ps.cache_get("translate", key)
        if not hit:
            continue
        try:
            out[key] = json.loads(hit)
        except ValueError:
            continue
    return out


def _band_of(topic: data_loader.Topic) -> str:
    if topic.source == "k12":
        return topic.book
    if topic.source == "kkg":
        return "高中"
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
    payload["translations"] = _translations_for(
        [n["key"] for n in payload["nodes"]], ps)
    return schemas.ok(payload)


@router.get("/graph/recommend")
def recommend_path(k: int = 5, band: str = ""):
    k = max(1, min(40, k))
    gs = _pick_service(band)
    ps = state.get_progress()
    steps = gs.recommend_paths(ps.get_all(), k)
    logger.debug("路径推荐[band=%s]: %d 步", band or "全部", len(steps))
    return schemas.ok({"steps": steps})
