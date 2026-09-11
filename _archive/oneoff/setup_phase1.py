# -*- coding: utf-8 -*-
r'''
setup_phase1.py - Sprint 1 一键交付脚本（Python 3.9 兼容版）
交付文件：
  paths.py / logging_setup.py / backend/config.py / backend/data_loader.py
  tests/conftest.py / tests/test_data_loader.py
规则：
  1. 文件不存在才写入，--force 参数可强制覆盖
  2. .env.example 为模板直接覆盖；.env 仅修正 SUBJECTS 行，绝不动 API Key
  3. 末尾自检：文件数量、三反引号污染、弯引号污染
'''
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FORCE = "--force" in sys.argv

ENV_EXAMPLE = """AI_PROVIDER=zhipu
AI_MODEL=glm-4-flash
AI_API_KEY=your-api-key-here
AI_BASE_URL_ZHIPU=https://open.bigmodel.cn/api/paas/v4
AI_BASE_URL_GITHUB=https://models.inference.ai.azure.com
SUBJECTS=English,Mathematics
HOST=127.0.0.1
PORT=8000
LOG_LEVEL=DEBUG
"""

FILES = {}

FILES["paths.py"] = r'''# -*- coding: utf-8 -*-
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
'''

FILES["logging_setup.py"] = r'''# -*- coding: utf-8 -*-
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
'''

FILES["backend/config.py"] = r'''# -*- coding: utf-8 -*-
"""集中读取 .env，全项目唯一配置入口（Python 3.9 兼容写法）"""
import os
from typing import List

from dotenv import load_dotenv

import paths

load_dotenv(paths.writable_file(".env"))


def _get(key: str, default: str = "") -> str:
    v = os.getenv(key, default)
    return v.strip() if v else default


AI_PROVIDER = _get("AI_PROVIDER", "zhipu").lower()
AI_API_KEY = _get("AI_API_KEY", "")
AI_MODEL = _get("AI_MODEL", "glm-4-flash")
AI_BASE_URL = (
    _get("AI_BASE_URL_ZHIPU") if AI_PROVIDER == "zhipu"
    else _get("AI_BASE_URL_GITHUB")
)
SUBJECTS: List[str] = [
    s for s in _get("SUBJECTS", "English,Mathematics").split(",") if s
]
HOST = _get("HOST", "127.0.0.1")
PORT = int(_get("PORT", "8000"))
LOG_LEVEL = _get("LOG_LEVEL", "DEBUG")
'''

FILES["backend/data_loader.py"] = r'''# -*- coding: utf-8 -*-
"""双源图谱加载：Marble（小学，按学科过滤）+ pep（初中数学），合并为统一图。

关键设计：
  - 节点全局键 = source + ":" + id（marble:mt_xxx / pep:mt_chN_MMM），天然隔离
  - 边只允许同源解析（两套数据各自是独立 DAG，跨源引用一律丢弃）
  - pep 数据为 6 册分目录（data/ 根 + 5 个子目录），用 glob 统一扫描
  - get_graph() 线程安全惰性单例，首次调用才读盘
"""
import glob
import json
import os
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import paths
from backend import config


@dataclass
class Topic:
    key: str
    id: str
    source: str
    name: str
    type: str
    subject: str
    domain: str
    grade: int
    description: str = ""
    evidence: List[str] = field(default_factory=list)


@dataclass
class Edge:
    prereq_key: str
    topic_key: str
    strength: str = "hard"
    reason: str = ""


@dataclass
class Graph:
    nodes: Dict[str, Topic] = field(default_factory=dict)
    edges: List[Edge] = field(default_factory=list)
    adj_out: Dict[str, List[str]] = field(default_factory=dict)
    adj_in: Dict[str, List[str]] = field(default_factory=dict)

    def add_node(self, t: Topic) -> None:
        self.nodes.setdefault(t.key, t)

    def add_edge(self, e: Edge) -> bool:
        if e.prereq_key == e.topic_key:
            return False
        if e.prereq_key not in self.nodes or e.topic_key not in self.nodes:
            return False
        if e.topic_key in self.adj_out.get(e.prereq_key, []):
            return False
        self.edges.append(e)
        self.adj_out.setdefault(e.prereq_key, []).append(e.topic_key)
        self.adj_in.setdefault(e.topic_key, []).append(e.prereq_key)
        return True


def _read_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _to_topic(t: dict, source: str) -> Topic:
    try:
        grade = max(1, int(t.get("ageRangeEnd")) - 5)
    except (TypeError, ValueError):
        grade = 0
    return Topic(
        key=source + ":" + str(t.get("id", "")),
        id=str(t.get("id", "")),
        source=source,
        name=str(t.get("name", "")),
        type=str(t.get("type", "")),
        subject=str(t.get("subject", "")),
        domain=str(t.get("domain", "")),
        grade=grade,
        description=str(t.get("description", "")),
        evidence=list(t.get("evidence") or []),
    )


def _marble_part(marble_dir: str, subjects: List[str]) -> Tuple[List[dict], List[dict]]:
    data_dir = os.path.join(marble_dir, "data")
    tp = os.path.join(data_dir, "topics.json")
    dp = os.path.join(data_dir, "dependencies.json")
    if not os.path.isfile(tp):
        return [], []
    raw = _read_json(tp).get("topics", [])
    allowed = set(subjects) if subjects else None
    topics = [t for t in raw if allowed is None or t.get("subject") in allowed]
    deps = []
    if os.path.isfile(dp):
        deps = _read_json(dp).get("dependencies", [])
    return topics, deps


def _pep_parts(pep_dir: str) -> List[Tuple[List[dict], List[dict]]]:
    data_dir = os.path.join(pep_dir, "data")
    if not os.path.isdir(data_dir):
        return []
    patterns = [
        os.path.join(data_dir, "topics.json"),
        os.path.join(data_dir, "*", "topics.json"),
    ]
    found = sorted(sum([glob.glob(p) for p in patterns], []))
    parts = []
    for tp in found:
        dp = os.path.join(os.path.dirname(tp), "dependencies.json")
        deps = _read_json(dp).get("dependencies", []) if os.path.isfile(dp) else []
        parts.append((_read_json(tp).get("topics", []), deps))
    return parts


def build_graph(marble_topics: List[dict], marble_deps: List[dict],
                pep_parts: List[Tuple[List[dict], List[dict]]],
                subjects: Optional[List[str]] = None) -> Graph:
    g = Graph()
    allowed = set(subjects) if subjects else None
    index: Dict[Tuple[str, str], Topic] = {}

    for t in marble_topics:
        if allowed is not None and t.get("subject") not in allowed:
            continue
        topic = _to_topic(t, "marble")
        g.add_node(topic)
        index[("marble", topic.id)] = topic

    for topics, _ in pep_parts:
        for t in topics:
            topic = _to_topic(t, "pep")
            g.add_node(topic)
            index[("pep", topic.id)] = topic

    def wire(source: str, dep: dict) -> None:
        pre = index.get((source, str(dep.get("prerequisiteId", ""))))
        nxt = index.get((source, str(dep.get("topicId", ""))))
        if pre is None or nxt is None:
            return
        g.add_edge(Edge(prereq_key=pre.key, topic_key=nxt.key,
                        strength=str(dep.get("strength", "hard")),
                        reason=str(dep.get("reason", ""))))

    for d in marble_deps:
        wire("marble", d)
    for _, deps in pep_parts:
        for d in deps:
            wire("pep", d)
    return g


def build_graph_from_dirs(base_dir=None,
                          subjects: Optional[List[str]] = None) -> Graph:
    root = str(base_dir) if base_dir else paths.project_root()
    mt, md = _marble_part(os.path.join(root, "marble-data"),
                          subjects or config.SUBJECTS)
    pp = _pep_parts(os.path.join(root, "pep-data"))
    return build_graph(mt, md, pp, subjects)


def stats(g: Graph) -> dict:
    by_source: Dict[str, int] = {}
    by_subject: Dict[str, int] = {}
    for n in g.nodes.values():
        by_source[n.source] = by_source.get(n.source, 0) + 1
        by_subject[n.subject] = by_subject.get(n.subject, 0) + 1
    return {
        "total_nodes": len(g.nodes),
        "total_edges": len(g.edges),
        "by_source": by_source,
        "by_subject": by_subject,
    }


_graph: Optional[Graph] = None
_lock = threading.Lock()


def get_graph() -> Graph:
    global _graph
    if _graph is None:
        with _lock:
            if _graph is None:
                _graph = build_graph_from_dirs()
    return _graph


if __name__ == "__main__":
    graph = get_graph()
    s = stats(graph)
    print("nodes=" + str(s["total_nodes"]) + " edges=" + str(s["total_edges"]))
    print("by_source=" + json.dumps(s["by_source"], ensure_ascii=False))
    print("by_subject=" + json.dumps(s["by_subject"], ensure_ascii=False))
'''

FILES["tests/conftest.py"] = r'''# -*- coding: utf-8 -*-
"""公共 fixture：微型双源数据目录，测试完全不依赖真实仓库"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def mini_sources(tmp_path: Path) -> Path:
    marble = tmp_path / "marble-data" / "data"
    _write(marble / "topics.json", {"topics": [
        {"id": "mt_A", "type": "CONCEPTUAL", "subject": "Mathematics",
         "domain": "d", "name": "A", "description": "",
         "ageRangeStart": 7, "ageRangeEnd": 8},
        {"id": "mt_B", "type": "PROCEDURAL", "subject": "Mathematics",
         "domain": "d", "name": "B", "description": "",
         "ageRangeStart": 8, "ageRangeEnd": 9},
        {"id": "mt_C", "type": "CONCEPTUAL", "subject": "Science",
         "domain": "d", "name": "C", "description": "",
         "ageRangeStart": 8, "ageRangeEnd": 9},
    ]})
    _write(marble / "dependencies.json", {"dependencies": [
        {"topicId": "mt_B", "prerequisiteId": "mt_A",
         "strength": "hard", "reason": "r1"},
        {"topicId": "mt_B", "prerequisiteId": "mt_C",
         "strength": "soft", "reason": "C 属 Science 应被过滤，此边须丢弃"},
    ]})
    pep = tmp_path / "pep-data" / "data"
    _write(pep / "topics.json", {"topics": [
        {"id": "mt_ch1_001", "type": "CONCEPTUAL", "subject": "Mathematics",
         "domain": "有理数", "name": "正负数", "description": "",
         "ageRangeStart": 12, "ageRangeEnd": 12},
    ]})
    _write(pep / "7-up" / "topics.json", {"topics": [
        {"id": "mt_ch2_001", "type": "PROCEDURAL", "subject": "Mathematics",
         "domain": "整式", "name": "合并同类项", "description": "",
         "ageRangeStart": 12, "ageRangeEnd": 13},
    ]})
    _write(pep / "7-up" / "dependencies.json", {"dependencies": [
        {"topicId": "mt_ch2_001", "prerequisiteId": "mt_ch1_001",
         "strength": "hard", "reason": "整式运算需要先会有理数"},
        {"topicId": "mt_ch2_001", "prerequisiteId": "mt_A",
         "strength": "soft", "reason": "跨源引用无法解析，此边须丢弃"},
    ]})
    return tmp_path
'''

FILES["tests/test_data_loader.py"] = r'''# -*- coding: utf-8 -*-
"""data_loader 单元测试：合成数据验证逻辑 + 真实数据冒烟（缺失则跳过）"""
import os

import pytest

import paths
from backend import data_loader

HAS_MARBLE = os.path.isdir(paths.MARBLE_DATA_DIR)
HAS_PEP = os.path.isdir(paths.PEP_DATA_DIR)


def test_mini_merge_filter_and_edges(mini_sources):
    g = data_loader.build_graph_from_dirs(mini_sources,
                                          subjects=["Mathematics"])
    # Science 节点被过滤，只剩 marble 两点 + pep 两点
    assert set(g.nodes.keys()) == {
        "marble:mt_A", "marble:mt_B", "pep:mt_ch1_001", "pep:mt_ch2_001",
    }
    keys = [(e.prereq_key, e.topic_key) for e in g.edges]
    # 同源 marble 边保留
    assert ("marble:mt_A", "marble:mt_B") in keys
    # 同源 pep 跨册边保留（7-up 依赖根目录册）
    assert ("pep:mt_ch1_001", "pep:mt_ch2_001") in keys
    # 引用被过滤节点的边丢弃
    assert ("marble:mt_C", "marble:mt_B") not in keys
    # 跨源引用丢弃
    assert ("marble:mt_A", "pep:mt_ch2_001") not in keys
    assert len(g.edges) == 2


def test_mini_stats(mini_sources):
    g = data_loader.build_graph_from_dirs(mini_sources,
                                          subjects=["Mathematics"])
    s = data_loader.stats(g)
    assert s["total_nodes"] == 4
    assert s["total_edges"] == 2
    assert s["by_source"] == {"marble": 2, "pep": 2}
    assert s["by_subject"] == {"Mathematics": 4}


def test_mini_grade_and_adjacency(mini_sources):
    g = data_loader.build_graph_from_dirs(mini_sources,
                                          subjects=["Mathematics"])
    assert g.nodes["pep:mt_ch2_001"].grade == 8  # ageEnd 13 - 5
    assert g.adj_out["marble:mt_A"] == ["marble:mt_B"]
    assert g.adj_in["marble:mt_B"] == ["marble:mt_A"]


def test_no_self_loop(mini_sources):
    g = data_loader.build_graph_from_dirs(mini_sources,
                                          subjects=["Mathematics"])
    added = g.add_edge(
        data_loader.Edge(prereq_key="marble:mt_A", topic_key="marble:mt_A"))
    assert added is False


@pytest.mark.skipif(not HAS_MARBLE, reason="marble-data 未克隆")
def test_real_marble_subjects_filtered():
    topics, _ = data_loader._marble_part(
        paths.MARBLE_DATA_DIR, ["English", "Mathematics"])
    assert len(topics) > 700  # 数学503 + 英语286 约789
    assert all(t["subject"] in ("English", "Mathematics") for t in topics)


@pytest.mark.skipif(not HAS_PEP, reason="pep-data 未克隆")
def test_real_pep_six_books():
    parts = data_loader._pep_parts(paths.PEP_DATA_DIR)
    total = sum(len(t) for t, _ in parts)
    assert 160 <= total <= 180  # README 声称 169
    g = data_loader.build_graph_from_dirs(subjects=["Mathematics"])
    pep_keys = [k for k in g.nodes if k.startswith("pep:")]
    assert len(pep_keys) >= 160


@pytest.mark.skipif(not (HAS_MARBLE and HAS_PEP),
                    reason="需要两个数据仓库齐备")
def test_real_graph_smoke():
    g = data_loader.get_graph()
    s = data_loader.stats(g)
    assert s["total_nodes"] > 900
    assert s["total_edges"] > 2000
'''


BAD_BACKTICK = "`" * 3
BAD_QUOTES = ("“", "”", "‘", "’")
EXCLUDE = {"venv", "marble-data", "pep-data", ".git", "__pycache__",
           ".pytest_cache", ".ruff_cache", "node_modules"}


def write_files():
    print("[1/4] 写入交付文件（不存在才写，--force 可覆盖）...")
    ok, skip = 0, 0
    for rel, content in FILES.items():
        p = ROOT / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists() and not FORCE:
            print("  [SKIP] " + rel + "（已存在）")
            skip += 1
            continue
        p.write_text(content, encoding="utf-8")
        print("  [OK]   " + rel)
        ok += 1
    for pkg in ("backend/__init__.py", "backend/routes/__init__.py",
                "tests/__init__.py"):
        p = ROOT / pkg
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.touch()
            print("  [OK]   " + pkg + "（补建空包标识）")
    print("  合计：写入 " + str(ok) + "，跳过 " + str(skip))


def write_env():
    print("[2/4] 更新配置模板与 .env ...")
    (ROOT / ".env.example").write_text(
        ENV_EXAMPLE, encoding="utf-8")
    print("  [FIX]  .env.example 已更新（模板文件，直接覆盖）")
    env = ROOT / ".env"
    if not env.exists():
        env.write_text(ENV_EXAMPLE, encoding="utf-8")
        print("  [WARN] .env 原不存在，已按模板生成，请填入真实 API Key")
        return
    lines = env.read_text(encoding="utf-8").splitlines()
    target = "SUBJECTS=English,Mathematics"
    out, changed, found = [], False, False
    for ln in lines:
        if ln.strip().startswith("SUBJECTS="):
            found = True
            if ln.strip() != target:
                changed = True
            out.append(target)
        else:
            out.append(ln)
    if not found:
        out.append(target)
        changed = True
    if changed:
        env.write_text("\n".join(out) + "\n", encoding="utf-8")
        print("  [FIX]  .env 的 SUBJECTS 已改为 " + target)
    else:
        print("  [SKIP] .env 的 SUBJECTS 已是最新")


def check_content(name: str, text: str, bad_b: list, bad_q: list):
    if BAD_BACKTICK in text:
        bad_b.append(name)
    for q in BAD_QUOTES:
        if q in text:
            bad_q.append(name)
            return


def self_check():
    print("=" * 60)
    print("[3/4] 自检报告")
    bad_b, bad_q = [], []
    total = 0
    for rel, content in FILES.items():
        total += 1
        check_content(rel, content, bad_b, bad_q)
        p = ROOT / rel
        if p.exists():
            check_content(rel + "（磁盘）",
                          p.read_text(encoding="utf-8"), bad_b, bad_q)
    for p in ROOT.rglob("*"):
        if not p.is_file():
            continue
        if set(p.parts) & EXCLUDE:
            continue
        if p.suffix in (".py", ".txt", ".md", ".example"):
            try:
                check_content(str(p.relative_to(ROOT)),
                              p.read_text(encoding="utf-8"), bad_b, bad_q)
                total += 1
            except Exception:
                pass
    print("  扫描文件数: " + str(total))
    if bad_b:
        print("  [FAIL] 三反引号污染: " + ", ".join(sorted(set(bad_b))))
    else:
        print("  [PASS] 无三反引号污染")
    if bad_q:
        print("  [FAIL] 弯引号污染: " + ", ".join(sorted(set(bad_q))))
    else:
        print("  [PASS] 无弯引号污染")


def next_steps():
    print("[4/4] 建议下一步命令")
    print("  python -m pytest tests/ -v")
    print("  python -m backend.data_loader")
    print("  ruff check .")
    print("=" * 60)


if __name__ == "__main__":
    print("项目根: " + str(ROOT))
    write_files()
    write_env()
    self_check()
    next_steps()
