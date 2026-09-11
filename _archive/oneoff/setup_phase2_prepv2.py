# -*- coding: utf-8 -*-
r'''setup_phase2_prepv2.py - 三合一:
    1) runner守卫: 无候选任务直判null(via=cand-empty), 不烧API调用
    2) prep v2: 无节点池的科目整个跳过(结构性无解, 计数上报)
    3) audit重写: 学段过滤(上次锚点笔误未装上)'''
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def save(p, text):
    compile(text, str(p), "exec")
    p.write_text(text, encoding="utf-8")
    print("[OK] " + p.name)


# ---- 1) runner 守卫 ----
rp = ROOT / "run_phase2.py"
src = rp.read_text(encoding="utf-8")
if "cand-empty" in src:
    print("[SKIP] runner 已有守卫")
else:
    lines = src.splitlines()
    anchor = "        batch = tasks[bi:bi + BATCH]"
    idxs = [i for i, ln in enumerate(lines) if ln == anchor]
    assert len(idxs) == 1, "runner锚点异常: " + str(len(idxs))
    guard = [
        '        direct = [t for t in batch if not t.get("candidates")]',
        '        batch = [t for t in batch if t.get("candidates")]',
        '        for t in direct:',
        '            f.write(json.dumps(',
        '                {"norm": t["norm"], "raw": t["name"],',
        '                 "subject": t["subject"], "band": t["band"],',
        '                 "mentions": t["mentions"], "via": "cand-empty",',
        '                 "sim": 0.0, "id": None, "node": None, "ret": ""},',
        '                ensure_ascii=False) + "\\n")',
        '        nulls += len(direct)',
        '        if direct:',
        '            print("  直接判无(无候选,不烧调用) " + str(len(direct)))',
        '        if not batch:',
        '            continue',
    ]
    lines[idxs[0] + 1:idxs[0] + 1] = guard
    save(rp, "\n".join(lines) + "\n")

# ---- 2) prep v2 ----
pp = ROOT / "prep_phase2.py"
src = pp.read_text(encoding="utf-8")
if "POOL = set(subj_nodes)" in src:
    print("[SKIP] prep 已是 v2")
else:
    lines = src.splitlines()
    a = "    inv[subj] = d"
    i = [j for j, ln in enumerate(lines) if ln == a]
    assert len(i) == 1, "prep锚点1异常"
    lines[i[0] + 1:i[0] + 1] = ["", "POOL = set(subj_nodes)"]
    a = "tasks = {}"
    i = [j for j, ln in enumerate(lines) if ln.strip() == a]
    assert len(i) == 1, "prep锚点2异常"
    lines[i[0] + 1:i[0] + 1] = ["pool_skip_files = 0"]
    a = "    band, subject = p.parent.name, p.stem"
    i = [j for j, ln in enumerate(lines) if ln == a]
    assert len(i) == 1, "prep锚点3异常"
    lines[i[0] + 1:i[0] + 1] = [
        "    if subject not in POOL:",
        "        pool_skip_files += 1",
        "        continue",
    ]
    a = 'print("任务总数 " + str(len(tasks)) + " "'
    i = [j for j, ln in enumerate(lines) if ln.startswith(a)]
    assert len(i) == 1, "prep锚点4异常"
    lines[i[0]:i[0]] = [
        'print("跳过无节点池科目文件 " + str(pool_skip_files) + " 个(结构性无解)")'
    ]
    save(pp, "\n".join(lines) + "\n")

# ---- 3) audit 重写(带学段过滤) ----
ap = ROOT / "audit_phase2_pilot.py"
AUDIT = '''# -*- coding: utf-8 -*-
r\'\'\'audit_phase2_pilot.py v2 - 只读抽检; 学段过滤v2
    用法: python audit_phase2_pilot.py [高中|初中|小学] (不传=全部)\'\'\'
import json
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MAP = ROOT / "data" / "phase2_map.jsonl"
_sc = sys.argv[1] if len(sys.argv) > 1 else ""

recs = [json.loads(ln) for ln in
        MAP.read_text(encoding="utf-8").splitlines() if ln.strip()]
if _sc:
    recs = [r for r in recs if r.get("band") == _sc]
print("记录 " + str(len(recs)) + (" (band=" + _sc + ")" if _sc else ""))
if not recs:
    raise SystemExit("[ABORT] 无记录")
via = Counter(r.get("via", "?") for r in recs)
print("via: " + json.dumps(dict(via), ensure_ascii=False))
ok = [r for r in recs if r.get("id")]
nulls = [r for r in recs if not r.get("id")]
m_ok = sum(r.get("mentions", 0) for r in ok)
m_all = sum(r.get("mentions", 0) for r in recs)
print("提及覆盖: " + str(m_ok) + "/" + str(m_all)
      + " (" + str(round(m_ok / max(1, m_all) * 100)) + "%)")
print("解析到的唯一节点 " + str(len({r["id"] for r in ok})) + " 个")
print("--- 高频解析 Top15 (重点审) ---")
for r in sorted(ok, key=lambda x: -x.get("mentions", 0))[:15]:
    print("  [" + str(r.get("mentions", 0)) + "x] " + r["raw"][:22] + "  =>  "
          + str(r.get("node"))[:26] + " (" + r.get("band", "?")
          + ", sim" + str(r.get("sim", "?")) + ")")
rest = sorted(ok, key=lambda x: -x.get("mentions", 0))[15:]
random.seed(11)
for r in random.sample(rest, min(10, len(rest))):
    print("  [尾" + str(r.get("mentions", 0)) + "x] " + r["raw"][:22]
          + "  =>  " + str(r.get("node"))[:26] + " ("
          + r.get("band", "?") + ")")
print("--- 高频判无 Top8 (具体概念名=报警, 能力描述=正常) ---")
for r in sorted(nulls, key=lambda x: -x.get("mentions", 0))[:8]:
    print("  [" + str(r.get("mentions", 0)) + "x] " + r["raw"][:30]
          + "  (via=" + r.get("via", "?") + ")")
'''
save(ap, AUDIT)
print("完成。下一步: diag -> prep重跑 -> purge -> 高中pilot重跑")
