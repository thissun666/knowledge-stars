# -*- coding: utf-8 -*-
r'''
setup_phase9_hotfix.py - 热修: k12 分片文件兼容裸数组形态
  1. data_loader 增加 _k12_load_split: dict 走原逻辑 / 裸数组收节点弃边并告警
  2. 测试对账改用同一规范化函数(与加载器行为强一致)
  3. 附 scan_k12_shapes.py: 扫出哪个文件是什么结构, 定位异常分片
'''
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CURLY = "".join(chr(c) for c in (0x201C, 0x201D, 0x2018, 0x2019))
BAD_BACKTICK = chr(96) * 3

HELPER = '''def _k12_load_split(path: str) -> Tuple[List[dict], List[dict]]:
    """读取 k12 分片: 兼容 {knowledge_points, relations} 与裸数组两种形态。"""
    d = _read_json(path)
    if isinstance(d, dict):
        return (d.get("knowledge_points") or [],
                d.get("relations") or [])
    if isinstance(d, list):
        logger.warning("k12 分片为裸数组(缺少 relations): %s", path)
        return d, []
    logger.warning("k12 分片结构未知, 跳过: %s", path)
    return [], []


'''

OLD_READ = (
    "    if load_k12:\n"
    "        for band, _subject, path in _k12_split_files(root, bands):\n"
    "            d = _read_json(path)\n"
    "            groups.append({\"band\": band, \"topics\": d.get(\"knowledge_points\", []),\n"
    "                           \"relations\": d.get(\"relations\", [])})\n"
)
NEW_READ = (
    "    if load_k12:\n"
    "        for band, _subject, path in _k12_split_files(root, bands):\n"
    "            kps, rels = _k12_load_split(path)\n"
    "            groups.append({\"band\": band, \"topics\": kps,\n"
    "                           \"relations\": rels})\n"
)
ANCHOR = "def _to_k12_topic(t: dict, band: str) -> Topic:"

OLD_TEST = (
    "        d = data_loader._read_json(path)\n"
    "        exp_nodes += len(d.get(\"knowledge_points\", []))\n"
    "        for r in d.get(\"relations\", []):\n"
)
NEW_TEST = (
    "        kps, rels = data_loader._k12_load_split(path)\n"
    "        exp_nodes += len(kps)\n"
    "        for r in rels:\n"
)

SCAN = r'''# -*- coding: utf-8 -*-
"""扫描 k12-data/split 各文件顶层结构, 找出格式异常的分片。"""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
split = ROOT / "k12-data" / "split"
if not split.is_dir():
    print("未找到 k12-data/split")
    raise SystemExit(0)
for band in sorted(os.listdir(split)):
    bdir = split / band
    if not bdir.is_dir():
        continue
    for fname in sorted(os.listdir(bdir)):
        if not fname.endswith(".json"):
            continue
        p = bdir / fname
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            print("[BAD-JSON] " + band + "/" + fname + ": " + str(e))
            continue
        if isinstance(d, dict):
            kp = len(d.get("knowledge_points") or [])
            rl = len(d.get("relations") or [])
            print("[dict ] " + band + "/" + fname +
                  "  kps=" + str(kp) + " rels=" + str(rl))
        elif isinstance(d, list):
            print("[LIST!] " + band + "/" + fname + "  items=" + str(len(d)))
            if d and isinstance(d[0], dict):
                print("        首元素键: " + ", ".join(sorted(d[0].keys())))
        else:
            print("[其他 ] " + band + "/" + fname + ": " + type(d).__name__)
'''


def main():
    print("项目根: " + str(ROOT))
    ok = True

    dl_p = ROOT / "backend" / "data_loader.py"
    dl = dl_p.read_text(encoding="utf-8")
    if OLD_READ not in dl:
        print("[FAIL] data_loader 未找到读取段锚点, 中止(文件可能已被改动)")
        ok = False
    if ok and ANCHOR not in dl:
        print("[FAIL] data_loader 未找到插入锚点, 中止")
        ok = False
    if ok:
        for piece in (HELPER, NEW_READ):
            if BAD_BACKTICK in piece or any(x in piece for x in CURLY):
                print("[FAIL] 注入内容自检未过, 中止")
                ok = False
                break
    if ok:
        dl = dl.replace(OLD_READ, NEW_READ)
        dl = dl.replace(ANCHOR, HELPER + ANCHOR)
        dl_p.write_text(dl, encoding="utf-8")
        print("[OK] backend/data_loader.py: 兼容裸数组 + 告警日志")

    if ok:
        t_p = ROOT / "tests" / "test_data_loader.py"
        t = t_p.read_text(encoding="utf-8")
        if OLD_TEST not in t:
            print("[FAIL] 测试未找到锚点, 中止")
            ok = False
        else:
            t = t.replace(OLD_TEST, NEW_TEST)
            t_p.write_text(t, encoding="utf-8")
            print("[OK] tests/test_data_loader.py: 对账走同一规范化函数")

    if ok:
        scan_p = ROOT / "scan_k12_shapes.py"
        scan_p.write_text(SCAN, encoding="utf-8")
        print("[OK] scan_k12_shapes.py: 结构扫描工具")

    if ok:
        print("验收:")
        print("  python -m pytest tests/ -q     (预期 69 passed)")
        print("  python -m backend.data_loader  (应能正常出报告)")
        print("  python scan_k12_shapes.py      (把输出发我, 定位异常文件)")
        print("=" * 56)


if __name__ == "__main__":
    main()
