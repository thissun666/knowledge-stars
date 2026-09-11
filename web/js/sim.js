(function () {
  "use strict";
  var CELL = 46, REST = 52;
  var CW = 800, CH = 600;

  function finite(v) { return typeof v === "number" && isFinite(v); }

  function create(nodes, w, h) {
    CW = w || 800; CH = h || 600;
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      if (!finite(n.x) || !finite(n.y)) {
        var a = Math.random() * Math.PI * 2;
        var r = Math.sqrt(Math.random()) * Math.min(CW, CH) * 0.35;
        n.x = CW / 2 + Math.cos(a) * r;
        n.y = CH / 2 + Math.sin(a) * r;
      }
      if (!finite(n.vx)) n.vx = 0;
      if (!finite(n.vy)) n.vy = 0;
    }
  }

  function tick(nodes, edges, alpha) {
    if (!nodes.length) return;
    var i, j, n;
    var grid = {};
    var cs = CELL;
    for (i = 0; i < nodes.length; i++) {
      n = nodes[i];
      var k = Math.floor(n.x / cs) + "," + Math.floor(n.y / cs);
      (grid[k] || (grid[k] = [])).push(n);
    }
    var R2 = CELL * CELL;
    for (i = 0; i < nodes.length; i++) {
      var a = nodes[i];
      var gx = Math.floor(a.x / cs), gy = Math.floor(a.y / cs);
      for (var ox = -1; ox <= 1; ox++) {
        for (var oy = -1; oy <= 1; oy++) {
          var cell = grid[(gx + ox) + "," + (gy + oy)];
          if (!cell) continue;
          for (j = 0; j < cell.length; j++) {
            var b = cell[j];
            if (b === a) continue;
            var dx = a.x - b.x, dy = a.y - b.y;
            var d2 = dx * dx + dy * dy;
            if (d2 >= R2 || d2 < 0.01) continue;
            var d = Math.sqrt(d2);
            var f = ((CELL - d) / CELL) * 1.6 * alpha / d;
            var fx = dx * f, fy = dy * f;
            a.vx += fx; a.vy += fy;
            b.vx -= fx; b.vy -= fy;
          }
        }
      }
    }
    for (i = 0; i < edges.length; i++) {
      var e = edges[i];
      var p = e._na, q = e._nb;
      var ddx = q.x - p.x, ddy = q.y - p.y;
      var dist = Math.sqrt(ddx * ddx + ddy * ddy) || 0.01;
      var fs = (dist - REST) / dist * 0.06 * alpha;
      var sx = ddx * fs, sy = ddy * fs;
      p.vx += sx; p.vy += sy;
      q.vx -= sx; q.vy -= sy;
    }
    var maxV = 10;
    for (i = 0; i < nodes.length; i++) {
      n = nodes[i];
      if (finite(n._gx)) {
        n.vx += (n._gx - n.x) * 0.02 * alpha;
        n.vy += (n._gy - n.y) * 0.02 * alpha;
      }
      n.vx += (CW / 2 - n.x) * 0.0012 * alpha;
      n.vy += (CH / 2 - n.y) * 0.0012 * alpha;
      n.vx *= 0.82;
      n.vy *= 0.82;
      var sp = Math.sqrt(n.vx * n.vx + n.vy * n.vy);
      if (sp > maxV) { n.vx = n.vx / sp * maxV; n.vy = n.vy / sp * maxV; }
      n.x += n.vx;
      n.y += n.vy;
    }
  }

  window.Sim = { create: create, tick: tick };
})();
