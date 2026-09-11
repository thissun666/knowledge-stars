# -*- coding: utf-8 -*-
r'''prep_phase2.py - 阶段2准备(纯确定性): 未命中前置名 -> LLM任务文件
    跳过 exact/relaxed 已解析名; 每任务附 同科目 bigram-Jaccard Top5 候选
    产物: data/phase2_tasks.jsonl (按 科目x名字 去重)'''
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPLIT = ROOT / "k12-data" / "split"
OUT = ROOT / "data" / "phase2_tasks.jsonl"

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
        for suf in ("的基础", "能力", "基本", "初步", "入门", "概念", "认读"):
            if s.endswith(suf) and len(s) > len(suf) + 1:
                s = s[:-len(suf)]
                changed = True
    return s


def bigrams(s: str) -> set:
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) >= 2 else {s}


print("[1/4] 加载图与已解析名集合...")
from backend import data_loader
g = data_loader.get_graph()
exact_idx, relax_idx = set(), set()
for t in g.nodes.values():
    if t.source != "k12":
        continue
    names = [t.name]
    try:
        al = json.loads(t.aliases) if isinstance(t.aliases, str) else []
        names += [x for x in al if isinstance(x, str)]
    except Exception:
        pass
    for nm in names:
        n = norm(nm)
        if n:
            exact_idx.add(n)
            relax_idx.add(relaxed(n))

print("[2/4] 同科目 bigram 倒排索引...")
subj_nodes = defaultdict(list)
for t in g.nodes.values():
    if t.source != "k12":
        continue
    bg = bigrams(norm(t.name))
    if bg:
        subj_nodes[t.subject].append((t.name, bg, t.book, t.id))
inv = {}
for subj, lst in subj_nodes.items():
    d = defaultdict(list)
    for i, (_, bg, _, _) in enumerate(lst):
        for b in bg:
            d[b].append(i)
    inv[subj] = d

POOL = set(subj_nodes)


def candidates(subj, n, k=5):
    lst = subj_nodes.get(subj)
    if not lst:
        return []
    qb = bigrams(n)
    if not qb:
        return []
    cnt = Counter()
    for b in qb:
        for i in inv.get(subj, {}).get(b, ()):
            cnt[i] += 1
    out = []
    for i, ov in cnt.most_common(40):
        nb = lst[i][1]
        jac = ov / (len(qb) + len(nb) - ov)
        if jac <= 0.15:
            break
        out.append((lst[i][0], round(jac, 2)))
        if len(out) >= k:
            break
    return out


def scope_of(rel: Path):
    s = rel.as_posix().replace("\\", "/")
    if "/高中/" in s:
        return "高中全部"
    if s.endswith("语文.json") and ("/小学/" in s or "/初中/" in s):
        return "小学+初中语文"
    if s.endswith("英语.json") and "/初中/" in s:
        return "初中英语"
    return None


print("[3/4] 扫描分片, 收集未命中任务...")
tasks = {}
pool_skip_files = 0
for p in sorted(SPLIT.rglob("*.json")):
    sc = scope_of(p)
    if not sc:
        continue
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        continue
    kps = (d.get("knowledge_points") or []) if isinstance(d, dict) else []
    band, subject = p.parent.name, p.stem
    if subject not in POOL:
        pool_skip_files += 1
        continue
    for kp in kps:
        raw = kp.get("annotations")
        try:
            ann = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception:
            ann = {}
        pres = (ann.get("prerequisites") or {}).get("value") or []
        tgt = norm(kp.get("canonical_name", ""))
        for name in pres:
            if not isinstance(name, str):
                continue
            n = norm(name)
            if not n or n in exact_idx or relaxed(n) in relax_idx:
                continue
            k = subject + "|" + n
            t = tasks.get(k)
            if t is None:
                t = tasks[k] = {"name": name.strip()[:40], "norm": n,
                                "subject": subject, "band": band,
                                "scope": sc, "mentions": 0, "targets": []}
            t["mentions"] += 1
            if (tgt and len(t["targets"]) < 3
                    and kp.get("canonical_name", "")[:30] not in t["targets"]):
                t["targets"].append(kp.get("canonical_name", "")[:30])

print("[4/4] 生成候选并落盘...")
OUT.parent.mkdir(exist_ok=True)
by_scope = Counter()
with OUT.open("w", encoding="utf-8") as f:
    for k in sorted(tasks):
        t = tasks[k]
        t["candidates"] = candidates(t["subject"], t["norm"])
        f.write(json.dumps(t, ensure_ascii=False) + "\n")
        by_scope[t["scope"]] += 1
print("跳过无节点池科目文件 " + str(pool_skip_files) + " 个(结构性无解)")
print("任务总数 " + str(len(tasks)) + " "
      + json.dumps(dict(by_scope), ensure_ascii=False))
print("批次(40/批)约 " + str(-(-len(tasks) // 40)) + " 次调用")
print("产物: " + str(OUT.relative_to(ROOT)))
