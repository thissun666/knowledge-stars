# -*- coding: utf-8 -*-
r'''
setup_phase2_hotfix5.py - Sprint 2 热修
  1. routes/progress.py 真实 bug: POST /api/progress 未在变更前预热边界
     缓存, 导致进程内首次变更的 newly/lost 恒为空集(apply_change 契约:
     _boundary 必须反映变更前状态)。修复: 写库前 compute_boundary 预热
  2. test_api.py 断言修正: 快照恢复后 mt_A 已掌握, 边界应顺移到 mt_B,
     原断言误用空进度初始边界 ROOTS
  3. 新增 test_progress_diff_on_second_update: 钉死连续更新的 diff 路径
  预期: 29 passed
'''
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

CURLY = "".join(chr(c) for c in (0x201C, 0x201D, 0x2018, 0x2019))
BAD_BACKTICK = chr(96) * 3

FILES = {}

FILES["backend/routes/progress.py"] = r'''# -*- coding: utf-8 -*-
"""进度读写 + 快照/恢复。业务错误以统一格式 code!=0 返回(HTTP 200)。

hotfix5: POST /api/progress 在写库前先用当前 DB 状态预热边界缓存。
apply_change 的契约要求 _boundary 反映"变更前"状态(见 graph_service
与单元测试 test_delta_matches_full 的用法), 否则进程内首次变更时
newly/lost 会退化为空集。
"""
import logging

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
    gs.compute_boundary(ps.get_all())  # 预热为变更前状态, 保证 diff 正确
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

FILES["tests/test_api.py"] = r'''# -*- coding: utf-8 -*-
"""API 集成测试: 统一响应格式 + 进度流 + 快照恢复, 合成数据零外部依赖。

hotfix5: 修正 restore 后的边界期望(恢复的进度里 mt_A 已掌握,
边界应顺移到其后继 mt_B), 并新增连续更新的 diff 测试。
"""

ROOTS = {"marble:mt_A", "pep:7-up:mt_ch1_001",
         "pep:8-down:mt_ch21_001", "pep:9-up:mt_ch21_001"}
# mt_A 掌握后的边界: mt_A 退出, 其后继 mt_B 进入
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


def test_progress_diff_on_second_update(client):
    """钉死预热路径: 第一次更新后缓存已热, 第二次更新 diff 仍须正确。"""
    r1 = client.post("/api/progress", json={
        "topic_key": "marble:mt_A", "status": "mastered"}).json()
    assert r1["code"] == 0
    assert r1["data"]["newly_unlocked"] == ["marble:mt_B"]
    assert r1["data"]["no_longer_boundary"] == ["marble:mt_A"]
    r2 = client.post("/api/progress", json={
        "topic_key": "marble:mt_B", "status": "mastered"}).json()
    assert r2["code"] == 0
    assert r2["data"]["newly_unlocked"] == []  # mt_B 无后继
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
    assert r["code"] == 0
    sid = r["data"]["snapshot_id"]
    client.post("/api/progress", json={
        "topic_key": "marble:mt_B", "status": "mastered"})
    r2 = client.post("/api/progress/restore",
                     json={"snapshot_id": sid}).json()
    assert r2["code"] == 0
    assert r2["data"]["restored"] == 1
    # 恢复后进度回到 {mt_A: mastered}; 此时 mt_A 退出边界, mt_B 进入
    assert set(r2["data"]["boundary"]) == AFTER_A
    r3 = client.get("/api/progress").json()
    assert r3["data"]["progress"] == {"marble:mt_A": "mastered"}
    assert set(r3["data"]["boundary"]) == AFTER_A
'''


def write_files():
    print("[1/3] 覆盖交付文件 ...")
    for rel, content in FILES.items():
        p = ROOT / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        print("  [FIX]  " + rel)


def self_check():
    print("[2/3] 自检报告")
    ok = True
    for rel, content in FILES.items():
        if BAD_BACKTICK in content:
            print("  [FAIL] " + rel + " 三反引号污染")
            ok = False
        if any(c in content for c in CURLY):
            print("  [FAIL] " + rel + " 弯引号污染")
            ok = False
    if ok:
        print("  [PASS] 交付内容无三反引号/弯引号污染")
    cases = sum(content.count("def test_") for content in FILES.values())
    print("  test_api.py 用例数: " + str(cases))


def next_steps():
    print("[3/3] 下一步命令")
    print("  python -m pytest tests/ -v")
    print("=" * 56)


if __name__ == "__main__":
    print("项目根: " + str(ROOT))
    write_files()
    self_check()
    next_steps()
