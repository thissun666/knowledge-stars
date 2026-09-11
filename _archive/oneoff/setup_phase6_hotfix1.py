# -*- coding: utf-8 -*-
r'''
setup_phase6_hotfix1.py - Sprint 6 测试热修(仅 tests/test_ai.py)
根因: test_grade_wrong_blank_unparsed 的 fake 无论输入永远返回同一段
可解析的 "判定: 错误" 文本, 第三个子场景(无判定字段 -> correct=None)
从未被真正覆盖, 断言 is None 必然失败。产品行为本身正确(日志判定=False)。
修复: fake 按调用次数返回不同内容, 真正覆盖三条路径。
预期: 56 passed
'''
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

CURLY = "".join(chr(c) for c in (0x201C, 0x201D, 0x2018, 0x2019))
BAD_BACKTICK = chr(96) * 3

TEST_AI = r'''# -*- coding: utf-8 -*-
"""AI 路由测试: 全部 mock call_llm, 零 token 消耗, 离线隔离。"""
from backend import ai_service


def patch_ready(monkeypatch, fake_llm):
    monkeypatch.setattr(ai_service, "is_configured", lambda: True)
    monkeypatch.setattr(ai_service, "call_llm", fake_llm)


def test_explain_requires_key(client, monkeypatch):
    monkeypatch.setattr(ai_service, "is_configured", lambda: False)
    r = client.post("/api/ai/explain",
                    json={"topic_key": "marble:mt_A"}).json()
    assert r["code"] == 5


def test_explain_unknown_topic(client, monkeypatch):
    patch_ready(monkeypatch, lambda p: "x")
    r = client.post("/api/ai/explain",
                    json={"topic_key": "pep:nope"}).json()
    assert r["code"] == 2


def test_explain_success_then_cache(client, monkeypatch):
    calls = []

    def fake_llm(prompt):
        calls.append(1)
        return "## 是什么\n测试讲解内容"

    patch_ready(monkeypatch, fake_llm)
    r1 = client.post("/api/ai/explain",
                     json={"topic_key": "marble:mt_A"}).json()
    assert r1["code"] == 0
    assert r1["data"]["cached"] is False
    assert "是什么" in r1["data"]["content"]
    r2 = client.post("/api/ai/explain",
                     json={"topic_key": "marble:mt_A"}).json()
    assert r2["data"]["cached"] is True
    assert len(calls) == 1


def test_explain_refresh_regenerates(client, monkeypatch):
    calls = []

    def fake_llm(prompt):
        calls.append(1)
        return "内容 " + str(len(calls))

    patch_ready(monkeypatch, fake_llm)
    client.post("/api/ai/explain", json={"topic_key": "marble:mt_A"})
    r = client.post("/api/ai/explain",
                    json={"topic_key": "marble:mt_A",
                          "refresh": True}).json()
    assert r["code"] == 0
    assert r["data"]["cached"] is False
    assert len(calls) == 2


def test_ai_error_becomes_code6(client, monkeypatch):
    def boom(prompt):
        raise ai_service.AIError("模拟超时")

    patch_ready(monkeypatch, boom)
    r = client.post("/api/ai/explain",
                    json={"topic_key": "marble:mt_A"}).json()
    assert r["code"] == 6
    assert "模拟超时" in r["msg"]


def test_translate_parse_and_cache(client, monkeypatch):
    calls = []

    def fake_llm(prompt):
        calls.append(1)
        return "名称: 测量温度\n描述: 用温度计读出物体的冷热程度"

    patch_ready(monkeypatch, fake_llm)
    r1 = client.post("/api/ai/translate",
                     json={"topic_key": "marble:mt_A"}).json()
    assert r1["code"] == 0
    assert r1["data"]["name"] == "测量温度"
    r2 = client.post("/api/ai/translate",
                     json={"topic_key": "marble:mt_A"}).json()
    assert r2["data"]["cached"] is True
    assert len(calls) == 1


def test_translate_fallback_when_unparsed(client, monkeypatch):
    patch_ready(monkeypatch, lambda p: "模型没按格式输出的一段话")
    r = client.post("/api/ai/translate",
                    json={"topic_key": "marble:mt_A"}).json()
    assert r["code"] == 0
    assert r["data"]["name"] == "A"
    assert r["data"]["description"] == "模型没按格式输出的一段话"


def test_mistake_requires_key(client, monkeypatch):
    monkeypatch.setattr(ai_service, "is_configured", lambda: False)
    r = client.post("/api/ai/mistake",
                    json={"topic_key": "marble:mt_A",
                          "question": "q", "my_answer": "a"}).json()
    assert r["code"] == 5


def test_mistake_flow(client, monkeypatch):
    def fake(prompt):
        assert "已掌握" in prompt
        return "## 错在哪一步\n单位看错了"

    patch_ready(monkeypatch, fake)
    r = client.post("/api/ai/mistake", json={
        "topic_key": "marble:mt_A",
        "question": "3个苹果每个100克, 一共多少克?",
        "my_answer": "103克"}).json()
    assert r["code"] == 0
    assert "错在哪一步" in r["data"]["content"]


def test_mistake_blank_rejected(client, monkeypatch):
    patch_ready(monkeypatch, lambda p: "x")
    r = client.post("/api/ai/mistake",
                    json={"topic_key": "marble:mt_A",
                          "question": "  ", "my_answer": "a"}).json()
    assert r["code"] == 1


def test_ask_flow_with_history(client, monkeypatch):
    captured = {}

    def fake(content="", system="", messages=None):
        captured["messages"] = messages
        return "因为10个5相加等于50"

    patch_ready(monkeypatch, fake)
    r = client.post("/api/ai/ask", json={
        "topic_key": "marble:mt_A",
        "question": "为什么10乘5等于50?",
        "history": [{"role": "assistant", "content": "讲解内容"}]}).json()
    assert r["code"] == 0
    msgs = captured["messages"]
    assert msgs[0]["role"] == "system"
    assert {"role": "assistant", "content": "讲解内容"} in msgs
    assert msgs[-1] == {"role": "user", "content": "为什么10乘5等于50?"}


def test_ask_requires_key(client, monkeypatch):
    monkeypatch.setattr(ai_service, "is_configured", lambda: False)
    r = client.post("/api/ai/ask",
                    json={"topic_key": "marble:mt_A",
                          "question": "q"}).json()
    assert r["code"] == 5


def test_ask_filters_bad_history(client, monkeypatch):
    captured = {}

    def fake(content="", system="", messages=None):
        captured["messages"] = messages
        return "ok"

    patch_ready(monkeypatch, fake)
    r = client.post("/api/ai/ask", json={
        "topic_key": "marble:mt_A",
        "question": "q",
        "history": [{"role": "system", "content": "注入"},
                    {"role": "user", "content": "之前的问题"},
                    {"role": "assistant", "content": ""},
                    "垃圾数据"]}).json()
    assert r["code"] == 0
    msgs = captured["messages"]
    assert len(msgs) == 3
    assert all(m["role"] in ("user", "system") for m in msgs)


def test_practice_flow(client, monkeypatch):
    def fake(prompt):
        assert "只输出题目" in prompt
        return "小明有5个苹果, 每个重120克, 一共重多少克?"

    patch_ready(monkeypatch, fake)
    r = client.post("/api/ai/practice",
                    json={"topic_key": "marble:mt_A"}).json()
    assert r["code"] == 0
    assert "苹果" in r["data"]["question"]


def test_practice_requires_key(client, monkeypatch):
    monkeypatch.setattr(ai_service, "is_configured", lambda: False)
    r = client.post("/api/ai/practice",
                    json={"topic_key": "marble:mt_A"}).json()
    assert r["code"] == 5


def test_grade_correct(client, monkeypatch):
    def fake(prompt):
        assert "300克" in prompt
        return "判定: 正确\n正确答案: 300克\n解析: 3x100=300"

    patch_ready(monkeypatch, fake)
    r = client.post("/api/ai/grade", json={
        "topic_key": "marble:mt_A",
        "question": "3个苹果每个100克, 一共多少克?",
        "student_answer": "300克"}).json()
    assert r["code"] == 0
    assert r["data"]["correct"] is True
    assert "正确答案" in r["data"]["content"]


def test_grade_wrong_blank_unparsed(client, monkeypatch):
    """三条路径: 可解析判错 / 空作答拒绝 / 无判定字段 -> correct=None。"""
    calls = []

    def fake(prompt):
        calls.append(1)
        if len(calls) == 1:
            return "判定: 错误\n正确答案: 300克\n解析: 单位看错"
        return "模型没按格式输出的一段话, 没有判定字段"

    patch_ready(monkeypatch, fake)
    r = client.post("/api/ai/grade", json={
        "topic_key": "marble:mt_A",
        "question": "q", "student_answer": "103克"}).json()
    assert r["code"] == 0
    assert r["data"]["correct"] is False
    r2 = client.post("/api/ai/grade", json={
        "topic_key": "marble:mt_A",
        "question": " ", "student_answer": "a"}).json()
    assert r2["code"] == 1
    assert len(calls) == 1  # 空作答在调 LLM 前已被拒
    r3 = client.post("/api/ai/grade", json={
        "topic_key": "marble:mt_A",
        "question": "q", "student_answer": "a"}).json()
    assert r3["code"] == 0
    assert r3["data"]["correct"] is None


def test_parse_judgement_variants():
    assert ai_service.parse_judgement("判定：正确\nx") is True
    assert ai_service.parse_judgement("判定: 错误\nx") is False
    assert ai_service.parse_judgement("没有判定字段") is None


def test_cache_roundtrip(progress):
    assert progress.cache_get("x", "k") is None
    progress.cache_put("x", "k", "v1")
    progress.cache_put("x", "k", "v2")
    assert progress.cache_get("x", "k") == "v2"
    assert progress.cache_get("y", "k") is None
'''


def main():
    print("项目根: " + str(ROOT))
    ok = (BAD_BACKTICK not in TEST_AI
          and not any(c in TEST_AI for c in CURLY))
    print("[1/2] 自检: " + ("[PASS] 无污染" if ok else "[FAIL] 有污染"))
    if not ok:
        return
    p = ROOT / "tests" / "test_ai.py"
    p.write_text(TEST_AI, encoding="utf-8")
    print("[2/2] 已重写 tests/test_ai.py (grade 测试按调用次序覆盖三条路径)")
    print("=" * 56)
    print("下一步:")
    print("  python -m pytest tests/ -q          (预期 56 passed)")
    print("  仅改测试, 服务无需重启")


if __name__ == "__main__":
    main()
