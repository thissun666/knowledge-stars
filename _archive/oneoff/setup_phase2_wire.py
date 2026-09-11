# -*- coding: utf-8 -*-
r'''setup_phase2_wire.py - 把 phase2_links.json 接进 get_graph()
    双锚点断言 + compile 校验 + 幂等; 产物与 inferred_links 同等待遇
    (strength=inferred, add_edge守卫, 缺文件静默降级)'''
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DL = ROOT / "backend" / "data_loader.py"

src = DL.read_text(encoding="utf-8")
if "_load_phase2_links" in src:
    print("[SKIP] 已接线")
    raise SystemExit(0)

lines = src.splitlines()
gi = [i for i, ln in enumerate(lines) if ln.startswith("def get_graph()")]
assert len(gi) == 1, "get_graph 锚点异常: " + str(len(gi))

NEW_FN = '''
def _load_phase2_links(g: Graph) -> int:
    r"""Sprint11阶段2: 可选装载 data/phase2_links.json (LLM消歧前置缝合边,
    build_phase2_edges.py 产物, 构建时已过分科目地板+SCC 剔环)。
    文件缺失/损坏静默降级为 0 条; 逐条走 add_edge 守卫(自环/悬空/去重)。"""
    import json as _json
    from pathlib import Path as _P
    p = _P(__file__).resolve().parent.parent / "data" / "phase2_links.json"
    if not p.exists():
        logger.info("phase2边文件缺失, 跳过: %s", str(p))
        return 0
    try:
        entries = _json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        logger.warning("phase2边文件损坏, 跳过: %s", exc)
        return 0
    if not isinstance(entries, list):
        return 0
    added = 0
    for ent in entries:
        if g.add_edge(Edge(prereq_key=str(ent.get("from", "")),
                           topic_key=str(ent.get("to", "")),
                           strength="inferred",
                           reason="phase2")):
            added += 1
    logger.info("phase2边装载: %d/%d", added, len(entries))
    return added

'''
lines[gi[0]:gi[0]] = NEW_FN.splitlines() + ["", ""]

ci = [i for i, ln in enumerate(lines)
      if "_load_inferred_links(" in ln and not ln.strip().startswith("def ")]
assert len(ci) == 1, "调用锚点异常: " + str(len(ci))
indent = lines[ci[0]][:len(lines[ci[0]]) - len(lines[ci[0]].lstrip())]
lines[ci[0] + 1:ci[0] + 1] = [indent + "_load_phase2_links(g)"]

out = "\n".join(lines) + "\n"
compile(out, str(DL), "exec")
DL.write_text(out, encoding="utf-8")
print("[OK] 已接线: _load_phase2_links -> get_graph()")
