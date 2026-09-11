# -*- coding: utf-8 -*-
r'''purge_hs_map.py - 清除map中高中记录, 准备高中pilot重跑'''
import json
from pathlib import Path

p = Path(__file__).resolve().parent / "data" / "phase2_map.jsonl"
if not p.exists():
    raise SystemExit("无 map")
lines = [l for l in p.read_text(encoding="utf-8").splitlines()
         if l.strip() and json.loads(l).get("band") != "高中"]
p.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("保留 " + str(len(lines)) + " 条(初中英语)")
