# -*- coding: utf-8 -*-
# cleanup_oneoff.py - 一次性补丁/诊断脚本归档器(移动到 _archive/oneoff/, 不删除)
# 保险: 守卫名单永不移动 | 移动前引用扫描命中即跳过 | 幂等 | 生成MANIFEST
import fnmatch
import re
import shutil
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEST = ROOT / "_archive" / "oneoff"

GUARD = {
    "build_phase2_edges.py", "build_inferred_links.py",
    "prep_phase2.py", "run_phase2.py", "inspect_inferred_quality.py",
    "paths.py", "logging_setup.py", "setup_dirs.py",
    "pytest.ini", "README.md", "requirements.txt",
    ".gitignore", ".env.example",
}
PATTERNS = [
    "setup_phase*.py", "patch_*.py", "diag_*.py", "fix_*.py",
    "locate_*.py", "probe_*.py", "purge_*.py", "scan_*.py",
    "inspect_*.py", "extract_subject.py", "audit_phase2_pilot.py",
    "setup_cachebust.py",
]
SCAN_SKIP = {"node_modules", "dist", "build", "__pycache__",
             ".git", "venv", "_archive", "data", "logs"}


def corpus():
    texts = {}
    for d in ("backend", "tests", "web"):
        base = ROOT / d
        if not base.exists():
            continue
        for f in base.rglob("*.py"):
            if any(p in SCAN_SKIP for p in f.parts):
                continue
            try:
                texts[f] = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                pass
    for name in GUARD:
        f = ROOT / name
        if f.exists():
            try:
                texts[f] = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                pass
    return texts


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    cands = sorted(
        f for f in ROOT.glob("*.py")
        if f.name not in GUARD
        and any(fnmatch.fnmatch(f.name, p) for p in PATTERNS))
    texts = corpus()
    moved, held = [], []
    for f in cands:
        dst = DEST / f.name
        if dst.exists():
            continue
        pat = re.compile(r"\b" + re.escape(f.stem) + r"\b")
        hit = [str(t.relative_to(ROOT)) for t, s in texts.items()
               if pat.search(s)]
        if hit:
            held.append((f.name, hit[:3]))
            continue
        shutil.move(str(f), dst)
        moved.append(f.name)
    man = ["# 一次性脚本归档清单", "",
           "归档时间: " + time.strftime("%Y-%m-%d %H:%M"),
           "数量: " + str(len(moved)), "",
           "均为一次性补丁/诊断脚本, 效果已并入 backend/ 或 data/ 产物;",
           "本目录仅供审计追溯, 可整体删除。", ""]
    man += ["- " + n for n in moved]
    if held:
        man += ["", "## 因被现役代码引用而留在原地(需人工判断)", ""]
        man += ["- " + n + "  <- " + ", ".join(h) for n, h in held]
    (DEST / "MANIFEST.md").write_text("\n".join(man) + "\n", encoding="utf-8")
    print("[OK] 归档 " + str(len(moved)) + " 个 -> _archive/oneoff")
    for n in moved:
        print("  - " + n)
    for n, h in held:
        print("  [留在原地-被引用] " + n + " <- " + ", ".join(h))
    left = sorted(f.name for f in ROOT.glob("*.py"))
    print("[剩余根目录.py] " + ", ".join(left))


if __name__ == "__main__":
    main()
