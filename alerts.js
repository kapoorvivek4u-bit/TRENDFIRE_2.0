// Alert watchlist: the admin's own 10-15 stocks, checked every 10 minutes in market hours.
// When a stock "wakes up" (price crosses a level, volume or OI jumps, a setup turns Ready,
// the trend flips) it sends one email (Cloudflare Email Routing) and one Telegram message.
// The list, settings and state live in KV only, never in the public repo.

const K_LIST = "alerts:list", K_STATE = "alerts:state", K_LOG = "alerts:log";
const IST = t => new Date((t || Date.now()) + 5.5 * 3600e3);
const istDay = t => IST(t).toISOString().slice(0, 10);
const istTime = t => IST(t).toISOString().slice(11, 16);
const fmt = x => x == null || isNaN(x) ? "—" : Number(x).toLocaleString("en-IN", { maximumFractionDigits: 2 });
const pct = x => x == null ? "—" : (x > 0 ? "+" : "") + Number(x).toFixed(1) + "%";
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// which events each strategy listens to
export const STRATS = {
  Swing:      { name: "Swing (3-5 days)",       ev: ["level", "entry", "vol", "oi", "orb", "sq", "setup", "dimp", "vk"] },
  Positional: { name: "Positional (2-4 weeks)", ev: ["level", "entry", "oi", "setup", "dimp", "wimp", "vk"] },
  Squeeze:    { name: "Squeeze",                ev: ["level", "entry", "vol", "sq", "setup", "vk"] },
  Breakout:   { name: "Breakout",               ev: ["level", "entry", "vol", "oi", "orb", "setup"] },
  Overspill:  { name: "Overspill (pullback)",   ev: ["level", "entry", "vol", "setup", "dimp"] },
};
const EV_ICON = { level: "🎯", entry: "🚀", vol: "📊", oi: "🏦", orb: "⚡", sq: "🌀", setup: "✅", dimp: "📈", wimp: "📈", vk: "🟢", warn: "⚠️" };

export async function alertsGet(env) {
  const v = (await env.USERS.get(K_LIST, "json")) || {};
  return { items: v.items || [], email: v.email || "", on: v.on !== false, at: v.at || null };
}
export async function alertsPut(env, b) {
  const old = await alertsGet(env);
  const items = (Array.isArray(b.items) ? b.items : old.items).slice(0, 40).map(x => ({
    s: String(x.s || "").toUpperCase().replace(/[^A-Z0-9&\-]/g, "").slice(0, 20),
    strat: STRATS[x.strat] ? x.strat : "Swing",
    level: +x.level > 0 ? +(+x.level).toFixed(2) : null,
    note: String(x.note || "").slice(0, 120) })).filter(x => x.s);
  const email = b.email !== undefined ? String(b.email || "").trim().toLowerCase().slice(0, 120) : old.email;
  const v = { items, email, on: b.on !== undefined ? !!b.on : old.on, at: new Date().toISOString() };
  await env.USERS.put(K_LIST, JSON.stringify(v));
  return v;
}
export async function alertsLog(env) { return (await env.USERS.get(K_LOG, "json")) || []; }

async function siteData(env) {
  try {
    const r = await fetch(`https://raw.githubusercontent.com/${env.REPO}/main/data/site.json`, { cf: { cacheTtl: 120, cacheEverything: true } });
    return r.ok ? await r.json() : null;
  } catch (e) { return null; }
}
async function tgChat(env) {
  if (env.TG_CHAT_ID) return env.TG_CHAT_ID;
  try {                                                   // the bot's owner, saved by tg_alerts.py
    const r = await fetch(`https://raw.githubusercontent.com/${env.REPO}/main/data/telegram.json`, { cf: { cacheTtl: 3600 } });
    return r.ok ? (await r.json()).owner || null : null;
  } catch (e) { return null; }
}
export async function channels(env) {
  return { mail: !!env.MAIL, tg: !!(env.TG_BOT_TOKEN && await tgChat(env)) };
}
async function sendTg(env, text) {
  const chat = env.TG_BOT_TOKEN ? await tgChat(env) : null;
  if (!chat) return "Telegram not set";
  const r = await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/sendMessage`, { method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ chat_id: chat, text, parse_mode: "HTML", disable_web_page_preview: true }) }).catch(e => ({ ok: false, status: e.message }));
  return r.ok ? "ok" : "Telegram failed " + r.status;
}
export async function sendMail(env, to, subject, html, text) {
  if (!env.MAIL) return "Email not set (MAIL binding missing)";
  if (!to) return "No email address saved";
  const from = env.ALERT_FROM || "alerts@tradeready.in";
  const b = "tr" + Math.random().toString(36).slice(2);
  const enc = s => btoa(unescape(encodeURIComponent(s))).replace(/.{76}/g, "$&\r\n");
  const raw = [
    `From: TradeReady Alerts <${from}>`, `To: <${to}>`,
    `Subject: =?UTF-8?B?${btoa(unescape(encodeURIComponent(subject)))}?=`,
    `Date: ${new Date().toUTCString()}`, `Message-ID: <${Date.now()}.${b}@tradeready.in>`,
    "MIME-Version: 1.0", `Content-Type: multipart/alternative; boundary="${b}"`, "",
    `--${b}`, "Content-Type: text/plain; charset=UTF-8", "Content-Transfer-Encoding: base64", "", enc(text),
    `--${b}`, "Content-Type: text/html; charset=UTF-8", "Content-Transfer-Encoding: base64", "", enc(html),
    `--${b}--`, ""].join("\r\n");
  try {
    const { EmailMessage } = await import("cloudflare:email");
    await env.MAIL.send(new EmailMessage(from, to, raw));
    return "ok";
  } catch (e) { return "Email failed: " + e.message; }
}

function tv(s) { return `https://in.tradingview.com/chart/?symbol=NSE:${encodeURIComponent(s)}`; }

// What the scan says about one stock (hourly), for the message and for flips.
function scanView(site, s) {
  const m = (site.mtf || []).find(r => r.s === s);
  const rd = (site.ready || []).find(r => r.symbol === s);
  return { M: m && m.M, W: m && m.W, D: m && m.D, vk: (site.vk || {})[s] || null, sec: m && m.sec,
    setup: rd ? { name: rd.setup, stage: rd.stage, grade: rd.grade, entry: rd.entry, stop: rd.stop, target: rd.target, rr: rd.rr } : null,
    top5: ((site.top5 || {}).picks || []).some(p => p.symbol === s),
    ban: ((site.heads_up || {}).ban || []).includes(s), res: ((site.heads_up || {}).results || {})[s] || null };
}

// Called with each 10-minute intraday result (d), and by the hourly tick with d = null.
export async function checkAlerts(env, d) {
  const cfg = await alertsGet(env);
  if (!cfg.on || !cfg.items.length) return { sent: 0 };
  const site = await siteData(env);
  const now = Date.now(), day = istDay(now);
  let st = (await env.USERS.get(K_STATE, "json")) || {};
  if (st.day !== day) st = { day, fired: {}, prev: st.prev || {}, px: st.px || {} };
  st.px = st.px || {};
  const watch = (d && d.watch) || {};
  const out = [];
  for (const it of cfg.items) {
    const s = it.s, w = watch[s], v = site ? scanView(site, s) : {};
    const allowed = new Set(STRATS[it.strat].ev);
    const fired = st.fired[s] = st.fired[s] || {};
    const prev = st.prev[s] || {};
    const ev = [];
    const add = (key, text) => { if (!allowed.has(key.split(":")[0]) || fired[key]) return; fired[key] = istTime(now); ev.push({ key, text }); };
    const c = w ? w[0] : null, chg = w ? w[1] : null;
    if (w) {
      const [ , , rvol, oi, vwap, orb, sq, hi, lo] = w;
      const last = st.px[s];                               // last price we saw (earlier tick or an earlier day)
      const crossed = L => (last != null ? last < L : lo != null && lo < L) && (c >= L || (hi != null && hi >= L));
      if (it.level && crossed(it.level)) add("level", `Crossed your level ₹${fmt(it.level)} (now ₹${fmt(c)}, ${pct(chg)})`);
      if (v.setup && v.setup.entry && (v.setup.stage === "Ready" || v.setup.stage === "Triggered") && crossed(v.setup.entry))
        add("entry", `Crossed the ${v.setup.name} entry ₹${fmt(v.setup.entry)} (now ₹${fmt(c)})`);
      st.px[s] = c;
      if (rvol != null && rvol >= 2 && chg > 0) add("vol", `Volume ${rvol.toFixed(1)}x the usual for this time, price ${pct(chg)}`);
      if (oi != null && oi >= 3 && chg > 0) add("oi", `Long build-up: futures OI ${pct(oi)} today with price ${pct(chg)}`);
      if (oi != null && oi >= 4 && chg < -0.5 && !fired["warn:oi"]) { fired["warn:oi"] = istTime(now); ev.push({ key: "warn:oi", text: `Careful: short build-up, OI ${pct(oi)} with price ${pct(chg)}` }); }
      if (orb === 1 && vwap) add("orb", `Broke above the opening range and holds above VWAP (now ₹${fmt(c)})`);
      if (sq === 1) add("sq", "15-min squeeze fired upward");
    }
    if (site) {
      if (v.setup && (v.setup.stage === "Ready" || v.setup.stage === "Triggered") && prev.setup !== v.setup.name + v.setup.stage)
        add("setup:" + v.setup.name + v.setup.stage, `${v.setup.name} is ${v.setup.stage} (grade ${v.setup.grade})`);
      if (v.D === "Impulse" && prev.D && prev.D !== "Impulse") add("dimp", `Daily flipped ${prev.D} → Impulse${v.M === "Impulse" && v.W === "Impulse" ? " with Monthly + Weekly Impulse" : ""}`);
      if (v.W === "Impulse" && prev.W && prev.W !== "Impulse") add("wimp", `Weekly flipped ${prev.W} → Impulse`);
      if (v.vk === "BUY NOW" && prev.vk && prev.vk !== "BUY NOW") add("vk", "VK turned Buy now");
      st.prev[s] = { D: v.D || prev.D, W: v.W || prev.W, vk: v.vk || prev.vk, setup: v.setup ? v.setup.name + v.setup.stage : null };
    }
    if (ev.length) { if (site) v.chase = chaseLine((site.levels || {})[s], c ?? (site.closes || {})[s]); out.push({ it, v, c, chg, ev }); }
  }
  await env.USERS.put(K_STATE, JSON.stringify(st), { expirationTtl: 60 * 60 * 24 * 30 });
  if (!out.length) return { sent: 0 };
  const res = await deliver(env, cfg, out, false);
  const log = await alertsLog(env);
  for (const o of out) for (const e of o.ev) log.unshift({ t: new Date(now).toISOString(), s: o.it.s, strat: o.it.strat, key: e.key, text: e.text });
  await env.USERS.put(K_LOG, JSON.stringify(log.slice(0, 150)));
  return { sent: out.length, ...res };
}

function chaseLine(lv, c) {
  if (!lv || !lv.e20 || !lv.a || c == null) return null;
  const x = (c - lv.e20) / lv.a, n = `${x >= 0 ? "+" : ""}${x.toFixed(1)} ATR from the 20 EMA ₹${fmt(lv.e20)}`;
  return x > 2.5 ? `🔴 Don't chase (${n}): wait for a pullback` : x > 1 ? `🟡 Wait for a dip (${n})`
    : x >= -1.5 ? `🟢 Good entry zone (${n})` : `⚪ Below the 20 EMA (${n}): wait for it to turn up`;
}
function lines(o) {
  const v = o.v || {}, bits = [];
  if (v.chase) bits.push(v.chase);
  if (v.setup) bits.push(`${v.setup.name} ${v.setup.stage} · grade ${v.setup.grade}` + (v.setup.entry ? ` · entry ₹${fmt(v.setup.entry)} · stop ₹${fmt(v.setup.stop)} · target ₹${fmt(v.setup.target)} · R:R ${v.setup.rr ?? "—"}` : ""));
  if (v.M || v.W || v.D) bits.push(`M ${v.M || "—"} · W ${v.W || "—"} · D ${v.D || "—"}${v.vk ? " · VK " + v.vk : ""}${v.sec ? " · " + v.sec : ""}`);
  if (v.top5) bits.push("In today's Top 5 positional");
  if (v.ban) bits.push("⚠️ In the F&O ban list");
  if (v.res) bits.push(`⚠️ Results on ${v.res}`);
  return bits;
}

async function deliver(env, cfg, out, test) {
  const names = out.map(o => o.it.s).join(", ");
  const subject = (test ? "[Test] " : "") + `TradeReady alert: ${names}`;
  const tg = [`<b>${test ? "[Test] " : ""}TradeReady alert · ${istTime()} IST</b>`];
  const txt = [subject, ""];
  let html = `<div style="font-family:Arial,sans-serif;max-width:640px;color:#1e1b4b"><h2 style="margin:0 0 4px">${esc(subject)}</h2><p style="color:#6b6894;margin:0 0 16px">${istDay()} · ${istTime()} IST</p>`;
  for (const o of out) {
    const head = `${o.it.s} · ${STRATS[o.it.strat].name}${o.c != null ? ` · ₹${fmt(o.c)} (${pct(o.chg)})` : ""}`;
    tg.push("", `<b>${esc(head)}</b>`, ...o.ev.map(e => `${EV_ICON[e.key.split(":")[0]] || "•"} ${esc(e.text)}`), ...lines(o).map(l => `<i>${esc(l)}</i>`), tv(o.it.s));
    txt.push(head, ...o.ev.map(e => "- " + e.text), ...lines(o), tv(o.it.s), "");
    html += `<div style="border:1px solid #e6e3f3;border-radius:12px;padding:14px 16px;margin:0 0 12px"><div style="font-size:17px;font-weight:700">${esc(head)}</div>
      <ul style="margin:8px 0;padding-left:18px">${o.ev.map(e => `<li style="margin:3px 0">${EV_ICON[e.key.split(":")[0]] || "•"} ${esc(e.text)}</li>`).join("")}</ul>
      ${lines(o).map(l => `<div style="color:#6b6894;font-size:13px">${esc(l)}</div>`).join("")}
      ${o.it.note ? `<div style="color:#6b6894;font-size:13px">Your note: ${esc(o.it.note)}</div>` : ""}
      <a href="${tv(o.it.s)}" style="display:inline-block;margin-top:10px;color:#4f46e5;font-weight:700">Open chart ›</a></div>`;
  }
  html += `<p style="color:#6b6894;font-size:12px">Check the chart before trading. Data is a few minutes late. Manage the list at <a href="https://tradeready.in/#admin-alerts">tradeready.in</a>.</p></div>`;
  const [mail, tgr] = await Promise.all([sendMail(env, cfg.email, subject, html, txt.join("\n")), sendTg(env, tg.join("\n"))]);
  return { mail, tg: tgr };
}

export async function testAlert(env) {
  const cfg = await alertsGet(env);
  const site = await siteData(env);
  const it = cfg.items[0] || { s: "NIFTY", strat: "Swing" };
  return deliver(env, cfg, [{ it, v: site ? scanView(site, it.s) : {}, c: null, chg: null, ev: [{ key: "level", text: "This is a test. Real alerts look like this." }] }], true);
}

// 20:00 IST: one summary email of the whole list.
export async function eveningDigest(env) {
  const cfg = await alertsGet(env);
  if (!cfg.on || !cfg.items.length) return;
  const site = await siteData(env);
  if (!site) return;
  const st = (await env.USERS.get(K_STATE, "json")) || {};
  const today = st.day === istDay() ? st.fired || {} : {};
  const rows = cfg.items.map(it => ({ it, v: scanView(site, it.s), fired: Object.keys(today[it.s] || {}) }));
  const closes = site.closes || {};
  const subject = `TradeReady evening: ${rows.filter(r => r.v.setup && r.v.setup.stage === "Ready").length} Ready on your list`;
  let html = `<div style="font-family:Arial,sans-serif;max-width:680px;color:#1e1b4b"><h2 style="margin:0 0 4px">Your alert watchlist · ${istDay()}</h2>
    <p style="color:#6b6894;margin:0 0 14px">Market weather ${(site.strength || {}).score ?? "—"} · ${(site.market || {}).turn || ""}</p>
    <table style="border-collapse:collapse;width:100%;font-size:13px"><tr style="background:#f4f3fb;text-align:left"><th style="padding:6px">Stock</th><th style="padding:6px">Strategy</th><th style="padding:6px">M / W / D</th><th style="padding:6px">Setup</th><th style="padding:6px">Today's alerts</th></tr>`;
  const txt = [subject, ""];
  for (const r of rows) {
    const v = r.v, set = v.setup ? `${v.setup.name} ${v.setup.stage} (${v.setup.grade})` : "—";
    html += `<tr style="border-top:1px solid #e6e3f3"><td style="padding:6px"><a href="${tv(r.it.s)}" style="color:#4f46e5;font-weight:700">${esc(r.it.s)}</a><br><span style="color:#6b6894">₹${fmt((closes[r.it.s]))}</span></td>
      <td style="padding:6px">${esc(r.it.strat)}${r.it.level ? `<br><span style="color:#6b6894">level ₹${fmt(r.it.level)}</span>` : ""}</td><td style="padding:6px">${esc([v.M, v.W, v.D].map(x => x || "—").join(" / "))}</td>
      <td style="padding:6px">${esc(set)}${v.top5 ? "<br>Top 5" : ""}${v.res ? `<br>⚠️ results ${esc(v.res)}` : ""}</td><td style="padding:6px">${r.fired.length ? esc(r.fired.join(", ")) : "—"}</td></tr>`;
    txt.push(`${r.it.s} (${r.it.strat}): ${[v.M, v.W, v.D].join("/")} · ${set}${r.fired.length ? " · alerts: " + r.fired.join(", ") : ""}`);
  }
  html += `</table></div>`;
  await sendMail(env, cfg.email, subject, html, txt.join("\n"));
}
