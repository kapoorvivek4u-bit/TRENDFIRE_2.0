// Option chain for any F&O stock, on demand.
// 1) a recent copy in D1 (fetched earlier by anyone), 2) straight from NSE, 3) if NSE refuses the
// site's server, a GitHub Actions run (chain.yml) fetches it and sends it back (about a minute).
// Stored in D1, not KV: KV allows only 1,000 writes a day on the free plan.

const IST = t => new Date((t || Date.now()) + 5.5 * 3600e3);
let READY = false;
async function cdb(env) {
  if (!env.DB) return null;
  if (!READY) {
    await env.DB.batch([
      env.DB.prepare("CREATE TABLE IF NOT EXISTS chains (s TEXT, exp TEXT, at INTEGER, body TEXT, PRIMARY KEY (s, exp))"),
      env.DB.prepare("CREATE TABLE IF NOT EXISTS chain_pending (s TEXT PRIMARY KEY, at INTEGER)")]);
    READY = true;
  }
  return env.DB;
}

// how old a copy may be: 10 minutes in market hours, otherwise anything taken after the last close
function freshEnough(at, now = Date.now()) {
  const d = IST(now), wd = d.getUTCDay(), m = d.getUTCHours() * 60 + d.getUTCMinutes();
  if (wd >= 1 && wd <= 5 && m >= 555 && m <= 935) return now - at < 10 * 60e3;
  const c = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate(), 15, 30));   // IST clock
  if (wd === 0 || wd === 6 || m < 930) c.setUTCDate(c.getUTCDate() - 1);
  while (c.getUTCDay() === 0 || c.getUTCDay() === 6) c.setUTCDate(c.getUTCDate() - 1);
  return at >= c.getTime() - 5.5 * 3600e3;
}

const side = o => o ? { oi: o.openInterest || 0, chg: o.changeinOpenInterest || 0, ltp: o.lastPrice || 0,
  lc: Math.round((o.change || 0) * 100) / 100, iv: o.impliedVolatility || 0, vol: o.totalTradedVolume || 0 } : null;

function shape(symbol, exp, exps, data, band = 12) {
  let spot = null; const rows = {};
  for (const d of data) {
    const k = d.strikePrice; if (k == null) continue;
    for (const s of ["CE", "PE"]) if (d[s] && !spot) spot = d[s].underlyingValue;
    rows[k] = { k, ce: side(d.CE), pe: side(d.PE) };
  }
  const ks = Object.keys(rows).map(Number).sort((a, b) => a - b);
  if (!ks.length || !spot) return null;
  const atm = ks.reduce((a, k) => Math.abs(k - spot) < Math.abs(a - spot) ? k : a, ks[0]);
  const i = ks.indexOf(atm);
  const tot = (f, sd) => ks.reduce((t, k) => t + ((rows[k][sd] || {})[f] || 0), 0);
  return { s: symbol, spot, expiry: exp, expiries: exps.slice(0, 4), atm, at: IST().toISOString().slice(0, 16).replace("T", " "),
    tot: { ce_oi: tot("oi", "ce"), pe_oi: tot("oi", "pe"), ce_chg: tot("chg", "ce"), pe_chg: tot("chg", "pe") },
    all: ks.map(k => [k, (rows[k].ce || {}).oi || 0, (rows[k].pe || {}).oi || 0]),
    rows: ks.slice(Math.max(0, i - band), i + band + 1).map(k => rows[k]) };
}

async function nseDirect(symbol, expiry) {
  const ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36";
  const t = ms => AbortSignal.timeout(ms);
  const home = await fetch("https://www.nseindia.com/option-chain", { headers: { "user-agent": ua, accept: "text/html" }, signal: t(5000) });
  const cookie = (home.headers.getSetCookie ? home.headers.getSetCookie() : [home.headers.get("set-cookie") || ""])
    .map(c => c.split(";")[0]).filter(Boolean).join("; ");
  const h = { "user-agent": ua, accept: "application/json, text/plain, */*", referer: "https://www.nseindia.com/option-chain", cookie };
  const q = encodeURIComponent(symbol);
  const info = await (await fetch(`https://www.nseindia.com/api/option-chain-contract-info?symbol=${q}`, { headers: h, signal: t(5000) })).json();
  const exps = info.expiryDates || [];
  if (!exps.length) return null;
  const exp = exps.includes(expiry) ? expiry : exps[0];
  const kind = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"].includes(symbol) ? "Indices" : "Equity";
  const j = await (await fetch(`https://www.nseindia.com/api/option-chain-v3?type=${kind}&symbol=${q}&expiry=${exp}`, { headers: h, signal: t(6000) })).json();
  return shape(symbol, exp, exps, (j.records || {}).data || []);
}

async function save(d, c) {
  await d.prepare("INSERT OR REPLACE INTO chains (s, exp, at, body) VALUES (?, ?, ?, ?)").bind(c.s, c.expiry || "", Date.now(), JSON.stringify(c)).run();
}

export async function ingestChains(env, body) {
  const d = await cdb(env);
  if (!d) return { error: "DB missing" };
  let n = 0;
  for (const c of (body.chains || []).slice(0, 60)) {
    if (!c || !/^[A-Z0-9&\-]{1,20}$/.test(c.s || "")) continue;
    await save(d, c); n++;
    await d.prepare("DELETE FROM chain_pending WHERE s = ?").bind(c.s).run();
  }
  return { ok: true, saved: n };
}

export async function getChain(env, sym, exp, gh) {
  const s = String(sym || "").toUpperCase();
  if (!/^[A-Z0-9&\-]{1,20}$/.test(s)) return { error: "Bad symbol." };
  if (exp && !/^\d{2}-[A-Za-z]{3}-\d{4}$/.test(exp)) exp = "";
  const d = await cdb(env);
  if (!d) return { error: "The chain store (D1) is not connected." };
  const row = exp ? await d.prepare("SELECT at, body FROM chains WHERE s = ? AND exp = ?").bind(s, exp).first()
                  : await d.prepare("SELECT at, body FROM chains WHERE s = ? ORDER BY at DESC LIMIT 1").bind(s).first();
  const have = row ? JSON.parse(row.body) : null;
  if (have && freshEnough(row.at) && (!exp || have.expiry === exp)) return { chain: have, source: "saved" };
  try {                                                     // NSE straight from the site's server
    const c = await nseDirect(s, exp);
    if (c) { await save(d, c); return { chain: c, source: "nse" }; }
  } catch (e) { /* NSE often refuses data-centre servers: fall back to GitHub */ }
  const p = await d.prepare("SELECT at FROM chain_pending WHERE s = ?").bind(s).first();
  if (!p || Date.now() - p.at > 150e3) {
    const busy = await d.prepare("SELECT COUNT(*) AS n FROM chain_pending WHERE at > ?").bind(Date.now() - 3600e3).first();
    if (busy && busy.n >= 60) return { error: "Too many chain requests this hour. Try again later.", chain: have };
    if (!env.GH_TOKEN) return { error: "GH_TOKEN is not set.", chain: have };
    const [code] = await gh(env, "POST", "/actions/workflows/chain.yml/dispatches", { ref: "main", inputs: { symbol: s, expiry: exp || "" } });
    if (code >= 300) return { error: `Could not start the chain fetch (GitHub ${code}).`, chain: have };
    await d.prepare("INSERT OR REPLACE INTO chain_pending (s, at) VALUES (?, ?)").bind(s, Date.now()).run();
  }
  return { pending: true, chain: have, stale: !!have };
}
