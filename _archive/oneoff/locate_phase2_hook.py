# -*- coding: utf-8 -*-
r'''locate_phase2_hook.py - 只读: 打印 data_loader 推断边合并区'''
from pathlib import Path

lines = (Path(__file__).resolve().parent / "backend" / "data_loader.py") \
    .read_text(encoding="utf-8").splitlines()
printed = -1
for i, ln in enumerate(lines):
    if "inferred" not in ln.lower():
        continue
    lo, hi = max(0, i - 4), min(len(lines), i + 9)
    if lo <= printed:
        continue
    print("--- L" + str(lo + 1) + "-" + str(hi) + " ---")
    for j in range(lo, hi):
        print(str(j + 1).rjust(4) + ": " + lines[j])
    printed = hi
