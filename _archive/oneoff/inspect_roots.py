# -*- coding: utf-8 -*-
r'''inspect_roots.py - 只读诊断: 各数据组的根节点数/碎片率/最长链
    回答"推荐为何不像从第一单元开始": 前置链是否从基础贯通'''
from collections import defaultdict
from backend import data_loader

g = data_loader.get_graph()

groups = defaultdict(list)
for key, t in g.nodes.items():
    groups[(t.source, t.book or "-")].append(key)


def descendants(start):
    seen = {start}
    stack = [start]
    while stack:
        for nxt in g.adj_out.get(stack.pop(), []):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


print("组 | 节点 | 边 | 根数 | 根占比 | 最长链 | 杠杆Top3的根")
print("-" * 90)
for (source, book), keys in sorted(groups.items()):
    keyset = set(keys)
    edges = sum(1 for e in g.edges if e.prereq_key in keyset)
    roots = [k for k in keys if not g.adj_in.get(k)]
    best = []
    for r in roots:
        best.append((len(descendants(r)) - 1, g.nodes[r].name))
    best.sort(reverse=True)
    ratio = len(roots) / len(keys) * 100 if keys else 0
    top = best[0][0] if best else 0
    sample = " | ".join(n[:16] for _u, n in best[:3])
    print(source + "·" + book + " | " + str(len(keys)) + " | " + str(edges)
          + " | " + str(len(roots)) + " | " + str(round(ratio)) + "% | "
          + str(top) + " | " + sample)
print("-" * 90)
print("解读: 根占比高=前置链碎(假起点多, 杠杆只是挑了最长碎片);")
print("      最长链远小于节点数=图谱未从基础贯通, 需要 AI 离线补链。")
