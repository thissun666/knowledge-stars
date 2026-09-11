# -*- coding: utf-8 -*-
r'''diag_hs_candidates.py - 只读: 高中200全判无的机械归因'''
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
tasks = [json.loads(l) for l in
         (ROOT / "data" / "phase2_tasks.jsonl")
         .read_text(encoding="utf-8").splitlines() if l.strip()]
hs = [t for t in tasks if t["scope"] == "高中全部"]
first = hs[:200]
print("高中任务总数 " + str(len(hs)) + " | 本次烧掉的200条:")
print("  候选数分布 " + json.dumps(dict(Counter(
    "0候选" if not t["candidates"]
    else "1-2" if len(t["candidates"]) <= 2 else "3+" for t in first)),
    ensure_ascii=False))
print("  科目分布 " + json.dumps(dict(Counter(
    t["subject"] for t in first)), ensure_ascii=False))
zero = [t for t in hs if not t["candidates"]]
print("全部高中 0候选 " + str(len(zero)) + "/" + str(len(hs))
      + " | 科目 " + json.dumps(dict(Counter(
          t["subject"] for t in zero)), ensure_ascii=False))
