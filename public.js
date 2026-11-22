// tradeready.in front page for signed-out visitors and Google: today's market read, built on the server
// from the latest scan. It shows market weather, indexes, sector rotation, strongest F&O stocks, OI build-up,
// movers and the ban list. It never shows setups, buy/stop/target levels, the watchlist or the scorecard.

const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (v, d = 2) => v == null ? "—" : Number(v).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });
const pct = (v, d = 1) => v == null ? "—" : `${v > 0 ? "+" : ""}${Number(v).toFixed(d)}%`;
const cls = v => v > 0 ? "up" : v < 0 ? "down" : "";
const STAGE_C = { Leading: "var(--up)", Improving: "var(--info)", Weakening: "var(--warn)", Lagging: "var(--down)" };
const WX = sc => sc >= 70 ? ["sun", "Clear skies"] : sc >= 55 ? ["partly", "Partly sunny"] : sc >= 40 ? ["rain", "Light showers"] : ["storm", "Storm warning"];
const tv = s => `https://www.tradingview.com/chart/?symbol=NSE:${encodeURIComponent(s)}`;

function spark(v, color, w = 120, h = 34) {
  if (!v || v.length < 2) return "";
  const lo = Math.min(...v), hi = Math.max(...v), r = hi - lo || 1;
  const pts = v.map((x, i) => `${(i / (v.length - 1) * w).toFixed(1)},${(h - 2 - (x - lo) / r * (h - 4)).toFixed(1)}`).join(" ");
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true"><polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.8" stroke-linejoin="round" vector-effect="non-scaling-stroke"/></svg>`;
}

function rrg(secs) {
  secs = secs.filter(s => s.ratio != null && s.mom != null);
  if (!secs.length) return "";
  const W = 560, H = 380, P = 34;
  const xr = Math.max(...secs.map(s => Math.abs(s.ratio - 1)), .01) * 1.25, yr = Math.max(...secs.map(s => Math.abs(s.mom)), .005) * 1.25;
  const X = v => P + (v - (1 - xr)) / (2 * xr) * (W - 2 * P), Y = v => H - P - (v + yr) / (2 * yr) * (H - 2 * P);
  const q = (x, y, t, c) => `<text x="${x}" y="${y}" font-size="11" font-weight="700" fill="${c}" opacity=".8">${t}</text>`;
  const dots = secs.map(s => { const cx = X(s.ratio), cy = Y(s.mom), right = cx < W - 110;
    return `<g><circle cx="${cx}" cy="${cy}" r="6" fill="${STAGE_C[s.stage] || "var(--muted)"}" stroke="var(--surface)" stroke-width="2"/>
      <text x="${right ? cx + 9 : cx - 9}" y="${cy + 4}" text-anchor="${right ? "start" : "end"}" font-size="10.5" font-weight="600" fill="var(--ink)">${esc(s.name)}</text></g>`; }).join("");
  return `<svg class="rrg" viewBox="0 0 ${W} ${H}" role="img" aria-label="Sector rotation chart of NSE sectors against Nifty">
    <rect x="${X(1)}" y="${P}" width="${W - P - X(1)}" height="${Y(0) - P}" fill="var(--up)" opacity=".07"/><rect x="${P}" y="${P}" width="${X(1) - P}" height="${Y(0) - P}" fill="var(--info)" opacity=".07"/>
    <rect x="${P}" y="${Y(0)}" width="${X(1) - P}" height="${H - P - Y(0)}" fill="var(--down)" opacity=".06"/><rect x="${X(1)}" y="${Y(0)}" width="${W - P - X(1)}" height="${H - P - Y(0)}" fill="var(--warn)" opacity=".06"/>
    <line x1="${X(1)}" x2="${X(1)}" y1="${P}" y2="${H - P}" stroke="var(--line)"/><line x1="${P}" x2="${W - P}" y1="${Y(0)}" y2="${Y(0)}" stroke="var(--line)"/>
    ${q(W - P - 62, P + 14, "LEADING", "var(--up)")}${q(P + 6, P + 14, "IMPROVING", "var(--info)")}${q(P + 6, H - P - 8, "LAGGING", "var(--down)")}${q(W - P - 82, H - P - 8, "WEAKENING", "var(--warn)")}
    <text x="${W / 2}" y="${H - 8}" text-anchor="middle" font-size="10" fill="var(--muted)">Relative strength vs Nifty →</text>
    <text x="12" y="${H / 2}" text-anchor="middle" font-size="10" fill="var(--muted)" transform="rotate(-90 12 ${H / 2})">Momentum →</text>${dots}</svg>`;
}

const SKY = {
  sun: `<circle cx="70" cy="50" r="22" fill="#fbbf24"/><g stroke="#fbbf24" stroke-width="4" stroke-linecap="round">${[0, 45, 90, 135, 180, 225, 270, 315].map(a => { const r = a * Math.PI / 180; return `<line x1="${70 + Math.cos(r) * 31}" y1="${50 + Math.sin(r) * 31}" x2="${70 + Math.cos(r) * 39}" y2="${50 + Math.sin(r) * 39}"/>`; }).join("")}</g>`,
  partly: `<circle cx="58" cy="42" r="20" fill="#fbbf24"/><path d="M40 82h52a16 16 0 0 0 0-32 22 22 0 0 0-42 6 13 13 0 0 0-10 26z" fill="#fff" stroke="#c7cbe0" stroke-width="2"/>`,
  rain: `<path d="M30 64h60a17 17 0 0 0 0-34 24 24 0 0 0-46 6 14 14 0 0 0-14 28z" fill="#e4e8f2" stroke="#aeb6cc" stroke-width="2"/><g stroke="#60a5fa" stroke-width="3" stroke-linecap="round"><line x1="44" y1="74" x2="40" y2="86"/><line x1="62" y1="74" x2="58" y2="86"/><line x1="80" y1="74" x2="76" y2="86"/></g>`,
  storm: `<path d="M30 60h60a17 17 0 0 0 0-34 24 24 0 0 0-46 6 14 14 0 0 0-14 28z" fill="#8b93a8"/><path d="M62 62l-10 16h10l-6 14 16-20h-10l6-10z" fill="#fbbf24"/>`
};

function headline(s) {
  const secs = (s.sectors || []).filter(x => x.r1 != null).sort((a, b) => b.r1 - a.r1);
  const inform = (s.sectors || []).filter(x => x.stage === "Leading" || x.stage === "Improving").length;
  if (!secs.length) return { h: "Today's NSE market read", deck: "" };
  const nifty = (s.indexes || []).find(i => i.key === "NIFTY") || {}, bank = (s.indexes || []).find(i => i.key === "BANKNIFTY") || {};
  const m = s.market || {}, br = (m.daily_bull_hist || []).slice(-1)[0];
  return {
    best: secs[0].name, worst: secs[secs.length - 1].name,
    h: `${secs[0].name} lead, ${secs[secs.length - 1].name} drag. ${inform} of ${s.sectors.length} sectors in form.`,
    deck: `Nifty ${pct(nifty.chg1, 2)} and Bank Nifty ${pct(bank.chg1, 2)} on the day. ${m.adv ?? "—"} F&O stocks rose and ${m.dec ?? "—"} fell; ${br ?? "—"}% are bullish on the Daily chart and India VIX is ${m.vix ?? "—"}.`
  };
}

export function renderPublic(s, sj) {
  const st = s.strength || {}, m = s.market || {};
  const [kind, word] = WX(st.score ?? 50);
  const gen = s.generated_ist || "";
  const when = gen ? new Date(gen.replace(" ", "T") + ":00+05:30") : new Date();
  const dateTxt = when.toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: "Asia/Kolkata" });
  const timeTxt = when.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata" });
  const hd = headline(s);
  const secsFull = (sj && sj.sectors) || [];
  const secs = s.sectors || [];
  const byStage = ["Leading", "Improving", "Weakening", "Lagging"].map(g => [g, secs.filter(x => x.stage === g)]);

  const idx = (s.indexes || []).map(i => {
    const c = (i.chg1 ?? 0) >= 0 ? "var(--up)" : "var(--down)";
    return `<div class="ix"><div class="ixn">${esc(i.name)}</div><div class="ixv">${num(i.close)}</div>
      <div class="ixc"><span class="${cls(i.chg1)}">${pct(i.chg1, 2)}</span><span class="muted">5d ${pct(i.chg5, 1)}</span></div><div class="sp">${spark((i.spark || []).slice(-30), c)}</div></div>`;
  }).join("");

  const parts = (st.parts || []).map(p => { const c = p.value >= 60 ? "var(--up)" : p.value >= 40 ? "var(--warn)" : "var(--down)";
    return `<div class="part"><span>${esc(p.name)}</span><span class="bar"><i style="width:${p.value}%;background:${c}"></i></span><b>${p.value}</b><small>${esc(p.why)}</small></div>`; }).join("");

  const strong = (s.strongest_fo || []).slice(0, 12).map(x => `<tr>
      <td class="n rk">${x.rank}</td><td><a class="sym" href="${tv(x.symbol)}" target="_blank" rel="noopener">${esc(x.symbol)}</a><div class="muted sm">${esc(x.sector || "")}</div></td>
      <td class="n"><span class="score">${x.score}</span></td><td class="n ${cls(x.r1m)}">${pct(x.r1m)}</td><td class="n ${cls(x.r3m)}">${pct(x.r3m)}</td>
      <td class="why">${(x.reasons || []).filter(r => r.kind !== "vk").slice(0, 3).map(r => `<span class="tag">${esc(r.text)}</span>`).join("")}</td></tr>`).join("");

  const boxes = ((s.quadrants || {}).boxes || []);
  const BOX_C = { long: "var(--up)", short: "var(--down)", cover: "var(--info)", unwind: "var(--warn)" };
  const oi = boxes.map(b => `<div class="oib" style="--c:${BOX_C[b.key] || "var(--muted)"}"><div class="oih"><b>${esc(b.name)}</b><span>${b.count} stocks</span></div>
      ${(b.top || []).slice(0, 5).map(t => `<div class="oir"><a href="${tv(t.symbol)}" target="_blank" rel="noopener">${esc(t.symbol)}</a><span class="muted">OI ${pct(t.oi)}</span><span class="${cls(t.px)}">${pct(t.px)}</span></div>`).join("")}</div>`).join("");

  const mover = (list) => (list || []).slice(0, 6).map(g => `<div class="oir"><a href="${tv(g.symbol)}" target="_blank" rel="noopener">${esc(g.symbol)}</a><span class="muted">${esc(g.sector || "")}</span><span class="${cls(g.chg)}">${pct(g.chg, 2)}</span></div>`).join("");

  const hu = s.heads_up || {}, ban = hu.ban || [], res = Object.entries(hu.results || {}).sort((a, b) => a[1].localeCompare(b[1]));
  const fmtD = d => new Date(d + "T00:00:00+05:30").toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "Asia/Kolkata" });

  const faq = [
    ["What is TradeReady?", "TradeReady scans every NSE F&O stock on Monthly, Weekly, Daily and Hourly charts and reads futures open interest, sector rotation and market breadth. This page is the free daily summary; signed-in members also see the full setup scanner."],
    ["How often is it updated?", `Through the trading day, with the latest read at ${timeTxt} IST on ${dateTxt}. Open interest data is from the last NSE session${(s.quadrants || {}).date ? ` (${fmtD(s.quadrants.date)})` : ""}.`],
    ["Is it free?", "Yes. Signing in with any Google account is free and gives view access to the full scanner."],
    ["Is this investment advice?", "No. TradeReady is a study and tracking tool. It is not a SEBI-registered investment adviser or research analyst, and nothing here is a recommendation to buy or sell."]
  ];
  const ld = [{ "@context": "https://schema.org", "@type": "WebApplication", name: "TradeReady", url: "https://tradeready.in/", applicationCategory: "FinanceApplication", operatingSystem: "Web",
      description: "Daily NSE F&O market read: market strength, sector rotation, strongest stocks and futures open interest build-up.", offers: { "@type": "Offer", price: "0", priceCurrency: "INR" }, inLanguage: "en-IN" },
    { "@context": "https://schema.org", "@type": "FAQPage", mainEntity: faq.map(([q, a]) => ({ "@type": "Question", name: q, acceptedAnswer: { "@type": "Answer", text: a } })) }];

  const title = `NSE market today: ${hd.best ? `${hd.best} lead` : "market read"}, strength ${st.score ?? "—"} · TradeReady`;
  const desc = `${dateTxt}: ${hd.h} Market strength ${st.score ?? "—"}/100 (${st.label || ""}). Sector rotation, strongest F&O stocks, OI build-up and the F&O ban list.`;
  const g = `<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="#EA4335" d="M12 10.2v3.9h5.5c-.2 1.3-1.6 3.8-5.5 3.8-3.3 0-6-2.7-6-6.1s2.7-6.1 6-6.1c1.9 0 3.1.8 3.8 1.5l2.6-2.5C16.8 3.2 14.6 2.2 12 2.2 6.6 2.2 2.3 6.6 2.3 12s4.3 9.8 9.7 9.8c5.6 0 9.3-3.9 9.3-9.5 0-.6-.1-1.1-.2-1.6H12z"/></svg>`;
  const mark = `<span class="mark"><svg viewBox="0 0 32 32" aria-hidden="true"><path d="M5 22l6-6 5 4 10-11" fill="none" stroke="#fff" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"/><path d="M20 9h6v6" fill="none" stroke="#fff" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"/></svg></span>`;
  const readyFo = s.ready_fo ?? 0, readyAll = s.ready_count ?? 0;
  const ghost = Array.from({ length: 3 }, (_, i) => `<div class="ghost"><div class="gl" style="width:${50 + i * 12}%"></div><div class="gl" style="width:80%"></div><div class="gbars">${Array.from({ length: 16 }, (_, j) => `<i style="height:${20 + ((i * 7 + j * 13) % 60)}%"></i>`).join("")}</div><div class="gl" style="width:65%"></div></div>`).join("");

  return `<!doctype html>
<html lang="en-IN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>${esc(title)}</title>
<meta name="description" content="${esc(desc)}">
<link rel="canonical" href="https://tradeready.in/">
<meta name="robots" content="index,follow,max-image-preview:large">
<meta property="og:type" content="website"><meta property="og:site_name" content="TradeReady"><meta property="og:url" content="https://tradeready.in/">
<meta property="og:title" content="${esc(title)}"><meta property="og:description" content="${esc(desc)}">
<meta property="og:image" content="https://tradeready.in/og-image.png"><meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image"><meta name="twitter:title" content="${esc(title)}"><meta name="twitter:description" content="${esc(desc)}"><meta name="twitter:image" content="https://tradeready.in/og-image.png">
<meta name="theme-color" content="#f6f4ef">
<link rel="icon" href="/favicon.svg" type="image/svg+xml"><link rel="apple-touch-icon" href="/apple-touch-icon.png"><link rel="manifest" href="/manifest.webmanifest">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-title" content="TradeReady"><meta name="apple-mobile-web-app-status-bar-style" content="default">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@500;700&family=Space+Grotesk:wght@500;600;700&display=swap" rel="stylesheet">
<script type="application/ld+json">${JSON.stringify(ld).replace(/</g, "\\u003c")}</script>
<style>
:root{--bg:#f6f4ef;--bg2:#eceaff;--surface:#fff;--surface2:#f4f3fb;--line:#e6e3f3;--ink:#1e1b4b;--muted:#6b6894;--accent:#4f46e5;--accent2:#7c3aed;
  --up:#0f8a5f;--down:#e03131;--warn:#d9480f;--info:#2563eb;--shadow:0 6px 24px rgba(79,70,229,.08);
  --f-ui:Inter,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;--f-head:"Space Grotesk",Inter,system-ui,sans-serif;--f-num:"JetBrains Mono",ui-monospace,Menlo,monospace;color-scheme:light}
*{box-sizing:border-box}
html,body{margin:0;background:var(--bg);color:var(--ink)}
body{font-family:var(--f-ui);font-size:15px;line-height:1.5;-webkit-font-smoothing:antialiased;
  background:radial-gradient(1100px 520px at 75% -12%,var(--bg2),transparent 62%),var(--bg)}
a{color:inherit;text-decoration:none}
.wrap{max-width:1160px;margin:0 auto;padding-inline:max(16px,env(safe-area-inset-left,0px)) max(16px,env(safe-area-inset-right,0px))}
.up{color:var(--up)}.down{color:var(--down)}.muted{color:var(--muted)}.sm{font-size:12px}
header{position:sticky;top:0;z-index:10;background:color-mix(in srgb,var(--bg) 84%,transparent);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);border-bottom:1px solid var(--line);padding-top:env(safe-area-inset-top,0px)}
body::before{content:"";position:fixed;inset:0 0 auto;height:env(safe-area-inset-top,0px);z-index:11;background:var(--bg)}
.bar1{display:flex;align-items:center;justify-content:space-between;gap:12px;padding-block:12px}
.brand{display:flex;align-items:center;gap:9px;font-family:var(--f-head);font-weight:700;font-size:20px;letter-spacing:-.03em}
.brand b{color:var(--accent)}
.mark{width:32px;height:32px;border-radius:10px;display:grid;place-items:center;background:linear-gradient(135deg,var(--accent),var(--accent2));flex:none}
.mark svg{width:22px;height:22px}
.gbtn{display:inline-flex;align-items:center;gap:9px;padding:10px 16px;border-radius:12px;background:var(--ink);color:#fff;font-weight:700;font-size:14px;white-space:nowrap;box-shadow:0 8px 22px rgba(30,27,75,.18)}
.gbtn svg{background:#fff;border-radius:50%;padding:2px;width:22px;height:22px}
.gbtn.big{padding:14px 22px;font-size:15.5px}
@media (max-width:480px){.bar1 .gbtn .long{display:none}.bar1 .gbtn{padding:9px 13px}}

.hero{display:grid;gap:18px;padding-block:28px 8px;grid-template-columns:1fr}
@media (min-width:920px){.hero{grid-template-columns:minmax(0,1.35fr) minmax(0,1fr);align-items:stretch;padding-top:40px}}
.kicker{display:flex;gap:10px;align-items:center;flex-wrap:wrap;font-size:12.5px;font-weight:700;color:var(--muted);letter-spacing:.04em;text-transform:uppercase}
.live{display:inline-flex;align-items:center;gap:6px;color:var(--up)}
.live i{width:8px;height:8px;border-radius:50%;background:currentColor;box-shadow:0 0 0 4px color-mix(in srgb,currentColor 20%,transparent);animation:pulse 2s infinite}
@keyframes pulse{50%{box-shadow:0 0 0 7px color-mix(in srgb,currentColor 4%,transparent)}}
h1{font-family:var(--f-head);font-size:clamp(30px,5.2vw,52px);line-height:1.04;letter-spacing:-.035em;margin:14px 0 14px;text-wrap:balance;font-weight:700}
h1 em{font-style:normal;color:var(--up)} h1 s{text-decoration:none;color:var(--down)}
.deck{font-size:clamp(15px,1.7vw,17px);color:var(--muted);max-width:60ch;margin:0 0 20px}
.cta{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.cta small{color:var(--muted);font-size:12.5px}

.wx{position:relative;border-radius:22px;overflow:hidden;padding:22px;min-height:260px;display:flex;flex-direction:column;justify-content:space-between;gap:12px;
  background:linear-gradient(160deg,var(--sky1),var(--sky2));color:var(--wxink,#1e1b4b);border:1px solid var(--line);box-shadow:var(--shadow)}
.wx[data-w="sun"]{--sky1:#ffe9b0;--sky2:#fff8e6}.wx[data-w="partly"]{--sky1:#dbe6ff;--sky2:#f6f4ef}.wx[data-w="rain"]{--sky1:#c9d3e6;--sky2:#eef2f8}.wx[data-w="storm"]{--sky1:#5b6478;--sky2:#9aa3b5;--wxink:#fff}
.wx .sky{position:absolute;right:6px;top:4px;width:130px;height:110px;opacity:.95}
.lbl{font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;opacity:.75}
.wx .wscore{font-family:var(--f-num);font-size:72px;font-weight:700;letter-spacing:-.05em;line-height:1}
.wx .word{font-family:var(--f-head);font-size:22px;font-weight:600}
.wx .say{font-size:14px;max-width:34ch;opacity:.85;margin-top:6px}
.hist{display:flex;gap:4px;align-items:flex-end;height:30px}
.hist i{flex:1;border-radius:3px;background:currentColor;opacity:.25;min-height:3px}
.hist i:last-child{opacity:.9}

.strip{display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(min(150px,100%),1fr));margin:26px 0 8px}
.ix{background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:12px 14px;box-shadow:var(--shadow);min-width:0}
.ixn{font-size:12px;font-weight:700;color:var(--muted)}
.ixv{font-family:var(--f-num);font-size:20px;font-weight:700;letter-spacing:-.03em;margin-top:2px}
.ixc{display:flex;justify-content:space-between;font-family:var(--f-num);font-size:12px;font-weight:600}
.sp{height:34px;margin-top:6px}.sp svg{width:100%;height:100%;display:block}

section.blk{margin-top:34px}
.sh{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:12px}
.sh h2{font-family:var(--f-head);font-size:clamp(21px,2.6vw,27px);letter-spacing:-.025em;margin:0;text-wrap:balance}
.sh p{margin:0;color:var(--muted);font-size:13.5px;max-width:62ch}
.card{background:var(--surface);border:1px solid var(--line);border-radius:20px;padding:18px;box-shadow:var(--shadow);min-width:0}
.two{display:grid;gap:14px;grid-template-columns:1fr}
@media (min-width:920px){.two{grid-template-columns:minmax(0,1.4fr) minmax(0,1fr)}}
.rrg{width:100%;height:auto;display:block;font-family:Inter,sans-serif}
.stage{margin-bottom:14px}
.stage h3{margin:0 0 7px;font-size:11.5px;letter-spacing:.09em;text-transform:uppercase;color:var(--c)}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{font-size:12.5px;font-weight:700;padding:5px 10px;border-radius:999px;background:color-mix(in srgb,var(--c) 11%,transparent);color:var(--c)}
.chip small{font-family:var(--f-num);opacity:.8;margin-left:4px}

.parts{display:grid;gap:10px}
.part{display:grid;grid-template-columns:120px 1fr 32px;gap:4px 10px;align-items:center;font-size:13px}
.part .bar{height:8px;border-radius:9px;background:var(--surface2);overflow:hidden}.part .bar i{display:block;height:100%;border-radius:9px}
.part b{font-family:var(--f-num);text-align:right}
.part small{grid-column:1/-1;color:var(--muted);font-size:11.5px;margin-top:-2px}

.tw{overflow-x:auto;margin:0 -6px;padding:0 6px}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th{font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);text-align:left;font-weight:700;padding:8px;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:10px 8px;border-bottom:1px solid var(--line);vertical-align:middle}
td.n,th.n{text-align:right;font-family:var(--f-num);white-space:nowrap}
.rk{color:var(--muted)}
.sym{font-family:var(--f-num);font-weight:700;font-size:14.5px}
.sym:hover{color:var(--accent)}
.score{display:inline-block;min-width:34px;text-align:center;padding:3px 7px;border-radius:8px;background:color-mix(in srgb,var(--up) 12%,transparent);color:var(--up);font-weight:700}
.why{min-width:260px}
.tag{display:inline-block;font-size:11.5px;padding:3px 8px;border-radius:7px;background:var(--surface2);color:var(--muted);margin:2px 4px 2px 0;font-weight:600}

.oig{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(min(250px,100%),1fr))}
.oib{background:var(--surface);border:1px solid var(--line);border-top:3px solid var(--c);border-radius:16px;padding:14px;box-shadow:var(--shadow)}
.oih{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px}
.oih b{font-family:var(--f-head);font-size:16px;color:var(--c)} .oih span{font-size:12px;color:var(--muted)}
.oir{display:grid;grid-template-columns:1fr auto 64px;gap:10px;font-size:13px;padding:6px 0;border-bottom:1px dashed var(--line);align-items:center}
.oir:last-child{border-bottom:0}
.oir a{font-family:var(--f-num);font-weight:700}
.oir span:last-child{text-align:right;font-family:var(--f-num);font-weight:600}
.oir .muted{font-size:11.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

.heads{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(min(260px,100%),1fr))}
.lock{position:relative;overflow:hidden;border-radius:22px;padding:26px;color:#fff;background:linear-gradient(135deg,#1e1b4b,#4338ca 55%,#7c3aed);box-shadow:0 20px 50px rgba(67,56,202,.28)}
.lock h2{font-family:var(--f-head);font-size:clamp(24px,3.4vw,34px);letter-spacing:-.03em;margin:6px 0 8px;line-height:1.1}
.lock p{margin:0 0 18px;opacity:.85;max-width:52ch}
.lock .bignum{font-family:var(--f-num);font-size:clamp(46px,7vw,64px);font-weight:700;line-height:1;letter-spacing:-.05em}
.lock .gbtn{background:#fff;color:#1e1b4b}
.lockg{display:grid;gap:18px;grid-template-columns:1fr;align-items:center}
@media (min-width:860px){.lockg{grid-template-columns:minmax(0,1fr) minmax(0,1.1fr)}}
.ghosts{display:grid;gap:10px;grid-template-columns:repeat(3,minmax(0,1fr));filter:blur(3px);opacity:.55;pointer-events:none;user-select:none}
.ghost{background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.2);border-radius:14px;padding:12px;display:grid;gap:7px}
.gl{height:8px;border-radius:6px;background:rgba(255,255,255,.5)}
.gbars{display:flex;align-items:flex-end;gap:3px;height:48px}.gbars i{flex:1;background:rgba(255,255,255,.55);border-radius:2px}
.steps{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(min(240px,100%),1fr))}
.step{padding:18px}.step b{font-family:var(--f-num);color:var(--accent);font-size:13px}.step h3{font-family:var(--f-head);margin:6px 0 6px;font-size:18px;letter-spacing:-.02em}.step p{margin:0;color:var(--muted);font-size:13.5px}
details{border-bottom:1px solid var(--line);padding:14px 2px}
summary{cursor:pointer;font-weight:700;font-size:15px;list-style:none;display:flex;justify-content:space-between;gap:12px}
summary::after{content:"+";color:var(--accent);font-weight:700}
details[open] summary::after{content:"–"}
details p{color:var(--muted);margin:8px 0 0;font-size:14px;max-width:75ch}
footer{margin-top:44px;border-top:1px solid var(--line);padding-block:22px max(26px,env(safe-area-inset-bottom,0px));color:var(--muted);font-size:12.5px}
footer .row{display:flex;justify-content:space-between;gap:14px;flex-wrap:wrap}
footer a{font-weight:600;margin-right:14px}
.fade{animation:up .6s cubic-bezier(.2,.7,.2,1) both}
@keyframes up{from{opacity:0;transform:translateY(10px)}}
@media (prefers-reduced-motion:reduce){.fade,.live i{animation:none}}
</style>
</head>
<body>
<header><div class="wrap bar1">
  <a class="brand" href="/">${mark}<span>Trade<b>Ready</b></span></a>
  <a class="gbtn" href="/auth/google">${g}<span>Sign in<span class="long"> with Google</span></span></a>
</div></header>

<main class="wrap">
  <div class="hero">
    <div class="fade">
      <div class="kicker"><span class="live"><i></i>Updated ${esc(timeTxt)} IST</span><span>·</span><span>${esc(dateTxt)}</span></div>
      <h1>${hd.best ? `${esc(hd.best)} <em>lead</em>, ${esc(hd.worst)} <s>drag</s>. ${esc(hd.h.split(". ").slice(1).join(". "))}` : esc(hd.h)}</h1>
      <p class="deck">${esc(hd.deck)}</p>
      <div class="cta"><a class="gbtn big" href="/auth/google">${g}Open the full scanner, free</a><small>Any Google account. View only.</small></div>
    </div>
    <div class="wx fade" data-w="${kind}" style="animation-delay:.08s">
      <svg class="sky" viewBox="0 0 120 100" aria-hidden="true">${SKY[kind]}</svg>
      <div><span class="lbl">Market weather · NSE F&amp;O</span></div>
      <div><div style="display:flex;align-items:baseline;gap:12px;flex-wrap:wrap"><span class="wscore">${st.score ?? "—"}</span><span class="word">${esc(word)}</span></div>
        <div class="say">${esc(st.label || "")} · ${esc(st.trend || "")}. A 0-100 read of breadth, Nifty trend, sectors, money flow and VIX.</div></div>
      ${(st.history || []).length < 5 ? "" : `<div class="hist" title="Strength, recent days">${(st.history || []).slice(-20).map(h => `<i style="height:${Math.max(6, h.score)}%" title="${esc(h.date)}: ${h.score}"></i>`).join("")}</div>`}
    </div>
  </div>

  <div class="strip fade" style="animation-delay:.14s">${idx}</div>

  <section class="blk">
    <div class="sh"><h2>Sector rotation</h2><p>Where money is moving. Right of centre beats Nifty; above centre is gaining strength. Sectors usually travel clockwise: Improving → Leading → Weakening → Lagging.</p></div>
    <div class="two">
      <div class="card">${rrg(secsFull)}</div>
      <div class="card">${byStage.map(([gname, list]) => `<div class="stage" style="--c:${STAGE_C[gname]}"><h3>${gname} · ${list.length}</h3><div class="chips">${list.map(x => `<span class="chip" style="--c:${STAGE_C[gname]}">${esc(x.name)}<small>${pct(x.r1)}</small></span>`).join("") || `<span class="muted sm">None today</span>`}</div></div>`).join("")}
        <div class="muted sm">Figures are 1 month vs Nifty.</div></div>
    </div>
  </section>

  <section class="blk">
    <div class="sh"><h2>Inside the market score</h2><p>Six readings make the ${st.score ?? ""} above. Each is 0-100; higher is better for long trades.</p></div>
    <div class="two"><div class="card"><div class="parts">${parts}</div></div>
      <div class="card"><div class="lbl" style="color:var(--muted)">Breadth</div>
        <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin-top:10px">
          <div><div class="muted sm">Advance / decline (F&amp;O)</div><div style="font-family:var(--f-num);font-size:22px;font-weight:700"><span class="up">${m.adv ?? "—"}</span> / <span class="down">${m.dec ?? "—"}</span></div></div>
          <div><div class="muted sm">Bullish on Daily</div><div style="font-family:var(--f-num);font-size:22px;font-weight:700">${(m.daily_bull_hist || []).slice(-1)[0] ?? "—"}%</div></div>
          <div><div class="muted sm">Bullish on Weekly</div><div style="font-family:var(--f-num);font-size:22px;font-weight:700">${m.weekly_bull_pct ?? "—"}%</div></div>
          <div><div class="muted sm">India VIX</div><div style="font-family:var(--f-num);font-size:22px;font-weight:700">${m.vix ?? "—"}</div></div>
        </div></div></div>
  </section>

  <section class="blk">
    <div class="sh"><h2>Strongest F&amp;O stocks</h2><p>Ranked on relative strength, trend, momentum across timeframes and sector. A list to study, not a buy list.</p></div>
    <div class="card"><div class="tw"><table><thead><tr><th class="n">#</th><th>Stock</th><th class="n">Score</th><th class="n">1M</th><th class="n">3M</th><th>Why it ranks</th></tr></thead><tbody>${strong}</tbody></table></div></div>
  </section>

  <section class="blk">
    <div class="sh"><h2>Futures OI build-up</h2><p>Open interest and price change in stock futures from the last NSE session${(s.quadrants || {}).date ? ` (${esc(fmtD(s.quadrants.date))})` : ""}. Rising OI with rising price is long build-up; falling OI with rising price is short covering.</p></div>
    <div class="oig">${oi}</div>
  </section>

  <section class="blk">
    <div class="sh"><h2>Movers and heads-up</h2><p>Biggest F&amp;O moves of the day, stocks in the F&amp;O ban, and results coming up.</p></div>
    <div class="heads">
      <div class="oib" style="--c:var(--up)"><div class="oih"><b>Top gainers</b><span>F&amp;O</span></div>${mover(s.gainers)}</div>
      <div class="oib" style="--c:var(--down)"><div class="oih"><b>Top losers</b><span>F&amp;O</span></div>${mover(s.losers)}</div>
      <div class="oib" style="--c:var(--warn)"><div class="oih"><b>Heads-up</b><span>F&amp;O</span></div>
        <div class="muted sm" style="margin:4px 0 6px;font-weight:700">In F&amp;O ban</div><div class="chips">${ban.map(b => `<span class="chip" style="--c:var(--down)">${esc(b)}</span>`).join("") || `<span class="muted sm">None today</span>`}</div>
        <div class="muted sm" style="margin:12px 0 6px;font-weight:700">Results soon</div><div class="chips">${res.map(([k, d]) => `<span class="chip" style="--c:var(--info)">${esc(k)}<small>${esc(fmtD(d))}</small></span>`).join("") || `<span class="muted sm">None this week</span>`}</div></div>
    </div>
  </section>

  <section class="blk">
    <div class="lock"><div class="lockg">
      <div><div class="lbl">Members see more</div>
        <div class="bignum">${readyFo}</div>
        <h2>F&amp;O setups are Ready today${readyAll > readyFo ? `, plus ${readyAll - readyFo} in equity` : ""}.</h2>
        <p>Pullbacks, squeezes, overspills, flags and VCPs on four timeframes, with charts, unusual OI, block deals and every index in detail. Free with Google sign-in.</p>
        <a class="gbtn big" href="/auth/google">${g}Sign in to see them</a></div>
      <div class="ghosts" aria-hidden="true">${ghost}</div>
    </div></div>
  </section>

  <section class="blk">
    <div class="sh"><h2>How TradeReady works</h2></div>
    <div class="steps">
      <div class="card step"><b>01</b><h3>Scan every F&amp;O stock</h3><p>Each stock is read on Monthly, Weekly, Daily and Hourly charts, every hour of the trading day.</p></div>
      <div class="card step"><b>02</b><h3>Add the money trail</h3><p>Futures open interest, short squeezes, block deals and sector rotation show where big money is moving.</p></div>
      <div class="card step"><b>03</b><h3>Wait for Ready</h3><p>A setup turns Ready only when its own rules line up and the market allows it. Fewer, cleaner trades.</p></div>
    </div>
  </section>

  <section class="blk">
    <div class="sh"><h2>Questions</h2></div>
    <div>${faq.map(([q, a]) => `<details><summary>${esc(q)}</summary><p>${esc(a)}</p></details>`).join("")}</div>
  </section>
</main>

<footer><div class="wrap">
  <div class="row"><div><a href="/privacy">Privacy</a><a href="/terms">Terms</a><a href="/login">Sign in</a><a href="/admin">Admin</a></div><div>© ${new Date().getFullYear()} TradeReady · tradeready.in</div></div>
  <p style="margin:12px 0 0;max-width:90ch">For study and tracking only. TradeReady is not a SEBI-registered investment adviser or research analyst, and nothing on this site is a recommendation to buy or sell. Market data can be delayed or wrong; check with your broker before you trade.</p>
</div></footer>
</body>
</html>`;
}

// Cached for 5 minutes so Google and visitors share one copy.
export async function publicPage(env, ctx) {
  const key = new Request("https://tradeready.cache/public-v1");
  const hit = await caches.default.match(key);
  if (hit) return hit;
  const get = f => fetch(`https://raw.githubusercontent.com/${env.REPO}/main/data/${f}`, { cf: { cacheTtl: 300, cacheEverything: true } })
    .then(r => r.ok ? r.json() : null).catch(() => null);
  const [s, sj] = await Promise.all([get("site.json"), get("sectors.json")]);
  if (!s) return null;
  const res = new Response(renderPublic(s, sj), { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "public, max-age=300" } });
  ctx && ctx.waitUntil ? ctx.waitUntil(caches.default.put(key, res.clone())) : await caches.default.put(key, res.clone());
  return res;
}
