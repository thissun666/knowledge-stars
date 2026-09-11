# -*- coding: utf-8 -*-
"""API 集成测试: 统一响应格式 + 进度流 + 快照恢复 + 路径推荐 + 学段子图。"""
ROOTS = {"marble:mt_A", "pep:7-up:mt_ch1_001",
         "pep:8-down:mt_ch21_001", "pep:9-up:mt_ch21_001"}
AFTER_A = (ROOTS - {"marble:mt_A"}) | {"marble:mt_B"}


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


def test_graph_band_primary(client):
    r = client.get("/api/graph", params={"band": "小学"}).json()
    assert r["code"] == 0
    d = r["data"]
    assert {n["key"] for n in d["nodes"]} == {"marble:mt_A", "marble:mt_B"}
    assert len(d["edges"]) == 1
    assert set(d["boundary"]) == {"marble:mt_A"}


def test_graph_band_middle(client):
    r = client.get("/api/graph", params={"band": "初中"}).json()
    d = r["data"]
    keys = {n["key"] for n in d["nodes"]}
    assert "pep:7-up:mt_ch1_001" in keys
    assert "marble:mt_A" not in keys
    assert len(keys) == 6
    assert set(d["boundary"]) == ROOTS - {"marble:mt_A"}


def test_graph_band_unknown_falls_back_full(client):
    r = client.get("/api/graph", params={"band": "幼儿园"}).json()
    assert len(r["data"]["nodes"]) == 8


def test_graph_band_progress_shared(client):
    client.post("/api/progress", json={
        "topic_key": "marble:mt_A", "status": "mastered"})
    r = client.get("/api/graph", params={"band": "小学"}).json()
    assert r["data"]["progress"] == {"marble:mt_A": "mastered"}
    assert set(r["data"]["boundary"]) == {"marble:mt_B"}


def test_graph_recommend(client):
    r = client.get("/api/graph/recommend").json()
    assert r["code"] == 0
    steps = r["data"]["steps"]
    assert 1 <= len(steps) <= 5
    assert "key" in steps[0] and "reason" in steps[0]
    assert steps[0]["order"] == 1
    assert set(s["key"] for s in steps) <= ROOTS


def test_graph_recommend_band(client):
    r = client.get("/api/graph/recommend", params={"band": "小学"}).json()
    steps = r["data"]["steps"]
    assert all(s["key"].startswith("marble:") for s in steps)


def test_progress_update_flow(client):
    r = client.post("/api/progress", json={
        "topic_key": "pep:7-up:mt_ch1_001",
        "status": "mastered"}).json()
    assert r["code"] == 0
    assert r["data"]["newly_unlocked"] == ["pep:7-down:mt_ch5_001"]
    assert r["data"]["no_longer_boundary"] == ["pep:7-up:mt_ch1_001"]


def test_progress_diff_on_second_update(client):
    r1 = client.post("/api/progress", json={
        "topic_key": "marble:mt_A", "status": "mastered"}).json()
    assert r1["data"]["newly_unlocked"] == ["marble:mt_B"]
    r2 = client.post("/api/progress", json={
        "topic_key": "marble:mt_B", "status": "mastered"}).json()
    assert r2["data"]["newly_unlocked"] == []
    assert r2["data"]["no_longer_boundary"] == ["marble:mt_B"]


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
    sid = r["data"]["snapshot_id"]
    client.post("/api/progress", json={
        "topic_key": "marble:mt_B", "status": "mastered"})
    r2 = client.post("/api/progress/restore",
                     json={"snapshot_id": sid}).json()
    assert r2["code"] == 0
    assert set(r2["data"]["boundary"]) == AFTER_A
    r3 = client.get("/api/progress").json()
    assert r3["data"]["progress"] == {"marble:mt_A": "mastered"}
    assert set(r3["data"]["boundary"]) == AFTER_A
