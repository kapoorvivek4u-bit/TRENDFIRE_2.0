/* Sector rotation graph (RRG) with trails. Shared by the app (Sectors page, Today snapshot) and the public homepage.
   rotGraph(sectors, {w, h, sel, mini, tap}) -> SVG string.
   sectors: [{name, stage, n, ratio, mom, trail:[{date, stage, ratio, mom}]}] as in data/sectors.json.
   No backticks-in-backticks tricks: plain ES5 so it can also be inlined. */
(function () {
  var C = { Leading: "#22c55e", Improving: "#60a5fa", Weakening: "#fb923c", Lagging: "#f87171" };
  var SH = { "Consumer Durables": "Cons. Durables", "Construction Materials": "Cons. Materials", "Capital Goods": "Cap. Goods",
    "Consumer Services": "Cons. Services", "Metals & Mining": "Metals", "Healthcare Services": "Health Svcs" };
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function stageOf(r, m) { return r >= 1 && m >= 0 ? "Leading" : r >= 1 ? "Weakening" : m >= 0 ? "Improving" : "Lagging"; }

  window.rotGraph = function (secs, o) {
    o = o || {};
    var W = o.w || 900, H = o.h || 560, mini = !!o.mini, P = mini ? 18 : 46, TR = mini ? 5 : 8;
    var S = secs.filter(function (s) { return s.ratio != null && s.mom != null; });
    if (!S.length) return "";
    // trail points (oldest -> newest), ending at today's position
    S.forEach(function (s) {
      var t = (s.trail || []).filter(function (p) { return p.ratio != null && p.mom != null; }).slice(-TR);
      s._pts = t.map(function (p) { return [p.ratio, p.mom]; });
      if (!s._pts.length || s._pts[s._pts.length - 1][0] !== s.ratio) s._pts.push([s.ratio, s.mom]);
    });
    var xr = 0.004, yr = 0.002;
    S.forEach(function (s) { s._pts.forEach(function (p) { xr = Math.max(xr, Math.abs(p[0] - 1)); yr = Math.max(yr, Math.abs(p[1])); }); });
    xr *= 1.12; yr *= 1.12;
    var sq = function (v) { return (v < 0 ? -1 : 1) * Math.sqrt(Math.min(1, Math.abs(v))); };  // spreads the crowded middle
    var X = function (v) { return P + (1 + sq((v - 1) / xr)) / 2 * (W - 2 * P); };
    var Y = function (v) { return H - P - (1 + sq(v / yr)) / 2 * (H - 2 * P); };
    var cx = X(1), cy = Y(0), g = [];
    var q = function (x, y, w, h, c) { return '<rect x="' + x + '" y="' + y + '" width="' + w + '" height="' + h + '" fill="' + c + '" fill-opacity=".10"/>'; };
    g.push('<defs><marker id="rgA" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="context-stroke"/></marker>' +
      '<filter id="rgS"><feDropShadow dx="0" dy="1" stdDeviation="1.6" flood-opacity=".5"/></filter></defs>');
    g.push('<rect x="0" y="0" width="' + W + '" height="' + H + '" rx="' + (mini ? 14 : 18) + '" fill="#0f1420"/>');
    g.push(q(cx, P, W - P - cx, cy - P, C.Leading) + q(P, P, cx - P, cy - P, C.Improving) + q(P, cy, cx - P, H - P - cy, C.Lagging) + q(cx, cy, W - P - cx, H - P - cy, C.Weakening));
    g.push('<line x1="' + cx + '" x2="' + cx + '" y1="' + P + '" y2="' + (H - P) + '" stroke="#2a3245" stroke-width="1.5"/><line x1="' + P + '" x2="' + (W - P) + '" y1="' + cy + '" y2="' + cy + '" stroke="#2a3245" stroke-width="1.5"/>');
    var fq = mini ? 10.5 : 14, pad = mini ? 8 : 14;
    g.push('<g font-weight="800" letter-spacing="1.5" font-size="' + fq + '">' +
      '<text x="' + (P + pad) + '" y="' + (P + pad + fq) + '" fill="' + C.Improving + '">IMPROVING</text>' +
      '<text x="' + (W - P - pad) + '" y="' + (P + pad + fq) + '" text-anchor="end" fill="' + C.Leading + '">LEADING</text>' +
      '<text x="' + (P + pad) + '" y="' + (H - P - pad) + '" fill="' + C.Lagging + '">LAGGING</text>' +
      '<text x="' + (W - P - pad) + '" y="' + (H - P - pad) + '" text-anchor="end" fill="' + C.Weakening + '">WEAKENING</text></g>');
    if (!mini) {
      g.push('<text x="' + (W / 2) + '" y="' + (H - 12) + '" text-anchor="middle" font-size="12" fill="#8b93a7">Weaker than Nifty  ←  Relative strength  →  Stronger than Nifty</text>');
      g.push('<text transform="translate(16 ' + (H / 2) + ') rotate(-90)" text-anchor="middle" font-size="12" fill="#8b93a7">Momentum →</text>');
    }
    var sel = o.sel || null;
    // trails
    S.forEach(function (s) {
      if (s._pts.length < 2) return;
      var c = C[s.stage] || "#94a3b8", d = s._pts.map(function (p, i) { return (i ? "L" : "M") + X(p[0]).toFixed(1) + " " + Y(p[1]).toFixed(1); }).join(" ");
      var op = sel ? (s.name === sel ? 0.95 : 0.14) : 0.5;
      g.push('<path d="' + d + '" fill="none" stroke="' + c + '" stroke-width="' + (s.name === sel ? 2.6 : 1.6) + '" stroke-dasharray="4 4" stroke-opacity="' + op + '" marker-end="url(#rgA)"/>');
      s._pts.slice(0, -1).forEach(function (p) { g.push('<circle cx="' + X(p[0]).toFixed(1) + '" cy="' + Y(p[1]).toFixed(1) + '" r="' + (mini ? 1.4 : 2) + '" fill="' + c + '" fill-opacity="' + op + '"/>'); });
    });
    // dots + labels (placed so names never overlap)
    var fs = mini ? 10 : 12.5, pts = S.map(function (s) { return { s: s, x: X(s.ratio), y: Y(s.mom), r: (mini ? 4 : 6) + Math.sqrt(s.n || 4) * (mini ? 0.9 : 1.6) }; })
      .sort(function (a, b) { return a.y - b.y; }), placed = [];
    pts.forEach(function (p) {
      var nm = (mini || W < 700 ? SH[p.s.name] : 0) || p.s.name, w = nm.length * fs * 0.6 + 6, best = null;
      var dy = [0, -1, 1, -2, 2, -3, 3, -4, 4, -5, 5];
      for (var i = 0; i < dy.length && !best; i++) {
        var sides = p.x < W - 160 ? [1, -1] : [-1, 1];
        for (var j = 0; j < 2 && !best; j++) {
          var side = sides[j], ly = p.y + fs * 0.35 + dy[i] * (fs + 3), x0 = side > 0 ? p.x + p.r + 5 : p.x - p.r - 5 - w, bx = [x0, ly - fs, x0 + w, ly + 3];
          if (bx[0] < 4 || bx[2] > W - 4 || bx[1] < 4 || bx[3] > H - 4) continue;
          var hit = placed.some(function (b) { return !(bx[2] < b[0] || bx[0] > b[2] || bx[3] < b[1] || bx[1] > b[3]); }) ||
            pts.some(function (t) { return t !== p && t.x > bx[0] - t.r && t.x < bx[2] + t.r && t.y > bx[1] - t.r && t.y < bx[3] + t.r; });
          if (!hit) best = { side: side, ly: ly, bx: bx, nm: nm };
        }
      }
      p.lab = best || { side: 1, ly: p.y + fs * 0.35, bx: [p.x + p.r + 5, p.y - fs, p.x + p.r + 5 + w, p.y + 3], nm: nm };
      placed.push(p.lab.bx);
    });
    pts.forEach(function (p) {
      var c = C[p.s.stage] || "#94a3b8", on = !sel || p.s.name === sel, L = p.lab, tap = o.tap ? ' data-rotsel="' + esc(p.s.name) + '" style="cursor:pointer"' : "";
      g.push('<g' + tap + ' opacity="' + (on ? 1 : 0.35) + '">' +
        (p.s.name === sel ? '<circle cx="' + p.x + '" cy="' + p.y + '" r="' + (p.r + 6) + '" fill="none" stroke="#fff" stroke-width="1.5" stroke-opacity=".8"/>' : "") +
        '<circle cx="' + p.x + '" cy="' + p.y + '" r="' + p.r + '" fill="' + c + '" stroke="#0f1420" stroke-width="2" filter="url(#rgS)"/>' +
        (Math.abs(L.ly - p.y - fs * 0.35) > 4 ? '<line x1="' + p.x + '" y1="' + p.y + '" x2="' + (L.side > 0 ? L.bx[0] - 1 : L.bx[2] + 1) + '" y2="' + (L.ly - fs * 0.35) + '" stroke="#475069" stroke-width="1"/>' : "") +
        '<text x="' + (L.side > 0 ? L.bx[0] : L.bx[2]) + '" y="' + L.ly + '" text-anchor="' + (L.side > 0 ? "start" : "end") + '" font-size="' + fs + '" font-weight="' + (p.s.name === sel ? 800 : 600) + '" fill="#e8ecf4">' + esc(L.nm) + '</text></g>');
    });
    return '<svg viewBox="0 0 ' + W + ' ' + H + '" class="rotg" role="img" aria-label="Sector rotation graph with trails" style="width:100%;height:auto;display:block;font-family:Inter,system-ui,sans-serif">' + g.join("") + '</svg>';
  };
  window.rotStageOf = stageOf;
  window.ROT_C = C;
})();
