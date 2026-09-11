# -*- coding: utf-8 -*-
"""每科一条推荐: 选取/学习中锚定/掌握后更换(合成小图)。"""
from backend import data_loader
from backend.graph_service import GraphService


def mini_graph():
    g = data_loader.Graph()
    for kid, name in (("a1", "加法"), ("a2", "乘法"), ("a3", "方程")):
        g.add_node(data_loader.Topic(
            key="k12:" + kid, id=kid, source="k12", book="小学",
            name=name, type="", subject="数学", domain="数学", grade=3))
    for pre, nxt in (("a1", "a2"), ("a2", "a3")):
        g.add_edge(data_loader.Edge(prereq_key="k12:" + pre,
                                    topic_key="k12:" + nxt))
    for kid, name in (("b1", "字母"), ("b2", "单词")):
        g.add_node(data_loader.Topic(
            key="k12:" + kid, id=kid, source="k12", book="小学",
            name=name, type="", subject="英语", domain="英语", grade=3))
    g.add_edge(data_loader.Edge(prereq_key="k12:b1", topic_key="k12:b2"))
    return g


def test_one_per_subject():
    gs = GraphService(mini_graph())
    steps = gs.recommend_paths({}, k=30)
    assert [s["key"] for s in steps] == ["k12:a1", "k12:b1"]
    assert all(not s["learning"] for s in steps)


def test_learning_anchor_locks_subject():
    gs = GraphService(mini_graph())
    steps = gs.recommend_paths({"k12:a2": "learning"}, k=30)
    assert steps[0]["key"] == "k12:a2"
    assert steps[0]["learning"] is True
    assert "前置未掌握" in steps[0]["reason"]
    assert steps[1]["key"] == "k12:b1"


def test_switch_after_mastery():
    gs = GraphService(mini_graph())
    steps = gs.recommend_paths({"k12:a1": "mastered"}, k=30)
    assert [s["key"] for s in steps] == ["k12:a2", "k12:b1"]


def test_prefers_downstream_over_easy_deadend():
    """超简单死胡同不得胜过稍难但有下游的节点(旧排序此处必红)。"""
    g = mini_graph()
    t = data_loader.Topic(
        key="k12:a0", id="a0", source="k12", book="小学",
        name="凑十口诀", type="", subject="数学", domain="数学", grade=3)
    t.difficulty = 0.05
    g.add_node(t)
    steps = GraphService(g).recommend_paths({}, k=30)
    math = next(s for s in steps if s["subject"] == "数学")
    assert math["key"] == "k12:a1"
    assert math["unlock_count"] >= 2


def test_deadend_fallback_pure_difficulty():
    """全科边界皆无下游时, 回退纯难度且不炸。"""
    g = data_loader.Graph()
    for kid, diff in (("c1", 0.3), ("c2", 0.1)):
        t = data_loader.Topic(
            key="k12:" + kid, id=kid, source="k12", book="小学",
            name=kid, type="", subject="思政", domain="思政", grade=3)
        t.difficulty = diff
        g.add_node(t)
    steps = GraphService(g).recommend_paths({}, k=30)
    assert [s["key"] for s in steps] == ["k12:c2"]
