/* Money flow "river": shared by the app (index.html) and the public homepage (public.js).
   No backticks or template placeholders in here: it is pasted inside a template literal.
   river(canvas, {nodes:[{id,name,rel,chg,size}], mkt, label, mini, onPick}) -> {stop} */
function river(cv, o) {
  var g = cv.getContext("2d"), dpr = Math.min(2, window.devicePixelRatio || 1), W = 0, H = 0, raf = 0, dead = false, hover = null, tries = 0;
  var SH = {"Consumer Durables":"Cons. Durables","Construction Materials":"Cons. Materials","Capital Goods":"Cap. Goods","Consumer Services":"Cons. Services","Metals & Mining":"Metals","Healthcare Services":"Health Svcs"};
  var still = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  var MONO = "\"JetBrains Mono\",ui-monospace,Menlo,monospace", SANS = "Inter,system-ui,sans-serif";
  var outs = [], ins = [], L = [], P = [], G = {};
  function pick() {
    var max = W < 560 ? 7 : 9;
    outs = o.nodes.filter(function (n) { return n.rel < -0.15; }).sort(function (a, b) { return a.rel - b.rel; }).slice(0, max);
    ins = o.nodes.filter(function (n) { return n.rel > 0.15; }).sort(function (a, b) { return b.rel - a.rel; }).slice(0, max);
    var all = [];
    outs.forEach(function (a) { ins.forEach(function (b) { all.push({o: a, i: b, w: (-a.rel) * b.rel * Math.sqrt(Math.min(a.size, b.size) + 1)}); }); });
    L = [];
    outs.forEach(function (a) { all.filter(function (l) { return l.o === a; }).sort(function (x, y) { return y.w - x.w; }).slice(0, 2).forEach(function (l) { L.push(l); }); });
    ins.forEach(function (b) { if (!L.some(function (l) { return l.i === b; })) { var t = all.filter(function (l) { return l.i === b; }).sort(function (x, y) { return y.w - x.w; })[0]; if (t) L.push(t); } });
  }
  function layout() {
    var r = cv.getBoundingClientRect(); W = r.width; H = r.height; cv.width = W * dpr; cv.height = H * dpr; g.setTransform(dpr, 0, 0, dpr, 0, 0);
    pick();
    var narrow = W < 560, colW = narrow ? 7 : 10, lab = narrow ? 104 : Math.min(210, W * 0.17), top = narrow ? 48 : 40, gap = narrow ? 8 : 12, minH = narrow ? 28 : 32;
    G = {narrow: narrow, colW: colW, lx: lab, rx: W - lab - colW, top: top};
    function place(arr, key) {
      if (!arr.length) return;
      var avail = H - top - 14 - gap * (arr.length - 1), base = Math.min(minH, avail / arr.length), extra = Math.max(0, avail - base * arr.length);
      var tot = arr.reduce(function (s, n) { return s + L.filter(function (l) { return l[key] === n; }).reduce(function (a, l) { return a + l.w; }, 0); }, 0) || 1, y = top;
      arr.forEach(function (n) { var w = L.filter(function (l) { return l[key] === n; }).reduce(function (a, l) { return a + l.w; }, 0); n.h = base + extra * w / tot; n.y = y; y += n.h + gap; });
    }
    place(outs, "o"); place(ins, "i");
    outs.forEach(function (n) { var my = L.filter(function (l) { return l.o === n; }).sort(function (a, b) { return a.i.y - b.i.y; }), s = my.reduce(function (a, l) { return a + l.w; }, 0) || 1, y = n.y;
      my.forEach(function (l) { l.a0 = y; l.a1 = y + n.h * l.w / s; y = l.a1; }); });
    ins.forEach(function (n) { var my = L.filter(function (l) { return l.i === n; }).sort(function (a, b) { return a.o.y - b.o.y; }), s = my.reduce(function (a, l) { return a + l.w; }, 0) || 1, y = n.y;
      my.forEach(function (l) { l.b0 = y; l.b1 = y + n.h * l.w / s; y = l.b1; }); });
    P = []; var tw = L.reduce(function (a, l) { return a + l.w; }, 0) || 1;
    L.forEach(function (l) { for (var k = 0; k < Math.max(3, Math.round(l.w / tw * (narrow ? 110 : 190))); k++) P.push({l: l, t: Math.random(), s: 0.0016 + Math.random() * 0.0018, f: Math.random()}); });
  }
  function curve(y0, y1, t) { var u = 1 - t; return u * u * u * y0 + 3 * u * u * t * y0 + 3 * u * t * t * y1 + t * t * t * y1; }
  function px(t) { var x0 = G.lx + G.colW, x1 = G.rx, m = (x0 + x1) / 2, u = 1 - t; return u * u * u * x0 + 3 * u * u * t * m + 3 * u * t * t * m + t * t * t * x1; }
  function lit(l) { return !hover || l.o === hover || l.i === hover; }
  function frame() {
    if (dead || !cv.isConnected) return;
    var r = cv.getBoundingClientRect();
    if (!r.width) { if (++tries < 120) raf = requestAnimationFrame(frame); return; }
    if (Math.abs(r.width - W) > 2 || Math.abs(r.height - H) > 2) layout();
    g.clearRect(0, 0, W, H);
    var x0 = G.lx + G.colW, x1 = G.rx, m = (x0 + x1) / 2;
    g.textAlign = "center"; g.fillStyle = "#7d8590"; g.font = "600 11px " + SANS;
    var hy = G.narrow ? 34 : 16;
    g.textAlign = G.narrow ? "left" : "center"; g.fillText("← MONEY LEAVING", G.narrow ? 4 : G.lx + G.colW / 2 - 60, hy);
    g.textAlign = G.narrow ? "right" : "center"; g.fillText("MONEY ARRIVING →", G.narrow ? W - 4 : G.rx + G.colW / 2 + 60, hy);
    g.textAlign = "center"; g.fillStyle = "#c6ff3d"; g.font = "700 12px " + SANS; g.fillText((o.label || "MARKET") + "  " + (o.mkt >= 0 ? "+" : "") + o.mkt.toFixed(2) + "%", W / 2, 14);
    if (!L.length) { g.fillStyle = "#7d8590"; g.font = "500 14px " + SANS; g.fillText("Calm: no strong flows right now", W / 2, H / 2); raf = requestAnimationFrame(frame); return; }
    L.forEach(function (l) {
      var on = lit(l), gr = g.createLinearGradient(x0, 0, x1, 0), a = hover ? (on ? 0.55 : 0.07) : 0.3;
      gr.addColorStop(0, "rgba(248,81,73," + a + ")"); gr.addColorStop(1, "rgba(63,185,80," + (a + 0.05) + ")");
      g.fillStyle = gr; g.beginPath(); g.moveTo(x0, l.a0); g.bezierCurveTo(m, l.a0, m, l.b0, x1, l.b0); g.lineTo(x1, l.b1); g.bezierCurveTo(m, l.b1, m, l.a1, x0, l.a1); g.closePath(); g.fill();
    });
    P.forEach(function (p) {
      if (!still) p.t = (p.t + p.s) % 1; if (!lit(p.l)) return;
      var t = p.t, top = curve(p.l.a0, p.l.b0, t), bot = curve(p.l.a1, p.l.b1, t), y = top + (bot - top) * (0.15 + 0.7 * p.f), x = px(t);
      g.fillStyle = "rgba(240,246,252," + (0.2 + 0.65 * Math.sin(t * Math.PI)) + ")"; g.fillRect(x - 0.9, y - 0.9, 1.8, 1.8);
    });
    function side(arr, left) {
      arr.forEach(function (n) {
        var c = left ? "#f85149" : "#3fb950", x = left ? G.lx : G.rx, dim = hover && hover !== n && !L.some(function (l) { return lit(l) && (l.o === n || l.i === n); });
        g.globalAlpha = dim ? 0.35 : 1;
        g.fillStyle = c; g.fillRect(x, n.y, G.colW, n.h);
        var cy = n.y + n.h / 2, tx = left ? G.lx - 10 : G.rx + G.colW + 10;
        g.textAlign = left ? "right" : "left"; g.fillStyle = hover === n ? "#ffffff" : "#e6edf3"; g.font = (hover === n ? "700 " : "500 ") + (G.narrow ? 11.5 : 14) + "px " + SANS;
        var room = (left ? G.lx : W - G.rx - G.colW) - 16, nm = n.name;
        if (g.measureText(nm).width > room) nm = SH[n.name] || nm;
        while (nm.length > 4 && g.measureText(nm).width > room) nm = nm.slice(0, -2) + "…";
        g.fillText(nm, tx, cy - 2);
        g.fillStyle = c; g.font = "700 " + (G.narrow ? 10.5 : 12) + "px " + MONO; g.fillText((n.rel > 0 ? "+" : "") + n.rel.toFixed(2) + "%", tx, cy + (G.narrow ? 12 : 15));
        g.globalAlpha = 1;
      });
    }
    side(outs, true); side(ins, false);
    raf = requestAnimationFrame(frame);
  }
  function hit(e) {
    var r = cv.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top, arr = x < W / 2 ? outs : ins;
    if (x > G.lx + G.colW + 30 && x < G.rx - 30) return null;
    return arr.filter(function (n) { return y >= n.y - 4 && y <= n.y + n.h + 4; })[0] || null;
  }
  cv.onmousemove = function (e) { var n = hit(e); hover = n; cv.style.cursor = n || o.mini ? "pointer" : "default"; };
  cv.onmouseleave = function () { hover = null; };
  cv.onclick = function (e) { if (o.onPick) o.onPick(hit(e)); };
  layout(); frame();
  return {stop: function () { dead = true; cancelAnimationFrame(raf); }};
}
