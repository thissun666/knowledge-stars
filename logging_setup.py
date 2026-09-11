# -*- coding: utf-8 -*-
"""双日志机制：debug.log 全量自用可轮转；error.log 仅异常且强制脱敏后可导出"""
import logging
import os
import re
from logging.handlers import RotatingFileHandler

import paths

DEBUG_FILE = os.path.join(paths.LOGS_DIR, "debug.log")
ERROR_FILE = os.path.join(paths.LOGS_DIR, "error.log")
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 5

# 业务敏感字段：error.log 中出现一律打码（含堆栈文本）
SENSITIVE_PATTERN = re.compile(
    r"(student_name|answer|api_key|apikey|token|topic_id)\s*[=:]\s*[^\s,;&]+",
    re.IGNORECASE,
)


class SanitizedFormatter(logging.Formatter):
    """格式化后再过一遍脱敏正则，覆盖 message 参数展开与异常堆栈"""

    def format(self, record):
        text = super().format(record)
        return SENSITIVE_PATTERN.sub(r"\1=***", text)


def setup_logging(level: str = "DEBUG") -> logging.Logger:
    """幂等配置：重复调用（如 --reload）不会叠加 handler"""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.DEBUG))
    for h in list(root.handlers):
        root.removeHandler(h)

    fmt_dev = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    fmt_err = SanitizedFormatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    hd = RotatingFileHandler(DEBUG_FILE, maxBytes=MAX_BYTES,
                             backupCount=BACKUP_COUNT, encoding="utf-8")
    hd.setLevel(logging.DEBUG)
    hd.setFormatter(fmt_dev)
    root.addHandler(hd)

    he = RotatingFileHandler(ERROR_FILE, maxBytes=MAX_BYTES,
                             backupCount=BACKUP_COUNT, encoding="utf-8")
    he.setLevel(logging.ERROR)
    he.setFormatter(fmt_err)
    root.addHandler(he)

    hc = logging.StreamHandler()
    hc.setLevel(logging.INFO)
    hc.setFormatter(fmt_dev)
    root.addHandler(hc)

    return logging.getLogger("app")
