# -*- coding: utf-8 -*-
r'''
setup_phase5_hotfix1.py - Sprint 5 热修
根因: AskIn.history 声明为 List[dict], Pydantic 在路由前拒绝含非dict
条目的历史(code=422), 路由内的宽容过滤器未按设计意图执行。
修复: history 放宽为 List[Any], 由路由过滤器统一清洗
(跳过非dict/非法role/空content), 与 test_ask_filters_bad_history 对齐。
预期: 51 passed
'''
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

CURLY = "".join(chr(c) for c in (0x201C, 0x201D, 0x2018, 0x2019))
BAD_BACKTICK = chr(96) * 3

AI_ROUTES = r'''# -*- coding: utf-8 -*-
"""AI 路由: 讲解/翻译(带缓存) + 错题纠错 + 多轮追问(带图谱上下文)。

  code=5 AI未配置 | code=2 未知知识点 | code=6 AI调用失败 | code=1 参数缺失
  hotfix1: AskIn.history 放宽为 List[Any], 垃圾条目由路由过滤器清洗,
  避免 Pydantic 在路由前整体拒绝(设计意图: 宽容过滤而非硬失败)。
"""
import json
import logging
from typing import Any, List

from fastapi import APIRouter
from pydantic import BaseModel

from backend import ai_service, schemas, state

logger = logging.getLogger("app.api.ai")
router = APIRouter(tags=["ai"])


class ExplainIn(BaseModel):
    topic_key: str
    refresh: bool = False


class TranslateIn(BaseModel):
    topic_key: str


class MistakeIn(BaseModel):
    topic_key: str
    question: str
    my_answer: str


class AskIn(BaseModel):
    topic_key: str
    question: str
    history: List[Any] = []


def _topic_or_err(gs, topic_key: str):
    if not ai_service.is_configured():
        return None, schemas.err(
            "未配置 AI_API_KEY, 请在 .env 填入智谱 Key "
            "(open.bigmodel.cn 免费申请)并重启服务", code=5)
    topic = gs.graph.nodes.get(topic_key)
    if topic is None:
        return None, schemas.err("未知知识点: " + topic_key, code=2)
    return topic, None


def _prereq_status(gs, ps, topic_key: str) -> List[tuple]:
    progress = ps.get_all()
    out = []
    for pk in gs.graph.adj_in.get(topic_key, [])[:8]:
        t2 = gs.graph.nodes.get(pk)
        out.append((t2.name if t2 else pk,
                    progress.get(pk) == "mastered"))
    return out


@router.post("/ai/explain")
def ai_explain(body: ExplainIn):
    gs = state.get_graph_service()
    ps = state.get_progress()
    topic, err = _topic_or_err(gs, body.topic_key)
    if err:
        return err
    if not body.refresh:
        hit = ps.cache_get("explain", body.topic_key)
        if hit:
            return schemas.ok({"content": hit, "cached": True})
    pre_names = []
    for pk in gs.graph.adj_in.get(body.topic_key, [])[:6]:
        t2 = gs.graph.nodes.get(pk)
        if t2:
            pre_names.append(t2.name)
    prompt = ai_service.build_explain_prompt(
        topic.name, topic.subject, topic.domain, topic.grade,
        topic.description, pre_names)
    try:
        content = ai_service.call_llm(prompt)
    except ai_service.AIError as exc:
        return schemas.err(str(exc), code=6)
    ps.cache_put("explain", body.topic_key, content)
    logger.debug("讲解生成: %s (%d 字)", body.topic_key, len(content))
    return schemas.ok({"content": content, "cached": False})


@router.post("/ai/translate")
def ai_translate(body: TranslateIn):
    gs = state.get_graph_service()
    ps = state.get_progress()
    topic, err = _topic_or_err(gs, body.topic_key)
    if err:
        return err
    hit = ps.cache_get("translate", body.topic_key)
    if hit:
        try:
            return schemas.ok(dict(json.loads(hit), cached=True))
        except ValueError:
            pass
    prompt = ai_service.build_translate_prompt(
        topic.name, topic.domain, topic.description)
    try:
        text = ai_service.call_llm(prompt)
    except ai_service.AIError as exc:
        return schemas.err(str(exc), code=6)
    name, desc = ai_service.parse_translate(text, topic.name,
                                            topic.description)
    payload = json.dumps({"name": name, "description": desc},
                         ensure_ascii=False)
    ps.cache_put("translate", body.topic_key, payload)
    return schemas.ok({"name": name, "description": desc, "cached": False})


@router.post("/ai/mistake")
def ai_mistake(body: MistakeIn):
    gs = state.get_graph_service()
    ps = state.get_progress()
    topic, err = _topic_or_err(gs, body.topic_key)
    if err:
        return err
    q = body.question.strip()[:2000]
    a = body.my_answer.strip()[:2000]
    if not q or not a:
        return schemas.err("题目与你的答案/思路均不能为空", code=1)
    prompt = ai_service.build_mistake_prompt(
        topic.name, topic.subject, topic.domain, topic.grade,
        topic.description, _prereq_status(gs, ps, body.topic_key),
        q, a)
    try:
        content = ai_service.call_llm(prompt)
    except ai_service.AIError as exc:
        return schemas.err(str(exc), code=6)
    logger.debug("错题纠错: %s (%d 字)", body.topic_key, len(content))
    return schemas.ok({"content": content})


@router.post("/ai/ask")
def ai_ask(body: AskIn):
    gs = state.get_graph_service()
    ps = state.get_progress()
    topic, err = _topic_or_err(gs, body.topic_key)
    if err:
        return err
    q = body.question.strip()[:1000]
    if not q:
        return schemas.err("追问内容不能为空", code=1)
    system = ai_service.build_ask_system(
        topic.name, topic.subject, topic.domain, topic.grade,
        _prereq_status(gs, ps, body.topic_key))
    msgs = [{"role": "system", "content": system}]
    for h in body.history[-10:]:
        role = h.get("role") if isinstance(h, dict) else None
        content = h.get("content") if isinstance(h, dict) else None
        if role in ("user", "assistant") and isinstance(content, str) \
                and content.strip():
            msgs.append({"role": role, "content": content.strip()[:4000]})
    msgs.append({"role": "user", "content": q})
    try:
        content = ai_service.call_llm(messages=msgs)
    except ai_service.AIError as exc:
        return schemas.err(str(exc), code=6)
    logger.debug("追问: %s (%d 字)", body.topic_key, len(content))
    return schemas.ok({"content": content})
'''


def main():
    print("项目根: " + str(ROOT))
    ok = (BAD_BACKTICK not in AI_ROUTES
          and not any(c in AI_ROUTES for c in CURLY))
    print("[1/2] 自检: " + ("[PASS] 无污染" if ok else "[FAIL] 有污染"))
    if not ok:
        return
    p = ROOT / "backend" / "routes" / "ai.py"
    p.write_text(AI_ROUTES, encoding="utf-8")
    print("[2/2] 已重写 backend/routes/ai.py (history: List[Any] + 过滤器清洗)")
    print("=" * 56)
    print("下一步:")
    print("  python -m pytest tests/ -v          (预期 51 passed)")
    print("  重启服务后浏览器验收")


if __name__ == "__main__":
    main()
