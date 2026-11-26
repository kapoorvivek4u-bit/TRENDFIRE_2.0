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
const OUT = path.join(__dirname, "out"), DATA = path.join(__dirname, "..", "data");
const FORMAT = args.includes("--format") ? args[args.indexOf("--format") + 1] : null;   // force a format (testing)
const TUT_ARG = args.includes("--tutorial") ? args[args.indexOf("--tutorial") + 1] : null;

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

// ---------------------------------------------------------------- weekly rotation (Option A: no stock names)
// Mon weather · Tue OI build-up map · Wed sector rotation · Thu money flow · Fri week in review.
// A big-move day (market beyond ±1.5% or a sector beyond ±3% vs market) gets money flow instead.
// Mon / Wed / Fri also carry the next evergreen tutorial.
const ROTA = { 1: "weather", 2: "oi", 3: "rotation", 4: "flow", 5: "week" };
const TUTS = ["tut-chase", "tut-flow", "tut-impulse", "tut-chain", "tut-trapped"];
const TUT_TEXT = {
  "tut-chase": ["Bought a strong stock and it fell next day? You chased it", "The chase meter: how far a stock is from its 20 EMA, in ATRs. 🟢 near the average, 🟡 stretched, 🔴 far above. TradeReady shows this dot next to every F&O stock.", "#chasemeter #20ema #atr #tradingpsychology"],
  "tut-flow": ["How to read the money-flow river in 20 seconds", "Left = sectors money is leaving. Right = where it is going. Thicker band = bigger shift. Live every 10 minutes on TradeReady, and tap a sector to see its stocks.", "#moneyflow #sectorrotation #howtotrade"],
  "tut-impulse": ["Is the trend running or resting? Impulse vs corrective", "Impulse = moving with force. Corrective = resting. Check Monthly, Weekly and Daily together: the big timeframes give the trend, Daily tells you if it's running or pausing.", "#trendtrading #multitimeframe #priceaction"],
  "tut-chain": ["Read an option chain in 10 seconds", "Biggest call OI = ceiling. Biggest put OI = floor. Ceiling bars shrinking = call writers covering. On TradeReady, tap any stock and the chain is read for you in plain English.", "#optionchain #openinterest #optionstrading"],
  "tut-trapped": ["Why some stocks suddenly fly up: trapped shorts", "Price falls and OI rises: shorts pile in. Price turns up through their level: they're trapped. Their buying back pushes price up faster. TradeReady lists trapped shorts every day.", "#shortsqueeze #shortcovering #openinterest"] };
const BASE = "#stockmarketindia #nifty50 #banknifty #nse #fno #optionstrading #swingtrading #sharemarket #stockmarket #trading #marketupdate";
const WXW = sc => sc >= 70 ? "Clear skies" : sc >= 55 ? "Partly sunny" : sc >= 40 ? "Light showers" : "Storm warning";
const tutIndex = d => { let n = 0; for (let t = Date.UTC(2026, 10, 25); t <= d.getTime(); t += 864e5) { const w = new Date(t).getUTCDay(); if (w === 1 || w === 3 || w === 5) n++; } return Math.max(0, n - 1) % TUTS.length; };

function readJSON(f) { try { return JSON.parse(fs.readFileSync(path.join(DATA, f), "utf8")); } catch (e) { return null; } }
function sceneData(base) {
  const s = readJSON("site.json") || {}, sj = readJSON("sectors.json") || {}, st = s.strength || {}, hist = st.history || [], q = s.quadrants || { boxes: [] };
  const secs = (sj.sectors || []).filter(x => x.name);
  return { ...base, siteDay: String(s.generated_ist || "").slice(0, 10),
    weather: { score: st.score, label: st.label, trend: st.trend, parts: st.parts || [], history: hist },
    oi: q, oiDay: q.date ? "on " + new Date(q.date + "T00:00:00Z").toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" }) : "today",
    sectors: secs.map(x => ({ name: x.name, stage: x.stage, ratio: x.ratio, mom: x.mom, rw: x.rw })),
    moves: secs.map(x => { const tr = x.trail || [], a = tr[Math.max(0, tr.length - 6)]; return a && a.stage !== x.stage ? { name: x.name, from: a.stage, to: x.stage } : null; }).filter(Boolean),
    week: { range: base.weekRange, best: secs.slice().sort((a, b) => b.rw - a.rw), worst: secs.slice().sort((a, b) => a.rw - b.rw), wxFrom: hist.length > 4 ? hist[hist.length - 5].score : null },
    ban: (s.heads_up || {}).ban || [] };
}
const foot = "Live and free 👉 tradeready.in (link in bio)\nA study of market data, not investment advice.";
function formatCaptions(fmt, d) {
  const date = d.dateLabel;
  if (fmt === "weather") { const w = d.weather, word = WXW(w.score);
    return { headline: `Market weather: ${word}, ${w.score}/100`, tags: "#marketweather #niftyoutlook #marketbreadth",
      body: `☀️ Market weather for the week (${date}): ${w.score}/100, ${word}\n${w.label} · ${w.trend}\n\nWhat's behind it:\n${w.parts.map(p => `• ${p.name}: ${p.value}`).join("\n")}\n\nOne 0–100 score from breadth, Nifty trend, sectors, money flow and India VIX.` }; }
  if (fmt === "oi") { const B = Object.fromEntries(d.oi.boxes.map(b => [b.key, b.count])), tot = Object.values(B).reduce((a, b) => a + b, 0) || 1, bull = Math.round(((B.long || 0) + (B.cover || 0)) / tot * 100);
    return { headline: `OI build-up map: ${bull}% of F&O stocks on the up side`, tags: "#openinterest #longbuildup #shortcovering #oidata",
      body: `🏦 What F&O traders did ${d.oiDay}\n🟢 Long build-up: ${B.long || 0}\n🔴 Short build-up: ${B.short || 0}\n🔵 Short covering: ${B.cover || 0}\n🟠 Long unwinding: ${B.unwind || 0}\n\n${bull}% saw long build-up or short covering.\nPrice ↑ OI ↑ = new buyers · Price ↓ OI ↑ = new sellers · Price ↑ OI ↓ = sellers exiting · Price ↓ OI ↓ = buyers exiting.` }; }
  if (fmt === "rotation") { const L = d.sectors.filter(s => s.stage === "Leading").map(s => s.name), I = d.sectors.filter(s => s.stage === "Improving").map(s => s.name);
    return { headline: `Sector rotation: ${L.length} leading, ${I.length} improving`, tags: "#sectorrotation #rrg #sectoranalysis",
      body: `🔄 Sector rotation (${date})\nLeading: ${L.join(", ") || "none"}\nImproving: ${I.join(", ") || "none"}\n${d.moves.length ? "\nMoved this week:\n" + d.moves.slice(0, 4).map(m => `• ${m.name}: ${m.from} → ${m.to}`).join("\n") + "\n" : ""}\nRight = beating Nifty, up = gaining speed. Sectors travel clockwise: Improving → Leading → Weakening → Lagging.` }; }
  if (fmt === "week") { const b = d.week.best.slice(0, 3), w = d.week.worst.slice(0, 3), f = x => (x > 0 ? "+" : "") + x.toFixed(1) + "%";
    return { headline: `Week in review: ${b[0] ? b[0].name : "—"} led, ${w[0] ? w[0].name : "—"} lagged`, tags: "#weeklyrecap #sectorrotation #marketrecap",
      body: `📅 The week in review (${d.weekRange})\nBest sectors vs Nifty: ${b.map(s => `${s.name} ${f(s.rw)}`).join(", ")}\nWorst: ${w.map(s => `${s.name} ${f(s.rw)}`).join(", ")}\nMarket weather: ${d.week.wxFrom != null ? d.week.wxFrom + " → " : ""}${d.weather.score} (${WXW(d.weather.score)})${d.ban.length ? `\nF&O ban list: ${d.ban.join(", ")}` : ""}` }; }
  return null;
}
function fullCaptions(c, tags) {
  return { headline: c.headline,
    instagram: `${c.body}\n\n${foot}\n\n${BASE} ${tags}`.trim(),
    youtube_title: `${c.headline} | NSE ${c.date || ""} #shorts`.replace(/ +/g, " ").slice(0, 100),
    youtube_desc: `${c.body}\n\nWatch it live, free: https://tradeready.in\n#nifty #banknifty #fno #swingtrading #sharemarket\nNot investment advice.`,
    x: `${c.headline}.\nLive on 👉 tradeready.in\n#Nifty #StockMarketIndia`,
    whatsapp: `📊 *${c.headline}*\n${c.body}\nLive, free: https://tradeready.in\n_Not investment advice_` };
}
function tutCaptions(mode) { const [title, body, tags] = TUT_TEXT[mode];
  return { mode, headline: title, instagram: `📚 ${title}\n\n${body}\n\nTry it free 👉 tradeready.in (link in bio)\nEducation, not investment advice.\n\n${BASE} ${tags} #tradingeducation`,
    youtube_title: `${title} #shorts`.slice(0, 100), youtube_desc: `${body}\nTry it free: https://tradeready.in\n#tradingeducation #nifty #fno\nNot investment advice.`,
    x: `📚 ${title}\n${body.split(". ")[0]}.\n👉 tradeready.in\n#StockMarketIndia`, whatsapp: `📚 *${title}*\n${body}\nhttps://tradeready.in` }; }

const KEYT = { weather: 7.5, oi: 12.5, rotation: 13, week: 6, "tut-chase": 9, "tut-flow": 11, "tut-impulse": 13.5, "tut-chain": 8, "tut-trapped": 15 };
async function filmScene(page, mode, name) {
  const FPS = 30, T = 21, file = path.join(OUT, name), tmp = file.replace(/\.mp4$/, "_tmp.mp4");
  await page.goto("file://" + path.join(__dirname, "scenes.html") + "?mode=" + mode); await page.waitForTimeout(400);
  const ff = spawn("ffmpeg", ["-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", String(FPS), "-c:v", "mjpeg", "-i", "-",
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", tmp], { stdio: ["pipe", "ignore", "inherit"] });
  const done = new Promise((ok, no) => ff.on("exit", c => c === 0 ? ok() : no(new Error("ffmpeg " + c))));
  let keyShot = null;
  for (let i = 0; i < FPS * T; i++) {
    const t = i / FPS; await page.evaluate(t => { if (window.__step) { __step(); __step(); } setT(t); }, t);
    const buf = await page.screenshot({ type: "jpeg", quality: 92 });
    if (!keyShot && t >= (KEYT[mode] || 8)) keyShot = buf;
    if (!ff.stdin.write(buf)) await new Promise(r => ff.stdin.once("drain", r));
  }
  ff.stdin.end(); await done; fs.renameSync(tmp, file);
  return { file, keyShot };
}
async function sceneStills(page, mode, keyShot) {
  const cover = path.join(OUT, "cover.jpg"), post = path.join(OUT, "post.jpg");
  fs.writeFileSync(cover, keyShot);                                // the busiest moment, as the cover
  // 4:5 picture post: the same frame, whole, centred on the dark background (no cropping of the header or footer)
  await page.setViewportSize({ width: 720, height: 900 });
  await page.setContent(`<html><body style="margin:0;width:720px;height:900px;background:#0d1117;display:grid;place-items:center">
    <img src="data:image/jpeg;base64,${keyShot.toString("base64")}" style="height:900px;width:auto;display:block"></body></html>`);
  await page.waitForTimeout(200);
  await page.screenshot({ path: post, type: "jpeg", quality: 90 });
  return { cover, post };
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const live = await source();
  if (!live || !(live.rows || []).length) throw new Error("No live money-flow data.");
  const sec = sectors(live), now = istNow();
  const day = new Date((live.session || now.toISOString().slice(0, 10)) + "T00:00:00Z"), wd = day.getUTCDay();
  const mon = new Date(day.getTime() - (wd - 1) * 864e5), dl = x => x.toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "UTC" });
  const base = { ...sec, at: String(live.at || "").slice(11, 16), session: live.session, dateLabel: dl(day), weekRange: `${dl(mon)} – ${dl(day)}`,
    hook: HOOKS[day.getUTCDate() % HOOKS.length] };
  const big = Math.abs(sec.mkt) > 1.5 || sec.nodes.some(n => Math.abs(n.rel) > 3);
  let fmt = FORMAT || (big ? "flow" : ROTA[wd] || "flow");
  const data = sceneData(base);
  if (fmt !== "flow" && data.siteDay && data.siteDay < live.session) { console.log("site.json is from", data.siteDay, "- using money flow instead"); fmt = "flow"; }
  const tutMode = TUT_ARG === "none" ? null : TUT_ARG || ([1, 3, 5].includes(wd) ? TUTS[tutIndex(day)] : null);

  let cap;
  if (fmt === "flow") cap = captions(base);
  else { const c = formatCaptions(fmt, data); cap = fullCaptions({ ...c, date: base.dateLabel }, c.tags); }
  const tut = tutMode ? tutCaptions(tutMode) : null;
  const held = check(base, { ...cap, ...(tut ? { t_ig: tut.instagram, t_x: tut.x } : {}) }, live);
  if (held.length && !FORCE) console.log("HELD:", held.join(" | "));
  console.log("format:", fmt, big ? "(big-move day)" : "", "| tutorial:", tutMode || "none", "|", cap.headline);

  const b = await chromium.launch();
  const errs = [], page = async (w, h, d) => { const p = await b.newPage({ viewport: { width: w, height: h }, deviceScaleFactor: 1.5 }); p.on("pageerror", e => errs.push(e.message)); await p.addInitScript(x => { window.__DATA = x; }, d); return p; };
  let video, cover, post, tutVideo = null;
  if (fmt === "flow") {
    const vid = await page(720, 1280, base);
    video = STILLS ? path.join(OUT, "video.mp4") : await film(vid, base); cover = await still(vid, "cover", "cover.jpg");
    post = await still(await page(720, 900, base), "post", "post.jpg");
  } else {
    const sp = await page(720, 1280, data), r = await filmScene(sp, fmt, "video.mp4"); video = r.file;
    ({ cover, post } = await sceneStills(await page(720, 1280, data), fmt, r.keyShot));
  }
  if (tutMode && !STILLS) tutVideo = (await filmScene(await page(720, 1280, data), tutMode, "tutorial.mp4")).file;
  await b.close();
  if (errs.length) throw new Error("Page errors: " + errs.join("; "));
  const meta = { date: data.session, at: base.at, made: new Date().toISOString(), mkt: base.mkt, format: fmt, big, held: FORCE ? [] : held, ...cap,
    top_in: base.nodes.filter(n => n.rel > .15).slice(0, 3), top_out: base.nodes.filter(n => n.rel < -.15).slice(-3).reverse(), tutorial: tutVideo ? tut : null };
  fs.writeFileSync(path.join(OUT, "kit.json"), JSON.stringify(meta, null, 2));
  const files = [["video", video], ["cover", cover], ["post", post]].concat(tutVideo ? [["tut", tutVideo]] : []);
  for (const [, f] of files) console.log(path.basename(f), fs.existsSync(f) ? (fs.statSync(f).size / 1e6).toFixed(2) + " MB" : "missing");
  if (DRY) { console.log("Dry run: files in", OUT); return; }
  if (!KEY) throw new Error("INGEST_KEY is not set.");
  for (const [f, file] of files) {
    const r = await fetch(`${SITE}/api/ingest/social-file?d=${meta.date}&f=${f}`, { method: "POST",
      headers: { "content-type": "application/octet-stream", "x-ingest-key": KEY }, body: fs.readFileSync(file) });
    console.log("sent", f, r.status, (await r.text()).slice(0, 120)); if (!r.ok) process.exit(1);
  }
  const r = await fetch(`${SITE}/api/ingest/social`, { method: "POST", headers: { "content-type": "application/json", "x-ingest-key": KEY }, body: JSON.stringify({ meta }) });
  console.log("sent kit", r.status, (await r.text()).slice(0, 200));
  if (!r.ok) process.exit(1);
})().catch(e => { console.error(e); process.exit(1); });
