// Market headlines for the Today news card: pulled from public RSS feeds at most every 2 hours, kept in KV.
// Only the headline, source, time and link are shown; the link opens the publisher's own page.
const FEEDS = [
  ["Economic Times", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"],
  ["Economic Times", "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms"],
  ["Mint", "https://www.livemint.com/rss/markets"],
  ["Business Standard", "https://www.business-standard.com/rss/markets-106.rss"],
  ["BusinessLine", "https://www.thehindubusinessline.com/markets/feeder/default.rss"],
];
const KEY = "news:latest", EVERY = 2 * 3600e3;
const SKIP = /bitcoin|crypto|binance|wazirx|ethereum|horoscope|concert|cricket/i;

function txt(s) {
  return String(s || "").replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1").replace(/<[^>]+>/g, "")
    .replace(/&amp;/g, "&").replace(/&quot;/g, '"').replace(/&#39;|&#039;|&apos;|#39;/g, "'").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/\s+/g, " ").trim();
}
function parse(xml, src) {
  const out = [];
  for (const m of xml.matchAll(/<item\b[\s\S]*?<\/item>/g)) {
    const it = m[0], g = t => (it.match(new RegExp(`<${t}[^>]*>([\\s\\S]*?)</${t}>`)) || [])[1];
    const t = txt(g("title")), l = txt(g("link")) || txt(g("guid")), d = Date.parse(txt(g("pubDate")) || txt(g("dc:date")) || "");
    if (t && /^https?:\/\//.test(l) && !SKIP.test(t)) out.push({ t, l, src, at: isNaN(d) ? null : d });
  }
  return out;
}
async function pull() {
  const got = await Promise.all(FEEDS.map(([src, u]) => fetch(u, { headers: { "user-agent": "Mozilla/5.0 TradeReady news" }, cf: { cacheTtl: 600 } })
    .then(r => r.ok ? r.text() : "").then(x => parse(x, src)).catch(() => [])));
  const now = Date.now(), seen = new Set(), items = [];
  for (const it of got.flat().map(x => ({ ...x, at: x.at && x.at <= now + 6e5 ? x.at : now - 3600e3 })).sort((a, b) => b.at - a.at)) {
    const k = it.t.toLowerCase().replace(/[^a-z0-9]/g, "").slice(0, 48);
    if (seen.has(k) || now - it.at > 3 * 86400e3) continue;
    seen.add(k); items.push(it);
    if (items.length >= 20) break;
  }
  return { fetched: now, items };
}
// stale copy is returned at once; a fresh pull runs in the background when it is over 2 hours old
export async function getNews(env, ctx) {
  const cur = await env.USERS.get(KEY, "json");
  const refresh = async () => { const n = await pull(); if (n.items.length) await env.USERS.put(KEY, JSON.stringify(n)); return n; };
  if (!cur || !cur.items || !cur.items.length) return await refresh();
  if (Date.now() - cur.fetched > EVERY) { const p = refresh().catch(() => null); if (ctx) ctx.waitUntil(p); }
  return cur;
}
