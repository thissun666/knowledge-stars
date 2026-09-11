# -*- coding: utf-8 -*-
r'''inspect_phase15.py - 只读: D段预算(list形状已修) + 阶段1.5挂钩点侦察'''
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPLIT = ROOT / "k12-data" / "split"

print("=" * 22 + " D. 阶段二预算(形状已修) " + "=" * 22)
groups, mentions = defaultdict(set), Counter()
for p in sorted(SPLIT.rglob("*.json")):
    s = p.as_posix().replace("\\", "/")
    tag = ("高中全部" if "/高中/" in s else
           "小学+初中语文" if s.endswith("语文.json")
           and ("/小学/" in s or "/初中/" in s) else
           "初中英语" if s.endswith("英语.json") and "/初中/" in s else None)
    if not tag:
        continue
    d = json.loads(p.read_text(encoding="utf-8"))
    kps = (d.get("knowledge_points") or []) if isinstance(d, dict) else []
    for kp in kps:
        raw = kp.get("annotations")
        try:
            ann = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception:
            ann = {}
        for nm in (ann.get("prerequisites") or {}).get("value") or []:
            if isinstance(nm, str) and nm.strip():
                groups[tag].add(nm.strip())
                mentions[tag] += 1
for tag, names in sorted(groups.items()):
    print("  " + tag + " | 唯一名 " + str(len(names))
          + " (提及" + str(mentions[tag]) + ") | 约"
          + str(math.ceil(len(names) / 40)) + " 次批量调用")

print("=" * 22 + " E1. graph_service 排序区 " + "=" * 22)
gs = ROOT / "backend" / "graph_service.py"
lines = gs.read_text(encoding="utf-8").splitlines()
for i in range(69, min(160, len(lines))):
    print(str(i + 1).rjust(4) + ": " + lines[i])

print("=" * 22 + " E2. data_loader Topic构造区 " + "=" * 22)
dl = ROOT / "backend" / "data_loader.py"
lines = dl.read_text(encoding="utf-8").splitlines()
for i in range(114, min(233, len(lines))):
    print(str(i + 1).rjust(4) + ": " + lines[i])
