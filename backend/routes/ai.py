# -*- coding: utf-8 -*-
"""AI 路由: 讲解/翻译(缓存) + 纠错 + 追问 + 出题/批改(练习闭环)。

  code=5 AI未配置 | code=2 未知知识点 | code=6 AI调用失败 | code=1 参数缺失
  出题不缓存(每次新题); 批改无状态(题目+作答由前端回传)。
"""
import json
import logging
from typing import Any, List

from fastapi import APIRouter
from pydantic import BaseModel

from backend import ai_service, schemas, state, ai_settings

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


class PracticeIn(BaseModel):
    topic_key: str


class GradeIn(BaseModel):
    topic_key: str
    question: str
    student_answer: str


def _topic_or_err(gs, topic_key: str):
    if not ai_service.is_configured():
        return None, schemas.err(
            "AI 未配置: 点右上角 [AI 设置], 选供应商填 Key 即可 "
            "保存后即时生效", code=5)
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


def _prereq_names(gs, topic_key: str) -> List[str]:
    out = []
    for pk in gs.graph.adj_in.get(topic_key, [])[:6]:
        t2 = gs.graph.nodes.get(pk)
        if t2:
            out.append(t2.name)
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
    prompt = ai_service.build_explain_prompt(
        topic.name, topic.subject, topic.domain, topic.grade,
        topic.description, _prereq_names(gs, body.topic_key))
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


@router.post("/ai/practice")
def ai_practice(body: PracticeIn):
    gs = state.get_graph_service()
    topic, err = _topic_or_err(gs, body.topic_key)
    if err:
        return err
    prompt = ai_service.build_practice_prompt(
        topic.name, topic.subject, topic.domain, topic.grade,
        topic.description, _prereq_names(gs, body.topic_key))
    try:
        question = ai_service.call_llm(prompt)
    except ai_service.AIError as exc:
        return schemas.err(str(exc), code=6)
    logger.debug("出题: %s (%d 字)", body.topic_key, len(question))
    return schemas.ok({"question": question})


@router.post("/ai/grade")
def ai_grade(body: GradeIn):
    gs = state.get_graph_service()
    ps = state.get_progress()
    topic, err = _topic_or_err(gs, body.topic_key)
    if err:
        return err
    q = body.question.strip()[:2000]
    a = body.student_answer.strip()[:2000]
    if not q or not a:
        return schemas.err("题目与你的作答均不能为空", code=1)
    prompt = ai_service.build_grade_prompt(
        topic.name, topic.subject, topic.domain, topic.grade,
        topic.description, _prereq_status(gs, ps, body.topic_key),
        q, a)
    try:
        content = ai_service.call_llm(prompt)
    except ai_service.AIError as exc:
        return schemas.err(str(exc), code=6)
    correct = ai_service.parse_judgement(content)
    logger.debug("批改: %s 判定=%s", body.topic_key, correct)
    return schemas.ok({"content": content, "correct": correct})


class AISettingsIn(BaseModel):
    provider: str = ""
    key: str = ""
    model: str = ""
    base_url: str = ""


@router.get("/ai/config")
def ai_config_get():
    return schemas.ok(ai_settings.current())


@router.post("/ai/config")
def ai_config_set(body: AISettingsIn):
    try:
        d = ai_settings.apply(body.provider, body.key, body.model, body.base_url)
    except ValueError as exc:
        return schemas.err(str(exc), code=1)
    return schemas.ok({"provider": d["provider"], "model": d["model"],
                       "base_url": d["base_url"]})


@router.post("/ai/config/test")
def ai_config_test():
    try:
        reply = ai_service.call_llm("只回复两个字母: OK", system="连通性测试")
    except ai_service.AIError as exc:
        return schemas.err(str(exc), code=6)
    return schemas.ok({"reply": reply[:60]})
