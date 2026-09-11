# -*- coding: utf-8 -*-
# ACCEPT-GATE-V2 | OUT-ARRAY-V2 | UNLINK-STALE-V2
# build_phase2_edges.py - 阶段2收割(独立后处理器, 不碰构建器)
# map -> 分科目地板(英语/语文0.5, 其余0.35) + 包含规则 -> 本地重解析 -> 剔环
# 产物 data/phase2_links.json (JSON数组, 由 data_loader._load_phase2_links 装载)
# 纯本地零LLM调用 | 重跑幂等(先删旧产物防自污染) | main有守卫可被pytest导入
import json
import random
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPLIT = ROOT / "k12-data" / "split"
MAP = ROOT / "data" / "phase2_map.jsonl"
OUT = ROOT / "data" / "phase2_links.json"
FLOOR = {"英语": 0.5, "语文": 0.5}
DEFAULT_FLOOR = 0.40
SPOT_N = 20

_FW = ("０１２３４５６７８９"
       "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
       "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ")
_ASC = ("0123456789"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "abcdefghijklmnopqrstuvwxyz")
_FW2ASC = str.maketrans(_FW, _ASC)


def norm(s):
    s = unicodedata.normalize("NFC", s or "").strip().lower()
    s = s.translate(_FW2ASC)
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", s)


def floor_of(subject):
    return FLOOR.get(subject, DEFAULT_FLOOR)


def accept(rec):
    """地板裁决 + 包含规则救回; 无id(llm-low)由main用ret本地重解析。"""
    if rec.get("via") not in ("llm", "llm-low"):
        return False
    if rec.get("sim", 0.0) >= floor_of(rec.get("subject", "")):
        return True
    n_raw = norm(rec.get("raw", ""))
    n_ret = norm(rec.get("ret", ""))
    if (n_ret and min(len(n_raw), len(n_ret)) >= 6
            and (n_ret in n_raw or n_raw in n_ret)):
        return True
    return False


def tarjan_scc(nodes, adj):
    index, low, on, stack, sccs = {}, {}, set(), [], []
    counter = 0
    for root in nodes:
        if root in index:
            continue
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on.add(root)
        work = [(root, iter(adj.get(root, ())))]
        while work:
            u, it = work[-1]
            advanced = False
            for w in it:
                if w not in index:
                    index[w] = low[w] = counter
                    counter += 1
                    stack.append(w)
                    on.add(w)
                    work.append((w, iter(adj.get(w, ()))))
                    advanced = True
                    break
                if w in on:
                    low[u] = min(low[u], index[w])
            if advanced:
                continue
            work.pop()
            if work:
                pu = work[-1][0]
                low[pu] = min(low[pu], low[u])
            if low[u] == index[u]:
                comp = []
                while True:
                    w = stack.pop()
                    on.discard(w)
                    comp.append(w)
                    if w == u:
                        break
                sccs.append(comp)
    return sccs


def scope_of(rel):
    s = str(rel).replace("\\", "/")
    if "/高中/" in s:
        return "高中全部"
    if s.endswith("语文.json") and ("/小学/" in s or "/初中/" in s):
        return "小学+初中语文"
    if s.endswith("英语.json") and "/初中/" in s:
        return "初中英语"
    return None


def main():
    # 关键: 先删旧产物。get_graph()会自动装载旧phase2_links.json,
    # 否则旧边混进既有adj -> 去重吞掉新边 + SCC误判, 产物净损失。
    if OUT.exists():
        OUT.unlink()
        print("[INIT] 已删除旧产物(防自污染)")
    print("[1/5] 加载图(含全部既有边)...")
    from backend import data_loader
    g = data_loader.get_graph()
    adj = {k: set(v) for k, v in g.adj_out.items()}
    name2node = defaultdict(list)
    for t in g.nodes.values():
        if t.source != "k12":
            continue
        names = [t.name]
        try:
            al = json.loads(t.aliases) if isinstance(t.aliases, str) else []
            names += [x for x in al if isinstance(x, str)]
        except Exception:
            pass
        for nm in {norm(x) for x in names if norm(x)}:
            name2node[nm].append((t.id, t.name, t.subject, t.book))

    def reresolve(rec):
        hits = name2node.get(norm(rec.get("ret", "")), [])
        if not hits:
            return None
        same_s = [h for h in hits if h[2] == rec.get("subject")]
        pick = (same_s or hits)[0]
        return pick[0], pick[1]

    print("[2/5] map 裁决...")
    recs = []
    if MAP.exists():
        recs = [json.loads(ln) for ln in
                MAP.read_text(encoding="utf-8").splitlines() if ln.strip()]
    resolved, n_floor, n_contain = {}, 0, 0
    for r in recs:
        if not accept(r):
            if r.get("via") in ("llm", "llm-low"):
                n_floor += 1
            continue
        if not r.get("id"):
            hit = reresolve(r)
            if not hit:
                n_floor += 1
                continue
            r["id"], r["node"] = hit
            n_contain += 1
        resolved[r["subject"] + "|" + r["norm"]] = r
    print("  记录 " + str(len(recs)) + " | 收割名 " + str(len(resolved))
          + " (包含救回 " + str(n_contain) + ") | 拦截 " + str(n_floor))

    print("[3/5] 重扫分片生成边(去重/自环)...")
    cand = {}
    for p in sorted(SPLIT.rglob("*.json")):
        if not scope_of(p):
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        kps = (d.get("knowledge_points") or []) if isinstance(d, dict) else []
        subject = p.stem
        for kp in kps:
            raw = kp.get("annotations")
            try:
                ann = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except Exception:
                ann = {}
            tgt = "k12:" + str(kp.get("id", ""))
            if tgt not in g.nodes:
                continue
            prereq = (ann.get("prerequisites") or {}).get("value") or []
            for name in prereq:
                if not isinstance(name, str):
                    continue
                r = resolved.get(subject + "|" + norm(name))
                if not r:
                    continue
                a = "k12:" + str(r["id"])
                if a == tgt or a not in g.nodes or tgt in adj.get(a, ()):
                    continue
                k = (a, tgt)
                if k not in cand or r.get("sim", 0) > cand[k].get("sim", 0):
                    cand[k] = r
    print("  新候选边 " + str(len(cand)))

    print("[4/5] 剔环(只删新边, sim低先删)...")
    new_edges = set(cand)
    removed = 0
    for _ in range(10):
        merged = defaultdict(list)
        for a, outs in adj.items():
            merged[a].extend(outs)
        for a, b in new_edges:
            merged[a].append(b)
        bad = set()
        for c in tarjan_scc(g.nodes, merged):
            if len(c) < 2:
                continue
            cs = set(c)
            bad.update(e for e in new_edges if e[0] in cs and e[1] in cs)
        if not bad:
            break
        for e in sorted(bad, key=lambda x: cand[x].get("sim", 0)):
            new_edges.discard(e)
            removed += 1
    print("  删新边 " + str(removed))

    print("[5/5] 落盘(JSON数组)+审计...")
    rows = [{"from": a, "to": b, "via": "phase2",
             "sim": cand[(a, b)].get("sim", 0),
             "mentions": cand[(a, b)].get("mentions", 0),
             "subject": cand[(a, b)].get("subject", ""),
             "node": cand[(a, b)].get("node", "")}
            for a, b in sorted(new_edges)]
    OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print("  收割边 " + str(len(rows)) + " "
          + json.dumps(dict(Counter(x["subject"] for x in rows)),
                       ensure_ascii=False) + " -> " + OUT.name)
    if rows:
        random.seed(11)
        print("  --- 抽样 " + str(min(SPOT_N, len(rows))) + " 条人工过目 ---")
        for x in random.sample(rows, min(SPOT_N, len(rows))):
            tn = g.nodes[x["to"]].name[:18] if x["to"] in g.nodes else "?"
            print("  [sim" + str(x["sim"]) + " " + str(x["mentions"]) + "x] "
                  + x["node"][:20] + " -> " + tn + " (" + x["subject"] + ")")


if __name__ == "__main__":
    main()
