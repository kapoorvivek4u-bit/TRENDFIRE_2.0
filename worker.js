import { publicPage } from "./public.js";
import { isPublicPage, servePublicPage } from "./pages.js";
// tradeready.in server (Cloudflare Worker). It does four jobs:
//  1. Sign-in. Users sign in with Google (any Google account). The admin signs in at /admin
//     with a password. Nobody sees the site without signing in.
//  2. The login log: who signed in, when, how often, from which city and device (Cloudflare KV).
//  3. Watchlist changes and "Scan all" (admin only), using the GitHub key that lives only here.
//  4. Scan results (/data/*.json), fetched from GitHub once a minute and shared by everyone.
// Secrets (Cloudflare dashboard, never in this file): GH_TOKEN, GOOGLE_CLIENT_ID,
// GOOGLE_CLIENT_SECRET, ADMIN_PASSWORD, SESSION_SECRET.
const WATCH_PATH = "data/watchlist.json";
const SCANS = { "F&O setups": "scan.yml", "Equity": "equity.yml", "Unusual": "unusual.yml",
                "Sectors": "sector.yml", "Deals": "deals.yml", "Star investors": "stars.yml" };

const json = (body, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "content-type": "application/json", "cache-control": "no-store" } });

async function gh(env, method, path, body) {
  const r = await fetch(`https://api.github.com/repos/${env.REPO}${path}`, {
    method, body: body ? JSON.stringify(body) : undefined,
    headers: { authorization: `Bearer ${env.GH_TOKEN}`, accept: "application/vnd.github+json",
               "user-agent": "tradeready-worker", "x-github-api-version": "2022-11-28" } });
  let data = null;
  try { data = await r.json(); } catch (e) {}
  return [r.status, data];
}

const b64dec = s => new TextDecoder().decode(Uint8Array.from(atob(s.replace(/\n/g, "")), c => c.charCodeAt(0)));
const b64enc = s => { const b = new TextEncoder().encode(s); let t = ""; b.forEach(x => t += String.fromCharCode(x)); return btoa(t); };

const WATCH_KEY = "https://tradeready.cache/watchlist";   // one cached copy for every visitor
async function cachedWatch(env) {
  const cache = caches.default;
  const hit = await cache.match(WATCH_KEY);
  if (hit) return hit.json();
  const [items, , code] = await readWatch(env);
  if (code !== 200) return null;
  await cache.put(WATCH_KEY, new Response(JSON.stringify(items), { headers: { "cache-control": "max-age=60" } }));
  return items;
}
async function storeWatch(items) {
  await caches.default.put(WATCH_KEY, new Response(JSON.stringify(items), { headers: { "cache-control": "max-age=60" } }));
}

async function readWatch(env) {
  const [code, data] = await gh(env, "GET", `/contents/${WATCH_PATH}?ref=main`);
  if (code !== 200) return [[], null, code];
  try { return [JSON.parse(b64dec(data.content)), data.sha, 200]; }
  catch (e) { return [[], data.sha, 200]; }
}

const today = () => new Date(Date.now() + 5.5 * 3600e3).toISOString().slice(0, 10);
const clean = s => String(s || "").toUpperCase().replace(/[^A-Z0-9&\-_.]/g, "").slice(0, 30);

async function changeWatch(env, body) {
  const add = (body.add || []).map(clean).filter(Boolean);
  const remove = (body.remove || []).map(clean).filter(Boolean);
  const note = Object.fromEntries(Object.entries(body.note || {}).map(([k, v]) => [clean(k), String(v).slice(0, 1000).trim()]));
  for (let attempt = 0; attempt < 3; attempt++) {
    const [list, sha, rc] = await readWatch(env);
    if (rc !== 200 && rc !== 404) return json({ error: `Could not read the watchlist (GitHub code ${rc}).` }, 502);
    const items = Object.fromEntries(list.map(x => [x.symbol, x]));
    for (const s of add) items[s] = items[s] || { symbol: s, added: today() };
    for (const s of remove) delete items[s];
    for (const [s, t] of Object.entries(note)) { items[s] = items[s] || { symbol: s, added: today() }; items[s].note = t; }
    const out = Object.values(items).sort((a, b) => a.symbol.localeCompare(b.symbol));
    const msg = "Watchlist: " + [...add.map(s => "+" + s), ...remove.map(s => "-" + s), ...Object.keys(note).map(s => "note " + s)].join(", ");
    const [code] = await gh(env, "PUT", `/contents/${WATCH_PATH}`,
      { message: msg, branch: "main", content: b64enc(JSON.stringify(out, null, 1)), ...(sha ? { sha } : {}) });
    if (code === 200 || code === 201) { await storeWatch(out); return json({ items: out }); }
    if (code === 409 || code === 422) continue;          // changed meanwhile: read again
    return json({ error: { 401: "GitHub refused the key (expired or deleted).",
                           403: "The GitHub key needs Contents set to Read and write.",
                           404: "The GitHub key needs Contents set to Read and write." }[code]
                         || `Could not save (GitHub code ${code}).` }, 502);
  }
  return json({ error: "Could not save the watchlist; try again." }, 409);
}

async function scanAll(env) {
  const runs = await latestRuns(env);
  const started = [], problems = [];
  for (const [name, wf] of Object.entries(SCANS)) {
    if (runs[name] && runs[name].status !== "completed") { problems.push(`${name}: already running`); continue; }
    const [code] = await gh(env, "POST", `/actions/workflows/${wf}/dispatches`, { ref: "main" });
    if (code === 204) started.push(name);
    else problems.push(`${name}: ` + ({ 401: "GitHub refused the key.", 403: "the key needs Actions set to Read and write.",
                                        404: `.github/workflows/${wf} not found.` }[code] || `GitHub code ${code}`));
  }
  return json({ started, problems });
}

async function latestRuns(env) {
  const out = {};
  await Promise.all(Object.entries(SCANS).map(async ([name, wf]) => {
    const [code, data] = await gh(env, "GET", `/actions/workflows/${wf}/runs?per_page=1`);
    const r = code === 200 && data.workflow_runs && data.workflow_runs[0];
    if (r) out[name] = { status: r.status, conclusion: r.conclusion, started: r.run_started_at, updated: r.updated_at };
  }));
  return out;
}

// ------------------------------------------------------------------ sessions
const enc = new TextEncoder();
const b64url = buf => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const b64urlText = t => b64url(enc.encode(t));
const fromB64url = s => new TextDecoder().decode(Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/")), c => c.charCodeAt(0)));
async function hmac(env, text) {
  const key = await crypto.subtle.importKey("raw", enc.encode(env.SESSION_SECRET || ""), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return b64url(await crypto.subtle.sign("HMAC", key, enc.encode(text)));
}
async function sameText(env, a, b) { return (await hmac(env, "cmp:" + a)) === (await hmac(env, "cmp:" + b)); }
const SESSION_DAYS = 30;
async function makeSession(env, user) {
  const body = b64urlText(JSON.stringify({ ...user, x: Date.now() + SESSION_DAYS * 864e5 }));
  return `${body}.${await hmac(env, body)}`;
}
async function readSession(env, req) {
  const m = (req.headers.get("cookie") || "").match(/(?:^|;\s*)tr_s=([^;]+)/);
  if (!m || !env.SESSION_SECRET) return null;
  const [body, sig] = m[1].split(".");
  if (!body || !sig || (await hmac(env, body)) !== sig) return null;
  try { const u = JSON.parse(fromB64url(body)); return u.x > Date.now() ? u : null; } catch (e) { return null; }
}
const cookie = (name, value, maxAge) => `${name}=${value}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${maxAge}`;
const redirect = (to, cookies = []) => {
  const h = new Headers({ location: to, "cache-control": "no-store" });
  cookies.forEach(c => h.append("set-cookie", c));
  return new Response(null, { status: 302, headers: h });
};

// ------------------------------------------------------------------ login log (KV)
function device(req) {
  const ua = req.headers.get("user-agent") || "";
  const os = /iPhone|iPad/.test(ua) ? "iPhone" : /Android/.test(ua) ? "Android" : /Windows/.test(ua) ? "Windows"
           : /Mac OS/.test(ua) ? "Mac" : /Linux/.test(ua) ? "Linux" : "Other";
  const br = /Edg\//.test(ua) ? "Edge" : /Chrome\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : /Firefox\//.test(ua) ? "Firefox" : "";
  return br ? `${os} · ${br}` : os;
}
function place(req) {
  const c = req.cf || {};
  return [c.city, c.region, c.country].filter(Boolean).join(", ");
}
async function logLogin(env, req, user, how) {
  const now = Date.now(), at = new Date(now).toISOString();
  const row = { email: user.email, name: user.name || "", role: user.role, how, at, place: place(req), device: device(req) };
  console.log("LOGIN", JSON.stringify(row));
  if (!env.USERS) return;
  const key = "u:" + user.email.toLowerCase();
  const prev = (await env.USERS.get(key, "json")) || { first: at, logins: 0, visits: 0 };
  await env.USERS.put(key, JSON.stringify({ ...prev, email: user.email, name: user.name || prev.name || "",
    picture: user.picture || prev.picture || "", role: user.role, last: at, seen: at, logins: (prev.logins || 0) + 1,
    place: row.place, device: row.device }));
  // newest first when listed: the key counts down
  await env.USERS.put(`l:${String(9999999999999 - now).padStart(13, "0")}:${user.email.toLowerCase()}`, JSON.stringify(row),
                      { expirationTtl: 60 * 60 * 24 * 120 });
}
async function logVisit(env, req, user) {        // at most one write per user per hour
  if (!env.USERS) return;
  const key = "u:" + user.email.toLowerCase();
  const prev = await env.USERS.get(key, "json");
  if (!prev) return;
  if (prev.seen && Date.now() - Date.parse(prev.seen) < 3600e3) return;
  await env.USERS.put(key, JSON.stringify({ ...prev, seen: new Date().toISOString(), visits: (prev.visits || 0) + 1,
                                            place: place(req) || prev.place, device: device(req) }));
}
async function adminLog(env) {
  if (!env.USERS) return { error: "The login log needs the USERS storage (KV) connected." };
  const users = [], logins = [];
  let cur;
  do { const r = await env.USERS.list({ prefix: "u:", cursor: cur }); cur = r.list_complete ? null : r.cursor;
       for (const k of r.keys) { const v = await env.USERS.get(k.name, "json"); if (v) users.push(v); } } while (cur);
  const r = await env.USERS.list({ prefix: "l:", limit: 100 });
  for (const k of r.keys) { const v = await env.USERS.get(k.name, "json"); if (v) logins.push(v); }
  users.sort((a, b) => (b.seen || b.last || "").localeCompare(a.seen || a.last || ""));
  return { users, logins };
}

// ------------------------------------------------------------------ page views and online now (D1 database "DB")
// KV allows only 1,000 writes a day on the free plan, so page views go to D1 (100,000 writes a day free).
let DB_READY = false;
async function db(env) {
  if (!env.DB) return null;
  if (!DB_READY) {
    await env.DB.batch([
      env.DB.prepare("CREATE TABLE IF NOT EXISTS hits (ts INTEGER, day TEXT, email TEXT, page TEXT)"),
      env.DB.prepare("CREATE INDEX IF NOT EXISTS hits_day ON hits(day)"),
      env.DB.prepare("CREATE TABLE IF NOT EXISTS online (email TEXT PRIMARY KEY, name TEXT, picture TEXT, ts INTEGER, page TEXT, device TEXT)")]);
    DB_READY = true;
  }
  return env.DB;
}
const istDay = ms => new Date(ms + 5.5 * 3600e3).toISOString().slice(0, 10);
async function hit(env, req, user) {             // a page opened, or a 2-minute "still here" ping (no page)
  try {
    const d = await db(env);
    if (!d || user.role === "admin") return json({ ok: true });
    let b = {}; try { b = JSON.parse(await req.text()); } catch (e) {}
    const page = /^[a-z0-9-]{1,30}$/.test(b.p || "") ? b.p : null, now = Date.now(), email = user.email.toLowerCase();
    const st = [d.prepare("INSERT INTO online (email,name,picture,ts,page,device) VALUES (?1,?2,?3,?4,?5,?6) " +
                          "ON CONFLICT(email) DO UPDATE SET name=?2, picture=?3, ts=?4, page=COALESCE(?5,page), device=?6")
                 .bind(email, user.name || "", user.picture || "", now, page, device(req))];
    if (page) st.push(d.prepare("INSERT INTO hits (ts,day,email,page) VALUES (?1,?2,?3,?4)").bind(now, istDay(now), email, page));
    await d.batch(st);
  } catch (e) { console.log("hit failed", e.message); }
  return json({ ok: true });
}
async function adminStats(env) {
  const d = await db(env);
  if (!d) return { error: "no-db" };
  const now = Date.now(), since = istDay(now - 29 * 864e5), today = istDay(now);
  await d.prepare("DELETE FROM hits WHERE ts < ?").bind(now - 120 * 864e5).run();
  const [daily, pages, perUser, todayRows, online] = await d.batch([
    d.prepare("SELECT day, COUNT(DISTINCT email) AS users, COUNT(*) AS views FROM hits WHERE day >= ? GROUP BY day ORDER BY day").bind(since),
    d.prepare("SELECT page, COUNT(*) AS views, COUNT(DISTINCT email) AS users FROM hits WHERE day >= ? GROUP BY page ORDER BY views DESC").bind(since),
    d.prepare("SELECT email, page, COUNT(*) AS views FROM hits WHERE day >= ? GROUP BY email, page ORDER BY views DESC").bind(since),
    d.prepare("SELECT email, COUNT(*) AS views, MIN(ts) AS first, MAX(ts) AS last FROM hits WHERE day = ? GROUP BY email ORDER BY last DESC").bind(today),
    d.prepare("SELECT email, name, picture, ts, page, device FROM online WHERE ts > ? ORDER BY ts DESC").bind(now - 5 * 60e3)]);
  return { since, today, daily: daily.results, pages: pages.results, per_user: perUser.results, today_users: todayRows.results, online: online.results };
}


// ------------------------------------------------------------------ full access: trade levels only for the admin and a short list
// Everyone else (any Google account) sees the scanner without Buy above / Stop / Target, R:R, position size,
// buy/sell signal words or advice lines. Done here on the server so the numbers never reach their browser.
const FULL_KEY = "full:list";
let FULL_CACHE = { at: 0, list: [] };
async function fullList(env) {
  if (!env.USERS) return [];
  if (Date.now() - FULL_CACHE.at < 60e3) return FULL_CACHE.list;
  const list = (await env.USERS.get(FULL_KEY, "json")) || [];
  FULL_CACHE = { at: Date.now(), list };
  return list;
}
async function hasFull(env, user) {
  return user.role === "admin" || (await fullList(env)).includes(String(user.email || "").toLowerCase());
}
async function changeFull(env, body) {
  const list = new Set(await fullList(env));
  const email = String(body.email || "").trim().toLowerCase();
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) return { error: "Enter a full email address." };
  if (body.remove) list.delete(email); else list.add(email);
  const out = [...list].sort();
  await env.USERS.put(FULL_KEY, JSON.stringify(out));
  FULL_CACHE = { at: Date.now(), list: out };
  return { list: out };
}
const LEVEL_KEYS = new Set(["entry", "trigger", "stop", "target", "target_why", "target_word", "rr", "size", "advice", "ma", "hourly"]);
function stripLevels(x) {
  if (Array.isArray(x)) return x.map(stripLevels);
  if (x && typeof x === "object") {
    const o = {};
    for (const [k, v] of Object.entries(x)) {
      if (LEVEL_KEYS.has(k)) { if (k === "rr" && v) o.ok = 1; continue; }
      o[k] = stripLevels(v);
    }
    return o;
  }
  return x;
}
async function memberData(env, ctx, name, r) {
  const key = new Request(`https://tradeready.cache/member-v2/${name}`);
  const hit = await caches.default.match(key);
  const head = { "content-type": "application/json", "cache-control": "private, max-age=60" };
  if (hit) return new Response(hit.body, { headers: head });
  const txt = (await r.text()).replaceAll('"BUY NOW"', '"SIGNAL ON (Bullish)"').replaceAll('"SELL NOW"', '"SIGNAL ON (Bearish)"')
    .replaceAll("VK Buy now", "VK signal on").replaceAll("VK Sell now", "VK signal on");
  const d = stripLevels(JSON.parse(txt));
  if (name === "site.json") {
    delete d.scorecard;                                          // the scorecard is for the admin only
    delete d.top5;                                               // ranked picks with levels: full access only
    const gate = g => /size/i.test(g || "") ? "Open" : g;        // no position-size advice
    for (const st of d.strategies || []) { st.gate = gate(st.gate); delete st.exit; st.rules = (st.rules || []).filter(r => !/^Size:/i.test(r)).map(r => r.replace(/\s*\((full|half) size\)/gi, "")); }
    for (const g of Object.values(d.gates || {})) g.gate = gate(g.gate);
  }
  const body = JSON.stringify(d);
  const put = caches.default.put(key, new Response(body, { headers: { "content-type": "application/json", "cache-control": "public, max-age=60" } }));
  ctx && ctx.waitUntil ? ctx.waitUntil(put) : await put;
  return new Response(body, { headers: head });
}

// ------------------------------------------------------------------ Google and admin sign-in
function googleStart(env, url) {
  const state = crypto.randomUUID();
  const q = new URLSearchParams({ client_id: env.GOOGLE_CLIENT_ID, redirect_uri: `${url.origin}/auth/callback`,
    response_type: "code", scope: "openid email profile", state, prompt: "select_account" });
  return redirect("https://accounts.google.com/o/oauth2/v2/auth?" + q, [cookie("tr_st", state, 600)]);
}
async function googleCallback(env, req, url) {
  const st = (req.headers.get("cookie") || "").match(/(?:^|;\s*)tr_st=([^;]+)/);
  if (!st || st[1] !== url.searchParams.get("state") || !url.searchParams.get("code")) return redirect("/login?e=state");
  const r = await fetch("https://oauth2.googleapis.com/token", { method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ code: url.searchParams.get("code"), client_id: env.GOOGLE_CLIENT_ID,
      client_secret: env.GOOGLE_CLIENT_SECRET, redirect_uri: `${url.origin}/auth/callback`, grant_type: "authorization_code" }) });
  const t = await r.json().catch(() => ({}));
  if (!t.id_token) return redirect("/login?e=google");
  let p;
  try { p = JSON.parse(fromB64url(t.id_token.split(".")[1])); } catch (e) { return redirect("/login?e=google"); }
  if (p.aud !== env.GOOGLE_CLIENT_ID || !/accounts\.google\.com$/.test(p.iss || "") || !p.email || !p.email_verified)
    return redirect("/login?e=google");
  const user = { email: p.email, name: p.name || "", picture: p.picture || "", role: "user" };
  await logLogin(env, req, user, "Google");
  return redirect("/", [cookie("tr_s", await makeSession(env, user), SESSION_DAYS * 86400), cookie("tr_st", "", 0)]);
}
async function adminSignIn(env, req) {
  let body = {};
  try { body = await req.json(); } catch (e) {}
  if (!env.ADMIN_PASSWORD) return json({ error: "ADMIN_PASSWORD is not set in the Cloudflare secrets." }, 503);
  if (!body.password || !(await sameText(env, String(body.password), env.ADMIN_PASSWORD))) {
    await new Promise(r => setTimeout(r, 1200));                       // slows down guessing
    console.log("ADMIN LOGIN FAILED", JSON.stringify({ at: new Date().toISOString(), place: place(req), device: device(req) }));
    return json({ error: "Wrong password." }, 401);
  }
  const user = { email: "admin", name: "Admin", role: "admin" };
  await logLogin(env, req, user, "Admin password");
  const h = new Headers({ "content-type": "application/json", "cache-control": "no-store" });
  h.append("set-cookie", cookie("tr_s", await makeSession(env, user), SESSION_DAYS * 86400));
  return new Response(JSON.stringify({ ok: true }), { headers: h });
}


// ------------------------------------------------------------------ Kite Connect (admin)
// The admin taps "Connect Kite" each morning; Zerodha sends the request token back to
// /kite/callback, the token is swapped for today's access token and kept in KV.
// Scans (GitHub Actions) and the VM can fetch it from /api/kite/token with KITE_PULL_KEY.
const KITE_KEY = "kite:token";
async function sha256hex(text) {
  const d = await crypto.subtle.digest("SHA-256", enc.encode(text));
  return [...new Uint8Array(d)].map(b => b.toString(16).padStart(2, "0")).join("");
}
function next6amIST(ms) {                       // Kite tokens expire at 6:00 IST the next morning
  const ist = new Date(ms + 5.5 * 3600e3);
  const six = Date.UTC(ist.getUTCFullYear(), ist.getUTCMonth(), ist.getUTCDate(), 6, 0) - 5.5 * 3600e3;
  return six > ms ? six : six + 864e5;
}
async function kiteToken(env) {
  if (!env.USERS) return null;
  const t = await env.USERS.get(KITE_KEY, "json");
  return t && t.expires > Date.now() ? t : null;
}
function kiteLogin(env) {
  if (!env.KITE_API_KEY) return json({ error: "KITE_API_KEY is not set in the Cloudflare secrets." }, 503);
  return redirect(`https://kite.zerodha.com/connect/login?v=3&api_key=${encodeURIComponent(env.KITE_API_KEY)}`);
}
async function kiteCallback(env, url) {
  const rt = url.searchParams.get("request_token");
  if (url.searchParams.get("status") !== "success" || !rt) return redirect("/?kite=cancelled#admin");
  const body = new URLSearchParams({ api_key: env.KITE_API_KEY, request_token: rt,
    checksum: await sha256hex(env.KITE_API_KEY + rt + env.KITE_API_SECRET) });
  const r = await fetch("https://api.kite.trade/session/token", { method: "POST", body,
    headers: { "X-Kite-Version": "3", "content-type": "application/x-www-form-urlencoded" } });
  const d = await r.json().catch(() => ({}));
  if (!r.ok || !d.data || !d.data.access_token) {
    console.log("KITE LOGIN FAILED", r.status, d.message || "");
    return redirect("/?kite=failed#admin");
  }
  const now = Date.now();
  await env.USERS.put(KITE_KEY, JSON.stringify({ access_token: d.data.access_token, user_id: d.data.user_id,
    user_name: d.data.user_name || "", at: new Date(now).toISOString(), expires: next6amIST(now) }));
  console.log("KITE CONNECTED", d.data.user_id);
  return redirect("/?kite=ok#admin");
}
async function kiteStatus(env) {
  const t = await kiteToken(env);
  if (!t) return { connected: false, configured: !!(env.KITE_API_KEY && env.KITE_API_SECRET) };
  let live = null;
  try {
    const r = await fetch("https://api.kite.trade/user/profile", {
      headers: { "X-Kite-Version": "3", authorization: `token ${env.KITE_API_KEY}:${t.access_token}` } });
    live = r.ok;
  } catch (e) {}
  return { connected: live !== false, user_id: t.user_id, user_name: t.user_name, at: t.at,
           expires: new Date(t.expires).toISOString(), configured: true };
}
// morning reminder on Telegram when Kite is not connected (cron in wrangler.jsonc)
async function kiteReminder(env) {
  if (!env.TG_BOT_TOKEN || !env.TG_CHAT_ID) return;
  const ist = new Date(Date.now() + 5.5 * 3600e3);
  if (ist.getUTCDay() === 0 || ist.getUTCDay() === 6) return;
  if (await kiteToken(env)) return;
  await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/sendMessage`, { method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ chat_id: env.TG_CHAT_ID, text: "Good morning. Kite is not connected yet today.\nTap to connect: https://tradeready.in/kite/login" }) });
}

// ------------------------------------------------------------------ scan timetable
// GitHub's own cron runs hours late or not at all at busy times, so the site starts the scans itself.
// Cloudflare calls scheduled() every 15 minutes; times below are IST, weekdays only.
const IST_DAY = t => new Date(t + 5.5 * 3600e3);
const at = (h, m) => h * 60 + m;
const TIMETABLE = {
  "intraday.yml": m => m >= at(9, 15) && m <= at(15, 30),
  "scan.yml":     m => [at(10, 15), at(11, 15), at(12, 15), at(13, 15), at(14, 15), at(15, 15), at(16, 15), at(19, 30)].includes(m),
  "unusual.yml":  m => [at(10, 30), at(11, 30), at(12, 30), at(13, 30), at(14, 30), at(15, 30), at(16, 30), at(19, 45)].includes(m),
  "sector.yml":   m => m === at(16, 45),
  "deals.yml":    m => m === at(19, 0),
  "equity.yml":   m => m === at(19, 45),
};
async function runSchedule(env, when) {
  const d = IST_DAY(when || Date.now());
  const wd = d.getUTCDay(), m = Math.round((d.getUTCHours() * 60 + d.getUTCMinutes()) / 15) * 15;
  const jobs = [];
  if (wd !== 0 && wd !== 6) {
    if (m === at(8, 45)) jobs.push(kiteReminder(env));
    if (env.GH_TOKEN) for (const [wf, due] of Object.entries(TIMETABLE)) {
      if (!due(m)) continue;
      jobs.push(gh(env, "POST", `/actions/workflows/${wf}/dispatches`, { ref: "main" })
        .then(([code]) => console.log("dispatch", wf, code)).catch(e => console.log("dispatch failed", wf, e.message)));
    }
  }
  await Promise.all(jobs);
}

// pages and files anyone may open without signing in
const OPEN = new Set(["/login", "/login.html", "/admin", "/admin.html", "/favicon.svg", "/apple-touch-icon.png",
  "/icon-192.png", "/icon-512.png", "/manifest.webmanifest", "/robots.txt",
  "/privacy", "/privacy.html", "/terms", "/terms.html", "/sitemap.xml", "/og-image.png", "/sw.js"]);

async function api(req, env, url, user) {
  const p = url.pathname, admin = user.role === "admin";
  if (p === "/api/me") { await logVisit(env, req, user); return json({ email: user.email, name: user.name, picture: user.picture || "", role: user.role, full: await hasFull(env, user) }); }
  if (p === "/api/live" && req.method === "GET") {           // today's prices for the dashboard (no levels)
    const v = env.USERS ? await env.USERS.get("live:latest") : null;
    return new Response(v || "{}", { headers: { "content-type": "application/json", "cache-control": "private, max-age=60" } });
  }
  if (p === "/api/intraday" && req.method === "GET") {
    if (!admin) return json({ error: "Admin only." }, 403);
    const latest = await env.USERS.get("intraday:latest", "json");
    const past = [];                                     // recent BTST lists, newest first
    const l = await env.USERS.list({ prefix: "btst:" });
    for (const k of l.keys.map(k => k.name).sort().reverse().slice(0, 8)) { const v = await env.USERS.get(k, "json"); if (v) past.push(v); }
    return new Response(JSON.stringify({ latest, past }), { headers: { "content-type": "application/json", "cache-control": "no-store" } });
  }
  if (p === "/api/admin/full") {
    if (!admin) return json({ error: "Admin only." }, 403);
    if (req.method === "GET") return json({ list: await fullList(env) });
    if (req.method === "POST") { const r = await changeFull(env, await req.json().catch(() => ({}))); return json(r, r.error ? 400 : 200); }
  }
  if (p === "/api/hit" && req.method === "POST") return await hit(env, req, user);
  if (p === "/api/admin/stats" && req.method === "GET") return admin ? json(await adminStats(env)) : json({ error: "Admin only." }, 403);
  if (p === "/api/kite/status" && req.method === "GET") return admin ? json(await kiteStatus(env)) : json({ error: "Admin only." }, 403);
  if (!env.GH_TOKEN) return json({ error: "GH_TOKEN is not set in the Cloudflare secrets." }, 503);
  if (p === "/api/watch" && req.method === "GET") {
    if (!admin) return json({ items: [] });
    const items = await cachedWatch(env);
    return items ? json({ items }) : json({ error: "Could not read the watchlist from GitHub." }, 502);
  }
  if (p === "/api/scans" && req.method === "GET") return json(admin ? await latestRuns(env) : {});
  if (p === "/api/admin/log" && req.method === "GET") return admin ? json(await adminLog(env)) : json({ error: "Admin only." }, 403);
  if (req.method !== "POST") return json({ error: "Not found" }, 404);
  if (!admin) return json({ error: "Only the admin can change this." }, 403);
  let body = {};
  try { body = await req.json(); } catch (e) {}
  if (p === "/api/watch") return changeWatch(env, body);
  if (p === "/api/scan") return scanAll(env);
  return json({ error: "Not found" }, 404);
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(runSchedule(env, event.scheduledTime));
  },
  async fetch(req, env, ctx) {
    const url = new URL(req.url), p = url.pathname;
    try {
      if (p === "/auth/google") return googleStart(env, url);
      if (p === "/auth/callback") return await googleCallback(env, req, url);
      if (p === "/auth/admin" && req.method === "POST") return await adminSignIn(env, req);
      if (p === "/logout") return redirect("/login", [cookie("tr_s", "", 0)]);
      if (p === "/api/ingest/intraday" && req.method === "POST") {   // the intraday scan sends its result here
        const want = env.INGEST_KEY || (env.SESSION_SECRET ? await hmac(env, "ingest:intraday") : "");
        const got = req.headers.get("x-ingest-key") || "";
        if (!want || !(await sameText(env, got, want))) return json({ error: "Not allowed." }, 403);
        if (!env.USERS) return json({ error: "USERS storage missing." }, 500);
        const body = await req.text();
        let d; try { d = JSON.parse(body); } catch (e) { return json({ error: "Bad JSON." }, 400); }
        await env.USERS.put("intraday:latest", body, { expirationTtl: 60 * 60 * 24 * 5 });
        if (d.live) await env.USERS.put("live:latest", JSON.stringify({ at: d.at, session: d.session, rows: d.live,
          index: Object.fromEntries(Object.entries(d.index || {}).map(([k, x]) => [k, { c: x.c, chg: x.chg, ...((d.ixlive || {})[k] || {}) }])) }), { expirationTtl: 60 * 60 * 24 * 5 });
        if (d.final && d.session) {                      // keep the day's last BTST / STBT list to check next morning
          await env.USERS.put("btst:" + d.session, JSON.stringify({ session: d.session, at: d.at,
            btst: (d.btst || []).map(x => ({ s: x.s, c: x.c, score: x.bl })), stbt: (d.stbt || []).map(x => ({ s: x.s, c: x.c, score: x.bs })) }),
            { expirationTtl: 60 * 60 * 24 * 40 });
        }
        return json({ ok: true, bytes: body.length });
      }
      if (p === "/api/kite/token") {                     // for scans and the VM, with their own key
        if (!env.KITE_PULL_KEY || req.headers.get("x-kite-key") !== env.KITE_PULL_KEY) return json({ error: "Not allowed." }, 403);
        const t = await kiteToken(env);
        return t ? json({ api_key: env.KITE_API_KEY, access_token: t.access_token, expires: new Date(t.expires).toISOString() })
                 : json({ error: "Kite is not connected today." }, 404);
      }
      if (url.hostname.startsWith("www.")) {             // one address for Google: tradeready.in
        url.hostname = url.hostname.slice(4);
        return Response.redirect(url.toString(), 301);
      }
      if (req.method === "GET" && isPublicPage(p.replace(/\/+$/, "") || "/")) {   // public pages for Google, same for everyone
        try { const pg = await servePublicPage(env, ctx, url); if (pg) return pg; } catch (e) { console.log("public page failed", p, e.message); }
        if (p !== "/sitemap.xml") return new Response("Not found", { status: 404 });
      }
      const user = await readSession(env, req);
      if (!user && p === "/") {                          // signed-out visitors and Google see today's public market page
        try { const pub = await publicPage(env, ctx); if (pub) return pub; } catch (e) { console.log("public page failed", e.message); }
        const res = await env.ASSETS.fetch(new Request(new URL("/login", url), req));
        const out = new Response(res.body, res);
        out.headers.set("cache-control", "no-cache");
        return out;
      }
      if (OPEN.has(p)) {
        if (user && (p === "/login" || p === "/login.html")) return redirect("/");
        return env.ASSETS.fetch(req);          // /login serves login.html, /admin serves admin.html
      }
      if (!user) {
        if (p.startsWith("/api/") || p.startsWith("/data/")) return json({ error: "Please sign in." }, 401);
        return redirect("/login");
      }
      if (p === "/kite/login" || p === "/kite/callback") {
        if (user.role !== "admin") return redirect("/admin");
        return p === "/kite/login" ? kiteLogin(env) : await kiteCallback(env, url);
      }
      const m = p.match(/^\/data\/([A-Za-z0-9_.\-]+\.json)$/);
      if (m) {
        if (m[1] === "watchlist.json" && user.role !== "admin") return json([]);
        const r = await fetch(`https://raw.githubusercontent.com/${env.REPO}/main/data/${m[1]}`,
                              { cf: { cacheTtl: 60, cacheEverything: true } });
        if (r.ok && !(await hasFull(env, user))) return await memberData(env, ctx, m[1], r);   // no trade levels
        if (m[1] === "site.json" && user.role !== "admin" && r.ok) {      // the scorecard is for the admin only
          const d = await r.json(); delete d.scorecard;
          return new Response(JSON.stringify(d), { headers: { "content-type": "application/json", "cache-control": "private, max-age=60" } });
        }
        return new Response(r.body, { status: r.status, headers: {
          "content-type": r.ok ? "application/json" : "text/plain", "cache-control": "private, max-age=60" } });
      }
      if (p.startsWith("/api/")) return await api(req, env, url, user);
      const res = await env.ASSETS.fetch(req);
      const out = new Response(res.body, res);
      out.headers.set("cache-control", "private, no-cache");
      return out;
    } catch (e) {
      return json({ error: "Server error: " + e.message }, 500);
    }
  }
};
