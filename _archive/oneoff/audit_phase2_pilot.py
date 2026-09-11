# -*- coding: utf-8 -*-
r'''audit_phase2_pilot.py v2 - 只读抽检; 学段过滤v2
    用法: python audit_phase2_pilot.py [高中|初中|小学] (不传=全部)'''
import json
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MAP = ROOT / "data" / "phase2_map.jsonl"
_sc = sys.argv[1] if len(sys.argv) > 1 else ""

recs = [json.loads(ln) for ln in
        MAP.read_text(encoding="utf-8").splitlines() if ln.strip()]
if _sc:
    recs = [r for r in recs if r.get("band") == _sc]
print("记录 " + str(len(recs)) + (" (band=" + _sc + ")" if _sc else ""))
if not recs:
    raise SystemExit("[ABORT] 无记录")
via = Counter(r.get("via", "?") for r in recs)
print("via: " + json.dumps(dict(via), ensure_ascii=False))
ok = [r for r in recs if r.get("id")]
nulls = [r for r in recs if not r.get("id")]
m_ok = sum(r.get("mentions", 0) for r in ok)
m_all = sum(r.get("mentions", 0) for r in recs)
print("提及覆盖: " + str(m_ok) + "/" + str(m_all)
      + " (" + str(round(m_ok / max(1, m_all) * 100)) + "%)")
print("解析到的唯一节点 " + str(len({r["id"] for r in ok})) + " 个")
print("--- 高频解析 Top15 (重点审) ---")
for r in sorted(ok, key=lambda x: -x.get("mentions", 0))[:15]:
    print("  [" + str(r.get("mentions", 0)) + "x] " + r["raw"][:22] + "  =>  "
          + str(r.get("node"))[:26] + " (" + r.get("band", "?")
          + ", sim" + str(r.get("sim", "?")) + ")")
rest = sorted(ok, key=lambda x: -x.get("mentions", 0))[15:]
random.seed(11)
for r in random.sample(rest, min(10, len(rest))):
    print("  [尾" + str(r.get("mentions", 0)) + "x] " + r["raw"][:22]
          + "  =>  " + str(r.get("node"))[:26] + " ("
          + r.get("band", "?") + ")")
print("--- 高频判无 Top8 (具体概念名=报警, 能力描述=正常) ---")
for r in sorted(nulls, key=lambda x: -x.get("mentions", 0))[:8]:
    print("  [" + str(r.get("mentions", 0)) + "x] " + r["raw"][:30]
          + "  (via=" + r.get("via", "?") + ")")
