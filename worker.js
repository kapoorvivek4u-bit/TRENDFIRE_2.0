// tradeready.in API. The GitHub key (GH_TOKEN) lives only here, as a Cloudflare secret,
// never in the website. Changes need the PIN (WL_PIN, also a Cloudflare secret).
//   GET  /api/watch          the watchlist, fresh from GitHub
//   POST /api/watch          {add:[..], remove:[..], note:{SYM:"text"}}   (needs PIN)
//   POST /api/scan           start every scan on GitHub                    (needs PIN)
//   GET  /api/scans          latest run of each scan, for the progress line
//   POST /api/pin            check a PIN
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
    if (code === 200 || code === 201) return json({ items: out });
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

async function api(req, env, url) {
  if (!env.GH_TOKEN) return json({ error: "GH_TOKEN is not set in the Cloudflare secrets." }, 503);
  const pinOk = env.WL_PIN && req.headers.get("x-pin") === env.WL_PIN;
  const p = url.pathname;
  if (p === "/api/watch" && req.method === "GET") {
    const [items, , code] = await readWatch(env);
    return code === 200 ? json({ items }) : json({ error: `GitHub code ${code}` }, 502);
  }
  if (p === "/api/scans" && req.method === "GET") return json(await latestRuns(env));
  if (req.method !== "POST") return json({ error: "Not found" }, 404);
  if (!pinOk) return json({ error: "Wrong PIN." }, 401);
  if (p === "/api/pin") return json({ ok: true });
  let body = {};
  try { body = await req.json(); } catch (e) {}
  if (p === "/api/watch") return changeWatch(env, body);
  if (p === "/api/scan") return scanAll(env);
  return json({ error: "Not found" }, 404);
}

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    if (url.pathname.startsWith("/api/")) {
      try { return await api(req, env, url); }
      catch (e) { return json({ error: "Server error: " + e.message }, 500); }
    }
    return env.ASSETS.fetch(req);
  }
};
