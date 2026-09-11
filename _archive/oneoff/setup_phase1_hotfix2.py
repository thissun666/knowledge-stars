# -*- coding: utf-8 -*-
r'''
setup_phase1_hotfix2.py - 环与 id 冲突诊断（只读，不修改任何文件）
  A. marble-only / pep-only 子图分别 Kahn 检测，定位环在哪一侧
  B. DFS 抽取真实环路径作为证据（每侧最多 5 条）
  C. 打印 pep 重复 id 在各册中的名字（验证"同 id 不同知识点"猜想）
  D. 模拟两种 pep 按册命名空间策略，对比环是否消失
'''
import json
import sys
from collections import defaultdict, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from backend import config
from backend import data_loader as dl


def kahn_leftover(nodes, edges):
    indeg = {n: 0 for n in nodes}
    adj = defaultdict(list)
    for a, b in edges:
        adj[a].append(b)
        indeg[b] += 1
    q = deque(n for n in nodes if indeg[n] == 0)
    seen = 0
    while q:
        n = q.popleft()
        seen += 1
        for m in adj[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                q.append(m)
    return [n for n in nodes if indeg[n] > 0], len(nodes) - seen


def find_cycles(nodes, edges, limit=5):
    adj = defaultdict(list)
    for a, b in edges:
        adj[a].append(b)
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in nodes}
    parent = {}
    cycles = []
    for start in nodes:
        if color[start] != WHITE:
            continue
        color[start] = GRAY
        stack = [(start, iter(adj[start]))]
        while stack:
            node, it = stack[-1]
            advanced = False
            for nxt in it:
                if color[nxt] == WHITE:
                    color[nxt] = GRAY
                    parent[nxt] = node
                    stack.append((nxt, iter(adj[nxt])))
                    advanced = True
                    break
                elif color[nxt] == GRAY:
                    cyc = [node]
                    cur = node
                    while cur != nxt:
                        cur = parent.get(cur, nxt)
                        cyc.append(cur)
                    cyc.reverse()
                    cycles.append(cyc)
                    if len(cycles) >= limit:
                        return cycles
            if not advanced:
                color[node] = BLACK
                stack.pop()
    return cycles


def report_side(name, nodes, edges):
    print("--- " + name + " ---")
    print("  nodes=" + str(len(nodes)) + " edges=" + str(len(edges)))
    leftover, n_left = kahn_leftover(nodes, edges)
    if n_left == 0:
        print("  [PASS] 无环（DAG 成立）")
        return
    print("  [FAIL] 有环: 拓扑遗留节点 " + str(n_left) + " 个")
    for c in find_cycles(nodes, edges):
        print("    环: " + " -> ".join(c) + " -> " + c[0])


def marble_side():
    mt, md = dl._marble_part(str(ROOT / "marble-data"), config.SUBJECTS)
    nodes = set("marble:" + str(t.get("id")) for t in mt)
    edges = []
    for d in md:
        a = "marble:" + str(d.get("prerequisiteId"))
        b = "marble:" + str(d.get("topicId"))
        if a in nodes and b in nodes:
            edges.append((a, b))
    report_side("A1. marble-only（数英过滤后）", nodes, edges)
    return mt, md, nodes, edges


def pep_books():
    data_dir = ROOT / "pep-data" / "data"
    books = []
    if (data_dir / "topics.json").is_file():
        books.append(("7-up", data_dir / "topics.json",
                      data_dir / "dependencies.json"))
    if data_dir.is_dir():
        for d in sorted(data_dir.iterdir()):
            if d.is_dir() and (d / "topics.json").is_file():
                books.append((d.name, d / "topics.json",
                              d / "dependencies.json"))
    return books


def show_dup_names(books):
    print("--- C. pep 重复 id 的真实身份 ---")
    where = defaultdict(list)
    for book, tp, _ in books:
        for t in dl._read_json(str(tp)).get("topics", []):
            where[str(t.get("id"))].append((book, str(t.get("name", ""))))
    dups = {k: v for k, v in where.items() if len(v) > 1}
    print("  重复 id 共 " + str(len(dups)) + " 个")
    for k in sorted(dups):
        items = dups[k]
        desc = "  vs  ".join("[" + b + "] " + n for b, n in items)
        print("  " + k + " : " + desc)


def pep_current(books):
    nodes, edges = set(), []
    for book, tp, dp in books:
        for t in dl._read_json(str(tp)).get("topics", []):
            nodes.add("pep:" + str(t.get("id")))
    for book, tp, dp in books:
        if not dp.is_file():
            continue
        for d in dl._read_json(str(dp)).get("dependencies", []):
            a, b = "pep:" + str(d.get("prerequisiteId")), "pep:" + str(d.get("topicId"))
            if a in nodes and b in nodes:
                edges.append((a, b))
    report_side("A2. pep-only（当前按 id 合并，即线上行为）", nodes, edges)
    return nodes, edges


def pep_namespaced(books, cross_book):
    print("--- D. pep 按册命名空间模拟（跨册解析=" + cross_book + "）---")
    nodes, edges = set(), []
    id_index = defaultdict(list)
    for book, tp, dp in books:
        for t in dl._read_json(str(tp)).get("topics", []):
            k = "pep:" + book + ":" + str(t.get("id"))
            nodes.add(k)
            id_index[str(t.get("id"))].append((book, k))
    same_book_only = (cross_book == "same-only")
    dropped_cross, dropped_ambig = 0, 0
    for book, tp, dp in books:
        if not dp.is_file():
            continue
        for d in dl._read_json(str(dp)).get("dependencies", []):
            pid, tid = str(d.get("prerequisiteId")), str(d.get("topicId"))
            a, b = "pep:" + book + ":" + pid, "pep:" + book + ":" + tid
            if a not in nodes:
                hits = id_index.get(pid, [])
                if same_book_only or not hits:
                    dropped_cross += 1
                    continue
                if len(hits) > 1:
                    dropped_ambig += 1
                    continue
                a = hits[0][1]
            if b not in nodes:
                dropped_cross += 1
                continue
            edges.append((a, b))
    print("  丢弃跨册前置引用=" + str(dropped_cross)
          + "  丢弃多书歧义引用=" + str(dropped_ambig))
    report_side("D 结果", nodes, edges)


if __name__ == "__main__":
    print("项目根: " + str(ROOT))
    marble_side()
    books = pep_books()
    print("  册目录: " + ", ".join(b[0] for b in books))
    pep_current(books)
    show_dup_names(books)
    pep_namespaced(books, "same-only")
    pep_namespaced(books, "cross-unique")
    print("=" * 60)
    print("诊断完成（未修改任何文件）")
