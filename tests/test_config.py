# -*- coding: utf-8 -*-
"""config 测试: .env 解析与 AI 参数解析(全部 monkeypatch, 不读真实 .env)。"""
from backend import config


def test_parse_env_text():
    text = "# 注释\nA=1\n\n  B = \"hello world\"  \nC=x=y\nBAD LINE\nD=\n"
    d = config.parse_env_text(text)
    assert d == {"A": "1", "B": "hello world", "C": "x=y", "D": ""}


def test_resolve_prefers_ai_over_zhipu(monkeypatch):
    monkeypatch.setenv("AI_API_KEY", "k-new")
    monkeypatch.setenv("ZHIPU_API_KEY", "k-old")
    assert config.resolve_ai_config()["key"] == "k-new"


def test_resolve_falls_back_to_zhipu(monkeypatch):
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.setenv("ZHIPU_API_KEY", "k-old")
    assert config.resolve_ai_config()["key"] == "k-old"


def test_resolve_defaults(monkeypatch):
    for n in ("AI_API_KEY", "ZHIPU_API_KEY", "AI_MODEL", "ZHIPU_MODEL",
              "AI_BASE_URL", "ZHIPU_BASE_URL"):
        monkeypatch.delenv(n, raising=False)
    r = config.resolve_ai_config()
    assert r["model"] == "glm-4-flash"
    assert r["base_url"] == "https://open.bigmodel.cn/api/paas/v4"
    assert r["key"] == ""


def test_is_configured_rejects_placeholder(monkeypatch):
    from backend import ai_service
    monkeypatch.setattr(config, "AI_API_KEY", "略")
    assert ai_service.is_configured() is False
    monkeypatch.setattr(config, "AI_API_KEY", "real-key-123")
    assert ai_service.is_configured() is True
