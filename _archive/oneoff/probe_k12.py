# -*- coding: utf-8 -*-
r'''probe_k12.py - 探测 k12-knowledge-points 数据结构, 为写加载器收集事实'''
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "k12-data"

if not DATA.exists():
    print("[clone] 开始克隆(可能较慢)...")
    r = subprocess.run(["git", "clone", "--depth", "1",
                        "https://github.com/mmlong818/k12-knowledge-points.git",
                        str(DATA)], capture_output=True, text=True)
    print(r.stdout[-2000:] if r.stdout else "")
    print(r.stderr[-2000:] if r.stderr else "")
    if not DATA.exists():
        print("[FAIL] 克隆失败: 仓库不存在或网络不通, 请把上面的报错发我")
        sys.exit(1)

print("[OK] 仓库已就位: " + str(DATA))

split = DATA / "split"
if split.exists():
    print("\n[split 目录结构]")
    for p in sorted(split.rglob("*.json"))[:40]:
        print("  " + str(p.relative_to(DATA)) +
              "  (" + str(p.stat().st_size // 1024) + " KB)")
else:
    print("\n[提示] 没有 split/ 目录, 根目录内容:")
    for p in sorted(DATA.iterdir()):
        print("  " + p.name)

# 找一个中等大小的文件深入解剖
candidates = sorted(split.rglob("*.json"), key=lambda p: p.stat().st_size) \
    if split.exists() else sorted(DATA.rglob("*.json"), key=lambda p: p.stat().st_size)
if not candidates:
    print("[FAIL] 没找到任何 json")
    sys.exit(1)

target = candidates[len(candidates) // 2]
print("\n[解剖样本] " + str(target.relative_to(DATA)))
with open(target, encoding="utf-8") as f:
    data = json.load(f)

if isinstance(data, dict):
    print("顶层 keys: " + ", ".join(data.keys()))
    for k, v in data.items():
        if isinstance(v, list):
            print("  " + k + ": list, 长度 " + str(len(v)))
        elif isinstance(v, dict):
            print("  " + k + ": dict, keys=" + ", ".join(list(v.keys())[:10]))
        else:
            print("  " + k + ": " + repr(v)[:80])
    arr = next((v for v in data.values() if isinstance(v, list) and v), None)
else:
    arr = data
    print("顶层是 list, 长度 " + str(len(arr)))

def show(item, label):
    print("\n[" + label + "] 字段: " + ", ".join(item.keys()) if isinstance(item, dict) else "[" + label + "] " + type(item).__name__)
    print(json.dumps(item, ensure_ascii=False)[:500])

if arr:
    show(arr[0], "第1个元素")
    if len(arr) > 1:
        show(arr[-1], "最后1个元素")
    # 若同时存在节点数组和边数组, 各采样并统计关系类型
    lists = {k: v for k, v in (data.items() if isinstance(data, dict) else []) if isinstance(v, list)}
    for k, v in lists.items():
        sample = v[0]
        if isinstance(sample, dict):
            keys = tuple(sorted(sample.keys()))
            print("\n数组[" + k + "] 共同字段: " + ", ".join(keys))
    # 猜测边数组并统计 relation 类型与方向字段
    for k, v in lists.items():
        if not v or not isinstance(v[0], dict):
            continue
        for rk in ("relation", "relation_type", "type", "rel"):
            if rk in v[0]:
                from collections import Counter
                c = Counter(x.get(rk) for x in v)
                print("数组[" + k + "] 的 " + rk + " 分布: " + repr(dict(c)))
                break

print("\n[完成] 把以上全部输出发给我, 我据此写加载器")
