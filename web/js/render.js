(function () {
  "use strict";
  var BG = "#0f1526";
  var NODE_COLOR = { mastered: "#34c77b", learning: "#f5a623", locked: "#566179" };
  var TAU = Math.PI * 2;

  function ok(v) { return typeof v === "number" && isFinite(v); }

  function draw(ctx, view, model, o) {
    if (!ok(view.k) || view.k <= 0) {
      view.k = 1; view.x = o.width / 2; view.y = o.height / 2;
    }
    ctx.setTransform(o.dpr, 0, 0, o.dpr, 0, 0);
    ctx.fillStyle = BG;
    ctx.fillRect(0, 0, o.width, o.height);
    var k = view.k;
    var isoSet = o.iso && o.now < o.iso.until ? o.iso.set : null;
    ctx.translate(view.x, view.y);
    ctx.scale(k, k);
    var dim = model.dimFn;
    var i;
    ctx.lineWidth = 1 / k;
    for (var pass = 0; pass < 2; pass++) {
      ctx.strokeStyle = pass === 0 ? "rgba(148,163,220,0.22)"
                                   : "rgba(148,163,220,0.06)";
      ctx.beginPath();
      for (i = 0; i < model.edges.length; i++) {
        var e = model.edges[i];
        if (!ok(e._na.x) || !ok(e._na.y) || !ok(e._nb.x) || !ok(e._nb.y)) {
          continue;
        }
        if (isoSet && !(isoSet[e._na.key] && isoSet[e._nb.key])) continue;
        var dimmed = dim(e._na) || dim(e._nb);
        if (pass === 0 && dimmed) continue;
        if (pass === 1 && !dimmed) continue;
        ctx.moveTo(e._na.x, e._na.y);
        ctx.lineTo(e._nb.x, e._nb.y);
      }
      ctx.stroke();
    }
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    for (i = 0; i < model.nodes.length; i++) {
      var n = model.nodes[i];
      if (!ok(n.x) || !ok(n.y)) continue;
      var sx = n.x * k + view.x, sy = n.y * k + view.y;
      if (sx < -40 || sx > o.width + 40 || sy < -40 || sy > o.height + 40) continue;
      if (isoSet && !isoSet[n.key]) continue;
      var st = model.progress[n.key] || "locked";
      var d = dim(n);
      var tw = Math.sin(o.now * 0.0015 + i * 2.399963);
      var isoT = isoSet && o.iso.key === n.key;
      var r = isoT ? 11 : 6 + tw * 0.7;
      ctx.globalAlpha = d ? 0.15 : (0.85 + 0.15 * tw);
      ctx.beginPath();
      ctx.arc(n.x, n.y, r, 0, TAU);
      ctx.fillStyle = isoT ? "#ff5252" : (NODE_COLOR[st] || NODE_COLOR.locked);
      ctx.fill();
      if (d) continue;
      if (model.boundarySet.has(n.key)) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, 10 + tw * 0.5, 0, TAU);
        ctx.strokeStyle = "#ffd166";
        ctx.lineWidth = 2 / k;
        ctx.stroke();
      }
      var fo = o.focus;
      if (fo && fo.key === n.key) {
        var ft = o.now - fo.t0;
        if (ft >= 0 && ft < 2500) {
          var fade = 1 - ft / 2500;
          var ct = (ft % 830) / 830;
          ctx.beginPath();
          ctx.arc(n.x, n.y, 8 + ct * 30, 0, TAU);
          ctx.strokeStyle = "rgba(255,82,82," +
            (0.9 * (1 - ct) * fade).toFixed(3) + ")";
          ctx.lineWidth = 2.5 / k;
          ctx.stroke();
          ctx.beginPath();
          ctx.arc(n.x, n.y, 9.5, 0, TAU);
          ctx.strokeStyle = "rgba(255,82,82," +
            (0.95 * Math.min(1, fade * 2)).toFixed(3) + ")";
          ctx.lineWidth = 2 / k;
          ctx.stroke();
        }
      }
      var p = o.pulses[n.key];
      if (p) {
        var age = (o.now - p) / 1000;
        if (age >= 0 && age < 1.2) {
          ctx.beginPath();
          ctx.arc(n.x, n.y, 6 + age * 26, 0, TAU);
          ctx.strokeStyle = "rgba(255,209,102," +
            (0.8 * (1 - age / 1.2)).toFixed(3) + ")";
          ctx.lineWidth = 2 / k;
          ctx.stroke();
        }
      }
      var focused = n.key === o.hoverKey || n.key === o.selectedKey;
      if (focused) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, 8.5, 0, TAU);
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = 1.5 / k;
        ctx.stroke();
      }
      if (focused || k >= 1.3 || isoSet) {
        ctx.fillStyle = "#d7defc";
        ctx.font = (12 / k).toFixed(2) + "px sans-serif";
        ctx.fillText(n.label || n.name, n.x, n.y - 17);
      }
    }
    ctx.globalAlpha = 1;
    var eps = o.edgePulses || [];
    for (i = 0; i < eps.length; i++) {
      var ep = eps[i];
      var dur = 900;
      var dt = (o.now - ep.t0) / dur;
      if (dt < 0 || dt > 1.3) continue;
      if (!ok(ep.from.x) || !ok(ep.from.y) ||
          !ok(ep.to.x) || !ok(ep.to.y)) continue;
      var tt = Math.min(1, dt);
      var ease = tt * (2 - tt);
      var px = ep.from.x + (ep.to.x - ep.from.x) * ease;
      var py = ep.from.y + (ep.to.y - ep.from.y) * ease;
      var fade = dt <= 1 ? 1 : Math.max(0, 1 - (dt - 1) / 0.3);
      var tailT = Math.max(0, ease - 0.18);
      var tx = ep.from.x + (ep.to.x - ep.from.x) * tailT;
      var ty = ep.from.y + (ep.to.y - ep.from.y) * tailT;
      ctx.beginPath();
      ctx.moveTo(tx, ty);
      ctx.lineTo(px, py);
      ctx.strokeStyle = "rgba(255,209,102," + (0.85 * fade).toFixed(3) + ")";
      ctx.lineWidth = 3 / k;
      ctx.lineCap = "round";
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(px, py, 9, 0, TAU);
      ctx.fillStyle = "rgba(255,209,102," + (0.25 * fade).toFixed(3) + ")";
      ctx.fill();
      ctx.beginPath();
      ctx.arc(px, py, 4.5, 0, TAU);
      ctx.fillStyle = "rgba(255,224,138," + (0.95 * fade).toFixed(3) + ")";
      ctx.fill();
    }
    ctx.lineCap = "butt";
  }

  function fit(nodes, w, h) {
    if (!nodes.length) return { x: 0, y: 0, k: 1 };
    var minX = 1e9, minY = 1e9, maxX = -1e9, maxY = -1e9, i;
    for (i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      if (!ok(n.x) || !ok(n.y)) continue;
      if (n.x < minX) minX = n.x;
      if (n.y < minY) minY = n.y;
      if (n.x > maxX) maxX = n.x;
      if (n.y > maxY) maxY = n.y;
    }
    if (minX > maxX || minY > maxY) return { x: 0, y: 0, k: 1 };
    var pad = 70;
    var bw = (maxX - minX) || 1, bh = (maxY - minY) || 1;
    var k = Math.min((w - pad * 2) / bw, (h - pad * 2) / bh);
    k = Math.max(0.02, Math.min(2.2, k));
    var x = w / 2 - (minX + bw / 2) * k, y = h / 2 - (minY + bh / 2) * k;
    if (!ok(x) || !ok(y) || !ok(k) || k <= 0) return { x: 0, y: 0, k: 1 };
    return { k: k, x: x, y: y };
  }

  function toWorld(view, sx, sy) {
    return { x: (sx - view.x) / view.k, y: (sy - view.y) / view.k };
  }

  window.Renderer = { draw: draw, fit: fit, toWorld: toWorld };
})();
