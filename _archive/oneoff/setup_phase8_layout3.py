# -*- coding: utf-8 -*-
r'''
setup_phase8_layout3.py - 分团布局 v3: 行距也按团半径计算
  <=3团: 单行横排(高中3团/小学2团, 维持现状思路)
  >=4团: 2x2网格, 垂直间距 = 两行最大半径和 + gap(修复初中上下两行融合)
  单行超宽时整体等比缩小
  缓存版本号 v=9 -> v=10
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
    for (i = 0; i < names.length; i++) {
      rads.push(16 * Math.sqrt(groups[names[i]].length) + 26);
    }
    var rows = names.length >= 4 ? 2 : 1;
    var rowOf = [], rowR = [], rowW = [];
    for (i = 0; i < rows; i++) { rowR.push(0); rowW.push(0); }
    for (i = 0; i < names.length; i++) {
      var ro = rows === 2 ? i % 2 : 0;
      rowOf.push(ro);
      rowW[ro] += rads[i] * 2 + gap;
      if (rads[i] > rowR[ro]) rowR[ro] = rads[i];
    }
    for (i = 0; i < rows; i++) rowW[i] -= gap;
    var maxW = 0;
    for (i = 0; i < rows; i++) if (rowW[i] > maxW) maxW = rowW[i];
    var avail = W * 0.92;
    var k = maxW > avail ? avail / maxW : 1;
    if (k < 1) {
      for (i = 0; i < rads.length; i++) rads[i] *= k;
      for (i = 0; i < rows; i++) { rowW[i] *= k; rowR[i] *= k; }
    }
    var rowY = [H / 2];
    if (rows === 2) {
      var half = (rowR[0] + rowR[1]) / 2 + gap;
      rowY = [H / 2 - half, H / 2 + half];
    }
    var curX = [];
    for (i = 0; i < rows; i++) curX.push(W / 2 - rowW[i] / 2);
    for (i = 0; i < names.length; i++) {
      var ro2 = rowOf[i];
      var rr = rads[i];
      var cxx = curX[ro2] + rr;
      curX[ro2] += rr * 2 + gap;
      var cyy = rowY[ro2];
      var members = groups[names[i]];
      var spread = rr * 0.62;
      for (var j = 0; j < members.length; j++) {
        var m = members[j];
        var a2 = Math.random() * Math.PI * 2;
        var rd = Math.sqrt(Math.random()) * spread;
        m.x = cxx + Math.cos(a2) * rd;
        m.y = cyy + Math.sin(a2) * rd;
        m._gx = cxx;
        m._gy = cyy;
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
    print("[FAIL] 定位失败, 未写入; 请把 web/js/main.js 里 seedClusters 附近发我")
    raise SystemExit(1)
if any(c in NEW_FN for c in CURLY) or chr(96) * 3 in NEW_FN:
    print("[FAIL] 自检未过, 中止")
    raise SystemExit(1)
main_js.write_text(src[:start] + NEW_FN + src[end + len("\n  }\n"):],
                   encoding="utf-8")
print("[OK] seedClusters -> v3 (行距按半径, 4团2x2网格)")

html_p = ROOT / "web" / "index.html"
html = html_p.read_text(encoding="utf-8")
for name in ("api.js", "sim.js", "render.js", "ui.js", "main.js"):
    html = re.sub(r'src="/js/' + name + r'(\?v=\d+)?"',
                  'src="/js/' + name + '?v=10"', html)
html_p.write_text(html, encoding="utf-8")
print("[OK] 缓存版本号 -> v=10")
print("验收(浏览器普通刷新):")
print("  小学/高中: 单行横排, 团间空隙清晰")
print("  初中/全部: 2x2 网格, 四团互不重叠(初中修复重点)")
print("  观感微调: 16(团大小系数) / 42(团间距) 两个数")
