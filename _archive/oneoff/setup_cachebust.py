# -*- coding: utf-8 -*-
r'''setup_cachebust.py - index.html 脚本引用加版本参数, 根治浏览器缓存'''
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
p = ROOT / "web" / "index.html"
html = p.read_text(encoding="utf-8")
VER = "v=8"
for name in ("api.js", "sim.js", "render.js", "ui.js", "main.js"):
    html = re.sub(r'src="/js/' + name + r'(\?v=\d+)?"',
                  'src="/js/' + name + '?' + VER + '"', html)
p.write_text(html, encoding="utf-8")
print("[OK] 已写入版本参数 " + VER)
for line in html.splitlines():
    if "/js/" in line:
        print("  " + line.strip())
