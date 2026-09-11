# -*- coding: utf-8 -*-
"""统一路径管理：开发态用源码根，打包后只读资源走 _MEIPASS、可写内容走 exe 同级"""
import os
import sys


def is_frozen() -> bool:
    """是否处于 PyInstaller 打包环境"""
    return getattr(sys, "frozen", False)


def project_root() -> str:
    """项目根目录：开发态=paths.py 所在目录，打包后=exe 所在目录"""
    if is_frozen():
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def resource_path(rel: str) -> str:
    """只读资源（web 静态文件等）：打包后从 _MEIPASS 解包目录读"""
    if is_frozen():
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        base = project_root()
    return os.path.join(base, rel)


def writable_dir(rel: str) -> str:
    """可写目录（data/logs 等）：不存在则自动创建"""
    p = os.path.join(project_root(), rel)
    os.makedirs(p, exist_ok=True)
    return p


def writable_file(rel: str) -> str:
    """可写文件路径：只确保父目录存在，绝不创建同名文件"""
    p = os.path.join(project_root(), rel)
    parent = os.path.dirname(p)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return p


WEB_DIR = resource_path("web")
DATA_DIR = writable_dir("data")
LOGS_DIR = writable_dir("logs")
MARBLE_DATA_DIR = os.path.join(project_root(), "marble-data")
PEP_DATA_DIR = os.path.join(project_root(), "pep-data")
DB_FILE = writable_file(os.path.join("data", "student_progress.db"))
