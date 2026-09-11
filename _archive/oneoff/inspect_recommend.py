# -*- coding: utf-8 -*-
r'''inspect_recommend.py - 只读侦察: 打印推荐链路前后端代码, 不修改任何文件'''
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent

print("=" * 25 + " A. graph_service.recommend_paths " + "=" * 25)
src = (ROOT / "backend" / "graph_service.py").read_text(encoding="utf-8")
m = re.search(r"    def recommend_paths.*?(?=\n    def |\nclass |\Z)", src, re.S)
print(m.group(0) if m else "[未找到 recommend_paths]")
print("-" * 80)
m2 = re.search(r"    def compute_boundary.*?(?=\n    def |\nclass |\Z)", src, re.S)
print(m2.group(0) if m2 else "[未找到 compute_boundary]")

print("=" * 25 + " B. 前端推荐弹窗渲染 " + "=" * 25)
for js in sorted((ROOT / "web" / "js").glob("*.js")):
    t = js.read_text(encoding="utf-8")
    idx = t.find("推荐学习路径")
    if idx >= 0:
        print("--- " + js.name + " ---")
        print(t[max(0, idx - 300): idx + 1600])

print("=" * 25 + " C. api.js 推荐接口 " + "=" * 25)
api = (ROOT / "web" / "js" / "api.js").read_text(encoding="utf-8")
i = api.find("recommend")
print(api[max(0, i - 150): i + 400] if i >= 0 else "[api.js 未找到 recommend]")
