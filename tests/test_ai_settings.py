# -*- coding: utf-8 -*-
import json

import pytest

from backend import ai_settings, config


@pytest.fixture()
def clean():
    saved = (getattr(config, "AI_PROVIDER", ""), config.AI_API_KEY,
             config.AI_MODEL, config.AI_BASE_URL)
    if ai_settings.FILE.exists():
        ai_settings.FILE.unlink()
    yield
    config.AI_PROVIDER = saved[0]
    config.AI_API_KEY = saved[1]
    config.AI_MODEL = saved[2]
    config.AI_BASE_URL = saved[3]
    if ai_settings.FILE.exists():
        ai_settings.FILE.unlink()


def test_apply_requires_key(clean):
    with pytest.raises(ValueError):
        ai_settings.apply("zhipu", "", "glm-4-flash")


def test_apply_requires_model(clean):
    with pytest.raises(ValueError):
        ai_settings.apply("zhipu", "sk-test-123456", "")


def test_apply_unknown_provider(clean):
    with pytest.raises(ValueError):
        ai_settings.apply("nope", "sk-test-123456", "m")


def test_apply_updates_runtime_and_persists(clean):
    ai_settings.apply("deepseek", "sk-test-123456", "deepseek-chat")
    assert config.AI_API_KEY == "sk-test-123456"
    assert config.AI_MODEL == "deepseek-chat"
    assert config.AI_BASE_URL == "https://api.deepseek.com/v1"
    back = json.loads(ai_settings.FILE.read_text(encoding="utf-8"))
    assert back["provider"] == "deepseek"


def test_empty_key_keeps_existing(clean):
    ai_settings.apply("deepseek", "sk-first-123456", "deepseek-chat")
    ai_settings.apply("deepseek", "", "deepseek-reasoner")
    assert config.AI_API_KEY == "sk-first-123456"
    assert config.AI_MODEL == "deepseek-reasoner"
