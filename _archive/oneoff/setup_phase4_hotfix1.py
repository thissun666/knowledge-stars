# -*- coding: utf-8 -*-
r'''
setup_phase4_hotfix1.py - Sprint 4 配置链路热修
根因: .env 三代模板混写, 变量名与 config.py 读取名不匹配; 且
uvicorn --reload 不监测 .env, 修改后必须手动重启。
本脚本:
  1. 诊断: dotenv 依赖情况 / 旧 config 是否加载 .env / 现有变量名清单(不显示值)
  2. 备份并重写 .env 为 ZHIPU_* 规范模板(自动迁移旧文件里的真实 Key)
  3. 重写 .env.example
  4. 覆盖 backend/config.py: 零依赖 .env 解析器 + ZHIPU_*/AI_* 双兼容
  5. 覆盖 backend/ai_service.py: is_configured 增加占位符识别(略/changeme 等)
  6. 写入 tests/test_config.py(5 条, 全 monkeypatch)
  7. 打印解析结果(Key 掩码显示); --live 可选真实调用冒烟
'''
import importlib.util
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

CURLY = "".join(chr(c) for c in (0x201C, 0x201D, 0x2018, 0x2019))
BAD_BACKTICK = chr(96) * 3

PLACEHOLDERS = {"", "略", "changeme", "todo", "your-api-key-here"}

FILES = {}

FILES["backend/config.py"] = r'''# -*- coding: utf-8 -*-
"""应用配置: 零依赖 .env 加载 + AI 参数解析。

约定(hotfix1 定稿):
  - .env 采用 ZHIPU_* 命名(规范写法, 见 .env.example); AI_* 为兼容别名
  - 解析优先级: AI_* > ZHIPU_* > 内置默认
  - .env 位于项目根, 本模块导入时加载; 真实环境变量优先于 .env
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


RAW_ENV = load_env_file(paths.project_root() / ".env")


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

FILES["backend/ai_service.py"] = r'''# -*- coding: utf-8 -*-
"""glm-4-flash 客户端: OpenAI 兼容接口, 超时+有限重试+显式降级。

  - is_configured(): Key 为空或占位符(略/your-api-key 等)时返回 False,
    路由层据此返回 code=5, 前端 toast 引导, 不抛异常
  - call_llm(): 仅对网络/响应结构异常重试 MAX_RETRY 次
  - 所有调用方必须捕获 AIError 并转成统一响应, 禁止裸抛到框架层
"""
import logging
import re
import time
from typing import List, Optional, Tuple

import httpx

from backend import config

logger = logging.getLogger("app.ai")

TIMEOUT = 45.0
MAX_RETRY = 2

SUBJECT_CN = {"Mathematics": "数学", "English": "英语"}

_PLACEHOLDERS = {"", "略", "changeme", "todo",
                 "your-api-key-here", "yourapikey"}


class AIError(RuntimeError):
    """AI 调用失败(未配置/网络/响应异常), 由路由层转 code=6。"""


def is_configured() -> bool:
    key = (config.AI_API_KEY or "").strip().lower()
    if key.startswith("your-api-key"):
        return False
    return key not in _PLACEHOLDERS


def call_llm(prompt: str, system: str = "") -> str:
    if not is_configured():
        raise AIError("AI_API_KEY 未配置, 请在 .env 填入智谱 Key 后重启服务")
    messages: List[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    body = {"model": config.AI_MODEL, "messages": messages,
            "temperature": 0.6}
    headers = {"Authorization": "Bearer " + config.AI_API_KEY.strip()}
    url = config.AI_BASE_URL.rstrip("/") + "/chat/completions"
    last: Optional[Exception] = None
    for attempt in range(MAX_RETRY + 1):
        try:
            resp = httpx.post(url, json=body, headers=headers,
                              timeout=TIMEOUT)
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            if content and content.strip():
                return content.strip()
            raise ValueError("空响应")
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            last = exc
            logger.warning("LLM 调用失败(第 %d 次): %s", attempt + 1, exc)
            if attempt < MAX_RETRY:
                time.sleep(0.6 * (attempt + 1))
    raise AIError("AI 调用失败: " + str(last))


def build_explain_prompt(name: str, subject: str, domain: str,
                         grade: int, description: str,
                         prereq_names: List[str]) -> str:
    subj = SUBJECT_CN.get(subject, subject)
    g = grade if grade > 0 else 6
    pre = "、".join(prereq_names) if prereq_names else "无(根知识点)"
    return (
        "请为学生讲解知识点, 用简体中文。\n"
        "学科: " + subj + "\n年级: " + str(g) + "\n领域: " + domain + "\n"
        "知识点: " + name + "\n内容描述: " + (description or "无") +
        "\n前置知识: " + pre + "\n"
        "输出 Markdown, 依次包含四个小节(用 ## 开头):\n"
        "## 是什么\n## 生活中的例子\n## 常见误区\n## 小练习\n"
        "小练习只出 1 道并附答案与解析。"
        "总字数不超过 400 字, 语言贴近 " + str(g) + " 年级学生, 多用比喻。")


def build_translate_prompt(name: str, domain: str,
                           description: str) -> str:
    return (
        "将下面的中小学知识点信息翻译成简体中文。\n"
        "输出格式严格为两行, 不要输出任何其他内容:\n"
        "名称: <知识点中文名, 简洁准确, 不超过 15 个字>\n"
        "描述: <描述译文, 通俗易懂>\n\n"
        "知识点名: " + name + "\n领域: " + domain +
        "\n描述: " + (description or "无"))


def parse_translate(text: str,
                    fallback_name: str,
                    fallback_desc: str) -> Tuple[str, str]:
    """容错解析: 匹配 名称:/描述: 两行; 失败则整段作描述兜底。"""
    name, desc = fallback_name, fallback_desc
    m1 = re.search(r"名称[:：]\s*(.+)", text)
    if m1:
        name = m1.group(1).strip() or name
    m2 = re.search(r"描述[:：]\s*(.+)", text, re.S)
    if m2:
        desc = m2.group(1).strip() or desc
    elif text.strip():
        desc = text.strip()
    return name, desc
'''

FILES["tests/test_config.py"] = r'''# -*- coding: utf-8 -*-
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
'''

ENV_EXAMPLE = """# ===== 知识点星图 配置模板 =====
# 复制为 .env 并填入真实值

# 展示的学科(逗号分隔)
SUBJECTS=English,Mathematics

# 日志级别 DEBUG/INFO/WARNING
LOG_LEVEL=INFO

# ===== AI(智谱 glm-4-flash, 免费) =====
# 申请入口: https://open.bigmodel.cn/ -> 右上角头像 -> API Keys -> 新建
ZHIPU_API_KEY=
ZHIPU_BASE_URL=https://open.bigmodel.cn/api/paas/v4
ZHIPU_MODEL=glm-4-flash
"""


def parse_env(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def mask(k: str) -> str:
    if not k:
        return "(空)"
    if len(k) <= 8:
        return k[0] + "***"
    return k[:6] + "..." + k[-4:]


def diagnose():
    print("[1/6] 诊断现状")
    spec = importlib.util.find_spec("dotenv")
    print("  python-dotenv 已安装: " + ("是" if spec else "否")
          + "(无论装没装, 新 config 不再依赖它)")
    old_cfg = ROOT / "backend" / "config.py"
    if old_cfg.is_file():
        txt = old_cfg.read_text(encoding="utf-8", errors="ignore")
        has = "dotenv" in txt or "load_dotenv" in txt
        print("  旧 config.py 是否引用 dotenv: " + ("是" if has else "否"))
    p = ROOT / ".env"
    if p.is_file():
        d = parse_env(p.read_text(encoding="utf-8", errors="ignore"))
        ai_keys = [k for k in d if any(
            s in k.upper() for s in ("AI_", "ZHIPU", "KEY", "MODEL", "BASE_URL"))]
        print("  当前 .env 变量名(值不显示): " + ", ".join(sorted(d.keys())))
        empty = [k for k in ai_keys if d[k].strip() in PLACEHOLDERS]
        print("  其中占位/为空的: " + (", ".join(empty) if empty else "(无)"))
    else:
        print("  [WARN] .env 不存在")
    return p


def migrate_env(p: Path):
    print("[2/6] 备份并重写 .env(ZHIPU_* 规范, 迁移真实 Key)")
    old = {}
    if p.is_file():
        old = parse_env(p.read_text(encoding="utf-8", errors="ignore"))
        bak = ROOT / (".env.bak." + datetime.now().strftime("%Y%m%d%H%M%S"))
        bak.write_text(p.read_text(encoding="utf-8", errors="ignore"),
                       encoding="utf-8")
        print("  旧文件已备份: " + bak.name)
    key = ""
    for cand in (old.get("AI_API_KEY"), old.get("ZHIPU_API_KEY")):
        v = (cand or "").strip()
        if v.lower() not in PLACEHOLDERS and "your-api-key" not in v.lower():
            key = v
            break
    subjects = (old.get("SUBJECTS") or "English,Mathematics").strip()
    log_level = (old.get("LOG_LEVEL") or "INFO").strip()
    content = (
        "# 由 setup_phase4_hotfix1.py 生成(ZHIPU_* 为规范写法)\n"
        "SUBJECTS=" + subjects + "\n"
        "LOG_LEVEL=" + log_level + "\n\n"
        "# ===== AI(智谱 glm-4-flash, 免费) =====\n"
        "ZHIPU_API_KEY=" + key + "\n"
        "ZHIPU_BASE_URL=https://open.bigmodel.cn/api/paas/v4\n"
        "ZHIPU_MODEL=glm-4-flash\n")
    p.write_text(content, encoding="utf-8")
    print("  ZHIPU_API_KEY = " + (mask(key) if key
          else "(空! 请打开 .env 手动填入)"))
    if not key:
        print("  [WARN] 旧文件中没有识别到真实 Key, 请编辑 .env 填入")


def write_files():
    print("[3/6] 重写 .env.example / [4/6] 覆盖 config.py + ai_service.py "
          "/ [5/6] 写入 test_config.py")
    (ROOT / ".env.example").write_text(ENV_EXAMPLE, encoding="utf-8")
    print("  [OK] .env.example")
    for rel, content in FILES.items():
        p = ROOT / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        print("  [OK] " + rel)


def verify(live: bool):
    print("[6/6] 解析结果(新进程视角)")
    from backend import ai_service, config
    print("  AI_API_KEY  = " + mask(config.AI_API_KEY))
    print("  AI_MODEL    = " + config.AI_MODEL)
    print("  AI_BASE_URL = " + config.AI_BASE_URL)
    ready = ai_service.is_configured()
    print("  is_configured() = " + str(ready))
    if not ready:
        print("  [TODO] 编辑 .env 填入 ZHIPU_API_KEY 后, 手动重启 uvicorn")
        return
    print("  [提示] 浏览器若仍提示未配置, 手动重启 uvicorn"
          "(它不监测 .env 变化)")
    if live:
        try:
            out = ai_service.call_llm("用五个字以内回答: 1+1等于几?")
            print("  [PASS] 真实调用成功: " + out[:40])
        except ai_service.AIError as exc:
            print("  [FAIL] 真实调用失败: " + str(exc))


def self_check():
    bad = []
    for rel, content in FILES.items():
        if BAD_BACKTICK in content or any(c in content for c in CURLY):
            bad.append(rel)
    if ".env.example" and BAD_BACKTICK in ENV_EXAMPLE:
        bad.append(".env.example")
    print("  自检: " + ("[PASS] 交付内容无污染" if not bad
                      else "[FAIL] " + ", ".join(bad)))
    return not bad


if __name__ == "__main__":
    print("项目根: " + str(ROOT))
    env_path = diagnose()
    migrate_env(env_path)
    write_files()
    if self_check():
        verify("--live" in sys.argv)
    print("=" * 56)
    print("下一步:")
    print("  python -m pytest tests/ -q      (预期 42 passed)")
    print("  手动重启: uvicorn backend.main:app --reload --port 8000")
