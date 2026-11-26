#!/usr/bin/env python3
"""
TradeReady - the bearish side (F&O only: shorts go through futures or puts).

Short setups on completed daily bars, the mirror of the long ones:
  Bear Pullback   Weekly down. A down-leg of 3+ ATR, then a rally that gives back 20-75% of it
                  into the 20-50 EMA zone on lighter volume. Watch while the rally is still rising,
                  Ready when daily momentum turns down, Triggered on a close under the rally's low.
  Breakdown       Weekly not bullish. Price sitting within 1 ATR above its 55-day low with the range
                  narrowing: Ready. A close under that low on 1.3x+ volume in the last 3 sessions: Triggered.
  Bear Squeeze    Weekly momentum below zero. Daily squeeze on in the last 5 bars and momentum below
                  zero and falling: Ready. Squeeze released with momentum still falling: Triggered.
Grade: A when volume and futures OI both agree (light volume in the rally / heavy in the breakdown,
and OI rising on the way down without long build-up in the rally), B one of the two, C neither.

Weakest stocks: the mirror of Strongest (3-month and 1-month return vs Nifty, distance above the
52-week low, below the 200 DMA, weekly trend).
Bottom 5 positional: the five best shorts for 1-3 weeks, scored on every bearish signal.

Output: data/bear.json, data/bear_log.csv (first-of-day picks, to test later).
  python bear_scan.py             # live run
  python bear_scan.py --selftest  # offline check
"""
import datetime as dt
import importlib.util
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, file))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def r1(x, n=1):
    try:
        return None if x is None or not np.isfinite(x) else round(float(x), n)
    except Exception:
        return None


def jload(name):
    try:
        with open(os.path.join(DATA, name)) as f:
            return json.load(f)
    except Exception:
        return {}


def oi_move(oi, sym, d0, d1):
    """% change of futures OI and price between two dates (None if missing)."""
    if oi is None or sym not in oi.columns:
        return None
    s = oi[sym].dropna()
    a, b = s[s.index <= d0], s[s.index <= d1]
    if not len(a) or not len(b) or a.iloc[-1] <= 0:
        return None
    return (b.iloc[-1] / a.iloc[-1] - 1) * 100


# ------------------------------------------------------------------ setups
def bear_setups(E, sym, df, oi):
    """All short setups for one stock. df: completed daily bars."""
    if df is None or len(df) < 120:
        return []
    h, l, c = (df[x].values.astype(float) for x in ("High", "Low", "Close"))
    v = df["Volume"].values.astype(float)
    ind = E.indicators(h, l, c)
    atr, e20, e50, mom, sq = ind["atr"], ind["ema20"], ind["ema50"], ind["mom"], ind["sq"]
    W = E.tf_view(E.to_weekly(df), 3)
    M = E.tf_view(E.to_monthly(df), 1)
    if not W["ok"] or np.isnan(atr[-1]) or atr[-1] <= 0:
        return []
    a = atr[-1]
    dates = df.index
    states = {"M": ("Down impulse" if M.get("mom", 0) < 0 and M["state"] == "Bearish" else M["state"]) if M["ok"] else "No data",
              "W": W["state"], "D": E.state_name(mom[-1], mom[-2])}
    out = []

    def grade(vol_ok, oi_ok, warn=False):
        return "Warning" if warn else "ABC"[2 - int(bool(vol_ok)) - int(bool(oi_ok))]

    # --- Bear Pullback
    if W["mom"] < 0:
        look = 40
        lo_i = len(c) - look + int(np.argmin(l[-look:]))          # the down-leg's low
        hi_i = max(0, lo_i - 60) + int(np.argmax(h[max(0, lo_i - 60):lo_i + 1]))   # the top it fell from
        if lo_i < len(c) - 3 and hi_i < lo_i:
            leg = h[hi_i] - l[lo_i]
            rally_hi_i = lo_i + int(np.argmax(h[lo_i:]))
            depth = (h[rally_hi_i] - l[lo_i]) / leg if leg > 0 else 0
            in_zone = h[rally_hi_i] >= e20[rally_hi_i] - 0.2 * a and c[-1] <= e50[-1] + 0.5 * a
            if leg >= 3 * a and 0.2 <= depth <= 0.75 and in_zone and e20[-1] < e50[-1] * 1.01:
                leg_v = np.nanmean(v[hi_i:lo_i + 1]) if lo_i > hi_i else np.nan
                rally_v = np.nanmean(v[lo_i + 1:]) if lo_i + 1 < len(v) else np.nan
                vol_ok = bool(leg_v and rally_v and rally_v <= 0.85 * leg_v)
                oi_leg = oi_move(oi, sym, dates[hi_i], dates[lo_i])
                oi_rally = oi_move(oi, sym, dates[lo_i], dates[-1])
                oi_ok = bool(oi_leg is not None and oi_leg > 1)
                warn = bool(oi_rally is not None and oi_rally >= 3 and c[-1] > c[lo_i] * 1.005)   # longs building into the rally
                prev_lo = float(np.min(l[-4:-1]))                     # the last 3 sessions before today
                if mom[-1] < mom[-2] and c[-1] < prev_lo and rally_hi_i < len(c) - 1:
                    stage, trig = "Triggered", prev_lo
                elif mom[-1] < mom[-2]:
                    stage, trig = "Ready", float(np.min(l[-3:]))
                else:
                    stage, trig = "Watch", float(np.min(l[-3:]))
                stop = h[rally_hi_i] + 0.2 * a
                out.append({"setup": "Bear Pullback", "stage": stage, "grade": grade(vol_ok, oi_ok, warn),
                            "entry": trig, "stop": stop, "target_level": float(l[lo_i]),
                            "detail": f"Rally gave back {depth * 100:.0f}% of the fall · Volume {rally_v / leg_v:.1f}x in rally" if leg_v and rally_v else f"Rally gave back {depth * 100:.0f}% of the fall",
                            "oi": f"OI {oi_leg:+.0f}% in the fall" + (f", {oi_rally:+.0f}% in the rally" if oi_rally is not None else "") if oi_leg is not None else "OI n/a"})

    # --- Breakdown
    if W["state"] in ("Bearish", "Topping", "Bottoming") and not W["impulse"]:
        base = l[-56:-1]
        floor = float(np.min(base))
        touches = int(np.sum(np.abs(l[-56:-1] - floor) <= 0.35 * a))
        narrowing = np.nanmean(atr[-10:]) < np.nanmean(atr[-40:-10])
        avg_v = np.nanmean(v[-23:-3])
        broke = [i for i in range(len(c) - 3, len(c)) if c[i] < floor and v[i] >= 1.3 * avg_v]
        stage = None
        if broke and c[-1] < floor:
            stage = "Triggered" if c[-1] > floor - 2.5 * a else None
        elif c[-1] - floor <= a and touches >= 2 and narrowing:
            stage = "Ready"
        if stage:
            vol_ok = bool(broke) if stage == "Triggered" else bool(np.nanmean(v[-10:]) <= avg_v * 1.05)
            oi5 = oi_move(oi, sym, dates[-6], dates[-1])
            oi_ok = bool(oi5 is not None and oi5 > 1)
            height = float(np.max(h[-56:-1]) - floor)
            out.append({"setup": "Breakdown", "stage": stage, "grade": grade(vol_ok, oi_ok),
                        "entry": floor, "stop": floor + 1.5 * a, "target_level": floor - min(height, 4 * a),
                        "detail": f"55-day floor ₹{floor:,.1f} touched {touches}x · range {'narrowing' if narrowing else 'steady'}",
                        "oi": f"OI {oi5:+.0f}% in 5 days" if oi5 is not None else "OI n/a"})

    # --- Bear Squeeze
    if W["mom"] < 0 and sq[-6:-1].any() and mom[-1] < 0 and mom[-1] < mom[-2]:
        stage = "Ready" if sq[-1] else "Triggered"
        dn, up = 0.0, 0.0
        chg = np.diff(c[-11:])
        dn, up = v[-10:][chg < 0].sum(), v[-10:][chg > 0].sum()
        vol_ok = bool(up > 0 and dn >= 1.2 * up)
        oi5 = oi_move(oi, sym, dates[-6], dates[-1])
        oi_ok = bool(oi5 is not None and oi5 > 2 and c[-1] < c[-6])
        out.append({"setup": "Bear Squeeze", "stage": stage, "grade": grade(vol_ok, oi_ok),
                    "entry": float(np.min(l[-5:])), "stop": float(np.max(h[-5:])) + 0.1 * a, "target_level": None,
                    "detail": f"Squeeze {int(sq[-20:].sum())} of 20 days · down/up volume {dn / up:.1f}x" if up else "Squeeze firing down",
                    "oi": f"OI {oi5:+.0f}% in 5 days" if oi5 is not None else "OI n/a"})

    rows = []
    for x in out:
        e, s = x["entry"], x["stop"]
        risk = s - e
        if risk <= 0:
            continue
        tl = x.pop("target_level")
        tgt = e - 2 * risk if tl is None or e - tl < 2 * risk else tl
        rows.append({"symbol": sym, "close": r1(c[-1], 2), **x, "entry": r1(e, 2), "stop": r1(s, 2), "target": r1(tgt, 2),
                     "rr": r1((e - tgt) / risk), "risk_pct": r1(risk / e * 100), "states": states,
                     "ext": r1((c[-1] - e20[-1]) / a), "d_bear": bool(mom[-1] < 0)})
    return rows


# ------------------------------------------------------------------ weakest
def weakest(daily, nifty, sectors):
    rows = []
    n = nifty["Close"].dropna()
    for s, df in daily.items():
        c = df["Close"].dropna()
        if len(c) < 200:
            continue
        j = c.index.intersection(n.index)
        if len(j) < 130:
            continue
        cc, nn = c.loc[j], n.loc[j]
        r3 = (cc.iloc[-1] / cc.iloc[-64] - 1) * 100 - (nn.iloc[-1] / nn.iloc[-64] - 1) * 100
        r1m = (cc.iloc[-1] / cc.iloc[-22] - 1) * 100 - (nn.iloc[-1] / nn.iloc[-22] - 1) * 100
        lo52, hi52 = cc.iloc[-250:].min(), cc.iloc[-250:].max()
        above_lo = (cc.iloc[-1] / lo52 - 1) * 100
        sma200 = cc.rolling(200).mean().iloc[-1]
        rows.append({"symbol": s, "close": r1(cc.iloc[-1], 2), "r3": r1(r3), "r1m": r1(r1m), "lo52": r1(above_lo),
                     "hi52": r1((cc.iloc[-1] / hi52 - 1) * 100), "b200": bool(cc.iloc[-1] < sma200),
                     "sector": (sectors.get(s) or {}).get("sector"), "sector_stage": (sectors.get(s) or {}).get("stage"),
                     "_raw": -r3 * 0.6 - r1m * 0.4 + max(0, 15 - above_lo) * 0.8 + (8 if cc.iloc[-1] < sma200 else -8)})
    if not rows:
        return []
    raw = np.array([r["_raw"] for r in rows])
    pr = pd.Series(raw).rank(pct=True).values
    for r, p in zip(rows, pr):
        r["score"] = int(round(p * 100))
        r.pop("_raw")
        why = []
        if r["r3"] is not None and r["r3"] < 0:
            why.append(f"{r['r3']:+.0f}% vs Nifty in 3 months")
        if r["lo52"] is not None and r["lo52"] <= 5:
            why.append("At its 52-week low" if r["lo52"] <= 1 else f"{r['lo52']:.0f}% above 52-week low")
        if r["b200"]:
            why.append("Below the 200 DMA")
        if r["sector_stage"] in ("Lagging", "Weakening"):
            why.append(f"{r['sector']} sector {r['sector_stage'].lower()}")
        r["why"] = why
    rows.sort(key=lambda r: -r["score"])
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    return rows


# ------------------------------------------------------------------ Bottom 5
def bottom5(setups, weak, daily, ctx):
    wk = {r["symbol"]: r for r in weak}
    picks = []
    for x in setups:
        if x["stage"] not in ("Ready", "Triggered") or x["grade"] not in ("A", "B"):
            continue
        s = x["symbol"]
        if s in ctx["ban"]:
            continue
        res = ctx["results"].get(s)
        if res:
            try:
                if (dt.date.fromisoformat(res) - ctx["today"]).days <= 14:
                    continue
            except Exception:
                pass
        if x["stage"] == "Triggered" and x["close"] < x["entry"] * 0.97:
            continue                                       # already fell well past the entry
        chk = []

        def add(t, p):
            if p:
                chk.append({"t": t, "p": round(p, 1)})

        add(f"Grade {x['grade']} {x['setup']} ({x['stage']})", 15 if x["grade"] == "A" else 8)
        st = x["states"]
        add(f"Weekly {st['W']}", {"Bearish": 10, "Topping": 4, "Bottoming": -3, "Bullish": -8}.get(st["W"], 0))
        add(f"Monthly {st['M']}", {"Bearish": 5, "Down impulse": 5, "Topping": 2, "Bullish": -5}.get(st["M"], 0))
        add(f"Daily {st['D']}", {"Bearish": 5, "Topping": 3, "Bottoming": -3, "Bullish": -4}.get(st["D"], 0))
        w = wk.get(s)
        if w:
            add(f"Weakest rank {w['rank']} (score {w['score']})", (w["score"] - 50) * 0.25)
            if w["b200"]:
                add("Below the 200 DMA", 4)
        ss = (ctx["sectors"].get(s) or {}).get("stage")
        if ss:
            add(f"Sector {ss.lower()}", {"Lagging": 8, "Weakening": 5, "Improving": -4, "Leading": -8}.get(ss, 0))
        f5 = ctx["five"].get(s)
        if f5 and f5.get("oi5") is not None and f5.get("px5") is not None:
            if f5["oi5"] >= 3 and f5["px5"] < 0:
                add(f"Short build-up: OI {f5['oi5']:+.0f}% in 5 days", 6)
            elif f5["oi5"] >= 3 and f5["px5"] > 0:
                add(f"Long build-up: OI {f5['oi5']:+.0f}% while price rose", -6)
        if s in ctx["fii_dist"]:
            add("FII-style selling (FII footprint)", 4)
        if s in ctx["fii_acc"]:
            add("FII-style buying (FII footprint)", -5)
        u = ctx["unusual"].get(s)
        if u == "Selling":
            add("Unusual activity: selling", 3)
        elif u == "Buying":
            add("Unusual activity: buying", -4)
        vk = ctx["vk"].get(s)
        if vk == "SELL NOW":
            add("VK Sell now", 4)
        elif vk == "ALMOST READY (Bearish)":
            add("VK Almost ready (bearish)", 2)
        elif vk in ("BUY NOW", "ALMOST READY (Bullish)"):
            add("VK bullish", -4)
        lv = ctx["levels"].get(s) or {}
        sup = [(n, (1 - lv[k] / x["close"]) * 100) for k, n in (("d200", "200 DMA"), ("w20", "20W EMA"), ("w50", "50W EMA"))
               if lv.get(k) and 0 <= (1 - lv[k] / x["close"]) * 100 <= 4]
        if sup:
            add(f"{sup[0][0]} only {sup[0][1]:.1f}% below (support)", -6)
        if x.get("rr"):
            add(f"Reward {x['rr']}x the risk", 3 if x["rr"] >= 2 else -2)
        add(f"Risk {x['risk_pct']}% to the stop", 3 if x["risk_pct"] <= 5 else 0 if x["risk_pct"] <= 8 else -5)
        if x.get("ext") is not None and x["ext"] < -3:
            add(f"Already {abs(x['ext'])} ranges under the 20 EMA (oversold)", -5)
        score = round(sum(c["p"] for c in chk), 1)
        picks.append({**{k: x[k] for k in ("symbol", "setup", "stage", "grade", "close", "entry", "stop", "target", "rr", "risk_pct", "states", "detail")},
                      "sector": (ctx["sectors"].get(s) or {}).get("sector"), "sector_stage": ss, "score": score,
                      "checks": sorted(chk, key=lambda c: -c["p"]), "cautions": [c["t"] for c in chk if c["p"] < 0]})
    best = {}
    for p in sorted(picks, key=lambda p: -p["score"]):
        best.setdefault(p["symbol"], p)
    return list(best.values())[:5], len(best)


# ------------------------------------------------------------------ run
def main():
    E = _load("setups_scan", "setups_scan.py")
    now = dt.datetime.now(IST)
    fo = pd.read_csv(os.path.join(DATA, "fo_oi.csv.gz"), parse_dates=["date"])
    syms = sorted(fo.loc[fo["date"] == fo["date"].max(), "symbol"])
    oi = fo.pivot_table(index="date", columns="symbol", values="fut_oi").sort_index()
    daily = E.fetch_bars(syms, period="2y", interval="1d")
    nd = E.yf_download(["^NSEI"], period="2y", interval="1d")
    nifty = (nd["^NSEI"] if isinstance(nd.columns, pd.MultiIndex) else nd).dropna()
    nifty.index = pd.to_datetime(nifty.index)
    if nifty.index.tz is not None:
        nifty.index = nifty.index.tz_localize(None)
    cut = pd.Timestamp(now.date())
    if (now.hour, now.minute) < (15, 40):                 # completed days only
        daily = {s: d[d.index < cut] for s, d in daily.items()}
        nifty = nifty[nifty.index < cut]
    print(f"daily bars: {len(daily)} stocks")
    site, sj, uj, fii, setups = jload("site.json"), jload("sectors.json"), jload("unusual.json"), jload("fii.json"), jload("setups.json")
    sectors = sj.get("map") or {}
    rows = []
    for s, df in daily.items():
        try:
            rows += bear_setups(E, s, df, oi)
        except Exception as ex:
            print(s, "failed:", ex)
    order = {"A": 0, "B": 1, "C": 2, "Warning": 3}
    rows.sort(key=lambda r: (order[r["grade"]], {"Triggered": 0, "Ready": 1, "Watch": 2}[r["stage"]], r["symbol"]))
    weak = weakest(daily, nifty, sectors)
    hu = site.get("heads_up") or {}
    # 5-day OI and price change
    five = {}
    px = fo.pivot_table(index="date", columns="symbol", values="fut_close").sort_index()
    if len(oi) >= 6:
        o5 = (oi.iloc[-1] / oi.iloc[-6] - 1) * 100
        p5 = (px.iloc[-1] / px.iloc[-6] - 1) * 100
        five = {s: {"oi5": r1(o5.get(s)), "px5": r1(p5.get(s))} for s in o5.dropna().index}
    vk = {}
    for lab, items in (setups.get("vk_labels") or {}).items():
        for x in items:
            vk[x["symbol"]] = lab
    ctx = {"ban": set(hu.get("ban") or []), "results": hu.get("results") or {}, "today": now.date(), "sectors": sectors, "five": five,
           "fii_dist": {x["symbol"] for x in fii.get("distribution") or []}, "fii_acc": {x["symbol"] for x in fii.get("accumulation") or []},
           "unusual": {r["symbol"]: r.get("bias") for r in uj.get("rows") or []}, "vk": vk, "levels": site.get("levels") or {}}
    b5, considered = bottom5(rows, weak, daily, ctx)
    weather = (site.get("strength") or {}).get("score")
    turn = (site.get("market") or {}).get("turn")
    res = {"generated_ist": now.strftime("%Y-%m-%d %H:%M"), "asof": str(max(d.index[-1] for d in daily.values()).date()) if daily else None,
           "bear_mode": bool((weather is not None and weather < 40) or turn in ("Falling", "Still falling")), "weather": weather, "turn": turn,
           "setups": rows, "counts": {st: sum(1 for r in rows if r["stage"] == st) for st in ("Ready", "Triggered", "Watch")},
           "weakest": weak[:60], "bottom5": {"picks": b5, "considered": considered}}
    with open(os.path.join(DATA, "bear.json"), "w") as f:
        json.dump(res, f, separators=(",", ":"), allow_nan=False, default=str)
    try:
        path, day = os.path.join(DATA, "bear_log.csv"), now.strftime("%Y-%m-%d")
        log = pd.read_csv(path) if os.path.exists(path) else pd.DataFrame(columns=["date"])
        if not (log["date"].astype(str) == day).any() and b5:
            new = pd.DataFrame([{"date": day, "rank": i + 1, "symbol": p["symbol"], "setup": p["setup"], "score": p["score"],
                                 "entry": p["entry"], "stop": p["stop"], "target": p["target"], "close": p["close"]} for i, p in enumerate(b5)])
            pd.concat([log, new], ignore_index=True).to_csv(path, index=False)
    except Exception as ex:
        print("bear log skipped:", ex)
    print(f"bear: {len(rows)} setups ({res['counts']}), weakest {len(weak)}, bottom 5 from {considered}, bear mode {res['bear_mode']}")


# ------------------------------------------------------------------ offline check
def selftest():
    E = _load("setups_scan", "setups_scan.py")
    idx = pd.bdate_range("2025-01-01", periods=260)
    # long downtrend, a down-leg, then a rally into the 20 EMA that turns down
    base = np.r_[np.linspace(200, 150, 180), np.linspace(150, 120, 50), np.linspace(120, 132, 25), [131, 129.5, 128, 127, 126]]
    c = pd.Series(base[:260], index=idx)
    df = pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c,
                       "Volume": np.r_[np.full(230, 2e6), np.full(30, 1e6)]}, index=idx)
    rows = bear_setups(E, "DOWN", df, None)
    names = {(r["setup"], r["stage"]) for r in rows}
    assert any(n[0] == "Bear Pullback" for n in names), names
    bp = next(r for r in rows if r["setup"] == "Bear Pullback")
    assert bp["stop"] > bp["entry"] > bp["target"] and bp["rr"] >= 1, bp
    n = pd.DataFrame({"Close": np.linspace(100, 110, 260)}, index=idx)
    up = pd.DataFrame({"Close": np.linspace(100, 160, 260)}, index=idx)
    w = weakest({"DOWN": df, "UP": up}, n, {})
    assert w[0]["symbol"] == "DOWN" and w[0]["b200"], w
    ctx = {"ban": set(), "results": {}, "today": dt.date(2026, 1, 1), "sectors": {}, "five": {}, "fii_dist": set(), "fii_acc": set(),
           "unusual": {}, "vk": {}, "levels": {}}
    fake = dict(bp, stage="Ready", grade="A")
    b5, k = bottom5([fake], w, {}, ctx)
    assert b5 and b5[0]["symbol"] == "DOWN", b5
    print("selftest ok")


if __name__ == "__main__":
    selftest() if "--selftest" in sys.argv else main()
