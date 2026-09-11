# -*- coding: utf-8 -*-
r'''inspect_meta.py - 只读: 查看 k12 原始分片字段, 判断有无年级/单元可排序'''
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
split = ROOT / "k12-data" / "split"

for band, fname in (("初中", "物理.json"), ("初中", "数学.json"),
                    ("小学", "语文.json"), ("高中", "物理.json")):
    p = split / band / fname
    d = json.loads(p.read_text(encoding="utf-8"))
    kps = d.get("knowledge_points", [])
    rels = d.get("relations", [])
    print("=" * 25, band + "/" + fname,
          "kps=" + str(len(kps)) + " rels=" + str(len(rels)))
    if not kps:
        continue
    print("字段清单:", sorted(kps[0].keys()))
    for kp in kps[:2]:
        print("  样本:", json.dumps(kp, ensure_ascii=False)[:260])
    for field in sorted(kps[0].keys()):
        vals = Counter(str(kp.get(field)) for kp in kps)
        if 1 < len(vals) <= 15:
            top = ", ".join(v + "(" + str(c) + ")"
                            for v, c in vals.most_common(10))
            print("  [" + field + "] " + str(len(vals)) + "种: " + top)
    if rels:
        print("  关系样本:", json.dumps(rels[0], ensure_ascii=False)[:200])

print("=" * 56)
print("判读: 若存在 年级/册/单元 类字段且取值>学段数, 快赢可行;")
print("      若只有 name/type/subject, 排序无料可用, 直接上 AI 补链。")
