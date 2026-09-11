# -*- coding: utf-8 -*-
r'''
setup_phase4_hotfix2.py - 修复 config.py 对 paths.project_root() 返回类型的
错误假设(str 而非 Path), 仅重写 backend/config.py 一个文件。
验证: python setup_phase4_hotfix2.py [--live]
'''
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

CURLY = "".join(chr(c) for c in (0x201C, 0x201D, 0x2018, 0x2019))
BAD_BACKTICK = chr(96) * 3
PLACEHOLDERS = {"", "略", "changeme", "todo", "your-api-key-here"}

CONFIG = r'''# -*- coding: utf-8 -*-
"""应用配置: 零依赖 .env 加载 + AI 参数解析。

约定(hotfix2 定稿):
  - .env 采用 ZHIPU_* 命名(规范写法, 见 .env.example); AI_* 为兼容别名
  - 解析优先级: AI_* > ZHIPU_* > 内置默认
  - .env 位于项目根, 本模块导入时加载; 真实环境变量优先于 .env
  - paths.project_root() 返回 str, 必须 Path() 包裹后再做路径运算
  - 修改 .env 后需重启进程(uvicorn --reload 只监测 .py, 不监测 .env)
"""
import os
from pathlib import Path

import paths

ZHIPU_DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_MODEL = "glm-4-flash"


def parse_env_text(text: str) -> dict:
    """纯函数: 解析 .env 文本; 规则: 忽略注释/空行/无等号行, 去成对引号。"""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        if k:
            out[k] = v
    return out


def load_env_file(path) -> dict:
    """读取 .env 并 setdefault 进 os.environ(真实环境变量优先)。"""
    p = Path(path)
    data = {}
    if p.is_file():
        try:
            data = parse_env_text(p.read_text(encoding="utf-8"))
        except OSError:
            data = {}
    for k, v in data.items():
        os.environ.setdefault(k, v)
    return data


RAW_ENV = load_env_file(Path(paths.project_root()) / ".env")


def _get(*names, default=""):
    for n in names:
        v = (os.environ.get(n) or "").strip()
        if v:
            return v
    return default


def resolve_ai_config() -> dict:
    """实时解析 AI 三元组(便于测试); 模块属性在导入时固化。"""
    return {
        "key": _get("AI_API_KEY", "ZHIPU_API_KEY"),
        "model": _get("AI_MODEL", "ZHIPU_MODEL", default=DEFAULT_MODEL),
        "base_url": _get("AI_BASE_URL", "ZHIPU_BASE_URL",
                         default=ZHIPU_DEFAULT_BASE_URL).rstrip("/"),
    }


_AI = resolve_ai_config()
AI_API_KEY = _AI["key"]
AI_MODEL = _AI["model"]
AI_BASE_URL = _AI["base_url"]

SUBJECTS = [s.strip() for s in
            _get("SUBJECTS", default="English,Mathematics").split(",")
            if s.strip()]
LOG_LEVEL = _get("LOG_LEVEL", default="INFO")
'''


def mask(k: str) -> str:
    if not k:
        return "(空)"
    if len(k) <= 8:
        return k[0] + "***"
    return k[:6] + "..." + k[-4:]


def main():
    print("项目根: " + str(ROOT))
    p = ROOT / "backend" / "config.py"
    p.write_text(CONFIG, encoding="utf-8")
    print("[1/3] 已重写 backend/config.py (Path() 包裹 .env 路径)")
    ok = BAD_BACKTICK not in CONFIG and not any(c in CONFIG for c in CURLY)
    print("[2/3] 自检: " + ("[PASS] 无污染" if ok else "[FAIL] 有污染"))
    if not ok:
        return
    print("[3/3] 解析结果(新进程视角)")
    from backend import ai_service, config
    print("  project_root 类型 = " + type(paths_root()).__name__)
    print("  AI_API_KEY  = " + mask(config.AI_API_KEY))
    print("  AI_MODEL    = " + config.AI_MODEL)
    print("  AI_BASE_URL = " + config.AI_BASE_URL)
    if ai_service.is_configured():
        print("  is_configured() = True")
        if "--live" in sys.argv:
            try:
                out = ai_service.call_llm("用五个字以内回答: 1+1等于几?")
                print("  [PASS] 真实调用成功: " + out[:40])
            except ai_service.AIError as exc:
                print("  [FAIL] 真实调用失败: " + str(exc))
        else:
            print("  (可加 --live 做真实调用冒烟)")
    else:
        print("  is_configured() = False -> 请编辑 .env 填 ZHIPU_API_KEY")
    print("=" * 56)
    print("下一步:")
    print("  python -m pytest tests/ -q      (预期 42 passed)")
    print("  手动重启: uvicorn backend.main:app --reload --port 8000")


def paths_root():
    import paths
    return paths.project_root()


if __name__ == "__main__":
    main()
