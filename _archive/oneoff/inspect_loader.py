# -*- coding: utf-8 -*-
r'''inspect_loader.py - 只读: 为 setup_phase11 定位 data_loader 挂钩点
    A. Edge/Graph/add_edge 段  B. Graph实例化与get_graph  C. 尾部
    D. routes/graph.py 的band服务  E. inferred_links.json 分布'''
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def show(path, lo, hi, tag):
    lines = path.read_text(encoding="utf-8").splitlines()
    print("--- " + tag + " (行" + str(lo) + "-" + str(hi) + ") ---")
    for i in range(lo - 1, min(hi, len(lines))):
        print(str(i + 1).rjust(4) + ": " + lines[i])


dl = ROOT / "backend" / "data_loader.py"
lines = dl.read_text(encoding="utf-8").splitlines()


def find(pat):
    for i, ln in enumerate(lines, 1):
        if re.search(pat, ln):
            return i
    return -1


a_lo = find(r"class Topic")
a_hi = find(r"def _read_json")
show(dl, a_lo, a_hi, "A. Topic/Edge/Graph/add_edge")

print("--- B. Graph()实例化 / get_graph / _GRAPH 所在行 ---")
for i, ln in enumerate(lines, 1):
    if re.search(r"Graph\(\)|def get_graph|_GRAPH|_INSTANCE", ln):
        print(str(i).rjust(4) + ": " + ln.strip())

print("--- C. 文件尾部(最后40行) ---")
for i in range(max(0, len(lines) - 40), len(lines)):
    print(str(i + 1).rjust(4) + ": " + lines[i])

rg = ROOT / "backend" / "routes" / "graph.py"
rlines = rg.read_text(encoding="utf-8").splitlines()
bs = next((i for i, ln in enumerate(rlines, 1)
           if "_band_service" in ln and "def " in ln), -1)
if bs > 0:
    nxt = next((i for i, ln in enumerate(rlines[bs:], bs + 1)
                if ln.startswith("def ")), len(rlines) + 1)
    show(rg, bs, nxt, "D. _band_service")

print("--- E. inferred_links.json 分布 ---")
jf = ROOT / "data" / "inferred_links.json"
if jf.exists():
    d = json.loads(jf.read_text(encoding="utf-8"))
    es = d.get("edges", [])
    print("count字段=" + str(d.get("count")) + " 实际=" + str(len(es)))
    print("via分布: " + str(dict(Counter(e["via"] for e in es))))
    for e in es[:3]:
        print("  样本: " + json.dumps(e, ensure_ascii=False)[:150])
