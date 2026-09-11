# -*- coding: utf-8 -*-
r"""Sprint11.5: difficulty 解析与推荐排序契约"""
import json

from backend.data_loader import Edge, Graph, Topic, _k12_difficulty
from backend.graph_service import GraphService


def test_parse_and_default():
    assert _k12_difficulty(
        {"annotations": json.dumps({"difficulty": {"value": 0.3}})}) == 0.3
    assert _k12_difficulty(
        {"annotations": json.dumps({"difficulty": {"value": 7}})}) == 1.0
    assert _k12_difficulty({}) == 0.5
    assert _k12_difficulty({"annotations": "{坏json"}) == 0.5
    t = Topic(key="k", id="k", source="kkg", book="", name="n", type="",
              subject="数学", domain="", grade=10)
    assert t.difficulty == 0.5


def _node(key, diff=0.5, subj="数学"):
    return Topic(key=key, id=key, source="k12", book="初中", name=key,
                 type="", subject=subj, domain=subj, grade=7,
                 difficulty=diff)


def _svc(nodes, edges=()):
    g = Graph()
    for key, diff in nodes:
        g.add_node(_node(key, diff))
    for a, b in edges:
        g.add_edge(Edge(prereq_key=a, topic_key=b))
    return GraphService(g)


def test_connected_root_wins_over_easier_deadend():
    """规格v14: 有下游边界节点整层优先于更简单的死胡同。

    旧规格(难度绝对优先)经v14改判: 学完无法推进任何节点的
    死胡同不占该科唯一推荐位; 层内难度排序仍由本文件其余
    用例覆盖。死胡同经回退层仍可达, 仅被降权。
    """
    svc = _svc([("r_hard", 0.9), ("m1", 0.5), ("m2", 0.5),
                ("r_easy", 0.1)],
               [("r_hard", "m1"), ("m1", "m2")])
    entry = next(s for s in svc.recommend_paths({}, k=10)
                 if s["subject"] == "数学")
    assert entry["key"] == "r_hard"
    assert entry["unlock_count"] == 2

def test_difficulty_tie_breaks_by_unlock():
    svc = _svc([("r_low_unlock", 0.1), ("r_high_unlock", 0.1), ("c1", 0.5)],
               [("r_high_unlock", "c1")])
    entry = next(s for s in svc.recommend_paths({}, k=10)
                 if s["subject"] == "数学")
    assert entry["key"] == "r_high_unlock"
