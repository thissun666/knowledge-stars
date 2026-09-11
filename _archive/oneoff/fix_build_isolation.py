# -*- coding: utf-8 -*-
r'''fix_build_isolation.py - 修复构建器自食循环: 加载后剥离推断边, 基底运行'''
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BL = ROOT / "build_inferred_links.py"

src = BL.read_text(encoding="utf-8")
if "剔除推断" in src:
    print("[SKIP] 已修过")
    sys.exit(0)

lines = src.splitlines()
hits = [i for i, ln in enumerate(lines) if ln.strip() == "g = data_loader.get_graph()"]
if len(hits) != 1:
    print("[ABORT] 锚点异常: " + str(len(hits)))
    sys.exit(1)
i = hits[0]
lines[i + 1:i + 1] = [
    "# Sprint11.5修复: 构建须以无推断边基底运行, 否则上一轮产物被当已有边吞掉",
    "g.edges = [e for e in g.edges if e.strength != \"inferred\"]",
    "g.adj_out, g.adj_in = {}, {}",
    "for _e in g.edges:",
    "    g.adj_out.setdefault(_e.prereq_key, []).append(_e.topic_key)",
    "    g.adj_in.setdefault(_e.topic_key, []).append(_e.prereq_key)",
    "print(\"  基底边(剔除推断) \" + str(len(g.edges)))",
]
out = "\n".join(lines) + "\n"
try:
    compile(out, str(BL), "exec")
except SyntaxError as exc:
    print("[ABORT] 语法错误, 未写盘: " + str(exc))
    sys.exit(1)
BL.write_text(out, encoding="utf-8")
print("[OK] 构建器已隔离推断边")

r = subprocess.run([sys.executable, "build_inferred_links.py"],
                   cwd=str(ROOT), capture_output=True, text=True)
out_lines = (r.stdout or "").strip().splitlines()
for ln in out_lines[-12:]:
    print("  | " + ln)
if r.returncode != 0:
    print("[FAIL] " + (r.stderr or "")[-400:])
