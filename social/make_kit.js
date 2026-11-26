// Daily social kit: a 21 s vertical money-flow video, a cover, a 4:5 picture post and ready captions.
// Runs in GitHub Actions (social.yml) after the close; sends the kit to the site (Admin -> Social kit).
//   node social/make_kit.js             make today's kit and send it
//   node social/make_kit.js --dry       make it, keep the files in social/out, send nothing
//   node social/make_kit.js --file x.json --dry   use a saved /api/flow reply instead of the site
const fs = require("fs"), path = require("path"), { spawn } = require("child_process");
const { chromium } = require("playwright");
const SITE = process.env.SITE || "https://tradeready.in", KEY = process.env.INGEST_KEY || "";
const args = process.argv.slice(2), DRY = args.includes("--dry") || !!process.env.DRY_RUN, FORCE = args.includes("--force");
const FILE = args.includes("--file") ? args[args.indexOf("--file") + 1] : null;
const OUT = path.join(__dirname, "out");

const istNow = () => new Date(Date.now() + 5.5 * 3600e3);
const pct = x => (x > 0 ? "+" : "") + x.toFixed(2) + "%";

async function source() {
  if (FILE) return JSON.parse(fs.readFileSync(FILE, "utf8")).live || JSON.parse(fs.readFileSync(FILE, "utf8"));
  const r = await fetch(`${SITE}/api/social/source`, { headers: { "x-ingest-key": KEY } });
  if (!r.ok) throw new Error("source " + r.status + " " + (await r.text()).slice(0, 200));
  return (await r.json()).live;
}

// Same reading as the homepage: each sector's move against the whole F&O market, weighted by traded value.
function sectors(live) {
  const rows = (live.rows || []).filter(r => r[2] != null && r[3] && r[3] !== "Unclassified" && r[3] !== "Others");
  const val = r => (r[6] && r[6] > 0 ? r[6] : 1);
  const all = rows.reduce((a, r) => (a.v += val(r), a.s += r[2] * val(r), a), { v: 0, s: 0 }), mkt = all.v ? all.s / all.v : 0;
  const by = {}; for (const r of rows) (by[r[3]] = by[r[3]] || []).push(r);
  const nodes = Object.entries(by).filter(([, l]) => l.length >= 2).map(([k, l]) => {
    const v = l.reduce((a, r) => a + val(r), 0), chg = l.reduce((a, r) => a + r[2] * val(r), 0) / v;
    const rel = chg - mkt, best = l.slice().sort((a, b) => Math.sign(rel || 1) * ((b[2] - chg) * val(b) - (a[2] - chg) * val(a)))[0];
    return { id: k, name: k, chg: +chg.toFixed(2), rel: +rel.toFixed(2), size: +v.toFixed(1), lead: best ? best[0] : "" };
  }).sort((a, b) => b.rel - a.rel);
  return { mkt: +mkt.toFixed(2), nodes, rows: rows.length };
}

const TAGS = { "IT": "#itstocks #niftyit", "PSU Banks": "#psubanks #psubankstocks", "Banks": "#bankstocks #banknifty", "Pharma": "#pharmastocks #niftypharma",
  "Healthcare": "#healthcarestocks", "FMCG": "#fmcgstocks #niftyfmcg", "Auto": "#autostocks #niftyauto", "Metals & Mining": "#metalstocks #niftymetal",
  "Realty": "#realtystocks #niftyrealty", "Oil & Gas": "#oilandgas #energystocks", "Power": "#powerstocks", "Capital Goods": "#capitalgoods #defencestocks",
  "Finance": "#financestocks #finnifty", "Chemicals": "#chemicalstocks", "Consumer Durables": "#consumerdurables", "Telecom": "#telecomstocks",
  "Construction": "#infrastocks", "Construction Materials": "#cementstocks", "Consumer Services": "#consumerstocks", "Services": "#servicestocks", "Textiles": "#textilestocks" };
const tagOf = n => TAGS[n] || "#" + n.toLowerCase().replace(/[^a-z0-9]/g, "");
const HOOKS = ["Where did the money go today?", "Follow the money 💸", "Today's sector money flow"];

function captions(d) {
  const ins = d.nodes.filter(n => n.rel > .15), outs = d.nodes.filter(n => n.rel < -.15).reverse(), TP = ins[0], BT = outs[0];
  const calm = !TP || !BT, date = d.dateLabel;
  const head = calm ? "A calm day: no sector stood out" : `Money left ${BT.name} and went into ${TP.name}`;
  const inL = ins.slice(0, 3).map(n => `${n.name} ${pct(n.rel)}`).join(", ") || "none standing out";
  const outL = outs.slice(0, 3).map(n => `${n.name} ${pct(n.rel)}`).join(", ") || "none standing out";
  const dyn = calm ? "" : [tagOf(BT.name), tagOf(TP.name), TP.lead ? "#" + TP.lead.toLowerCase().replace(/[^a-z0-9]/g, "") : ""].join(" ").trim();
  const base = "#stockmarketindia #nifty50 #banknifty #nse #fno #optionstrading #swingtrading #sharemarket #stockmarket #trading #moneyflow #sectorrotation #marketupdate";
  return {
    headline: head,
    instagram: `${calm ? "😴" : "📉➡️📈"} ${head} (${date})\n\nF&O market: ${pct(d.mkt)}\n🟢 Money in: ${inL}\n🔴 Money out: ${outL}\n\n` +
      `% = each sector compared with the whole F&O market, weighted by traded value.\nLive money flow, free 👉 tradeready.in (link in bio)\n` +
      `A study of market data, not investment advice.\n\n${base} ${dyn}`.trim(),
    youtube_title: (calm ? `Calm day in the market | NSE Money Flow ${date} #shorts` : `Where did the money go today? ${BT.name} ➡️ ${TP.name} | NSE Money Flow ${date} #shorts`).slice(0, 100),
    youtube_desc: `Sector money flow for NSE F&O stocks, ${date}. Red = money leaving, green = money arriving.\nMoney in: ${inL}\nMoney out: ${outL}\n` +
      `Watch it live, free: https://tradeready.in\n#nifty #banknifty #fno #swingtrading #sharemarket #moneyflow\nNot investment advice.`,
    x: calm ? `Calm day: no sector stood out against the F&O market (${pct(d.mkt)}).\nLive sector money flow 👉 tradeready.in\n#Nifty #StockMarketIndia`
      : `Money left ${BT.name} (${pct(BT.rel)} vs market) and went into ${TP.name} (${pct(TP.rel)}) today.\nLive sector money flow 👉 tradeready.in\n#Nifty #StockMarketIndia`,
    whatsapp: `📊 *Money flow today (${date})*\nF&O market ${pct(d.mkt)}\n🟢 In: ${inL}\n🔴 Out: ${outL}\nLive, free: https://tradeready.in\n_Not investment advice_`,
  };
}

// Guard: nothing that reads like a tip, and data that looks like a real, finished session.
function check(d, c, live) {
  const why = [];
  const text = Object.values(c).join("\n");
  const bad = text.match(/\b(buy|sell|target|stop ?loss|sure ?shot|jackpot|guarantee\w*|multibagger|tips?|entry|exit)\b/gi);
  if (bad) why.push("Words that read like a tip: " + [...new Set(bad.map(w => w.toLowerCase()))].join(", "));
  const today = istNow().toISOString().slice(0, 10);
  if (live.session !== today) why.push(`Data is from ${live.session}, not today (${today}).`);
  if (String(live.at || "").slice(11, 16) < "15:00") why.push(`Last update is ${String(live.at || "").slice(11, 16)}, before the close.`);
  if (d.rows < 150 || d.nodes.length < 10) why.push(`Only ${d.rows} stocks / ${d.nodes.length} sectors in the data.`);
  if (Math.abs(d.mkt) > 8) why.push(`Market move ${pct(d.mkt)} looks wrong.`);
  return why;
}

async function film(page, data) {
  const FPS = 30, T = 21, file = path.join(OUT, "video.mp4"), tmp = path.join(OUT, "video_tmp.mp4");
  await page.goto("file://" + path.join(__dirname, "page.html") + "?mode=video"); await page.waitForTimeout(400);
  const ff = spawn("ffmpeg", ["-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", String(FPS), "-c:v", "mjpeg", "-i", "-",
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", tmp], { stdio: ["pipe", "ignore", "inherit"] });
  const done = new Promise((ok, no) => ff.on("exit", c => c === 0 ? ok() : no(new Error("ffmpeg " + c))));
  for (let i = 0; i < FPS * T; i++) {
    await page.evaluate(t => { __step(); __step(); setT(t); }, i / FPS);
    const buf = await page.screenshot({ type: "jpeg", quality: 92 });
    if (!ff.stdin.write(buf)) await new Promise(r => ff.stdin.once("drain", r));
  }
  ff.stdin.end(); await done; fs.renameSync(tmp, file); return file;
}
async function still(page, mode, name) {
  await page.goto("file://" + path.join(__dirname, "page.html") + "?mode=" + mode); await page.waitForTimeout(400);
  await page.evaluate(() => { for (let i = 0; i < 240; i++) __step(); });
  const file = path.join(OUT, name); await page.screenshot({ path: file, type: "jpeg", quality: 90 }); return file;
}

const STILLS = args.includes("--stills");
(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const live = await source();
  if (!live || !(live.rows || []).length) throw new Error("No live money-flow data.");
  const sec = sectors(live), now = istNow();
  const day = new Date((live.session || now.toISOString().slice(0, 10)) + "T00:00:00Z");
  const data = { ...sec, at: String(live.at || "").slice(11, 16), session: live.session,
    dateLabel: day.toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "UTC" }),
    hook: HOOKS[day.getUTCDate() % HOOKS.length] };
  const cap = captions(data), held = check(data, cap, live);
  if (held.length && !FORCE) console.log("HELD:", held.join(" | "));
  console.log(cap.headline);

  const b = await chromium.launch();
  const vid = await b.newPage({ viewport: { width: 720, height: 1280 }, deviceScaleFactor: 1.5 });
  const errs = []; vid.on("pageerror", e => errs.push(e.message));
  await vid.addInitScript(d => { window.__DATA = d; }, data);
  const video = STILLS ? path.join(OUT, "video.mp4") : await film(vid, data), cover = await still(vid, "cover", "cover.jpg");
  const pp = await b.newPage({ viewport: { width: 720, height: 900 }, deviceScaleFactor: 1.5 });
  pp.on("pageerror", e => errs.push(e.message));
  await pp.addInitScript(d => { window.__DATA = d; }, data);
  const post = await still(pp, "post", "post.jpg");
  await b.close();
  if (errs.length) throw new Error("Page errors: " + errs.join("; "));
  const meta = { date: data.session, at: data.at, made: new Date().toISOString(), mkt: data.mkt, held: FORCE ? [] : held, ...cap,
    top_in: data.nodes.filter(n => n.rel > .15).slice(0, 3), top_out: data.nodes.filter(n => n.rel < -.15).slice(-3).reverse() };
  fs.writeFileSync(path.join(OUT, "kit.json"), JSON.stringify(meta, null, 2));
  for (const f of [video, cover, post]) console.log(path.basename(f), (fs.statSync(f).size / 1e6).toFixed(2), "MB");
  if (DRY) { console.log("Dry run: files in", OUT); return; }
  if (!KEY) throw new Error("INGEST_KEY is not set.");
  for (const [f, file] of [["video", video], ["cover", cover], ["post", post]]) {
    const r = await fetch(`${SITE}/api/ingest/social-file?d=${meta.date}&f=${f}`, { method: "POST",
      headers: { "content-type": "application/octet-stream", "x-ingest-key": KEY }, body: fs.readFileSync(file) });
    console.log("sent", f, r.status, (await r.text()).slice(0, 120)); if (!r.ok) process.exit(1);
  }
  const r = await fetch(`${SITE}/api/ingest/social`, { method: "POST", headers: { "content-type": "application/json", "x-ingest-key": KEY }, body: JSON.stringify({ meta }) });
  console.log("sent kit", r.status, (await r.text()).slice(0, 200));
  if (!r.ok) process.exit(1);
})().catch(e => { console.error(e); process.exit(1); });
