// Public pages for Google, built on the server from data/seo.json (written by site_data.py after each scan):
//   /stocks, /stock/SYMBOL, /oi-buildup, /fo-ban-list, /sector-rotation, /strongest-stocks and /sitemap.xml.
// Data only: no setups, no buy/stop/target levels, no scorecard.
import { shell, esc, num, pct, cls, rrg, STAGE_C } from "./public.js";

const Q = { long: ["Long build-up", "var(--up)", "price up, OI up: fresh long positions"],
            short: ["Short build-up", "var(--down)", "price down, OI up: fresh short positions"],
            cover: ["Short covering", "var(--info)", "price up, OI down: shorts closing their positions"],
            unwind: ["Long unwinding", "var(--warn)", "price down, OI down: longs closing their positions"] };
const TF_C = { Impulse: "var(--up)", Bullish: "var(--up)", Bottoming: "var(--info)", Topping: "var(--warn)", Bearish: "var(--down)" };
const day = d => d ? new Date(d + "T00:00:00+05:30").toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric", timeZone: "Asia/Kolkata" }) : "";
const dayShort = d => d ? new Date(d + "T00:00:00+05:30").toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "Asia/Kolkata" }) : "";
const lakh = v => v == null ? "—" : v >= 1e7 ? `${(v / 1e7).toFixed(2)} Cr` : `${(v / 1e5).toFixed(1)} L`;
const sym = s => `<a class="sym" href="/stock/${encodeURIComponent(s)}">${esc(s)}</a>`;
const crumb = items => `<nav class="crumb" aria-label="Breadcrumb">${items.map(([h, t], i) => i < items.length - 1 ? `<a href="${h}">${esc(t)}</a> › ` : `<span>${esc(t)}</span>`).join("")}</nav>`;
const crumbLd = items => ({ "@context": "https://schema.org", "@type": "BreadcrumbList",
  itemListElement: items.map(([h, t], i) => ({ "@type": "ListItem", position: i + 1, name: t, item: "https://tradeready.in" + h })) });
const cta = (text = "See setups, charts and unusual OI for every F&O stock") => `<section class="blk"><div class="lock" style="padding:22px"><div class="lbl">Free with Google sign-in</div>
  <h2 style="font-size:clamp(20px,2.6vw,26px)">${esc(text)}</h2><a class="gbtn big" href="/auth/google">Sign in with Google</a></div></section>`;
const asOf = d => `<span class="live"><i></i>NSE data as of ${esc(day(d.oi_asof))}</span>`;

// ------------------------------------------------------------------ one stock
function oiChart(st, dates) {
  const oi = st.oi || [], px = st.px || [], n = oi.length;
  if (n < 3) return "";
  const W = 640, H = 230, P = 34, B = 26;
  const oiv = oi.filter(v => v != null), pxv = px.filter(v => v != null);
  const omax = Math.max(...oiv) * 1.05, omin = Math.min(...oiv) * .9;
  const plo = Math.min(...pxv), phi = Math.max(...pxv), pr = (phi - plo) || 1;
  const bw = (W - 2 * P) / n;
  const bars = oi.map((v, i) => { if (v == null) return ""; const h = (v - omin) / (omax - omin) * (H - P - B);
    return `<rect x="${(P + i * bw + 2).toFixed(1)}" y="${(H - B - h).toFixed(1)}" width="${(bw - 4).toFixed(1)}" height="${h.toFixed(1)}" rx="2" fill="var(--accent)" opacity="${i === n - 1 ? .55 : .2}"><title>${esc(dayShort(dates[i]))}: OI ${lakh(v)}</title></rect>`; }).join("");
  const pts = px.map((v, i) => v == null ? null : `${(P + i * bw + bw / 2).toFixed(1)},${(P + (1 - (v - plo) / pr) * (H - P - B - 20)).toFixed(1)}`).filter(Boolean).join(" ");
  const labs = [0, Math.floor(n / 2), n - 1].map(i => `<text x="${(P + i * bw + bw / 2).toFixed(1)}" y="${H - 8}" text-anchor="${i === 0 ? "start" : i === n - 1 ? "end" : "middle"}" font-size="11" fill="var(--muted)">${esc(dayShort(dates[i]))}</text>`).join("");
  return `<svg class="oichart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Futures open interest and price, last ${n} sessions">
    <line x1="${P}" x2="${W - P}" y1="${H - B}" y2="${H - B}" stroke="var(--line)"/>${bars}
    <polyline points="${pts}" fill="none" stroke="var(--ink)" stroke-width="2.2" stroke-linejoin="round"/>
    <text x="${P}" y="16" font-size="11" fill="var(--muted)">₹${num(phi)} high · ₹${num(plo)} low</text>${labs}</svg>
    <div class="muted sm" style="display:flex;gap:16px;flex-wrap:wrap;margin-top:6px"><span><i style="display:inline-block;width:10px;height:10px;border-radius:3px;background:var(--accent);opacity:.45;margin-right:5px"></i>Futures open interest</span><span><i style="display:inline-block;width:14px;height:2px;background:var(--ink);vertical-align:3px;margin-right:5px"></i>Futures price</span></div>`;
}

function readLine(s, st) {
  const q = Q[st.q];
  const parts = [];
  if (q) parts.push(`On ${day(s.oi_asof)}, ${esc(st.n || s.sym)} futures ${st.d >= 0 ? "rose" : "fell"} ${Math.abs(st.d ?? 0).toFixed(1)}% while open interest ${st.oid >= 0 ? "rose" : "fell"} ${Math.abs(st.oid ?? 0).toFixed(1)}%: <b style="color:${q[1]}">${q[0]}</b> (${q[2]}).`);
  if (st.oi5 != null && st.d5 != null) parts.push(`Over 5 sessions the price is ${pct(st.d5)} and OI ${pct(st.oi5)}.`);
  if (st.rk) parts.push(`It ranks <b>${st.rk}</b> of ${s.total} F&amp;O stocks on TradeReady's strength score (${st.sc}/100).`);
  if (st.stage) parts.push(`Its sector, ${esc(st.sec)}, is <b style="color:${STAGE_C[st.stage]}">${esc(st.stage.toLowerCase())}</b> against Nifty.`);
  return parts.join(" ");
}

export function stockPage(d, s) {
  const st = d.stocks[s];
  const total = Object.keys(d.stocks).length;
  const q = Q[st.q] || ["—", "var(--muted)", ""];
  const name = st.n || s;
  const peers = (d.sectors.find(x => x.name === st.sec) || { members: Object.keys(d.stocks).filter(k => d.stocks[k].sec === st.sec) }).members.filter(x => x !== s && d.stocks[x]).slice(0, 12);
  const title = `${s} OI analysis today: ${q[0].toLowerCase()}, open interest chart · TradeReady`;
  const desc = `${name} (${s}) futures ₹${num(st.c)} (${pct(st.d)}) on ${day(d.oi_asof)}: OI ${pct(st.oid)}, ${q[0].toLowerCase()}. 5-day OI ${pct(st.oi5)}. ${st.sec} sector${st.stage ? ` (${st.stage})` : ""}. Strength rank ${st.rk ?? "—"} of ${total}.`;
  const items = [["/", "Market today"], ["/stocks", "F&O stocks"], [`/stock/${s}`, s]];
  const tf = ["M", "W", "D", "H"].filter(k => st.st && st.st[k]);
  const body = `${crumb(items)}
  <div class="kicker">${asOf(d)}<span>·</span><span>${esc(st.sec)}</span></div>
  <h1 class="ph">${esc(s)} futures OI analysis <span class="muted" style="font-weight:600">· ${esc(name)}</span></h1>
  <p class="lede">${readLine({ ...d, sym: s, total }, st)}</p>
  ${st.ban ? `<div class="empty2" style="background:color-mix(in srgb,var(--down) 10%,var(--surface));color:var(--down);font-weight:700;margin:10px 0">${esc(s)} is in the F&amp;O ban period: no new F&amp;O positions until open interest falls below 80% of the market-wide limit. <a href="/fo-ban-list" style="text-decoration:underline">F&amp;O ban list</a></div>` : ""}
  ${st.res ? `<div class="empty2" style="margin:10px 0">Quarterly results due on <b>${esc(day(st.res))}</b>.</div>` : ""}
  <div class="kpis">
    <div class="kpi"><small>Futures price</small><b>₹${num(st.c)}</b><span class="${cls(st.d)}">${pct(st.d)} on the day</span></div>
    <div class="kpi"><small>Open interest</small><b>${lakh((st.oi || []).slice(-1)[0])}</b><span class="${cls(st.oid)}">${pct(st.oid)} on the day</span></div>
    <div class="kpi"><small>OI read</small><b style="color:${q[1]};font-family:var(--f-head);font-size:18px">${q[0]}</b><span>${esc(q[2])}</span></div>
    <div class="kpi"><small>5 sessions</small><b class="${cls(st.d5)}">${pct(st.d5)}</b><span>OI ${pct(st.oi5)}</span></div>
    <div class="kpi"><small>Strength rank</small><b>${st.rk ?? "—"}<span style="display:inline;font-size:13px"> / ${total}</span></b><span>Score ${st.sc ?? "—"}/100</span></div>
    <div class="kpi"><small>Vs Nifty</small><b class="${cls(st.r3m)}">${pct(st.r3m)}</b><span>3 months · 1M ${pct(st.r1m)}</span></div>
  </div>
  <section class="blk" style="margin-top:20px"><div class="sh"><h2>Open interest vs price</h2><p>Daily futures open interest (bars) and futures price (line), last ${(st.oi || []).length} sessions.</p></div>
    <div class="card">${oiChart(st, d.dates)}</div></section>
  ${tf.length ? `<section class="blk"><div class="sh"><h2>Trend on each timeframe</h2><p>Momentum state of the ${esc(s)} chart on each timeframe.</p></div>
    <div class="tf" style="grid-template-columns:repeat(${tf.length},minmax(0,1fr))">${tf.map(k => { const v = st.st[k]; return `<div style="--c:${TF_C[v] || "var(--muted)"}"><small>${{ M: "MONTHLY", W: "WEEKLY", D: "DAILY", H: "HOURLY" }[k]}</small><b>${esc(v || "—")}</b></div>`; }).join("")}</div></section>` : ""}
  ${(st.rs || []).length ? `<section class="blk"><div class="sh"><h2>Why ${esc(s)} ranks ${st.rk}</h2></div><div class="card"><div>${st.rs.map(r => `<span class="tag" style="font-size:13px">${esc(r)}</span>`).join("")}</div></div></section>` : ""}
  ${(st.ev || []).length ? `<section class="blk"><div class="sh"><h2>Unusual activity</h2><p>Volume and open-interest events from the last few sessions.</p></div>
    <div class="card">${st.ev.map(([w, t]) => `<div class="oir" style="grid-template-columns:70px 1fr"><span class="muted">${esc(w)}</span><span style="text-align:left;font-family:var(--f-ui);font-weight:500">${esc(t)}</span></div>`).join("")}</div></section>` : ""}
  ${peers.length ? `<section class="blk"><div class="sh"><h2>Other ${esc(st.sec)} stocks</h2><p>F&amp;O stocks in the same sector, strongest first.</p></div>
    <div class="grid3">${peers.map(p => { const x = d.stocks[p]; return `<a class="sl" href="/stock/${encodeURIComponent(p)}"><b>${esc(p)}</b><span class="${cls(x.d)}">${pct(x.d)} · ${esc((Q[x.q] || [""])[0])}</span></a>`; }).join("")}</div></section>` : ""}
  <section class="blk"><div class="sh"><h2>How to read ${esc(s)} open interest</h2></div><div class="prose card">
    <p>Open interest (OI) is the number of ${esc(s)} futures contracts still open at the end of the day. Read together with price, it shows whether money is coming in or going out.</p>
    <p><b style="color:var(--up)">Long build-up</b>: price up and OI up. <b style="color:var(--down)">Short build-up</b>: price down and OI up. <b style="color:var(--info)">Short covering</b>: price up and OI down. <b style="color:var(--warn)">Long unwinding</b>: price down and OI down.</p>
    <p>A single day can mislead; a move that holds for several sessions, with OI rising steadily, says more. See today's full list on the <a href="/oi-buildup" style="color:var(--accent);font-weight:700">OI build-up page</a>.</p></div></section>
  ${cta(`See ${s} setups, hourly charts and alerts`)}`;
  return shell({ title, desc, path: `/stock/${s}`, ld: [crumbLd(items)], body });
}

// ------------------------------------------------------------------ all stocks A-Z
export function stocksPage(d) {
  const syms = Object.keys(d.stocks).sort();
  const letters = [...new Set(syms.map(s => /[A-Z]/.test(s[0]) ? s[0] : "#"))];
  const title = `NSE F&O stocks list (${syms.length}): OI analysis, trend and strength for each · TradeReady`;
  const desc = `All ${syms.length} NSE F&O stocks with futures open interest, OI build-up, timeframe trend and strength rank, updated after every session. Data as of ${day(d.oi_asof)}.`;
  const body = `${crumb([["/", "Market today"], ["/stocks", "F&O stocks"]])}
  <div class="kicker">${asOf(d)}</div>
  <h1 class="ph">NSE F&amp;O stocks: OI analysis for all ${syms.length}</h1>
  <p class="lede">Open any stock for its futures open interest chart, today's OI read (long build-up, short covering and more), trend on each timeframe and where it ranks on strength.</p>
  <div class="az">${letters.map(l => `<a href="#l-${l}">${l}</a>`).join("")}</div>
  ${letters.map(l => `<section class="blk" id="l-${l}" style="margin-top:22px"><div class="sh"><h2>${l}</h2></div><div class="grid3">${syms.filter(s => (/[A-Z]/.test(s[0]) ? s[0] : "#") === l).map(s => { const x = d.stocks[s];
    return `<a class="sl" href="/stock/${encodeURIComponent(s)}"><b>${esc(s)}</b><span title="${esc(x.n)}">${esc(x.n || x.sec)}</span></a>`; }).join("")}</div></section>`).join("")}
  ${cta()}`;
  return shell({ title, desc, path: "/stocks", ld: [crumbLd([["/", "Market today"], ["/stocks", "F&O stocks"]])], body });
}

// ------------------------------------------------------------------ OI build-up
export function oiPage(d) {
  const qs = d.quads;
  const title = `Long build-up, short build-up & short covering stocks today (NSE F&O, ${dayShort(d.oi_asof)}) · TradeReady`;
  const desc = `NSE F&O OI analysis for ${day(d.oi_asof)}: ${qs.long.length} stocks in long build-up, ${qs.short.length} in short build-up, ${qs.cover.length} short covering and ${qs.unwind.length} long unwinding. Full lists with OI and price change.`;
  const table = k => `<div class="oib" style="--c:${Q[k][1]}"><div class="oih"><b>${Q[k][0]}</b><span>${qs[k].length} stocks</span></div>
    <div class="muted sm" style="margin:-2px 0 8px">${esc(Q[k][2])}</div>
    ${qs[k].slice(0, 25).map(x => `<div class="oir"><a href="/stock/${encodeURIComponent(x.s)}">${esc(x.s)}</a><span class="muted">OI ${pct(x.oi)}</span><span class="${cls(x.px)}">${pct(x.px)}</span></div>`).join("") || `<div class="muted sm">None today</div>`}
    ${qs[k].length > 25 ? `<details style="border:0;padding:8px 0 0"><summary class="sm" style="font-size:13px">All ${qs[k].length}</summary>${qs[k].slice(25).map(x => `<div class="oir"><a href="/stock/${encodeURIComponent(x.s)}">${esc(x.s)}</a><span class="muted">OI ${pct(x.oi)}</span><span class="${cls(x.px)}">${pct(x.px)}</span></div>`).join("")}</details>` : ""}</div>`;
  const bld = d.building || [];
  const body = `${crumb([["/", "Market today"], ["/oi-buildup", "OI build-up"]])}
  <div class="kicker">${asOf(d)}</div>
  <h1 class="ph">OI build-up today: long build-up, short build-up and short covering</h1>
  <p class="lede">Every NSE F&amp;O stock placed in one of four boxes by how its futures price and open interest changed on ${esc(day(d.oi_asof))}. Sorted by the size of the OI change.</p>
  <div class="kpis">${Object.keys(Q).map(k => `<div class="kpi"><small>${Q[k][0]}</small><b style="color:${Q[k][1]}">${qs[k].length}</b><span>stocks</span></div>`).join("")}</div>
  <div class="oig">${Object.keys(Q).map(table).join("")}</div>
  ${bld.length ? `<section class="blk"><div class="sh"><h2>Positions building over several days</h2><p>OI rising on most of the last 7 sessions: big positions are being built. The price move shows which side, but not for certain.</p></div>
    <div class="card"><div class="tw"><table><thead><tr><th>Stock</th><th class="n">OI change</th><th class="n">Price change</th><th class="n">Days rising</th><th>Since</th></tr></thead><tbody>
    ${bld.map(x => `<tr><td>${sym(x.symbol)}</td><td class="n">${pct(x.oi)}</td><td class="n ${cls(x.px)}">${pct(x.px)}</td><td class="n">${x.days} of ${x.of}</td><td>${esc(dayShort(x.since))}</td></tr>`).join("")}</tbody></table></div></div></section>` : ""}
  <section class="blk"><div class="sh"><h2>What the four boxes mean</h2></div><div class="prose card">
    <p><b style="color:var(--up)">Long build-up</b>: price and open interest both rise. New buyers are opening positions.</p>
    <p><b style="color:var(--down)">Short build-up</b>: price falls while open interest rises. New sellers are opening positions.</p>
    <p><b style="color:var(--info)">Short covering</b>: price rises while open interest falls. Sellers are closing positions; the rise is not backed by fresh buying.</p>
    <p><b style="color:var(--warn)">Long unwinding</b>: price and open interest both fall. Buyers are closing positions.</p></div></section>
  ${cta("See unusual OI, short squeezes and setups for every F&O stock")}`;
  return shell({ title, desc, path: "/oi-buildup", ld: [crumbLd([["/", "Market today"], ["/oi-buildup", "OI build-up"]])], body });
}

// ------------------------------------------------------------------ F&O ban list
export function banPage(d) {
  const ban = d.ban || [], res = Object.entries(d.results || {}).sort((a, b) => a[1].localeCompare(b[1]));
  const gen = (d.generated_ist || "").slice(0, 10);
  const title = `F&O ban list today (${dayShort(gen) || "NSE"}): stocks in NSE F&O ban period · TradeReady`;
  const desc = ban.length ? `${ban.length} stock${ban.length > 1 ? "s" : ""} in the NSE F&O ban period: ${ban.join(", ")}. What the ban means, plus F&O results due this week.` : "No stock is in the NSE F&O ban period in the latest list. What the ban means, plus F&O results due this week.";
  const body = `${crumb([["/", "Market today"], ["/fo-ban-list", "F&O ban list"]])}
  <div class="kicker"><span class="live"><i></i>Updated ${esc(d.generated_ist || "")} IST</span></div>
  <h1 class="ph">F&amp;O ban list today</h1>
  <p class="lede">${ban.length ? `<b>${ban.length}</b> stock${ban.length > 1 ? "s are" : " is"} in the NSE F&amp;O ban period.` : "No stock is in the F&amp;O ban period right now."} The list comes from NSE and is checked through the day.</p>
  <div class="card" style="margin-top:14px">${ban.length ? `<div class="tw"><table><thead><tr><th>Stock</th><th>Sector</th><th class="n">Futures</th><th class="n">Day</th><th class="n">OI day</th><th>OI read</th></tr></thead><tbody>
    ${ban.map(s => { const x = d.stocks[s] || {}; return `<tr><td>${sym(s)}<div class="muted sm">${esc(x.n || "")}</div></td><td>${esc(x.sec || "")}</td><td class="n">₹${num(x.c)}</td><td class="n ${cls(x.d)}">${pct(x.d)}</td><td class="n">${pct(x.oid)}</td><td>${esc((Q[x.q] || ["—"])[0])}</td></tr>`; }).join("")}</tbody></table></div>`
    : `<div class="empty2">No stocks in the ban period.</div>`}</div>
  <section class="blk"><div class="sh"><h2>F&amp;O results due soon</h2><p>Quarterly results of F&amp;O stocks in the next 10 days.</p></div>
    <div class="grid3">${res.map(([s, dd]) => `<a class="sl" href="/stock/${encodeURIComponent(s)}"><b>${esc(s)}</b><span>${esc(day(dd))}</span></a>`).join("") || `<div class="empty2">None in the next 10 days.</div>`}</div></section>
  <section class="blk"><div class="sh"><h2>What is the F&amp;O ban period?</h2></div><div class="prose card">
    <p>NSE puts a stock in the F&amp;O ban period when open interest across all its futures and options crosses 95% of the market-wide position limit (MWPL).</p>
    <p>During the ban, traders can only reduce or close existing positions in that stock's derivatives; new positions are not allowed. The stock leaves the ban once open interest falls below 80% of the limit.</p>
    <p>Cash-market (delivery and intraday) trading in the stock carries on as usual.</p></div></section>
  ${cta()}`;
  return shell({ title, desc, path: "/fo-ban-list", ld: [crumbLd([["/", "Market today"], ["/fo-ban-list", "F&O ban list"]])], body });
}

// ------------------------------------------------------------------ sector rotation
export function sectorPage(d) {
  const secs = (d.sectors || []).slice().sort((a, b) => (b.r1 ?? -99) - (a.r1 ?? -99));
  const lead = secs.filter(s => s.stage === "Leading").map(s => s.name);
  const title = `Sector rotation today: NSE sectors vs Nifty (RRG) · ${lead.slice(0, 2).join(", ") || "TradeReady"} leading · TradeReady`;
  const desc = `Which NSE sectors lead and lag Nifty as of ${day(d.oi_asof)}: ${lead.length ? lead.join(", ") + " leading" : "no sector leading"}. Rotation chart, 1-week to 1-month relative strength and the F&O stocks in each sector.`;
  const body = `${crumb([["/", "Market today"], ["/sector-rotation", "Sector rotation"]])}
  <div class="kicker">${asOf(d)}</div>
  <h1 class="ph">Sector rotation: which sectors lead Nifty</h1>
  <p class="lede">Right of centre is stronger than Nifty; above centre is gaining strength. Sectors usually travel clockwise: Improving → Leading → Weakening → Lagging.</p>
  <div class="two" style="margin-top:14px"><div class="card">${rrg(secs)}</div>
    <div class="card">${["Leading", "Improving", "Weakening", "Lagging"].map(g => `<div class="stage" style="--c:${STAGE_C[g]}"><h3>${g}</h3><div class="chips">${secs.filter(x => x.stage === g).map(x => `<a class="chip" href="#s-${encodeURIComponent(x.name)}" style="--c:${STAGE_C[g]}">${esc(x.name)}<small>${pct(x.r1)}</small></a>`).join("") || `<span class="muted sm">None</span>`}</div></div>`).join("")}</div></div>
  <section class="blk"><div class="sh"><h2>All sectors vs Nifty</h2><p>Relative strength over 1 week, 2 weeks and 1 month, and the share of each sector's stocks bullish on the Daily chart.</p></div>
    <div class="card"><div class="tw"><table><thead><tr><th>Sector</th><th>Stage</th><th class="n">1W</th><th class="n">2W</th><th class="n">1M</th><th class="n">Daily bullish</th><th>F&amp;O stocks</th></tr></thead><tbody>
    ${secs.map(x => `<tr id="s-${encodeURIComponent(x.name)}"><td><b>${esc(x.name)}</b></td><td><span class="chip" style="--c:${STAGE_C[x.stage] || "var(--muted)"}">${esc(x.stage || "")}</span></td><td class="n ${cls(x.rw)}">${pct(x.rw)}</td><td class="n ${cls(x.r2w)}">${pct(x.r2w)}</td><td class="n ${cls(x.r1)}">${pct(x.r1)}</td><td class="n">${x.d_bull ?? "—"}%</td>
      <td style="min-width:220px">${(x.members || []).slice(0, 6).map(s => `<a href="/stock/${encodeURIComponent(s)}" class="tag" style="font-family:var(--f-num)">${esc(s)}</a>`).join("")}</td></tr>`).join("")}</tbody></table></div></div></section>
  ${cta()}`;
  return shell({ title, desc, path: "/sector-rotation", ld: [crumbLd([["/", "Market today"], ["/sector-rotation", "Sector rotation"]])], body });
}

// ------------------------------------------------------------------ strongest stocks
export function strongPage(d) {
  const rows = Object.entries(d.stocks).filter(([, x]) => x.rk).sort((a, b) => a[1].rk - b[1].rk).slice(0, 50);
  const title = `Strongest F&O stocks today: relative strength ranking (NSE) · TradeReady`;
  const desc = `Top 50 NSE F&O stocks ranked on relative strength vs Nifty, trend and momentum, as of ${day(d.oi_asof)}: ${rows.slice(0, 5).map(r => r[0]).join(", ")} lead.`;
  const body = `${crumb([["/", "Market today"], ["/strongest-stocks", "Strongest stocks"]])}
  <div class="kicker">${asOf(d)}</div>
  <h1 class="ph">Strongest F&amp;O stocks today</h1>
  <p class="lede">Every F&amp;O stock scored 0-100 on relative strength against Nifty, distance from its 52-week high, trend and momentum, and its sector. A list to study, not a buy list.</p>
  <div class="card" style="margin-top:14px"><div class="tw"><table><thead><tr><th class="n">#</th><th>Stock</th><th class="n">Score</th><th class="n">1M</th><th class="n">3M vs Nifty</th><th class="n">From 52W high</th><th>Why it ranks</th></tr></thead><tbody>
  ${rows.map(([s, x]) => `<tr><td class="n rk">${x.rk}</td><td>${sym(s)}<div class="muted sm">${esc(x.sec)}</div></td><td class="n"><span class="score">${x.sc}</span></td><td class="n ${cls(x.r1m)}">${pct(x.r1m)}</td><td class="n ${cls(x.r3m)}">${pct(x.r3m)}</td><td class="n">${pct(x.hi52)}</td>
    <td class="why">${(x.rs || []).slice(0, 3).map(r => `<span class="tag">${esc(r)}</span>`).join("")}</td></tr>`).join("")}</tbody></table></div></div>
  ${cta()}`;
  return shell({ title, desc, path: "/strongest-stocks", ld: [crumbLd([["/", "Market today"], ["/strongest-stocks", "Strongest stocks"]])], body });
}

// ------------------------------------------------------------------ sitemap
export function sitemap(d) {
  const lm = (d && d.oi_asof) || new Date().toISOString().slice(0, 10);
  const urls = [["/", "1.0"], ["/oi-buildup", "0.9"], ["/fo-ban-list", "0.9"], ["/sector-rotation", "0.8"], ["/strongest-stocks", "0.8"], ["/stocks", "0.8"]]
    .concat(Object.keys((d && d.stocks) || {}).sort().map(s => [`/stock/${encodeURIComponent(s)}`, "0.6"]))
    .concat([["/privacy", "0.2"], ["/terms", "0.2"]]);
  return `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls.map(([u, p]) =>
    `<url><loc>https://tradeready.in${u}</loc><lastmod>${lm}</lastmod><changefreq>daily</changefreq><priority>${p}</priority></url>`).join("\n")}\n</urlset>\n`;
}

// ------------------------------------------------------------------ routing, with a 10-minute shared cache per page
const ROUTES = { "/stocks": stocksPage, "/oi-buildup": oiPage, "/fo-ban-list": banPage, "/sector-rotation": sectorPage, "/strongest-stocks": strongPage };
export function isPublicPage(p) {
  return p === "/sitemap.xml" || ROUTES[p] || /^\/stock\/[A-Za-z0-9&%\-_.]{1,40}$/.test(p);
}
export async function servePublicPage(env, ctx, url) {
  const p = url.pathname.replace(/\/+$/, "") || "/";
  const key = new Request("https://tradeready.cache/pg-v1" + p);
  const hit = await caches.default.match(key);
  if (hit) return hit;
  const r = await fetch(`https://raw.githubusercontent.com/${env.REPO}/main/data/seo.json`, { cf: { cacheTtl: 300, cacheEverything: true } });
  const d = r.ok ? await r.json() : null;
  let html, type = "text/html; charset=utf-8", status = 200;
  if (p === "/sitemap.xml") { html = sitemap(d); type = "application/xml; charset=utf-8"; }
  else if (!d) return null;
  else if (ROUTES[p]) html = ROUTES[p](d);
  else {
    const s = decodeURIComponent(p.slice(7)).toUpperCase();
    if (!d.stocks[s]) return null;
    if (s !== decodeURIComponent(p.slice(7))) return Response.redirect(`${url.origin}/stock/${encodeURIComponent(s)}`, 301);
    html = stockPage(d, s);
  }
  const res = new Response(html, { status, headers: { "content-type": type, "cache-control": "public, max-age=600" } });
  ctx && ctx.waitUntil ? ctx.waitUntil(caches.default.put(key, res.clone())) : await caches.default.put(key, res.clone());
  return res;
}
