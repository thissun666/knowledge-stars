# -*- coding: utf-8 -*-
r'''setup_phase9_meta.py - 把 relations.json 识别为元数据文件, 不再当科目加载'''
from pathlib import Path

ROOT = Path(__file__).resolve().parent
p = ROOT / "backend" / "data_loader.py"
src = p.read_text(encoding="utf-8")

A1 = 'K12_GRADE = {"小学": 3, "初中": 7, "高中": 10}'
A2 = ('            subject = fname[:-5]\n'
      '            if subject in K12_EXCLUDE.get(band, set()):')

if "_K12_META_FILES" in src:
    print("[SKIP] 已打过补丁")
    raise SystemExit(0)
if A1 not in src or A2 not in src:
    print("[FAIL] 锚点未找到, 中止(文件可能已被改动)")
    raise SystemExit(1)

src = src.replace(A1, A1 + '\n\n'
                  '# 非科目文件: 切分工具可能留下的元数据, 不参与图谱加载\n'
                  '_K12_META_FILES = {"relations"}')
src = src.replace(A2,
                  '            subject = fname[:-5]\n'
                  '            if subject in _K12_META_FILES:\n'
                  '                continue\n'
                  '            if subject in K12_EXCLUDE.get(band, set()):')
p.write_text(src, encoding="utf-8")
print("[OK] relations.json 已列为元数据, 永久跳过")
print("验收:")
print("  python -m pytest tests/ -q     (预期 69 passed)")
print("  python -m backend.data_loader  (不再出现裸数组警告, 数字不变)")
print("  重启服务 -> 高中视图应出现 数学 按钮(559个kkg节点, 进语数团)")
