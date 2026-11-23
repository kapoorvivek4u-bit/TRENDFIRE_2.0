"""Builds data/site.json: everything the tradeready.in dashboard shows, worked out once
after each scan so the website only has to read one small file.

Run on its own (python site_data.py) or from the end of setups_scan.py."""
import datetime as dt
import json
import os

import pandas as pd

DATA = "data"
OUT = os.path.join(DATA, "site.json")
LOG = os.path.join(DATA, "strength_log.csv")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
SECTORS = {}          # symbol -> {"sector", "stage"}; filled in run()


def money(x):
    if x is None:
        return ""
    return f"{x:,.1f}" if x >= 1000 else f"{x:,.2f}"


# ---------------------------------------------------------------- strategies (same rules as app.py)
STRAT_RULES = {
    1: ("Sector Leader Swing", "F&O · 3-5 days",
        "Buy a short hourly dip in a strong stock inside a strong sector.",
        ["Market: Turning up with Daily breadth under 85% (full size), or No clear turn (half size)",
         "Swing Pullback, stage Ready, grade A, With trend",
         "Not Extended (under 4 ranges above the 50 EMA)",
         "Sector Leading or Improving, its index Daily Bullish, Wk bull 50% or more"],
        "Hourly close above the entry level. Book half at the 2R target, trail the rest under "
        "hourly swing lows, exit by day 5 if it has not worked."),
    2: ("Cooldown Buy", "F&O and Equity · 5-10 days",
        "Buy strong stocks on their daily pullback; best right after an overheated rally cools.",
        ["Market not Still falling, and Nifty Daily not Bearish",
         "Size: full when a cooldown is under way (Daily breadth was 85%+ in the last 6 "
         "sessions and has cooled to 60-78%), otherwise half",
         "Trend Pullback or Daily Overspill, stage Ready, grade A",
         "Under 2 ranges above the 50 EMA (near value)",
         "Sector Leading, Improving or Weakening (not Lagging)",
         "The same stocks at stage Watch are listed under 'Dipping now, next to turn'"],
        "Daily close above the entry level. Half at 2R, trail the rest under the 20 EMA."),
    3: ("Squeeze Coil", "F&O and Equity · 3-10 days",
        "Catch a coiled stock as momentum turns up, before the breakout.",
        ["Market: No clear turn, or Turning up with Daily breadth under 70%",
         "Squeeze Break, stage Ready, grade A or B",
         "Sector Leading or Improving",
         "Bonus: old label Prime Setup; Unusual page shows Buying"],
        "Daily close above the 5-day high, ideally on above-normal volume. Half at 2R; exit "
        "when the Daily turns Topping."),
    4: ("Weekly Overspill Positional", "F&O and Equity · 2-4 weeks",
        "Buy a long uptrend when the weekly chart dips into its 20-50 EMA zone and the Daily "
        "turns up.",
        ["Market: anything except Still falling",
         "Weekly Overspill, stage Ready, grade A, With trend",
         "Sector not Lagging",
         "Bonus (Equity): delivery higher than before"],
        "Accordion: one third now, one third on a daily close above the entry level, the last "
        "third when Weekly momentum turns up. The stop is wide: size the first lot so it costs "
        "your normal risk. Trail under the weekly 20 EMA."),
    5: ("Unusual Activity Confluence", "F&O and Equity · 3-5 days",
        "Big money showing up in a stock that is already on a setup.",
        ["Market: anything except Still falling",
         "Unusual page: bias Buying, score 4 or more",
         "Also on a setup page, stage Watch or Ready, grade not Warning",
         "Weekly Bullish or Impulse; sector Leading or Improving"],
        "Enter when the setup is Ready, on its entry level. Watch-stage names are listed so "
        "you can set alerts."),
    6: ("Equity Base Breakout", "Equity · 8-10 days",
        "Buy a midcap or smallcap breaking out of a tight base on volume.",
        ["Market (Equity): Turning up with Daily breadth under 85%",
         "Breakout page: Ready (close to the level), grade A",
         "RS 80 or more, base 20% deep or less, volume in the base below usual",
         "Sector Leading or Improving, or not classified"],
        "Daily close above the breakout level on volume at least 1.5x usual; no volume, no "
        "trade. Stop about one daily range below the level. Exit if it closes back inside the "
        "base within 3 days."),
    7: ("Breakout + Trend Overspill", "F&O · 3-7 days",
        "After a range breakout, buy the first hourly dips into the 20-50 EMA zone of the "
        "new trend.",
        ["Market: anything except Still falling (half size when Overheated)",
         "A Range breakout in the last 15 sessions that still holds (price above the old ceiling)",
         "Weekly or Daily in Impulse",
         "Hourly Overspill, stage Ready, Dip quality A",
         "Not Extended (under 4 ranges above the Daily 50 EMA); sector not Lagging"],
        "Hourly close above the entry (the hourly 20 EMA). Stop under the spill low. Book half "
        "at 2R, trail the rest under the hourly 50 EMA. Best on the first or second dip after "
        "the breakout; later dips are weaker."),
}


def gate_of(m):
    """Market gate from a page's market block."""
    h, t, nf = m.get("daily_bull_hist"), m.get("turn"), (m.get("nifty") or {}).get("state")
    g = {"turn": t or "Unknown", "last": h[-1] if h else None, "hist": h,
         "overheated": bool(h and t == "Turning up" and h[-1] >= 85),
         "cooldown": bool(h and max(h[:-1]) >= 85 and 60 <= h[-1] <= 78
                          and h[-1] < max(h[:-1]) and nf != "Bearish"),
         "falling": t == "Falling", "nifty": nf}
    if g["overheated"]:
        g["word"] = f"Overheated: {h[-1]}% of stocks already up on Daily"
    elif t == "Turning up":
        g["word"] = f"Turning up ({h[-1]}% up on Daily)"
    elif t == "Falling":
        g["word"] = "Still falling"
    elif t == "No clear turn":
        g["word"] = "No clear turn"
    else:
        g["word"] = "Market line not available yet"
    return g


def detail_num(r, pattern):
    import re as _re
    mt = _re.search(pattern, r.get("detail", ""))
    return float(mt.group(1)) if mt else None


def strategy_picks(data, sec_table):
    """Apply each strategy's rules. Returns {n: {"open": bool, "why_closed": str,
    "picks": [...], "blocked": [...]}} where blocked = names that pass every stock rule
    but not the market gate."""
    out = {}
    sec = lambda s: SECTORS.get(s, {})
    sec_ok = lambda s, ok: sec(s).get("stage") in ok
    ext_under = lambda r, x: r.get("ext") is None or r["ext"] < x

    def pick(n, r, market, size, checks, bonus=None, entry=None, stop=None):
        entry = r.get("trigger") if entry is None else entry
        stop = r.get("stop") if stop is None else stop
        tgt = (entry + 2 * (entry - stop)) if entry and stop and entry > stop else None
        return {"strategy": n, "symbol": r["symbol"], "market": market, "row": r,
                "entry": entry, "stop": stop, "target": tgt, "size": size,
                "checks": checks, "bonus": bonus or []}

    fo, eq, bo = data.get("F&O"), data.get("Equity"), data.get("Breakout")
    ufo, ueq = data.get("Unusual F&O", {}), data.get("Unusual Equity", {})
    gf = gate_of(fo["market"]) if fo else None
    ge = gate_of(eq["market"]) if eq else None

    def rows(res, keys=("rows", "overspill")):
        return [r for k in keys for r in (res or {}).get(k, [])]

    def sector_line(s):
        t = sec(s)
        return (f"Sector {t['sector']} {t['stage']}" if t.get("sector") and t.get("stage")
                else "Sector not ranked")

    # 1. Sector Leader Swing (F&O)
    res = {"picks": [], "blocked": []}
    if gf:
        open_ = not gf["falling"] and not gf["overheated"] and gf["turn"] in (
            "Turning up", "No clear turn")
        size = "Full size" if gf["turn"] == "Turning up" else "Half size"
        for r in rows(fo, ("rows",)):
            s = sec(r["symbol"])
            st_ = sec_table.get(s.get("sector"), {})
            if (r["setup"] == "Swing Pullback" and r["stage"] == "Ready" and r["grade"] == "A"
                    and r.get("trend") == "With trend" and ext_under(r, 4.0)
                    and s.get("stage") in ("Leading", "Improving")
                    and st_.get("idx_d") == "Bullish" and st_.get("wk_bull", 0) >= 50):
                p = pick(1, r, "F&O", size, [
                    "Swing Pullback Ready · grade A · With trend",
                    f"{r['ext']:.1f} ranges above the 50 EMA" if r.get("ext") is not None
                    else "Extension n/a",
                    f"{sector_line(r['symbol'])} · index Daily Bullish · Wk bull {st_['wk_bull']}%"],
                    [f"Old: {r['old']}"] if r.get("old") else [])
                (res["picks"] if open_ else res["blocked"]).append(p)
        res["open"] = open_
        res["gate"] = gf["word"]
    out[1] = res

    # 2. Cooldown Buy (both): open unless the market is falling; full size in a cooldown
    res = {"picks": [], "blocked": [], "open": False, "gate": ""}
    dipping, words = [], []
    for market, rs, g in (("F&O", fo, gf), ("Equity", eq, ge)):
        if not g:
            continue
        shut = g["falling"] or g.get("nifty") == "Bearish"
        size = "Full size (cooldown)" if g["cooldown"] else "Half size (no cooldown yet)"
        words.append(f"{market}: " + ("closed, market falling" if shut else
                                      "cooldown, full size" if g["cooldown"] else "half size"))
        for r in rows(rs):
            if (r["setup"] in ("Trend Pullback", "Daily Overspill") and r["grade"] == "A"
                    and r.get("ext") is not None and r["ext"] < 2
                    and sec_ok(r["symbol"], ("Leading", "Improving", "Weakening"))):
                if r["stage"] == "Watch":
                    dipping.append(dict(r, setup=f"{market} · {r['setup']}"))
                elif r["stage"] == "Ready":
                    p = pick(2, r, market, size, [
                        f"{r['setup']} Ready · grade A",
                        f"{r['ext']:.1f} ranges above the 50 EMA", sector_line(r["symbol"])])
                    (res["blocked"] if shut else res["picks"]).append(p)
        res["open"] = res["open"] or not shut
    res["gate"] = "; ".join(words)
    res["queue"] = {"dipping": sorted(dipping, key=lambda r: r["symbol"]), "waiting": [],
                    "caption": "Grade A daily dips near value in good sectors that have not "
                               "turned up yet. Set an alert at 'Above'."}
    out[2] = res

    # 3. Squeeze Coil (both)
    res = {"picks": [], "blocked": []}
    opens = []
    for market, rs, g, um in (("F&O", fo, gf, ufo), ("Equity", eq, ge, ueq)):
        if not g:
            continue
        open_ = (g["turn"] == "No clear turn" or
                 (g["turn"] == "Turning up" and (g["last"] or 100) < 70))
        opens.append(open_)
        for r in rows(rs, ("rows",)):
            if (r["setup"] == "Squeeze Break" and r["stage"] == "Ready" and r["grade"] in ("A", "B")
                    and sec_ok(r["symbol"], ("Leading", "Improving"))):
                bonus = []
                if r.get("old") == "🏆 Prime Setup":
                    bonus.append("Old: 🏆 Prime Setup")
                if um.get(r["symbol"], {}).get("bias") == "Buying":
                    bonus.append("Unusual: Buying")
                p = pick(3, r, market, "Full size" if r["grade"] == "A" else "Half size",
                         [f"Squeeze Break Ready · grade {r['grade']}", sector_line(r["symbol"])],
                         bonus)
                (res["picks"] if open_ else res["blocked"]).append(p)
    res["open"] = any(opens)
    res["gate"] = ("Open" if all(opens) else "Open for one market only" if res["open"] else
                   "Closed: works early in a turn, and Daily breadth is already 70% or more")
    out[3] = res

    # 4. Weekly Overspill Positional (both)
    res = {"picks": [], "blocked": []}
    for market, rs, g in (("F&O", fo, gf), ("Equity", eq, ge)):
        if not g:
            continue
        for r in rs.get("overspill", []):
            if (r["setup"] == "Weekly Overspill" and r["stage"] == "Ready" and r["grade"] == "A"
                    and r.get("trend") == "With trend"
                    and sec(r["symbol"]).get("stage") != "Lagging"):
                bonus = []
                d_now, d_before = (detail_num(r, r"Delivery (\d+)%"),
                                   detail_num(r, r"vs (\d+)% before"))
                if d_now and d_before and d_now > d_before:
                    bonus.append(f"Delivery {d_now:.0f}% vs {d_before:.0f}% before")
                p = pick(4, r, market, "Accordion: one third now", [
                    "Weekly Overspill Ready · grade A · With trend", sector_line(r["symbol"])],
                    bonus)
                (res["blocked"] if g["falling"] else res["picks"]).append(p)
    res["open"] = not (gf and gf["falling"])
    res["gate"] = "Open" if res["open"] else "Closed: market still falling"
    out[4] = res

    # 5. Unusual Activity Confluence (both)
    res = {"picks": [], "blocked": []}
    for market, rs, g, um in (("F&O", fo, gf, ufo), ("Equity", eq, ge, ueq)):
        if not g:
            continue
        by_sym = {}
        for r in rows(rs):
            if r["stage"] in ("Ready", "Watch") and r["grade"] != "Warning":
                by_sym.setdefault(r["symbol"], []).append(r)
        for sym, u in um.items():
            w = (u.get("states") or {}).get("W", "")
            if (u.get("bias") == "Buying" and u.get("score", 0) >= 4 and sym in by_sym
                    and w in ("Bullish", "Impulse")
                    and sec_ok(sym, ("Leading", "Improving"))):
                r = sorted(by_sym[sym], key=lambda x: (x["stage"] != "Ready", x["grade"]))[0]
                ev = u["events"][0]
                p = pick(5, r, market, "Full size" if r["stage"] == "Ready" else "Alert only", [
                    f"Unusual Buying · score {u['score']:.1f} · {ev['when']}: {ev['text']}",
                    f"{r['setup']} {r['stage']} · grade {r['grade']}",
                    f"Weekly {w} · {sector_line(sym)}"])
                (res["blocked"] if g["falling"] else res["picks"]).append(p)
    res["open"] = not (gf and gf["falling"])
    res["gate"] = "Open" if res["open"] else "Closed: market still falling"
    out[5] = res

    # 7. Breakout + Trend Overspill (F&O)
    res = {"picks": [], "blocked": []}
    if gf and fo:
        brk = fo.get("recent_breakouts", {})
        size = "Half size (market overheated)" if gf["overheated"] else "Full size"
        for r in fo.get("overspill", []):
            b = brk.get(r["symbol"])
            stt = r.get("states") or {}
            if (r["setup"] == "Hourly Overspill" and r["stage"] == "Ready" and r["grade"] == "A"
                    and b and ext_under(r, 4.0)
                    and ("Impulse" in (stt.get("W"), stt.get("D")))
                    and sec(r["symbol"]).get("stage") != "Lagging"):
                when = dt.date.fromisoformat(b["date"]).strftime("%a %d %b")
                p = pick(7, r, "F&O", size, [
                    f"Range breakout {when} ({b['days']} sessions ago): {b['weeks']}-week "
                    f"{b['kind']}, still holding above {money(b['level'])}",
                    "Hourly Overspill Ready · Dip quality A",
                    f"Weekly {stt.get('W', '?')} · Daily {stt.get('D', '?')} · {sector_line(r['symbol'])}"],
                    [f"VK: {r['vk']['final']}"] if r.get("vk") and
                    r["vk"]["final"] in ("ALMOST READY (Bullish)", "BUY NOW") else [])
                (res["blocked"] if gf["falling"] else res["picks"]).append(p)
        res["open"] = not gf["falling"]
        res["gate"] = ("Closed: market still falling" if gf["falling"] else
                       "Open (half size: overheated)" if gf["overheated"] else "Open")
        # the queue: breakout stocks dipping now, and breakout stocks not dipping yet
        dipping = [r for r in fo.get("overspill", []) if r["setup"] == "Hourly Overspill"
                   and r["symbol"] in brk and r["stage"] == "Watch" and r["grade"] != "Warning"]
        dsyms = {r["symbol"] for r in fo.get("overspill", []) if r["setup"] == "Hourly Overspill"}
        res["queue"] = {"dipping": sorted(dipping, key=lambda r: (r["grade"], r["symbol"])),
                        "waiting": sorted((s_, b) for s_, b in brk.items() if s_ not in dsyms)}
    out[7] = res

    # 6. Equity Base Breakout
    res = {"picks": [], "blocked": []}
    if bo:
        g = ge or gate_of(bo.get("market", {}))
        open_ = g["turn"] == "Turning up" and not g["overheated"]
        for r in bo.get("setting", []):
            rs_, base, vb = (detail_num(r, r"RS (\d+)"), detail_num(r, r"Base (\d+)% deep"),
                             detail_num(r, r"Volume in base ([\d.]+)x"))
            stg = sec(r["symbol"]).get("stage")
            if (r["stage"] == "Ready" and r["grade"] == "A" and (rs_ or 0) >= 80
                    and (base or 99) <= 20 and (vb or 9) < 1 and stg in (None, "", "Leading",
                                                                         "Improving")):
                p = pick(6, r, "Equity", "Full size", [
                    f"Ready · grade A · RS {rs_:.0f}", f"Base {base:.0f}% deep · volume {vb:.1f}x "
                    "usual in the base", sector_line(r["symbol"])])
                (res["picks"] if open_ else res["blocked"]).append(p)
        res["open"] = open_
        res["gate"] = ("Open" if open_ else f"Closed: Equity market is {g['word']}")
    out[6] = res
    for v in out.values():
        v.setdefault("open", False)
        v.setdefault("gate", "No data yet")
    return out


# ---------------------------------------------------------------- helpers
def jload(name):
    path = os.path.join(DATA, name)
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def r1(x, n=1):
    return None if x is None or pd.isna(x) else round(float(x), n)


STATE_SCORE = {"Impulse": 100, "Bullish": 85, "Bottoming": 55, "Topping": 40, "Bearish": 10}


def clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def oi_frame():
    """Futures OI and price per stock per day, from the bhavcopy history."""
    path = os.path.join(DATA, "fo_oi.csv.gz")
    if not os.path.exists(path):
        return None, None
    fo = pd.read_csv(path)
    oi = fo.pivot(index="date", columns="symbol", values="fut_oi").sort_index()
    px = fo.pivot(index="date", columns="symbol", values="fut_close").sort_index()
    return oi, px


QUADS = [("long", "Long build-up", 1, 1), ("short", "Short build-up", 1, -1),
         ("cover", "Short covering", -1, 1), ("unwind", "Long unwinding", -1, -1)]


def quadrants(oi, px):
    """Last session: every F&O stock in one of four boxes by OI change and price change."""
    if oi is None or len(oi) < 2:
        return None
    d_oi = (oi.iloc[-1] / oi.iloc[-2] - 1) * 100
    d_px = (px.iloc[-1] / px.iloc[-2] - 1) * 100
    df = pd.DataFrame({"oi": d_oi, "px": d_px}).dropna()
    df = df[(df.oi.abs() < 300)]                         # drop new listings / bad prints
    out = {"date": str(oi.index[-1]), "boxes": []}
    for key, name, so, sp in QUADS:
        sub = df[((df.oi > 0) if so > 0 else (df.oi <= 0)) & ((df.px > 0) if sp > 0 else (df.px <= 0))]
        sub = sub.reindex(sub.oi.abs().sort_values(ascending=False).index)
        out["boxes"].append({"key": key, "name": name, "count": int(len(sub)),
                             "top": [{"symbol": s, "oi": r1(r.oi), "px": r1(r.px)}
                                     for s, r in sub.head(6).iterrows()]})
    up = sum(b["count"] for b in out["boxes"] if b["key"] in ("long", "cover"))
    out["bull_share"] = round(100 * up / max(1, len(df)))
    return out


def day_moves(oi, px):
    """Day % change of every F&O stock (futures price) with its sector, for the heatmap."""
    if px is None or len(px) < 6:
        return [], {}
    d = (px.iloc[-1] / px.iloc[-2] - 1) * 100
    d5 = (px.iloc[-1] / px.iloc[-6] - 1) * 100
    o5 = (oi.iloc[-1] / oi.iloc[-6] - 1) * 100
    rows = []
    for s in d.dropna().index:
        t = SECTORS.get(s, {})
        rows.append({"symbol": s, "close": r1(px.iloc[-1][s], 2), "chg": r1(d[s], 2),
                     "sector": t.get("sector") or "Others"})
    five = {s: {"px5": r1(d5.get(s)), "oi5": r1(o5.get(s))} for s in d5.dropna().index}
    return rows, five


def strength(m, nifty, sectors, quads):
    """Market Strength 0-100: a weighted mix of breadth, Nifty's trend, sector
    participation, F&O money flow and VIX."""
    h = m.get("daily_bull_hist") or []
    parts = []
    if h:
        parts.append(("Daily breadth", h[-1], 25, f"{h[-1]}% of F&O stocks bullish on Daily"))
    if m.get("weekly_bull_pct") is not None:
        w = m["weekly_bull_pct"]
        parts.append(("Weekly breadth", w, 20, f"{w}% bullish on Weekly"))
    if nifty and nifty.get("states"):
        stt = nifty["states"]
        v = (STATE_SCORE.get(stt.get("W"), 50) + STATE_SCORE.get(stt.get("D"), 50)) / 2
        parts.append(("Nifty trend", v, 20, f"Nifty Weekly {stt.get('W')}, Daily {stt.get('D')}"))
    if sectors:
        good = sum(1 for s in sectors if s["stage"] in ("Leading", "Improving"))
        v = 100 * good / len(sectors)
        parts.append(("Sectors", v, 15, f"{good} of {len(sectors)} sectors leading or improving"))
    if quads:
        parts.append(("Money flow", quads["bull_share"], 10,
                      f"{quads['bull_share']}% of F&O stocks saw long build-up or short covering"))
    if m.get("vix"):
        v = clamp((24 - m["vix"]) / (24 - 11) * 100)
        parts.append(("VIX", v, 10, f"India VIX {m['vix']}"))
    if not parts:
        return None
    score = round(sum(v * w for _, v, w, _ in parts) / sum(w for _, _, w, _ in parts))
    label, advice = next((lab, adv) for cut, lab, adv in (
        (70, "Strong", "Trend market: press the winners, buy dips in leaders."),
        (55, "Positive", "Good for swing longs. Be selective, stick to A setups."),
        (45, "Neutral", "Mixed market. Only the best setups, smaller size."),
        (30, "Weak", "Defensive. Half size, book profits quickly."),
        (-1, "Very weak", "Stay light and protect capital."),
    ) if score >= cut)
    trend = ""
    if len(h) >= 3:
        diff = h[-1] - h[-3]
        trend = ("Breadth improving" if diff >= 5 else "Breadth falling" if diff <= -5
                 else "Breadth steady")
    return {"score": int(score), "label": label, "advice": advice, "trend": trend,
            "parts": [{"name": n, "value": round(v), "weight": w, "why": why}
                      for n, v, w, why in parts]}


def strength_history(today, score):
    """Keep one score per day so the gauge can show a trend line."""
    try:
        log = pd.read_csv(LOG) if os.path.exists(LOG) else pd.DataFrame(columns=["date", "score"])
    except Exception:
        log = pd.DataFrame(columns=["date", "score"])
    log = log[log["date"] != today]
    log = pd.concat([log, pd.DataFrame([{"date": today, "score": score}])])
    log = log.sort_values("date").tail(120)
    log.to_csv(LOG, index=False)
    return [{"date": d, "score": int(s)} for d, s in zip(log["date"], log["score"])][-30:]


def setup_rows(res):
    return [r for k in ("rows", "overspill", "ranges") for r in (res or {}).get(k, [])]


def levels(r, entry=None, stop=None):
    entry = r.get("trigger") if entry is None else entry
    stop = r.get("stop") if stop is None else stop
    tgt, word = r.get("target"), r.get("target_why")
    if not (entry and stop and entry > stop):
        return {"entry": entry, "stop": stop, "target": None, "rr": None}
    if not tgt or tgt <= entry:
        tgt, word = entry + 2 * (entry - stop), "2R"
    return {"entry": r1(entry, 2), "stop": r1(stop, 2), "target": r1(tgt, 2),
            "rr": r1((tgt - entry) / (entry - stop)), "target_word": word}


def card(r, market):
    vk = (r.get("vk") or {}).get("final")
    out = {"symbol": r["symbol"], "market": market, "setup": r.get("setup"),
           "stage": r.get("stage"), "grade": r.get("grade"), "close": r.get("close"),
           "trend": r.get("trend"), "detail": r.get("detail"), "vk": vk,
           "ext": r.get("ext"), "states": r.get("states"),
           "sector": SECTORS.get(r["symbol"], {}).get("sector"),
           "sector_stage": SECTORS.get(r["symbol"], {}).get("stage")}
    out.update(levels(r))
    if r.get("chart"):
        out["chart"] = r["chart"]
    return out


def reasons(x, ctx):
    """Plain-word reasons a stock ranks where it does on the strongest list."""
    s = x["symbol"]
    out = []
    if x.get("r3m") is not None:
        out.append(("rs", f"{x['r3m']:+.0f}% vs Nifty in 3 months"))
    if x.get("hi52") is not None:
        out.append(("hi", "At its 52-week high" if x["hi52"] >= -1 else
                    f"{abs(x['hi52']):.0f}% below 52-week high"))
    stt = x.get("states") or {}
    if stt.get("W") == "Impulse" and stt.get("D") == "Impulse":
        out.append(("mom", "Weekly + Daily in impulse"))
    elif stt.get("W") in ("Impulse", "Bullish"):
        out.append(("mom", f"Weekly {stt['W'].lower()}, Daily {str(stt.get('D', '')).lower()}"))
    if x.get("trend") == 3:
        out.append(("trend", "Above 50 and 200 DMA"))
    t = SECTORS.get(s, {})
    if t.get("stage") in ("Leading", "Improving"):
        out.append(("sector", f"{t['sector']} sector {t['stage'].lower()}"))
    f = ctx["five"].get(s)
    if f and f["oi5"] is not None and f["px5"] is not None and f["oi5"] >= 5 and f["px5"] > 0:
        out.append(("oi", f"Long build-up: OI {f['oi5']:+.0f}% in 5 days"))
    rd = ctx["ready"].get(s)
    if rd:
        out.append(("setup", f"{rd} Ready"))
    if ctx["vk"].get(s) == "BUY NOW":
        out.append(("vk", "VK Buy now"))
    b = ctx["brk"].get(s)
    if b:
        out.append(("brk", f"Broke a {b['weeks']}-week {b['kind']} {b['days']}d ago"))
    return [{"kind": k, "text": t} for k, t in out]


def strongest_block(rows, ctx, n=10):
    out = []
    for x in rows[:n]:
        out.append({"rank": x["rank"], "symbol": x["symbol"], "score": x["score"],
                    "close": x["close"], "r1m": x["r1m"], "r3m": x["r3m"], "hi52": x["hi52"],
                    "sector": x.get("sector") or SECTORS.get(x["symbol"], {}).get("sector"),
                    "reasons": reasons(x, ctx)})
    return out


def pct_over(vals, n):
    if not vals or len(vals) <= n:
        return None
    return (vals[-1] / vals[-1 - n] - 1) * 100


def write_names():
    """symbol -> company name, for the search box on the website."""
    names = {}
    for f in ("names.csv", "sector_map.csv"):
        path = os.path.join(DATA, f)
        try:
            df = pd.read_csv(path)
            if "name" in df.columns:
                names.update({s: n for s, n in zip(df["symbol"], df["name"])
                              if isinstance(s, str) and isinstance(n, str) and n})
        except Exception:
            pass
    if names:
        with open(os.path.join(DATA, "names.json"), "w") as f:
            json.dump(names, f, separators=(",", ":"), sort_keys=True)


def card_candles(symbols, n=45):
    """Daily candles plus 20 and 50 EMA for the stocks shown as cards on the dashboard,
    so each card can draw its own chart. Never stops the build."""
    out = {}
    symbols = sorted({s for s in symbols if s})
    if not symbols:
        return out
    try:
        import yfinance as yf
        tick = {s: s + ".NS" for s in symbols}
        d = yf.download(list(tick.values()), period="6mo", interval="1d", group_by="ticker",
                        progress=False, auto_adjust=False, threads=True)
        for s, t in tick.items():
            try:
                x = d[t] if isinstance(d.columns, pd.MultiIndex) else d
            except KeyError:
                continue
            x = x.dropna(subset=["Open", "High", "Low", "Close"])
            if len(x) < 25:
                continue
            c = x["Close"]
            e20, e50 = c.ewm(span=20, adjust=False).mean(), c.ewm(span=50, adjust=False).mean()
            x, e20, e50 = x.iloc[-n:], e20.iloc[-n:], e50.iloc[-n:]
            rnd = lambda col: [round(float(v), 2) for v in col]
            out[s] = {"o": rnd(x["Open"]), "h": rnd(x["High"]), "l": rnd(x["Low"]), "c": rnd(x["Close"]),
                      "e20": rnd(e20), "e50": rnd(e50)}
    except Exception as ex:
        print("site_data: candles skipped:", ex)
    return out

# ---------------------------------------------------------------- scorecard
HORIZON = {"Swing Pullback": 5, "Hourly Overspill": 5, "Squeeze Break": 10, "Trend Pullback": 10,
           "Daily Overspill": 10, "Weekly Overspill": 20}


def ready_episodes(path, market, gap_days=3):
    """First Ready sighting of each run of a stock on a setup (a new run after a gap)."""
    try:
        lg = pd.read_csv(path)
    except Exception:
        return []
    if lg.empty:
        return []
    lg["date"] = pd.to_datetime(lg["date"])
    lg = lg.sort_values("date")
    out = []
    for (sym, setup), g in lg.groupby(["symbol", "setup"]):
        prev = None
        for _, r in g.iterrows():
            if prev is None or (r["date"] - prev).days > gap_days:
                out.append({"market": market, "symbol": sym, "setup": setup, "grade": r.get("grade"),
                            "trend": r.get("trend"), "date": r["date"], "entry": r.get("trigger"),
                            "stop": r.get("stop"), "close": r.get("close")})
            prev = r["date"]
    return out


def daily_bars(symbols, start):
    """Daily OHLC from Yahoo for the scorecard. Returns {symbol: DataFrame}."""
    out = {}
    if not symbols:
        return out
    try:
        import yfinance as yf
        tick = {s: s + ".NS" for s in symbols}
        d = yf.download(list(tick.values()), start=(start - dt.timedelta(days=3)).strftime("%Y-%m-%d"),
                        interval="1d", group_by="ticker", progress=False, auto_adjust=False, threads=True)
        for s, t in tick.items():
            try:
                x = d[t] if isinstance(d.columns, pd.MultiIndex) else d
                x = x.dropna(subset=["High", "Low", "Close"])
                if len(x):
                    x.index = pd.to_datetime(x.index).tz_localize(None)
                    out[s] = x
            except KeyError:
                pass
    except Exception as ex:
        print("site_data: scorecard prices skipped:", ex)
    return out


def judge(ep, bars, today):
    """Walk the days after the signal: wait for the entry, then 2R target or stop, whichever
    comes first. A day that touches both counts as a loss. Returns the episode with its result."""
    e, st = ep["entry"], ep["stop"]
    res = dict(ep, date=ep["date"].strftime("%Y-%m-%d"), result="no data", r=None, days=None)
    if not (e and st and e > st) or bars is None:
        return res
    tgt = e + 2 * (e - st)
    res["target"] = round(float(tgt), 2)
    hz = HORIZON.get(ep["setup"], 10)
    after = bars[bars.index > ep["date"]]
    if after.empty:
        res["result"] = "waiting"
        return res
    trig_i = None
    for i, (d, b) in enumerate(after.iterrows()):
        if trig_i is None:
            if i >= hz:
                res["result"], res["days"] = "not triggered", i
                return res
            if b["High"] >= e:
                trig_i = i
            else:
                continue
        if b["Low"] <= st:
            res.update(result="stopped", r=-1.0, days=i + 1)
            return res
        if b["High"] >= tgt:
            res.update(result="target", r=2.0, days=i + 1)
            return res
        if i - trig_i + 1 >= hz:
            r = (float(b["Close"]) - e) / (e - st)
            res.update(result="time exit", r=round(r, 2), days=i + 1)
            return res
    last = float(after["Close"].iloc[-1])
    if trig_i is None:
        res["result"] = "waiting"
    else:
        res.update(result="open", r=round((last - e) / (e - st), 2), days=len(after))
    return res


def scorecard(today):
    eps = ready_episodes(os.path.join(DATA, "ready_log.csv"), "F&O") + \
          ready_episodes(os.path.join(DATA, "equity_ready_log.csv"), "Equity")
    if not eps:
        return None
    start = min(e["date"] for e in eps)
    bars = daily_bars(sorted({e["symbol"] for e in eps}), start)
    if not bars:
        return {"since": start.strftime("%Y-%m-%d"), "note": "Prices not available this run.", "setups": [], "recent": []}
    done = [judge(e, bars.get(e["symbol"]), today) for e in eps]
    rows = []
    for (mk, setup), grp in pd.DataFrame(done).groupby(["market", "setup"]):
        g = grp.to_dict("records")
        trig = [x for x in g if x["result"] in ("target", "stopped", "time exit", "open")]
        closed = [x for x in trig if x["result"] != "open"]
        wins = [x for x in closed if (x["r"] or 0) > 0]
        rows.append({"market": mk, "setup": setup, "signals": len(g), "triggered": len(trig),
                     "closed": len(closed), "targets": sum(1 for x in closed if x["result"] == "target"),
                     "stops": sum(1 for x in closed if x["result"] == "stopped"),
                     "win_pct": round(100 * len(wins) / len(closed)) if closed else None,
                     "avg_r": round(sum(x["r"] for x in closed) / len(closed), 2) if closed else None,
                     "open": len(trig) - len(closed),
                     "grade_a": {"closed": sum(1 for x in closed if x["grade"] == "A"),
                                 "wins": sum(1 for x in wins if x["grade"] == "A")}})
    rows.sort(key=lambda x: (-(x["closed"]), x["setup"]))
    recent = sorted([x for x in done if x["result"] in ("target", "stopped", "time exit")],
                    key=lambda x: x["date"], reverse=True)[:20]
    keep = ("market", "symbol", "setup", "grade", "date", "entry", "stop", "target", "result", "r", "days")
    all_closed = [x for x in done if x["result"] in ("target", "stopped", "time exit")]
    return {"since": start.strftime("%Y-%m-%d"), "signals": len(done),
            "closed": len(all_closed),
            "win_pct": round(100 * sum(1 for x in all_closed if (x["r"] or 0) > 0) / len(all_closed)) if all_closed else None,
            "avg_r": round(sum(x["r"] for x in all_closed) / len(all_closed), 2) if all_closed else None,
            "setups": rows, "recent": [{k: x.get(k) for k in keep} for x in recent]}


# ---------------------------------------------------------------- heads-up: F&O ban, results, new today
def heads_up(today):
    out = {"ban": [], "results": {}}
    try:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
                          "Accept": "*/*", "Referer": "https://www.nseindia.com/"})
        try:
            s.get("https://www.nseindia.com", timeout=15)
        except Exception:
            pass
        try:
            r = s.get("https://nsearchives.nseindia.com/content/fo/fo_secban.csv", timeout=30)
            if r.ok:
                for line in r.text.splitlines()[1:]:
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 2 and parts[1] and parts[1].isupper():
                        out["ban"].append(parts[1])
        except Exception as ex:
            print("site_data: ban list skipped:", ex)
        try:
            r = s.get("https://www.nseindia.com/api/event-calendar", timeout=30)
            if r.ok:
                for ev in r.json():
                    purpose = (ev.get("purpose") or "") + " " + (ev.get("bm_desc") or "")
                    if "result" not in purpose.lower():
                        continue
                    try:
                        d = dt.datetime.strptime(ev.get("date", ""), "%d-%b-%Y").date()
                    except ValueError:
                        continue
                    if 0 <= (d - today).days <= 10:
                        sym = ev.get("symbol")
                        if sym and (sym not in out["results"] or d.isoformat() < out["results"][sym]):
                            out["results"][sym] = d.isoformat()
        except Exception as ex:
            print("site_data: results calendar skipped:", ex)
    except Exception as ex:
        print("site_data: heads-up skipped:", ex)
    return out


def new_today():
    """symbol|setup pairs whose first Ready sighting in the last 10 days is the latest scan day."""
    keys = []
    for f in ("ready_log.csv", "equity_ready_log.csv"):
        try:
            lg = pd.read_csv(os.path.join(DATA, f))
        except Exception:
            continue
        if lg.empty:
            continue
        lg["date"] = pd.to_datetime(lg["date"])
        last = lg["date"].max()
        recent = lg[lg["date"] >= last - pd.Timedelta(days=10)]
        first = recent.groupby(["symbol", "setup"])["date"].min()
        keys += [f"{s}|{st}" for (s, st), d in first.items() if d == last]
    return sorted(set(keys))


# ---------------------------------------------------------------- public pages for Google (data/seo.json)
PUBLIC_REASONS = {"rs", "hi", "mom", "trend", "sector", "oi", "brk"}    # never setups or buy signals


def seo_data(out, fo, sj, uj, oi, px, ctx):
    """Everything the public stock, OI, ban and sector pages need, in one small file.
    No setups, no entry/stop/target levels, no scorecard."""
    if oi is None or len(oi) < 2:
        return None
    n = min(len(oi), 22)
    dates = [str(d) for d in oi.index[-n:]]
    try:
        with open(os.path.join(DATA, "names.json")) as f:
            names = json.load(f)
    except Exception:
        names = {}
    states = {}
    for k in ("rows", "overspill", "ranges"):
        for r in fo.get(k) or []:
            if isinstance(r, dict) and r.get("states"):
                states.setdefault(r["symbol"], r["states"])
    for r in uj.get("rows", []):
        if r.get("states"):
            states.setdefault(r["symbol"], r["states"])
    strong = {x["symbol"]: x for x in strongest_block(sj.get("strongest_fo") or [], ctx, 400)}
    for x in sj.get("strongest_fo") or []:
        states.setdefault(x["symbol"], x.get("states") or {})
    events = {r["symbol"]: [[e.get("when", ""), e.get("text", "")] for e in (r.get("events") or [])[:4]]
              for r in uj.get("rows", [])}
    hu = out.get("heads_up") or {}
    ban, results = set(hu.get("ban") or []), hu.get("results") or {}
    d_oi = (oi.iloc[-1] / oi.iloc[-2] - 1) * 100
    d_px = (px.iloc[-1] / px.iloc[-2] - 1) * 100
    k5 = min(6, len(oi))
    o5 = (oi.iloc[-1] / oi.iloc[-k5] - 1) * 100
    p5 = (px.iloc[-1] / px.iloc[-k5] - 1) * 100
    quads = {k: [] for k, *_ in QUADS}
    stocks = {}
    for s in oi.columns:
        o, p = oi[s].iloc[-n:], px[s].iloc[-n:]
        if pd.isna(o.iloc[-1]) or pd.isna(p.iloc[-1]):
            continue
        q = None
        if pd.notna(d_oi.get(s)) and pd.notna(d_px.get(s)) and abs(d_oi[s]) < 300:
            q = ("long" if d_px[s] > 0 else "short") if d_oi[s] > 0 else ("cover" if d_px[s] > 0 else "unwind")
            quads[q].append({"s": s, "oi": r1(d_oi[s]), "px": r1(d_px[s])})
        st = strong.get(s, {})
        stocks[s] = {
            "n": names.get(s, ""), "sec": SECTORS.get(s, {}).get("sector") or st.get("sector") or "Others",
            "stage": SECTORS.get(s, {}).get("stage"),
            "c": r1(p.iloc[-1], 2), "d": r1(d_px.get(s)), "d5": r1(p5.get(s)),
            "oid": r1(d_oi.get(s)), "oi5": r1(o5.get(s)), "q": q,
            "oi": [None if pd.isna(v) else int(v) for v in o], "px": [None if pd.isna(v) else r1(v, 2) for v in p],
            "st": states.get(s) or {}, "ev": events.get(s, []),
            "rk": st.get("rank"), "sc": st.get("score"), "r1m": st.get("r1m"), "r3m": st.get("r3m"), "hi52": st.get("hi52"),
            "rs": [r["text"] for r in st.get("reasons", []) if r["kind"] in PUBLIC_REASONS],
            "ban": s in ban, "res": results.get(s)}
    for k in quads:
        quads[k].sort(key=lambda x: -abs(x["oi"]))
    sectors = []
    for x in sj.get("sectors") or []:
        row = {k: x.get(k) for k in ("name", "stage", "direction", "rw", "r2w", "r1", "ratio", "mom", "d_bull", "n")}
        row["members"] = sorted([s for s in stocks if stocks[s]["sec"] == x.get("name")], key=lambda s: stocks[s]["rk"] or 999)
        sectors.append(row)
    res = {"generated_ist": out.get("generated_ist"), "oi_asof": dates[-1], "dates": dates,
           "strength": {k: (out.get("strength") or {}).get(k) for k in ("score", "label", "trend")},
           "stocks": stocks, "quads": quads, "building": (out.get("buildup") or {}).get("building", []),
           "sectors": sectors, "ban": sorted(ban), "results": results}
    path = os.path.join(DATA, "seo.json")
    with open(path, "w") as f:
        json.dump(res, f, separators=(",", ":"), allow_nan=False, default=str)
    print(f"site_data: wrote seo.json ({os.path.getsize(path) // 1024} KB, {len(stocks)} stocks)")
    return res


# ---------------------------------------------------------------- live index quotes (NSE) for the dashboard cards
LIVE_NAMES = {"NIFTY": "NIFTY 50", "BANKNIFTY": "NIFTY BANK", "MIDCAP": "NIFTY MIDCAP SELECT", "MIDCAP50": "NIFTY MIDCAP 50",
              "FINNIFTY": "NIFTY FINANCIAL SERVICES", "NEXT50": "NIFTY NEXT 50"}


def live_indexes():
    """Today's index level and change from NSE, so the cards never show yesterday's move during the day."""
    try:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
                          "Accept": "application/json, text/plain, */*", "Referer": "https://www.nseindia.com/option-chain"})
        s.get("https://www.nseindia.com/option-chain", timeout=20)
        r = s.get("https://www.nseindia.com/api/allIndices", timeout=25)
        if not r.ok:
            return {}, None
        j = r.json()
        by = {d.get("index"): d for d in j.get("data", [])}
        out = {}
        for k, n in LIVE_NAMES.items():
            d = by.get(n)
            if d and d.get("last") and d.get("previousClose"):
                out[k] = {"last": float(d["last"]), "prev": float(d["previousClose"]), "chg": float(d.get("percentChange") or 0)}
        return out, j.get("timestamp")
    except Exception as ex:
        print("site_data: live index quotes skipped:", ex)
        return {}, None


# ---------------------------------------------------------------- Top 5 positional (2-4 weeks), F&O only
TOP_LOG = os.path.join(DATA, "top5_log.csv")


def top5(fo, sj, uj, out, five, now):
    """Rank every Ready / Triggered F&O setup on everything the site knows and keep the best five.
    Each pick carries its checklist, so the score can be read line by line."""
    hu = out.get("heads_up") or {}
    ban, results = set(hu.get("ban") or []), hu.get("results") or {}
    strong = {x["symbol"]: x for x in sj.get("strongest_fo") or []}
    unusual = {r["symbol"]: r for r in uj.get("rows", [])}
    lv = out.get("levels") or {}
    track = {x["setup"]: x for x in (out.get("scorecard") or {}).get("setups", []) if x.get("market") == "F&O"}
    try:
        with open(os.path.join(DATA, "fii.json")) as f:
            fii_acc = {x["symbol"]: x for x in json.load(f).get("accumulation", [])}
    except Exception:
        fii_acc = {}
    weather = (out.get("strength") or {}).get("score")
    picks = []
    for r in setup_rows(fo):
        if r.get("stage") not in ("Ready", "Triggered") or r.get("grade") not in ("A", "B"):
            continue
        s = r["symbol"]
        c = card(r, "F&O")
        e, st = c.get("entry"), c.get("stop")
        if not (e and st and e > st) or s in ban:
            continue
        res = results.get(s)
        if res and (dt.date.fromisoformat(res) - now.date()).days <= 14:
            continue                                            # results inside the holding period
        px = c.get("close") or e
        risk = (e - st) / e * 100
        if c["stage"] == "Triggered" and px > e * 1.03:
            continue                                            # already ran away from the entry
        chk = []

        def add(label, pts, ok=None):
            chk.append({"t": label, "p": round(pts, 1), "ok": pts > 0 if ok is None else ok})

        add(f"Grade {c['grade']} {c['setup']} ({c['stage']})", 15 if c["grade"] == "A" else 8)
        tr = track.get(c["setup"])
        if tr and (tr.get("closed") or 0) >= 10 and tr.get("avg_r") is not None:
            add(f"{c['setup']} record: {tr['win_pct']}% wins, {tr['avg_r']:+.2f}R avg ({tr['closed']} closed)", 5 if tr["avg_r"] > 0 else -5)
        stt = c.get("states") or {}
        w, d_, m = stt.get("W"), stt.get("D"), stt.get("M")
        add(f"Weekly {w or 'n/a'}", 10 if w in ("Impulse", "Bullish") else 4 if w == "Bottoming" else -6 if w in ("Bearish", "Topping") else 0)
        add(f"Daily {d_ or 'n/a'}", 6 if d_ in ("Impulse", "Bullish") else 3 if d_ == "Bottoming" else -3 if d_ == "Topping" else -4 if d_ == "Bearish" else 0)
        if m:
            add(f"Monthly {m}", 4 if m in ("Impulse", "Bullish") else -3 if m == "Topping" else -5 if m == "Bearish" else 0)
        sg = strong.get(s)
        if sg:
            add(f"Strength rank {sg['rank']} of {len(strong)} (score {sg['score']})", sg["score"] * 0.15)
            if sg.get("hi52") is not None and sg["hi52"] >= -5:
                add("Within 5% of its 52-week high", 3)
        ss = c.get("sector_stage")
        if ss:
            add(f"{c.get('sector')} sector {ss.lower()}", {"Leading": 8, "Improving": 5, "Weakening": -3, "Lagging": -6}.get(ss, 0))
        L = lv.get(s) or {}
        walls = sorted([(n, (L[k] / px - 1) * 100) for k, n in (("d200", "200 DMA"), ("w20", "20W EMA"), ("w50", "50W EMA"))
                        if L.get(k) and 0 <= (L[k] / px - 1) * 100 <= 4], key=lambda x: x[1])
        if L:
            if walls:
                add(f"{walls[0][0]} only {walls[0][1]:.1f}% above (resistance)", -8 if walls[0][1] <= 2 else -4)
            else:
                add("No 200 DMA / weekly EMA wall within 4%", 6)
            if L.get("d200"):
                add("Above the 200 DMA" if px > L["d200"] else "Below the 200 DMA", 3 if px > L["d200"] else -5)
        f5 = five.get(s) or {}
        if f5.get("oi5") is not None and f5.get("px5") is not None:
            if f5["oi5"] >= 3 and f5["px5"] > 0:
                add(f"Long build-up: futures OI {f5['oi5']:+.0f}% in 5 days", 5)
            elif f5["oi5"] >= 3 and f5["px5"] < 0:
                add(f"Short build-up: futures OI {f5['oi5']:+.0f}% as price fell", -5)
        if s in fii_acc:
            add("Institution-style buying (FII footprint)", 4)
        u = unusual.get(s)
        if u and u.get("bias") in ("Buying", "Selling"):
            add(f"Unusual activity: {u['bias'].lower()}", 3 if u["bias"] == "Buying" else -4)
        vk = c.get("vk")
        if vk:
            add(f"VK {vk.title()}", 4 if vk == "BUY NOW" else 2 if vk == "ALMOST READY (Bullish)" else -4 if "Bearish" in vk or vk == "SELL NOW" else 0)
        if c.get("rr"):
            add(f"Reward {c['rr']}x the risk", 3 if c["rr"] >= 2 else -2)
        add(f"Risk {risk:.1f}% to the stop", 3 if risk <= 5 else 0 if risk <= 8 else -5)
        if c.get("ext") is not None and c["ext"] > 4:
            add(f"Stretched: {c['ext']} ranges above the 50 EMA", -4)
        score = round(sum(x["p"] for x in chk), 1)
        picks.append({**{k: c.get(k) for k in ("symbol", "setup", "stage", "grade", "close", "entry", "stop", "target", "rr",
                                                 "target_word", "sector", "sector_stage", "states", "vk", "detail")},
                      "risk_pct": round(risk, 1), "score": score, "checks": sorted([x for x in chk if x["p"]], key=lambda x: -x["p"]),
                      "cautions": [x["t"] for x in chk if x["p"] < 0]})
    best = {}
    for p in sorted(picks, key=lambda x: -x["score"]):
        best.setdefault(p["symbol"], p)                         # one setup per stock
    top = list(best.values())[:5]
    note = None
    if weather is not None and weather < 45:
        note = f"Weak market (weather {weather}): positional longs have lower odds; size down or wait."
    try:                                                         # keep a daily record to check later how the picks did
        day = now.strftime("%Y-%m-%d")
        log = pd.read_csv(TOP_LOG) if os.path.exists(TOP_LOG) else pd.DataFrame(columns=["date"])
        if not (log["date"].astype(str) == day).any() and top:
            new = pd.DataFrame([{"date": day, "rank": i + 1, "symbol": p["symbol"], "setup": p["setup"], "score": p["score"],
                                 "entry": p["entry"], "stop": p["stop"], "target": p["target"], "close": p["close"]} for i, p in enumerate(top)])
            pd.concat([log, new], ignore_index=True).to_csv(TOP_LOG, index=False)
    except Exception as ex:
        print("site_data: top5 log skipped:", ex)
    return {"picks": top, "considered": len(best), "weather": weather, "note": note, "at": now.strftime("%Y-%m-%d %H:%M")}


# ---------------------------------------------------------------- build
def run(now=None):
    global SECTORS
    now = now or dt.datetime.now(IST)
    fo, eq, bo = jload("setups.json"), jload("equity.json"), jload("breakout.json")
    sj, ij, uj = jload("sectors.json"), jload("index.json"), jload("unusual.json")
    ue = jload("unusual_equity.json")
    if not fo:
        print("site_data: no setups.json, skipped")
        return None
    SECTORS = sj.get("map", {})
    m = fo.get("market", {})
    oi, px = oi_frame()
    quads = quadrants(oi, px)
    moves, five = day_moves(oi, px)
    ixs = ij.get("indexes", [])
    nifty = next((x for x in ixs if x.get("key") == "NIFTY"), None)

    # market strength
    st_ = strength(m, nifty, sj.get("sectors", []), quads)
    if st_:
        st_["history"] = strength_history(now.strftime("%Y-%m-%d"), st_["score"])

    # indexes
    n20 = pct_over((nifty or {}).get("spark"), 20)
    indexes = []
    for x in ixs:
        if "close" not in x:
            continue
        op, fr = x.get("options") or {}, x.get("futures") or {}
        r20 = pct_over(x.get("spark"), 20)
        indexes.append({
            "key": x["key"], "name": x["name"], "close": x["close"], "chg1": x.get("chg1"),
            "chg5": x.get("chg5"), "spark": [round(v, 2) for v in (x.get("spark") or [])],
            "stage": x.get("stage"), "states": x.get("states"),
            "vk": (x.get("vk") or {}).get("final"),
            "fut": (fr.get("week") or {}).get("word"), "fut_side": (fr.get("week") or {}).get("side"),
            "pcr": op.get("pcr"), "max_pain": op.get("max_pain"), "spot": op.get("spot"),
            "res": (op.get("resistance") or [{}])[0].get("strike"),
            "sup": (op.get("support") or [{}])[0].get("strike"),
            "rs20": r1(r20 - n20) if r20 is not None and n20 is not None and x["key"] != "NIFTY" else None,
            "r20": r1(r20), "note": x.get("chart_note")})

    live, live_at = live_indexes()
    for x in indexes:
        q = live.get("MIDCAP50" if x["key"] == "MIDCAP" and x.get("note") else x["key"])   # chart is Midcap 50 when Yahoo lacks Midcap Select
        if not q:
            continue
        sp = x["spark"]
        prev_day = abs(q["prev"] / sp[-1] - 1) < 0.002 if sp else False      # spark ends at yesterday's close
        x["close"], x["chg1"], x["live_at"] = round(q["last"], 2), r1(q["chg"], 2), live_at
        if prev_day:
            x["spark"] = sp + [round(q["last"], 2)]
            if len(sp) >= 5:
                x["chg5"] = r1((q["last"] / sp[-5] - 1) * 100)

    # strategies and setups
    data = {"F&O": fo, "Equity": eq, "Breakout": bo,
            "Unusual F&O": {r["symbol"]: r for r in uj.get("rows", [])},
            "Unusual Equity": {r["symbol"]: r for r in ue.get("rows", [])}}
    sec_table = {s["name"]: s for s in sj.get("sectors", [])}
    try:
        res = strategy_picks({k: v for k, v in data.items() if v}, sec_table)
    except Exception as ex:                       # never let the dashboard break the scan
        print("site_data: strategies failed:", ex)
        res = {}
    picks = []
    for n in sorted(res):
        for p in res[n]["picks"]:
            c = card(p["row"], p["market"])
            c.update(levels(p["row"], p["entry"], p["stop"]))
            c.update({"strategy": n, "strategy_name": STRAT_RULES[n][0], "size": p["size"],
                      "checks": p["checks"], "bonus": p["bonus"]})
            picks.append(c)
    gates = {n: {"name": STRAT_RULES[n][0], "open": v.get("open"), "gate": v.get("gate"),
                 "count": len(v["picks"])} for n, v in sorted(res.items())}

    def strat_card(p):
        c = card(p["row"], p["market"])
        c.update(levels(p["row"], p["entry"], p["stop"]))
        c.update({"size": p["size"], "checks": p["checks"], "bonus": p["bonus"]})
        return c
    strategies = []
    for n in (1, 2, 4, 7, 3, 5, 6):
        if n not in res:
            continue
        v, rule = res[n], STRAT_RULES[n]
        q = v.get("queue") or {}
        strategies.append({
            "n": n, "name": rule[0], "horizon": rule[1], "idea": rule[2], "rules": rule[3],
            "exit": rule[4], "open": v.get("open"), "gate": v.get("gate"),
            "main": n in (1, 2, 4, 7),
            "picks": [strat_card(p) for p in v["picks"]],
            "blocked": [strat_card(p) for p in v["blocked"]],
            "dipping": [card(r, "F&O" if n == 7 else r.get("setup", "").split(" · ")[0])
                        for r in q.get("dipping", [])][:20],
            "waiting": [{"symbol": s_, **b} for s_, b in q.get("waiting", [])][:30],
            "queue_note": q.get("caption", "")})
    rank = {"A": 0, "B": 1, "C": 2}
    ready = sorted([card(r, mk) for mk, d in (("F&O", fo), ("Equity", eq)) for r in setup_rows(d)
                    if r.get("stage") == "Ready" and r.get("grade") in ("A", "B")],
                   key=lambda c: (rank.get(c["grade"], 9), -(c.get("rr") or 0)))
    ready_map = {}
    for c in ready:
        ready_map.setdefault(c["symbol"], c["setup"])
    vk = {x["symbol"]: lab for lab, xs in fo.get("vk_labels", {}).items() for x in xs}
    vk_count = {lab: len(xs) for lab, xs in fo.get("vk_labels", {}).items()}

    # strongest
    ctx = {"five": five, "ready": ready_map, "vk": vk, "brk": fo.get("recent_breakouts", {})}
    strong_fo = strongest_block(sj.get("strongest_fo") or [], ctx, 40)
    strong_n50 = strongest_block(sj.get("strongest_n50") or [], ctx, 50)

    # sectors
    sectors = [{"name": s["name"], "stage": s["stage"], "rw": s.get("rw"), "r2w": s.get("r2w"),
                "r1": s.get("r1"), "direction": s.get("direction"), "d_bull": s.get("d_bull"),
                "chg5": s.get("chg5"), "n": s.get("n"),
                "top": [t["symbol"] for t in (s.get("top") or [])[:3]]}
               for s in sj.get("sectors", [])]

    # heatmap by sector, gainers and losers
    heat = {}
    for r in moves:
        heat.setdefault(r["sector"], []).append(r)
    heatmap = sorted(({"sector": k, "avg": r1(sum(x["chg"] for x in v) / len(v), 2),
                       "stocks": sorted(v, key=lambda x: -x["chg"])} for k, v in heat.items()),
                     key=lambda g: -len(g["stocks"]))
    by_chg = sorted(moves, key=lambda x: x["chg"])
    adv = sum(1 for x in moves if x["chg"] > 0)

    # big money
    pb = uj.get("buildup") or {}
    squeeze = [{"symbol": x["symbol"], "stage": x["stage"], "close": x.get("close"),
                "short_oi": x.get("short_oi"), "kept": x.get("kept"), "above": x.get("above")}
               for x in uj.get("squeeze") or []]

    out = {
        "generated_ist": fo.get("generated_ist"), "built_ist": now.strftime("%Y-%m-%d %H:%M"),
        "oi_asof": fo.get("oi_asof"),
        "market": {"turn": m.get("turn"), "regime": m.get("regime"), "vix": m.get("vix"),
                   "weekly_bull_pct": m.get("weekly_bull_pct"),
                   "daily_bull_hist": m.get("daily_bull_hist"),
                   "gate": gate_of(m)["word"], "nifty": (m.get("nifty") or {}).get("state"),
                   "adv": adv, "dec": len(moves) - adv},
        "strength": st_, "indexes": indexes,
        "picks": picks, "gates": gates, "strategies": strategies, "ready": ([c for c in ready if c["market"] == "F&O"][:18] + [c for c in ready if c["market"] != "F&O"][:12]),
        "ready_count": len(ready), "ready_fo": sum(1 for c in ready if c["market"] == "F&O"),
        "vk_count": vk_count,
        "strongest_fo": strong_fo, "strongest_n50": strong_n50,
        "sectors": sectors, "heatmap": heatmap,
        "gainers": by_chg[::-1][:8], "losers": by_chg[:8],
        "quadrants": quads,
        "buildup": {"building": pb.get("building", []), "starting": pb.get("starting", [])},
        "squeeze": squeeze,
        "ready_syms": sorted(ready_map),
        "closes": {r["symbol"]: r["close"] for r in moves},
        "vk": vk,
    }
    syms = [c["symbol"] for c in picks] + [c["symbol"] for c in out["ready"]]
    syms += [c["symbol"] for st in strategies for k in ("picks", "blocked", "dipping") for c in st[k]]
    out["candles"] = card_candles(syms)
    try:
        out["scorecard"] = scorecard(now.date())
    except Exception as ex:
        print("site_data: scorecard failed:", ex)
    try:
        hu = heads_up(now.date())
        hu["results"] = {k: v for k, v in hu["results"].items() if k in out["closes"]}   # F&O stocks only
        out["heads_up"] = hu
    except Exception as ex:
        print("site_data: heads-up failed:", ex)
    out["new"] = new_today()
    lv = dict((eq or {}).get("levels") or {})
    lv.update(fo.get("levels") or {})
    out["levels"] = lv                                      # 200 DMA, 20/50 week EMA per stock
    try:
        out["top5"] = top5(fo, sj, uj, out, five, now)
    except Exception as ex:
        print("site_data: top5 failed:", ex)
    with open(OUT, "w") as f:
        json.dump(out, f, separators=(",", ":"), allow_nan=False, default=str)
    write_names()
    try:
        seo_data(out, fo, sj, uj, oi, px, ctx)
    except Exception as ex:
        print("site_data: seo pages data failed:", ex)
    print(f"site_data: wrote {OUT} ({os.path.getsize(OUT) // 1024} KB), "
          f"strength {st_['score'] if st_ else 'n/a'}, {len(picks)} picks, {len(ready)} ready")
    return out


if __name__ == "__main__":
    run()
