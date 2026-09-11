# -*- coding: utf-8 -*-
r'''
setup_dirs.py — 一键创建项目目录骨架与基础配置文件
规则：
  1. 目录已存在则跳过，绝不删除任何东西
  2. 配置文件不存在才写入，绝不覆盖
  3. 数据仓库目录自动改名（os-taxonomy -> marble-data 等）
  4. .env 不存在时从 .env.example 复制生成
  5. 末尾自检：文件数量、三反引号污染、弯引号污染
'''
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent

DIRS = [
    "backend",
    "backend/routes",
    "tests",
    "data",
    "logs",
    "web",
    "web/css",
    "web/js",
    "web/vendor",
]

# 空的包标识文件
EMPTY_FILES = [
    "backend/__init__.py",
    "backend/routes/__init__.py",
    "tests/__init__.py",
]

# 带初始内容的文件（仅不存在时写入）
TEXT_FILES = {
    "requirements.txt": """fastapi==0.115.6
uvicorn[standard]==0.30.6
pydantic==2.9.2
python-dotenv==1.0.1
httpx==0.27.2
pytest==8.3.3
pytest-asyncio==0.24.0
ruff==0.7.1
""",
    ".gitignore": """venv/
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
.env
data/
logs/
marble-data/
pep-data/
.DS_Store
""",
    ".env.example": """# ===== AI 接口配置（二选一，改 AI_PROVIDER 即可切换）=====
# 智谱：AI_PROVIDER=zhipu  AI_MODEL=glm-4-flash
# GitHub Models：AI_PROVIDER=github  AI_MODEL=gpt-4o-mini
AI_PROVIDER=zhipu
AI_API_KEY=your-api-key-here

# 智谱的 base_url
AI_BASE_URL_ZHIPU=https://open.bigmodel.cn/api/paas/v4
# GitHub Models 的 base_url
AI_BASE_URL_GITHUB=https://models.inference.ai.azure.com

# ===== 数据过滤：只加载这些学科（逗号分隔，留空 = 全部）=====
# Marble 含 8 个学科，本项目默认只取数学
SUBJECTS=Mathematics

# ===== 服务 =====
HOST=127.0.0.1
PORT=8000
LOG_LEVEL=DEBUG
""",
    "README.md": """# 知识点星图导航
基于 Marble（小学）+ pep-math-taxonomy（初中）的小学-初中数学知识图谱可视化工具。
详见项目计划书 v2.0。
""",
}

# 数据仓库目录改名映射
REPO_RENAME = [
    ("os-taxonomy", "marble-data"),
    ("pep-math-taxonomy", "pep-data"),
]

# 自检时排除的目录（不属于项目代码）
EXCLUDE = {"venv", "marble-data", "pep-data", ".git", "__pycache__",
           ".pytest_cache", ".ruff_cache"}

BAD_BACKTICK = "`" * 3
BAD_QUOTES = ("“", "”", "‘", "’")


def step_dirs():
    print("[1/5] 创建目录 ...")
    for d in DIRS:
        p = ROOT / d
        if p.exists():
            print("  [SKIP] " + d + " 已存在")
        else:
            p.mkdir(parents=True, exist_ok=True)
            print("  [OK]   " + d)


def step_empty_files():
    print("[2/5] 创建包标识文件 ...")
    for f in EMPTY_FILES:
        p = ROOT / f
        if p.exists():
            print("  [SKIP] " + f)
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.touch()
            print("  [OK]   " + f)


def step_text_files():
    print("[3/5] 写入配置文件（不存在才写）...")
    for name, content in TEXT_FILES.items():
        p = ROOT / name
        if p.exists():
            print("  [SKIP] " + name + " 已存在，不覆盖")
        else:
            p.write_text(content, encoding="utf-8")
            print("  [OK]   " + name)


def step_repos():
    print("[4/5] 检查数据仓库目录 ...")
    for old, new in REPO_RENAME:
        old_p, new_p = ROOT / old, ROOT / new
        if new_p.exists():
            print("  [OK]   " + new + " 已就位")
        elif old_p.exists():
            old_p.rename(new_p)
            print("  [MOVE] " + old + " -> " + new)
        else:
            print("  [WARN] 未找到 " + old + "，请确认克隆位置或手动改名为 " + new)


def step_env():
    print("[5/5] 生成 .env ...")
    env_p = ROOT / ".env"
    example_p = ROOT / ".env.example"
    if env_p.exists():
        print("  [SKIP] .env 已存在，不覆盖")
    elif example_p.exists():
        shutil.copy(example_p, env_p)
        print("  [OK]   .env 已从模板生成，请填入真实 API Key")
    else:
        print("  [WARN] 缺少 .env.example，跳过")


def iter_project_files():
    for p in ROOT.rglob("*"):
        if not p.is_file():
            continue
        parts = set(p.parts)
        if parts & EXCLUDE:
            continue
        yield p


def self_check():
    print("=" * 56)
    files = list(iter_project_files())
    print("自检报告")
    print("  项目内文件总数（不含 venv/数据仓库）: " + str(len(files)))

    ext_count = {}
    bad_backtick, bad_quote = [], []
    for p in files:
        ext = p.suffix.lower()
        ext_count[ext] = ext_count.get(ext, 0) + 1
        if ext not in (".py", ".txt", ".md", ".example", ".gitignore", ".env"):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        if BAD_BACKTICK in text:
            bad_backtick.append(str(p.relative_to(ROOT)))
        for q in BAD_QUOTES:
            if q in text:
                bad_quote.append(str(p.relative_to(ROOT)))
                break

    summary = "  文件类型统计: "
    summary += ", ".join(k + "=" + str(v) for k, v in sorted(ext_count.items()))
    print(summary)

    if bad_backtick:
        print("  [FAIL] 三反引号污染: " + ", ".join(bad_backtick))
    else:
        print("  [PASS] 无三反引号污染")
    if bad_quote:
        print("  [FAIL] 弯引号污染: " + ", ".join(bad_quote))
    else:
        print("  [PASS] 无弯引号污染")

    print("-" * 56)
    print("目录结构：")
    print_tree(ROOT, "")


def print_tree(directory: Path, prefix: str, depth: int = 0):
    if depth > 3:
        return
    entries = sorted(directory.iterdir(),
                     key=lambda x: (x.is_file(), x.name.lower()))
    for e in entries:
        if e.name in EXCLUDE:
            continue
        tag = "[D] " if e.is_dir() else "[F] "
        print(prefix + tag + e.name)
        if e.is_dir():
            print_tree(e, prefix + "    ", depth + 1)


if __name__ == "__main__":
    print("项目根: " + str(ROOT))
    step_dirs()
    step_empty_files()
    step_text_files()
    step_repos()
    step_env()
    self_check()
    print("完成。下一步: pip install -r requirements.txt")