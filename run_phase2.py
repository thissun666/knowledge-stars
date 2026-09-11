# -*- coding: utf-8 -*-
# run_phase2.py - 阶段2: LLM消歧 v2 (glm-4-flash)
# v2: SYS 同指规则(内嵌真实误选反例) + 相似度地板 SIM_FLOOR
#     病因: v1 做"主题最近邻"选择, 高频名错边伤害最大(pilot 72% < 80%)
# 不变: 往返校验防幻觉 | null也落盘 | 断点续跑
import json
import os
import re
import sys
import time
import unicodedata
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TASKS = ROOT / "data" / "phase2_tasks.jsonl"
MAP = ROOT / "data" / "phase2_map.jsonl"
BATCH = 40
MODEL = "glm-4-flash"
SIM_FLOOR = 0.35
PHASE2_RUNNER_V2 = True

args = sys.argv[1:]
scope = args[args.index("--scope") + 1] if "--scope" in args else None
limit = int(args[args.index("--limit") + 1]) if "--limit" in args else None

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


def bigrams(s: str) -> set:
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) >= 2 else {s}


def jac(a: str, b: str) -> float:
    a, b = bigrams(a), bigrams(b)
    ov = len(a & b)
    return ov / len(a | b) if (a and b) else 0.0


print("[1/4] 预检...")
try:
    from zhipuai import ZhipuAI
except ImportError:
    print("[ABORT] 缺 zhipuai: pip install 'zhipuai>=2'")
    sys.exit(1)
key = os.environ.get("ZHIPUAI_API_KEY", "")
if not key or not key.isascii() or key.count(".") != 1:
    print("[ABORT] ZHIPUAI_API_KEY 缺失或格式不对(应为 id.secret 形态)")
    sys.exit(1)
client = ZhipuAI(api_key=key)

print("[2/4] 节点名索引(往返校验用)...")
from backend import data_loader
g = data_loader.get_graph()
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
    seen = set()
    for nm in names:
        n = norm(nm)
        if n and n not in seen:
            seen.add(n)
            name2node[n].append((t.id, t.name, t.subject, t.book))


def resolve(ret_name, subject, band):
    hits = name2node.get(norm(ret_name), [])
    if not hits:
        return None
    same_s = [h for h in hits if h[2] == subject]
    same_sb = [h for h in same_s if h[3] == band]
    pick = (same_sb or same_s or hits)[0]
    return pick[0], pick[1]


print("[3/4] 断点续跑检查...")
done = set()
if MAP.exists():
    for ln in MAP.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(ln)
            done.add(e["subject"] + "|" + e["norm"])
        except Exception:
            pass
print("  已完成 " + str(len(done)))
tasks = []
for ln in TASKS.read_text(encoding="utf-8").splitlines():
    t = json.loads(ln)
    if scope and t["scope"] != scope:
        continue
    if t["subject"] + "|" + t["norm"] in done:
        continue
    tasks.append(t)
if limit:
    tasks = tasks[:limit]
print("  待处理 " + str(len(tasks)) + " -> "
      + str(-(-len(tasks) // BATCH)) + " 批")

SYS = (
    "你是K12知识点图谱对齐助手。输入是若干【缺失的前置名称】, 每条含科目/学段/"
    "目标节点样例/候选节点。请为每条选出与输入名称指称【同一个知识点】的节点。\n"
    "规则:\n"
    "1) 只有指同一知识才可选。主题相关但不是同一知识的一律输出空串: "
    "更宽泛(输入'一般现在时', 候选'if从句时态规则(一般现在时替代将来时)'只是"
    "用到一般现在时的规则, 不是一般现在时本身)、下游应用(输入'will构成的简单"
    "将来时', 候选'If..., ...will...简单条件状语从句'是will的后续应用, 不是"
    "will本身)、更狭窄、平行变体(肯定句vs否定句, do疑问句vs does疑问句), "
    "全部输出空串。\n"
    "2) 泛化能力描述(阅读理解能力/基本素养/学习兴趣/认读能力)输出空串。\n"
    "3) m只能是候选之一的原名或图中确定存在的标准知识点名, 严禁编造。\n"
    "4) 拿不准输出空串。判无不是失败, 错边才是事故。\n"
    "5) 严格输出JSON数组不要解释: "
    '[{"i":序号, "m":"节点原名或空串"}]')

MAP.parent.mkdir(exist_ok=True)
ok = nulls = skipped = lowsim = 0
t0 = time.time()
with MAP.open("a", encoding="utf-8") as f:
    for bi in range(0, len(tasks), BATCH):
        batch = tasks[bi:bi + BATCH]
        direct = [t for t in batch if not t.get("candidates")]
        batch = [t for t in batch if t.get("candidates")]
        for t in direct:
            f.write(json.dumps(
                {"norm": t["norm"], "raw": t["name"],
                 "subject": t["subject"], "band": t["band"],
                 "mentions": t["mentions"], "via": "cand-empty",
                 "sim": 0.0, "id": None, "node": None, "ret": ""},
                ensure_ascii=False) + "\n")
        nulls += len(direct)
        if direct:
            print("  直接判无(无候选,不烧调用) " + str(len(direct)))
        if not batch:
            continue
        payload = [{"i": i, "name": t["name"], "subject": t["subject"],
                    "band": t["band"], "targets": t["targets"],
                    "candidates": [c[0] for c in t["candidates"]]}
                   for i, t in enumerate(batch)]
        content = None
        for attempt in (1, 2):
            try:
                r = client.chat.completions.create(
                    model=MODEL, temperature=0.1,
                    messages=[{"role": "system", "content": SYS},
                              {"role": "user", "content":
                               json.dumps(payload, ensure_ascii=False)}])
                content = r.choices[0].message.content or ""
                break
            except Exception as exc:
                print("  批" + str(bi // BATCH) + "尝试" + str(attempt)
                      + "失败: " + str(exc)[:120])
                time.sleep(3)
        arr = []
        if content:
            m = re.search(r"\[.*\]", content, re.S)
            try:
                arr = json.loads(m.group(0)) if m else []
            except ValueError:
                arr = []
        if not arr:
            skipped += len(batch)
            print("  批" + str(bi // BATCH + 1) + " 跳过(重跑会补)")
            continue
        got = {}
        for it in arr:
            if isinstance(it, dict) and "i" in it:
                try:
                    got[int(it["i"])] = str(it.get("m", "") or "")
                except (ValueError, TypeError):
                    pass
        for i, t in enumerate(batch):
            ret = got.get(i, "")
            hit = resolve(ret, t["subject"], t["band"]) if ret.strip() else None
            sim = jac(norm(t["name"]), norm(ret)) if (hit and ret.strip()) else 0.0
            killed = bool(hit) and sim < SIM_FLOOR
            if killed:
                hit = None
                lowsim += 1
            rec = {"norm": t["norm"], "raw": t["name"],
                   "subject": t["subject"], "band": t["band"],
                   "mentions": t["mentions"],
                   "via": ("llm" if hit else "llm-low" if killed else "null"),
                   "sim": round(sim, 2),
                   "id": hit[0] if hit else None,
                   "node": hit[1] if hit else None, "ret": ret}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if hit:
                ok += 1
            elif not killed:
                nulls += 1
        f.flush()
        print("  批" + str(bi // BATCH + 1) + "/"
              + str(-(-len(tasks) // BATCH)) + " 解析" + str(ok)
              + " 判无" + str(nulls) + " 低相似拦截" + str(lowsim)
              + " 跳过" + str(skipped) + " " + str(int(time.time() - t0)) + "s")
print("=" * 60)
print("解析 " + str(ok) + " | 判无 " + str(nulls)
      + " | 低相似拦截 " + str(lowsim) + " | 跳过 " + str(skipped)
      + " / 共 " + str(len(tasks)))
print("产物: " + str(MAP.relative_to(ROOT)))
