# -*- coding: utf-8 -*-
"""AI 供应商运行时配置: data/ai_config.json 持久化, 应用到 config 模块属性。

优先级: 运行时设置(ai_config.json) > .env > 内置默认
- load_initial() 导入时应用已存设置(pytest 进程跳过, 防真实Key污染用例)
- apply() 校验+应用+落盘, 即时生效免重启
- Key 明文存本地 data/(已gitignore), 不上传
"""
import json
import logging
import os
import sys
from pathlib import Path

import paths
from backend import ai_service, config

logger = logging.getLogger("app.ai_settings")

FILE = Path(paths.writable_file(os.path.join("data", "ai_config.json")))

PROVIDERS = {
    "zhipu": {"label": "智谱 GLM", "base_url": "https://open.bigmodel.cn/api/paas/v4",
              "models": ["glm-4-flash", "glm-4-air", "glm-4-plus"]},
    "deepseek": {"label": "DeepSeek", "base_url": "https://api.deepseek.com/v1",
                 "models": ["deepseek-chat", "deepseek-reasoner"]},
    "kimi": {"label": "Kimi 月之暗面", "base_url": "https://api.moonshot.cn/v1",
             "models": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"]},
    "ark": {"label": "火山方舟", "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            "models": ["doubao-pro-32k", "doubao-lite-32k"]},
}


def current() -> dict:
    key = (config.AI_API_KEY or "").strip()
    masked = (key[:4] + "****" + key[-4:]) if len(key) >= 12 else ("已设置" if key else "")
    return {"provider": getattr(config, "AI_PROVIDER", ""), "key_masked": masked,
            "has_key": ai_service.is_configured(), "model": config.AI_MODEL,
            "base_url": config.AI_BASE_URL, "providers": PROVIDERS}


def apply(provider: str, key: str, model: str, base_url: str = "") -> dict:
    provider = (provider or "").strip()
    if provider and provider not in PROVIDERS:
        raise ValueError("未知供应商: " + provider)
    key = (key or "").strip() or (config.AI_API_KEY or "").strip()
    model = (model or "").strip()
    if not key:
        raise ValueError("API Key 不能为空")
    if not model:
        raise ValueError("模型不能为空")
    if not base_url.strip():
        base_url = PROVIDERS[provider]["base_url"] if provider in PROVIDERS \
            else (config.AI_BASE_URL or "")
    data = {"provider": provider, "key": key, "model": model,
            "base_url": base_url.strip().rstrip("/")}
    config.AI_PROVIDER = data["provider"]
    config.AI_API_KEY = data["key"]
    config.AI_MODEL = data["model"]
    config.AI_BASE_URL = data["base_url"]
    FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    logger.info("AI 设置已更新: provider=%s model=%s", provider, model)
    return data


def load_initial() -> None:
    if "pytest" in sys.modules or not FILE.is_file():
        return
    try:
        d = json.loads(FILE.read_text(encoding="utf-8"))
        config.AI_PROVIDER = d.get("provider", "")
        if d.get("key"):
            config.AI_API_KEY = d["key"]
        if d.get("model"):
            config.AI_MODEL = d["model"]
        if d.get("base_url"):
            config.AI_BASE_URL = d["base_url"]
        logger.info("已加载 AI 设置: provider=%s model=%s",
                    config.AI_PROVIDER, config.AI_MODEL)
    except (OSError, ValueError) as exc:
        logger.warning("ai_config.json 读取失败, 沿用 .env: %s", exc)


load_initial()
