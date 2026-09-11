# -*- coding: utf-8 -*-
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
