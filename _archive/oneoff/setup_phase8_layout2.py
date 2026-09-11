# -*- coding: utf-8 -*-
r'''
setup_phase8_layout2.py - 分团布局 v2: 串珠式横排
  团半径按规模 sqrt 缩放 -> 团心间距 = 团半径和 + gap(硬保证分离)
  一行放不下自动两行交错; 小学两团变左右, 不再是电灯泡
  同时把缓存版本号 v=8 -> v=9
'''
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
CURLY = "".join(chr(c) for c in (0x201C, 0x201D, 0x2018, 0x2019))

NEW_FN = r'''  function seedClusters() {
    if (!model) return;
    var groups = {}, i;
    for (i = 0; i < model.nodes.length; i++) {
      var n = model.nodes[i];
      var g = CLUSTER_OF[n._cs] || n._cs || "其他";
      (groups[g] || (groups[g] = [])).push(n);
    }
    var names = Object.keys(groups);
    var rads = [], gap = 42;
    var need = 0;
    for (i = 0; i < names.length; i++) {
      rads.push(16 * Math.sqrt(groups[names[i]].length) + 26);
      need += rads[i] * 2 + gap;
    }
    need -= gap;
    var rows = (need > W * 0.92 && names.length >= 3) ? 2 : 1;
    var rowW = [0, 0], rowOf = [];
    for (i = 0; i < names.length; i++) {
      rowOf.push(rows === 2 ? i % 2 : 0);
      rowW[rowOf[i]] += rads[i] * 2 + gap;
    }
    for (i = 0; i < rows; i++) rowW[i] -= gap;
    var rowY = rows === 2 ? [H * 0.34, H * 0.68] : [H / 2];
    var curX = [];
    for (i = 0; i < rows; i++) curX.push(W / 2 - rowW[i] / 2);
    for (i = 0; i < names.length; i++) {
      var r = rowOf[i];
      var cx = curX[r] + rads[i];
      curX[r] += rads[i] * 2 + gap;
      var cy = rowY[r];
      var members = groups[names[i]];
      var spread = rads[i] * 0.62;
      for (var j = 0; j < members.length; j++) {
        var m = members[j];
        var a2 = Math.random() * Math.PI * 2;
        var r2 = Math.sqrt(Math.random()) * spread;
        m.x = cx + Math.cos(a2) * r2;
        m.y = cy + Math.sin(a2) * r2;
        m._gx = cx;
        m._gy = cy;
      }
    }
  }
'''

main_js = ROOT / "web" / "js" / "main.js"
src = main_js.read_text(encoding="utf-8")

start = src.find("  function seedClusters() {")
end_marker = "\n  }\n\n  function rebuildSubjectChips"
end = src.find(end_marker, start)
if start < 0 or end < 0:
    print("[FAIL] 定位失败, 未写入任何文件; 请发我 web/js/main.js 的 seedClusters 附近内容")
    raise SystemExit(1)

if any(c in NEW_FN for c in CURLY) or chr(96) * 3 in NEW_FN:
    print("[FAIL] 新函数自检未过, 中止")
    raise SystemExit(1)

main_js.write_text(src[:start] + NEW_FN + src[end + len("\n  }\n"):],
                   encoding="utf-8")
print("[OK] seedClusters 已替换为串珠式横排布局")

html_p = ROOT / "web" / "index.html"
html = html_p.read_text(encoding="utf-8")
for name in ("api.js", "sim.js", "render.js", "ui.js", "main.js"):
    html = re.sub(r'src="/js/' + name + r'(\?v=\d+)?"',
                  'src="/js/' + name + '?v=9"', html)
html_p.write_text(html, encoding="utf-8")
print("[OK] 缓存版本号 -> v=9")
print("验收: 服务不重启也行(纯前端), 浏览器普通刷新:")
print("  1. 小学: 语数团在左, 英日团在右, 横向并列")
print("  2. 高中: 三团横向排(放不下则两行交错), 团间有明显空隙")
print("  3. 全部: 4-5团, 一行或两行, 团与团分开可见")
print("  4. 如果团仍偏挤或偏散, 回报学段+观感, 调 16(半径系数)/42(gap) 两个数即可")
