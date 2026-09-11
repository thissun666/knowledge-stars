# -*- coding: utf-8 -*-
r'''inspect_prereq_match.py - 只读: 评估 annotations.prerequisites 的可归并率
    v3: 兼容顶层为 dict/list 两种形状的 json, 并报告无法解析的文件'''
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPLIT = ROOT / "k12-data" / "split"

_FW = ("０１２３４５６７８９"
       "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
       "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ")
_ASC = ("0123456789"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "abcdefghijklmnopqrstuvwxyz")
assert len(_FW) == len(_ASC) == 62, "全角映射表长度校验失败"
_FW2ASC = str.maketrans(_FW, _ASC)


def norm(s: str) -> str:
    s = unicodedata.normalize("NFC", s or "").strip().lower()
    s = s.translate(_FW2ASC)
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", s)


def load_kps_rels(p: Path):
    d = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(d, dict):
        return (d.get("knowledge_points") or [],
                d.get("relations") or [], None)
    if isinstance(d, list):
        if d and isinstance(d[0], dict) and (
                "canonical_name" in d[0] or "id" in d[0]):
            return d, [], "(顶层数组, 按kps收编)"
        return [], [], "(顶层数组, 元素非kp, 跳过)"
    return [], [], "(类型" + type(d).__name__ + ", 跳过)"


files = sorted(SPLIT.rglob("*.json"))
name_idx = defaultdict(list)
canon = {}
corpus = []
odd = []
for p in files:
    kps, rels, note = load_kps_rels(p)
    fname = p.relative_to(SPLIT).as_posix()
    if note:
        odd.append(fname + " " + note + " kps=" + str(len(kps)))
    if not kps:
        continue
    corpus.append((fname, kps, rels))
    for kp in kps:
        pid, cn = kp.get("id", ""), norm(kp.get("canonical_name", ""))
        canon[pid] = cn
        subj = kp.get("subject", "?")
        name_idx[cn].append((p.name, pid, subj))
        raw = kp.get("aliases")
        try:
            aliases = json.loads(raw) if isinstance(raw, str) else (raw or [])
        except Exception:
            aliases = []
        for a in aliases:
            na = norm(a)
            if na:
                name_idx[na].append((p.name, pid, subj))

print("json总数=" + str(len(files)) + " 有效分片=" + str(len(corpus))
      + "  节点=" + str(len(canon))
      + "  索引键=" + str(len(name_idx))
      + "  重名键=" + str(sum(1 for v in name_idx.values() if len(v) > 1)))
if odd:
    print("形状异常文件:")
    for o in odd:
        print("  " + o)
print("-" * 88)

tot = Counter()
unmatched = Counter()
for fname, kps, rels in corpus:
    m = Counter()
    for kp in kps:
        raw = kp.get("annotations")
        try:
            ann = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception:
            ann = {}
        pre = (ann.get("prerequisites") or {}).get("value") or []
        subj = kp.get("subject", "?")
        for name in pre:
            n = norm(name)
            if not n:
                continue
            m["mentions"] += 1
            hits = name_idx.get(n, [])
            if not hits:
                unmatched[n] += 1
                continue
            m["matched"] += 1
            if any(h[1] == kp.get("id") for h in hits):
                m["self"] += 1
            if any(h[0] != fname for h in hits):
                m["cross_file"] += 1
            if any(h[2] != subj for h in hits):
                m["cross_subject"] += 1
    tot.update(m)
    if m.get("mentions"):
        rate = str(round(m["matched"] / m["mentions"] * 100)) + "%"
        print(fname + " | 提及" + str(m["mentions"]) + " 命中"
              + str(m["matched"]) + "(" + rate + ") 跨file"
              + str(m.get("cross_file", 0)) + " 跨科"
              + str(m.get("cross_subject", 0)))
print("-" * 88)
if tot.get("mentions"):
    print("总计: 提及" + str(tot["mentions"]) + " 命中"
          + str(tot["matched"]) + " ("
          + str(round(tot["matched"] / tot["mentions"] * 100)) + "%)"
          + " | 其中 跨file " + str(tot.get("cross_file", 0))
          + " 跨科 " + str(tot.get("cross_subject", 0))
          + " 自指 " + str(tot.get("self", 0)))
    print("候选推断边(去自指后) 约 "
          + str(tot["matched"] - tot.get("self", 0)) + " 条, 现有 leads-to 边 13433 条")
print("-" * 88)
print("未命中 Top25:")
for n, c in unmatched.most_common(25):
    print("  " + str(c) + "x " + n[:40])
