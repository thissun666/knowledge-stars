# -*- coding: utf-8 -*-
"""扫描 k12-data/split 各文件顶层结构, 找出格式异常的分片。"""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
split = ROOT / "k12-data" / "split"
if not split.is_dir():
    print("未找到 k12-data/split")
    raise SystemExit(0)
for band in sorted(os.listdir(split)):
    bdir = split / band
    if not bdir.is_dir():
        continue
    for fname in sorted(os.listdir(bdir)):
        if not fname.endswith(".json"):
            continue
        p = bdir / fname
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            print("[BAD-JSON] " + band + "/" + fname + ": " + str(e))
            continue
        if isinstance(d, dict):
            kp = len(d.get("knowledge_points") or [])
            rl = len(d.get("relations") or [])
            print("[dict ] " + band + "/" + fname +
                  "  kps=" + str(kp) + " rels=" + str(rl))
        elif isinstance(d, list):
            print("[LIST!] " + band + "/" + fname + "  items=" + str(len(d)))
            if d and isinstance(d[0], dict):
                print("        首元素键: " + ", ".join(sorted(d[0].keys())))
        else:
            print("[其他 ] " + band + "/" + fname + ": " + type(d).__name__)
