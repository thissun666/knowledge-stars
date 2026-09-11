# -*- coding: utf-8 -*-
r'''inspect_ai_meta.py - 只读侦察: AI客户端接口 + annotations子键 + kkg源文件
    不打印任何密钥值, 不修改任何文件'''
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent

print("=" * 25 + " A. annotations 子键分布 " + "=" * 25)
for band, fname in (("初中", "物理.json"), ("小学", "语文.json")):
    d = json.loads((ROOT / "k12-data" / "split" / band / fname)
                   .read_text(encoding="utf-8"))
    keyc, sample = Counter(), {}
    for kp in d.get("knowledge_points", []):
        raw = kp.get("annotations")
        if not raw:
            continue
        try:
            ann = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            continue
        if isinstance(ann, dict):
            for k, v in ann.items():
                keyc[k] += 1
                sample.setdefault(k, json.dumps(v, ensure_ascii=False)[:110])
    print(band + "/" + fname + " 键分布: "
          + str(dict(keyc.most_common(12))))
    for k, v in sample.items():
        print("   " + k + " => " + v)

print("=" * 25 + " B. AI 客户端定位 " + "=" * 25)
pat = re.compile(r"api_key|base_url|chat/completions|OpenAI|httpx"
                 r"|requests\.post|client\.chat|def |class ", re.I)
for p in sorted((ROOT / "backend").rglob("*.py")):
    lines = p.read_text(encoding="utf-8").splitlines()
    hits = [(i, ln.strip()) for i, ln in enumerate(lines, 1)
            if pat.search(ln)]
    if not hits:
        continue
    name = str(p.relative_to(ROOT))
    print("--- " + name + " (" + str(len(hits)) + " 处) ---")
    for i, ln in hits[:16]:
        print("  " + str(i) + ": " + ln[:150])

print("=" * 25 + " C. kkg 源文件 " + "=" * 25)
cands = []
for d in ("k12-data", "data", "pep-data", "kkg-data"):
    dp = ROOT / d
    if dp.exists():
        cands.extend(dp.rglob("*.json"))
cands += list(ROOT.glob("*.json"))
for p in [x for x in cands
          if any(s in x.name for s in ("必修", "选择", "kkg"))][:12]:
    print("  " + str(p.relative_to(ROOT)) + "  " + str(p.stat().st_size) + "B")

print("=" * 25 + " D. 配置变量名(只看名字) " + "=" * 25)
envp = ROOT / ".env"
if envp.exists():
    for ln in envp.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            print("  " + ln.split("=", 1)[0])
else:
    print("  (无 .env)")
