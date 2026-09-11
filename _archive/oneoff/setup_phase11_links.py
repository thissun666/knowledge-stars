# -*- coding: utf-8 -*-
r'''setup_phase11_links.py - Sprint11阶段1: data_loader 挂推断边装载口
    锚点: "def get_graph() -> Graph:" 与 "_graph = build_graph_from_dirs()"
    幂等: 已挂则零改动退出 | 写盘前内存编译 | 子进程全图装载冒烟'''
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DL = ROOT / "backend" / "data_loader.py"

FUNC = '''def _load_inferred_links(g: Graph, path: str = "") -> int:
    r"""Sprint11: 可选装载 data/inferred_links.json (prerequisites 推导缝合边,
    build_inferred_links.py 产物, 构建时已过 SCC 剔环)。
    文件缺失/损坏静默降级为 0 条; 逐条走 add_edge 守卫(自环/悬空/去重)。"""
    from pathlib import Path as _P
    p = _P(path) if path else (
        _P(__file__).resolve().parent.parent / "data" / "inferred_links.json")
    if not p.exists():
        logger.info("推断边文件缺失, 跳过: %s", str(p))
        return 0
    try:
        entries = json.loads(p.read_text(encoding="utf-8")).get("edges") or []
    except (ValueError, OSError) as exc:
        logger.warning("推断边文件损坏, 跳过: %s", exc)
        return 0
    added = 0
    for ent in entries:
        if g.add_edge(Edge(prereq_key=str(ent.get("from", "")),
                           topic_key=str(ent.get("to", "")),
                           strength="inferred",
                           reason="ai-prereq:" + str(ent.get("via", "")))):
            added += 1
    logger.info("推断边装载: %d/%d", added, len(entries))
    return added
'''

src = DL.read_text(encoding="utf-8")
if "def _load_inferred_links" in src:
    print("[SKIP] 已挂过 _load_inferred_links, 零改动退出")
    sys.exit(0)

lines = src.splitlines()


def anchor(pat, what):
    hits = [i for i, ln in enumerate(lines) if ln.strip() == pat]
    if len(hits) != 1:
        print("[ABORT] 锚点不唯一/缺失: " + what
              + " (命中" + str(len(hits)) + "处)")
        sys.exit(1)
    return hits[0]


i_def = anchor("def get_graph() -> Graph:", "def get_graph")
i_build = anchor("_graph = build_graph_from_dirs()", "build调用")

# 调用点插在 build 之后(先插高位, 不影响低位索引)
indent = lines[i_build][:len(lines[i_build]) - len(lines[i_build].lstrip())]
lines.insert(i_build + 1, indent + "_load_inferred_links(_graph)")

# 函数体插在 get_graph 定义之前(再插低位)
func_lines = FUNC.rstrip("\n").splitlines()
for off, fl in enumerate(func_lines):
    lines.insert(i_def + off, fl)
lines.insert(i_def + len(func_lines), "")
lines.insert(i_def + len(func_lines) + 1, "")

out = "\n".join(lines) + "\n"
try:
    compile(out, str(DL), "exec")
except SyntaxError as exc:
    print("[ABORT] 生成代码语法错误, 未写盘: " + str(exc))
    sys.exit(1)
DL.write_text(out, encoding="utf-8")
print("[OK] data_loader 已挂钩 (函数1处 + 调用1处), 语法校验通过")

r = subprocess.run(
    [sys.executable, "-c",
     "from backend import data_loader;"
     "g = data_loader.get_graph();"
     "print('inferred=' + str(sum(1 for e in g.edges"
     " if e.strength == 'inferred')))"],
    cwd=str(ROOT), capture_output=True, text=True)
tail = [ln for ln in (r.stdout or "").strip().splitlines() if ln]
print("[SMOKE] " + (tail[-1] if tail
                    else "(无输出) " + (r.stderr or "")[-300:]))
print("验收:")
print("  python -m pytest tests/ -q   (预期 78 passed)")
print("  python inspect_roots.py      (对比根占比/最长链)")
