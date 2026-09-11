# -*- coding: utf-8 -*-
r'''patch_phase2_v21.py - runner记录ret原始返回(构建期可重判) + audit按学段过滤'''
from pathlib import Path

ROOT = Path(__file__).resolve().parent

rp = ROOT / "run_phase2.py"
src = rp.read_text(encoding="utf-8")
if '"ret": ret' in src:
    print("[SKIP] runner 已有 ret")
else:
    old = '"node": hit[1] if hit else None}'
    assert src.count(old) == 1, "runner 锚点异常"
    src = src.replace(old, '"node": hit[1] if hit else None, "ret": ret}')
    compile(src, str(rp), "exec")
    rp.write_text(src, encoding="utf-8")
    print("[OK] runner v2.1: map 记录 ret (此前 llm-low 的原始返回不补)")

ap = ROOT / "audit_phase2_pilot.py"
src = ap.read_text(encoding="utf-8")
if "_sc" in src:
    print("[SKIP] audit 已有过滤")
else:
    anchor = 'MAP.read_text(encoding="utf-8").splitlines() if ln.strip())]'
    assert src.count(anchor) == 1, "audit 锚点异常"
    src = src.replace(anchor, anchor + '''
import sys as _sys
_sc = _sys.argv[1] if len(_sys.argv) > 1 else ""
if _sc:
    recs = [r for r in recs if r.get("band") == _sc]
    print("(过滤 band=" + _sc + ")")
if not recs:
    print("[ABORT] 过滤后无记录")
    raise SystemExit(0)''')
    compile(src, str(ap), "exec")
    ap.write_text(src, encoding="utf-8")
    print("[OK] audit 支持学段过滤: python audit_phase2_pilot.py 高中")
