# -*- coding: utf-8 -*-
r'''inspect_inferred_quality.py - 只读审计:
    A. 推断边方向: 同段/顺序缝合/逆序/跨源 统计+样本
    B. k12 difficulty 注记覆盖率与分布
    C. 模拟难度优先推荐: 各 学段x科目 视角最低难度根(含视角内链长)
    D. 阶段二预算: 弱势科目唯一前置名数量'''
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPLIT = ROOT / "k12-data" / "split"
BAND_ORD = {"小学": 0, "初中": 1, "高中": 2}

from backend import data_loader
g = data_loader.get_graph()
band_of = {}
for key, t in g.nodes.items():
    band_of[key] = (BAND_ORD.get(t.book, 9) if t.source == "k12"
                    else 2 if t.source == "kkg" else 9)

print("=" * 28 + " A. 推断边方向审计 " + "=" * 28)
c, samples = Counter(), defaultdict(list)
for e in [e for e in g.edges if e.strength == "inferred"]:
    pb, tb = band_of.get(e.prereq_key, 9), band_of.get(e.topic_key, 9)
    if pb == 9 or tb == 9:
        c["含marble端"] += 1
    elif pb == tb:
        c["同学段"] += 1
    elif pb < tb:
        c["顺序缝合(低到高)"] += 1
    else:
        c["逆序band(高到低)"] += 1
        samples["逆序"].append(e)
    if g.nodes[e.prereq_key].source != g.nodes[e.topic_key].source:
        c["跨源"] += 1
        samples["跨源"].append(e)
print("  " + json.dumps(dict(c), ensure_ascii=False))
for tag in ("跨源", "逆序"):
    for e in samples.get(tag, [])[:5]:
        print("  [" + tag + "] " + g.nodes[e.prereq_key].name[:20]
              + " -> " + g.nodes[e.topic_key].name[:20])

print("=" * 28 + " B. difficulty 覆盖 " + "=" * 28)
diff = {}
for p in sorted(SPLIT.rglob("*.json")):
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        continue
    for kp in (d.get("knowledge_points") or [] if isinstance(d, dict) else []):
        raw = kp.get("annotations")
        try:
            ann = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception:
            ann = {}
        v = (ann.get("difficulty") or {}).get("value")
        if isinstance(v, (int, float)):
            diff[kp.get("id", "")] = float(v)
id2key = {t.id: k for k, t in g.nodes.items()
          if t.id and t.source == "k12"}
have = [diff[i] for i in id2key if i in diff]
bk = Counter("<0.2" if v < 0.2 else "<0.4" if v < 0.4 else
             "<0.6" if v < 0.6 else "<0.8" if v < 0.8 else ">=0.8"
             for v in have)
print("  k12已加载 " + str(len(id2key)) + " | 有difficulty "
      + str(len(have)) + " ("
      + str(round(len(have) / max(1, len(id2key)) * 100)) + "%)")
print("  分布: " + json.dumps(dict(sorted(bk.items())), ensure_ascii=False))

print("=" * 25 + " C. 难度优先推荐位模拟 " + "=" * 25)
views = defaultdict(list)
for k, t in g.nodes.items():
    if t.source == "k12":
        views[(t.book, t.subject)].append(k)


def dsc(k, vset):
    seen, st = {k}, [k]
    while st:
        for nxt in g.adj_out.get(st.pop(), ()):
            if nxt in vset and nxt not in seen:
                seen.add(nxt)
                st.append(nxt)
    return len(seen) - 1


for (book, subj), keys in sorted(views.items()):
    vset = set(keys)
    roots = [k for k in keys
             if not any(x in vset for x in g.adj_in.get(k, ()))]
    if not roots:
        continue
    best = min(roots, key=lambda k: (diff.get(g.nodes[k].id, 0.5),
                                     -dsc(k, vset)))
    print("  " + book + "·" + subj + " | 根=" + g.nodes[best].name[:26]
          + " | 难度" + str(round(diff.get(g.nodes[best].id, -1), 2))
          + " | 视角内链" + str(dsc(best, vset))
          + " | 根数" + str(len(roots)))

print("=" * 26 + " D. 阶段二预算 " + "=" * 26)
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
    kps_d = (d.get("knowledge_points") or []) if isinstance(d, dict) else []
    for kp in kps_d:
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
          + str(math.ceil(len(names) / 40)) + " 次LLM批量调用")
