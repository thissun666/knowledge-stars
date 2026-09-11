# -*- coding: utf-8 -*-
r'''build_inferred_links.py v2 - Sprint11阶段1(确定性): prerequisites -> 推断边
    修复: 剔环改用 SCC 判定(仅删真环上的新边), 不再误杀旧环下游
    三级匹配: exact > relaxed > contains | 两端点均在已加载图内 | 去自环/去重/去已有
    产物: data/inferred_links.json'''
import json
import re
import time
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPLIT = ROOT / "k12-data" / "split"
OUT = ROOT / "data" / "inferred_links.json"
SUFFIXES = ("的基础", "能力", "基本", "初步", "入门", "概念", "认读")

_FW = ("０１２３４５６７８９"
       "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
       "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ")
_ASC = ("0123456789"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "abcdefghijklmnopqrstuvwxyz")
_FW2ASC = str.maketrans(_FW, _ASC)


def norm(s: str) -> str:
    s = unicodedata.normalize("NFC", s or "").strip().lower()
    s = s.translate(_FW2ASC)
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", s)


def relaxed(s: str) -> str:
    changed = True
    while changed:
        changed = False
        for suf in SUFFIXES:
            if s.endswith(suf) and len(s) > len(suf) + 1:
                s = s[:-len(suf)]
                changed = True
    return s


def _pick(cands, subj, band):
    """同科同学段 > 同科 > 全局; 同层按 key 稳定排序"""
    for tier in ([c for c in cands if c[1] == subj and c[2] == band],
                 [c for c in cands if c[1] == subj],
                 cands):
        if tier:
            return sorted(tier, key=lambda c: c[0])[0][0]
    return None


def kosaraju(nodes, adj):
    """迭代 Kosaraju, 返回 {分量id: [节点...]}"""
    order, visited = [], set()
    for start in nodes:
        if start in visited:
            continue
        visited.add(start)
        stack = [(start, iter(adj.get(start, ())))]
        while stack:
            node, it = stack[-1]
            pushed = False
            for nxt in it:
                if nxt not in visited:
                    visited.add(nxt)
                    stack.append((nxt, iter(adj.get(nxt, ()))))
                    pushed = True
                    break
            if not pushed:
                order.append(node)
                stack.pop()
    radj = defaultdict(list)
    for a, outs in adj.items():
        for b in outs:
            radj[b].append(a)
    comp, members, cid = {}, defaultdict(list), 0
    for start in reversed(order):
        if start in comp:
            continue
        comp[start] = cid
        members[cid].append(start)
        stack = [start]
        while stack:
            x = stack.pop()
            for y in radj.get(x, ()):
                if y not in comp:
                    comp[y] = cid
                    members[cid].append(y)
                    stack.append(y)
        cid += 1
    return members


def load_kps_rels(p: Path):
    d = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(d, dict):
        return d.get("knowledge_points") or [], d.get("relations") or []
    return [], []


print("[1/6] 加载实际图谱(与应用同一入口)...")
from backend import data_loader
g = data_loader.get_graph()
# Sprint11.5修复: 构建须以无推断边基底运行, 否则上一轮产物被当已有边吞掉
g.edges = [e for e in g.edges if e.strength != "inferred"]
g.adj_out, g.adj_in = {}, {}
for _e in g.edges:
    g.adj_out.setdefault(_e.prereq_key, []).append(_e.topic_key)
    g.adj_in.setdefault(_e.topic_key, []).append(_e.prereq_key)
print("  基底边(剔除推断) " + str(len(g.edges)))
id2key = {t.id: key for key, t in g.nodes.items() if t.id}
print("  已加载节点 " + str(len(g.nodes))
      + " (k12 id " + str(len(id2key)) + ")")

print("[2/6] 建立已加载节点名称索引(名+别名)...")
exact_idx = defaultdict(list)
relax_idx = defaultdict(list)
for key, t in g.nodes.items():
    names = [t.name]
    try:
        al = json.loads(t.aliases) if isinstance(t.aliases, str) else []
        names += [x for x in al if isinstance(x, str)]
    except Exception:
        pass
    seen = set()
    for nm in names:
        n = norm(nm)
        if n and n not in seen:
            seen.add(n)
            exact_idx[n].append((key, t.subject, t.book))
            relax_idx[relaxed(n)].append((key, t.subject, t.book))

print("[3/6] 遍历分片, 三级匹配...")
new_edges = {}
stat = Counter()
uniq_unmatched = {}
for p in sorted(SPLIT.rglob("*.json")):
    kps, _rels = load_kps_rels(p)
    for kp in kps:
        to_key = id2key.get(kp.get("id", ""))
        if not to_key:
            continue
        raw = kp.get("annotations")
        try:
            ann = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception:
            ann = {}
        pre = (ann.get("prerequisites") or {}).get("value") or []
        my_subj = g.nodes[to_key].subject
        my_band = g.nodes[to_key].book
        for name in pre:
            n = norm(name)
            if not n:
                continue
            stat["mentions"] += 1
            cands = exact_idx.get(n)
            via = "exact"
            if not cands:
                cands = relax_idx.get(relaxed(n))
                via = "relaxed"
            hit = None
            if cands:
                hit = _pick(cands, my_subj, my_band)
            elif len(n) >= 4:
                best = None
                for nn, lst in exact_idx.items():
                    if len(nn) < 4:
                        continue
                    if (n in nn or nn in n) and (
                            n[:8] == nn[:8] or abs(len(n) - len(nn)) <= 6):
                        pk = _pick(lst, my_subj, my_band)
                        if pk and (best is None or len(nn) < len(best[0])):
                            best = (nn, pk)
                if best:
                    hit, via = best[1], "contains"
            if not hit:
                stat["unmatched"] += 1
                uniq_unmatched[n] = uniq_unmatched.get(n, 0) + 1
                continue
            if hit == to_key:
                stat["self"] += 1
                continue
            if to_key in g.adj_out.get(hit, []):
                stat["exists"] += 1
                continue
            ek = (hit, to_key)
            if ek in new_edges:
                stat["dup"] += 1
                continue
            new_edges[ek] = {"via": via, "name": name[:40]}
            stat["added_" + via] += 1
            stat["added"] += 1

print("[4/6] peek 高中/relations.json(仅情报)...")
rp = SPLIT / "高中" / "relations.json"
if rp.exists():
    arr = json.loads(rp.read_text(encoding="utf-8"))
    print("  元素数=" + str(len(arr)))

print("[5/6] SCC 剔环(只删真环上的新边, 不碰旧边)...")
adj = defaultdict(set)
for k, outs in g.adj_out.items():
    adj[k] |= set(outs)
for a, b in new_edges:
    adj[a].add(b)
dropped_ring, iters = 0, 0
while True:
    iters += 1
    members = kosaraju(list(g.nodes), adj)
    bad = []
    for _c, mem in members.items():
        if len(mem) < 2:
            continue
        ms = set(mem)
        bad += [e for e in new_edges if e[0] in ms and e[1] in ms]
    if not bad or iters > 30:
        break
    for e in bad:
        adj[e[0]].discard(e[1])
        new_edges.pop(e, None)
        dropped_ring += 1
old_sccs = [(len(m), m) for _c, m in
            kosaraju(list(g.nodes), adj).items() if len(m) > 1]
print("  迭代 " + str(iters) + " 轮, 删新边 " + str(dropped_ring) + " 条")
if old_sccs:
    print("  残留旧环 SCC " + str(len(old_sccs)) + " 个(数据自带, 未动):")
    for size, mem in sorted(old_sccs, reverse=True)[:6]:
        names = " -> ".join(g.nodes[k].name[:14] for k in mem[:5])
        print("    [" + str(size) + "节点] " + names)

print("[6/6] 落盘...")
OUT.parent.mkdir(exist_ok=True)
edges_out = [{"from": a, "to": b, "via": v["via"], "prereq_name": v["name"]}
             for (a, b), v in sorted(new_edges.items())]
OUT.write_text(json.dumps({
    "version": 3, "generator": "build_inferred_links.py",
    "generated_at": int(time.time() * 1000),
    "count": len(edges_out), "edges": edges_out,
}, ensure_ascii=False, indent=1), encoding="utf-8")

print("=" * 72)
print("提及 " + str(stat["mentions"])
      + " | 新增边 " + str(stat["added"])
      + " (exact " + str(stat["added_exact"])
      + " / relaxed " + str(stat["added_relaxed"])
      + " / contains " + str(stat["added_contains"]) + ")")
print("未命中 " + str(stat["unmatched"])
      + " | 自环 " + str(stat["self"])
      + " | 已有边 " + str(stat["exists"])
      + " | 重复 " + str(stat["dup"])
      + " | SCC剔环 " + str(dropped_ring))
print("落盘 " + str(len(edges_out)) + " 条 -> " + str(OUT.relative_to(ROOT)))
print("未命中Top20 (供下一阶段LLM消歧):")
for n, c in Counter(uniq_unmatched).most_common(20):
    print("  " + str(c) + "x " + n[:40])
