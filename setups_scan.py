#!/usr/bin/env python3
"""
TrendFire Setups - scan engine (NSE F&O stocks).

One engine for all four timeframes. Each stock gets:
  setup  : Squeeze Break | Swing Pullback | Trend Pullback
           plus a separate list: Weekly | Daily | Hourly Overspill (20-50 EMA zone)
  stage  : Watch | Ready | Triggered   (else Running | Stretched | Fading)
  trend  : With trend | Early turn | Counter-trend      (from Monthly)
  grade  : A | B | C | Warning                          (volume + OI, last close)

Data
  F&O list + futures OI : NSE daily F&O bhavcopy (kept in data/fo_oi.csv.gz)
  Daily / Weekly / Monthly : yfinance daily bars (completed days only)
  Hourly                   : yfinance 1h bars (completed bars only)

Outputs
  data/setups.json     read by app.py
  data/ready_log.csv   every Ready sighting, for the later scorecard

Usage
  python setups_scan.py             # live run
  python setups_scan.py --selftest  # offline logic check, no network
"""
import argparse
import datetime as dt
import io
import json
import math
import os
import time
import zipfile

import numpy as np
import pandas as pd

CFG = {
    "data_dir": "data",
    "n": 20, "bb_mult": 2.0, "kc_mult": 1.5,          # TTM squeeze
    "hourly": {"look_high": 35, "look_leg": 49, "min_bars": 3},
    "daily": {"look_high": 30, "look_leg": 40, "min_bars": 3},
    "depth_min": 0.20, "depth_max": 0.75,              # pullback as share of up-leg
    "leg_min_atr": 3.0,                                # up-leg must be this many ATRs
    "vol_dry": 0.85,                                   # pullback volume vs up-leg volume
    "updown_vol": 1.2,                                 # squeeze: up-day vs down-day volume
    "oi_warn": 0.03, "px_warn": -0.005,                # short build-up in a pullback
    "oi_build": 0.03,                                  # squeeze: OI build-up over 5 days
    "spill_bars": 8,                                   # overspill: touch of the 20 EMA within this many bars
    "spill_from": 1.5,                                 # ... after trading this many ATRs above the 20 EMA
    "spill_deep": 3.0,                                 # ... and no further than this many ATRs under the 50 EMA
    "stretch_atr": 3.0,                                # close this many ATRs above EMA20
    "healthy": 60, "weak": 40,                         # % of stocks bullish on Weekly
    "range": {   # big ranges with a falling or flat ceiling (Range Breakout)
        "daily":  {"window": 160, "pivot": 4, "min_span": 25, "fresh": 5},
        "hourly": {"window": 380, "pivot": 6, "min_span": 90, "fresh": 14},
        "touch_atr": 0.35,       # a swing high this close to the line counts as a touch
        "min_touches": 3,
        "height_pct": (3.0, 30.0),   # range depth, % of price
        "ready_atr": 1.0,        # Ready when the close is this close under the ceiling
        "late_atr": 2.5,         # a breakout further away than this is no longer fresh
    },
    "ext_atr": 4.0,         # this many daily ranges above the 50 EMA = Extended
    "turn_pts": 5,          # Daily breadth change over 3 sessions that counts as a turn
    "fo_backfill_days": 40, "fo_keep_days": 120,
}
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
FO_URL = ("https://nsearchives.nseindia.com/content/fo/"
          "BhavCopy_NSE_FO_0_0_0_{d}_F_0000.csv.zip")
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}


# ------------------------------------------------------------------ engine
def rolling_linreg_end(y, n):
    """Value of an n-bar least-squares line at its last bar, for every bar."""
    x = np.arange(n)
    xb = (n - 1) / 2.0
    w = 1.0 / n + (x - xb) * ((n - 1) - xb) / ((x - xb) ** 2).sum()
    out = np.full(len(y), np.nan)
    if len(y) >= n:
        out[n - 1:] = np.convolve(y, w[::-1], mode="valid")
    return out


def indicators(h, l, c):
    n = CFG["n"]
    s = pd.Series(c)
    sma = s.rolling(n).mean().values
    sd = s.rolling(n).std(ddof=0).values
    prev = np.roll(c, 1)
    prev[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev), np.abs(l - prev)))
    atr = pd.Series(tr).rolling(n).mean().values
    sq = ((sma + CFG["bb_mult"] * sd) < (sma + CFG["kc_mult"] * atr)) & \
         ((sma - CFG["bb_mult"] * sd) > (sma - CFG["kc_mult"] * atr))
    hh = pd.Series(h).rolling(n).max().values
    ll = pd.Series(l).rolling(n).min().values
    mom = rolling_linreg_end(c - ((hh + ll) / 2 + sma) / 2, n)
    return {"mom": mom, "sq": sq, "atr": atr,
            "ema20": s.ewm(span=20, adjust=False).mean().values,
            "ema50": s.ewm(span=50, adjust=False).mean().values,
            "sma200": s.rolling(200).mean().values}


def state_name(m, p):
    if m is None or p is None or np.isnan(m) or np.isnan(p):
        return "No data"
    if m > 0:
        return "Bullish" if m >= p else "Topping"
    return "Bottoming" if m >= p else "Bearish"


def tf_view(df, k):
    """State of one timeframe. k = bars back used to judge 'rising'."""
    blank = {"ok": False, "state": "No data", "impulse": False, "mom": np.nan}
    if df is None or len(df) < 2 * CFG["n"] + k + 1:
        return blank
    h, l, c = (df[x].values.astype(float) for x in ("High", "Low", "Close"))
    ind = indicators(h, l, c)
    m, p = ind["mom"][-1], ind["mom"][-2]
    if np.isnan(m) or np.isnan(ind["mom"][-1 - k]):
        return blank
    impulse = bool(m > 0 and m > ind["mom"][-1 - k] and c[-1] > ind["ema20"][-1]
                   and ind["ema20"][-1] > ind["ema50"][-1]
                   and ind["ema20"][-1] > ind["ema20"][-1 - k])
    return {"ok": True, "state": state_name(m, p), "impulse": impulse, "mom": m,
            "h": h, "l": l, "c": c, "ind": ind}


OLD_LABELS = ["🏆 Prime Setup", "✅ Confirmed Bullish", "🎯 Consolidating",
              "⚠️ Extended (Gaining)", "⏳ Building Momentum", "⚠️ Extended",
              "⚠️ Extended (Weakening)", "❌ Avoid", "— Insufficient Data"]


VK_FINAL = ["BUY NOW", "ALMOST READY (Bullish)", "COMING CLOSE TO READY (Bullish)",
            "SELL NOW", "ALMOST READY (Bearish)", "COMING CLOSE TO READY (Bearish)",
            "NO SIGNAL"]


def vk_status(M, W, D, H):
    """The VK System (GHOST OF YOTEI) dashboard words, copied from its Pine Script.
    M, W, D, H are tf_view() results. Returns None without Monthly, Weekly and Daily."""
    if not (M["ok"] and W["ok"] and D["ok"]):
        return None

    def vals(T):
        m = T["ind"]["mom"]
        return float(m[-1]), float(m[-2])

    def f_status(v, p):
        if v > 0 and v > p: return "BULLISH RUN"
        if v > 0 and v <= p: return "TOPPING"
        if v < 0 and v < p: return "BEARISH RUN"
        if v < 0 and v >= p: return "BOTTOMING"
        return "FLAT"

    def f_ma(T):
        e20, e50, s200 = (float(T["ind"][k][-1]) for k in ("ema20", "ema50", "sma200"))
        if np.isnan(s200):
            return "Bullish" if e20 > e50 else "Bearish"
        if e20 > e50 > s200: return "Bullish"
        if e20 < e50 < s200: return "Bearish"
        return "Messy"

    valM, pM = vals(M)
    valW, pW = vals(W)
    valD, pD = vals(D)
    statM, statD = f_status(valM, pM), f_status(valD, pD)
    sqzOnD = bool(D["ind"]["sq"][-1])
    if H["ok"]:
        valH, pH = vals(H)
        statH = f_status(valH, pH)
        hm = H["ind"]["mom"]
        posH = bool(len(hm) >= 2 and hm[-1] > 0 and hm[-2] > 0)
        negH = bool(len(hm) >= 2 and hm[-1] <= 0 and hm[-2] <= 0)
    else:
        valH, statH, posH, negH = np.nan, "FLAT", False, False
    maM, maW, maD = f_ma(M), f_ma(W), f_ma(D)

    allBull = valM > 0 and valW > 0 and valD > 0
    allBear = valM < 0 and valW < 0 and valD < 0
    wdBull, wdBear = valW > 0 and valD > 0, valW < 0 and valD < 0
    cashSwing = allBull and sqzOnD                       # same as DO NOT MISS (bull) in Pine
    dnmBear = allBear and sqzOnD
    pendD = ((valD < 0 and valD > pD and abs(valD) < abs(pD) * 0.6) or
             (valD > 0 and valD < pD and abs(valD) < abs(pD) * 0.6))
    cascadeReady = valM > 0 and valW > 0 and (pendD or valD > 0)

    signal = ("ALL 3 ALIGNED BULLISH" if allBull else "ALL 3 ALIGNED BEARISH" if allBear else
              "W+D BULLISH (M lagging)" if wdBull else "W+D BEARISH (M lagging)" if wdBear else
              "CASCADE FORMING" if cascadeReady else "Mixed")

    if cashSwing:
        advice = "PERFECT FOR CASH SWING TRADE — M+W+D bullish + Daily SQZ ON. Hold 2-4 weeks!"
    elif dnmBear:
        advice = "DO NOT MISS — M+W+D bearish + squeeze ON! Short consider karo!"
    elif allBull:
        advice = "Sab bullish hain — entry le sakte ho, SL recent low ke neeche"
    elif allBear:
        advice = "Sell/Avoid — sab timeframe bearish hain"
    elif wdBull and valM < 0 and statM == "BOTTOMING":
        advice = "Valid Buy — W/D bullish, Monthly bottoming kar raha"
    elif wdBear and valM > 0 and statM == "TOPPING":
        advice = "Avoid Long — W/D bearish, Monthly topping"
    elif valM > 0 and valW > 0 and statD == "BOTTOMING":
        advice = "Watchlist mein rakho — M+W bullish, D pullback khatam hone ka wait karo"
    elif valM < 0 and valW < 0 and statD == "TOPPING":
        advice = "Bigger trend neeche — chhota bounce hai, chase mat karo"
    else:
        advice = "Mixed signal — confluence ka wait karo"

    if maM == "Bullish" and maW == "Bullish" and maD == "Bullish":
        ma_sig = "ONLY BUY" if valD > 0 else "Wait — Daily TTM not bullish yet"
    elif maW == "Bearish" and maD == "Bearish":
        ma_sig = ("ONLY SELL" if valD < 0 else
                  "Wait — Pullback in bearish trend (Daily TTM still positive)")
    elif maW == "Bullish" and maD == "Bullish":
        ma_sig = ("ONLY BUY (M lagging)" if valD > 0 else
                  "Wait — Daily TTM not bullish yet (M lagging)")
    elif maM == "Bullish" and maW == "Bullish" and maD == "Bearish":
        ma_sig = "Wait for Daily Setup"
    elif maM == "Bullish" and maW == "Bearish" and maD == "Bullish":
        ma_sig = "Avoid"
    else:
        ma_sig = "Mixed - No Clear Signal"

    htfBull = maW == "Bullish" and maD == "Bullish" and valD > 0
    htfBear = maW == "Bearish" and maD == "Bearish" and valD < 0
    botSoon = maM == maW == maD == "Bullish" and valW > 0 and valD < 0
    topSoon = maM == maW == maD == "Bearish" and valW < 0 and valD > 0
    hpos, hneg = H["ok"] and valH > 0, H["ok"] and valH < 0

    final = ("BUY NOW" if htfBull and hpos else "SELL NOW" if htfBear and hneg else
             "ALMOST READY (Bullish)" if htfBull and hneg else
             "ALMOST READY (Bearish)" if htfBear and hpos else
             "COMING CLOSE TO READY (Bullish)" if botSoon else
             "COMING CLOSE TO READY (Bearish)" if topSoon else "NO SIGNAL")
    daily = ("Daily Signal ON (Bullish)" if htfBull else "Daily Signal ON (Bearish)" if htfBear
             else "Keep Ready (Bullish soon)" if botSoon else
             "Keep Ready (Bearish soon)" if topSoon else "No signal yet")
    hourly = ("Dip on Hourly — watch, buy on turn" if htfBull and hneg else
              "Aligned — can buy now" if htfBull and hpos else
              "Bounce on Hourly — watch, sell on turn" if htfBear and hpos else
              "Aligned — can sell now" if htfBear and hneg else
              "Keep watching LTF" if botSoon or topSoon else "No signal yet")
    confirmed = ("CONFIRMED — Buy ON" if htfBull and posH else
                 "CONFIRMED — Sell ON" if htfBear and negH else "Not confirmed")
    if not H["ok"]:
        hourly, confirmed = "Hourly not available", "Not confirmed"
    return {"final": final, "signal": signal, "ma": ma_sig, "advice": advice,
            "daily": daily, "hourly": hourly, "confirmed": confirmed,
            "status": {"M": statM, "W": f_status(valW, pW), "D": statD, "H": statH}}


def find_range(h, l, c, atr, p):
    """A big range under a falling or flat ceiling.
    The ceiling is a line through swing highs (at least min_touches), never closed above
    until the breakout; the floor must hold (no new lows late in the range).
    Returns dict(stage, line_now, low, height, touches, span, kind, broke_at) or None."""
    R = CFG["range"]
    n = len(c)
    W = min(p["window"], n - 2)
    if W < p["min_span"] + 10 or np.isnan(atr[-1]):
        return None
    o = n - W
    k = p["pivot"]
    piv = [i for i in range(o + k, n - k)
           if h[i] == h[i - k:i + k + 1].max()]
    if len(piv) < R["min_touches"]:
        return None
    a_now = float(atr[-1])
    best = None
    for ia in range(len(piv)):
        a = piv[ia]
        for b in piv[ia + 1:]:
            if b - a < p["min_span"] // 2:
                continue
            slope = (h[b] - h[a]) / (b - a)
            if slope > 0.02 * a_now:              # rising ceilings are not ranges
                continue
            line = lambda t: h[a] + slope * (t - a)
            tol = R["touch_atr"] * a_now
            touches = [q for q in piv if q >= a and abs(h[q] - line(q)) <= tol]
            if len(touches) < R["min_touches"]:
                continue
            # no close above the line from the first touch to the last touch
            seg = np.arange(a, touches[-1] + 1)
            if (c[seg] > line(seg) + 0.15 * a_now).any():
                continue
            # first close above the line after the last touch = breakout
            after = np.arange(touches[-1] + 1, n)
            above = after[c[after] > line(after) + 0.1 * a_now] if len(after) else after
            brk = int(above[0]) if len(above) else None
            span = (brk if brk is not None else n - 1) - a
            if span < p["min_span"]:
                continue
            lo_end = brk if brk is not None else n
            lows = l[a:lo_end]
            base_low = float(lows.min())
            half = len(lows) // 2
            if half < 3 or lows[half:].min() < lows[:half].min() - 1.0 * a_now:
                continue                           # floor broke: a downtrend, not a range
            height = (line(a) - base_low) / c[-1] * 100
            if not (R["height_pct"][0] <= height <= R["height_pct"][1]):
                continue
            score = (len(touches), span)
            if best is None or score > best[0]:
                best = (score, a, slope, touches, brk, base_low, height, span)
    if best is None:
        return None
    _, a, slope, touches, brk, base_low, height, span = best
    line_now = float(h[a] + slope * (n - 1 - a))
    if brk is None:
        gap = (line_now - c[-1]) / a_now
        stage = "Ready" if gap <= R["ready_atr"] else "Watch"
    else:
        if (n - 1 - brk) > p["fresh"] or (c[-1] - line_now) / a_now > R["late_atr"] \
                or c[-1] < line_now - 0.5 * a_now:
            return None                            # old breakout, too far, or failed
        stage = "Triggered"
    return {"stage": stage, "line_now": line_now, "low": base_low, "height": height,
            "touches": len(touches), "span": int(span), "broke_at": brk,
            "kind": "flat top" if abs(slope) * span < 0.5 * a_now else "falling trendline"}


def range_rows(row, T, vol, idx, fo, name):
    """Range Breakout row for one timeframe, graded by volume (and OI for F&O)."""
    p = CFG["range"]["daily" if name.startswith("Daily") else "hourly"]
    rg = find_range(T["h"], T["l"], T["c"], T["ind"]["atr"], p)
    if not rg:
        return []
    unit = "days" if name.startswith("Daily") else "hours"
    weeks = rg["span"] / (5 if unit == "days" else 35)
    v = np.asarray(vol, float) if vol is not None else None
    vol_ok, vol_txt, oi_ok = False, "Volume n/a", False
    if v is not None and len(v) > 30:
        if rg["broke_at"] is not None:
            b = rg["broke_at"]
            base = np.nanmean(v[max(0, b - 20):b]) if unit == "days" else 1.0
            rel = v[b] / base if base and base > 0 else np.nan
            vol_ok = bool(not np.isnan(rel) and rel >= 1.5)
            vol_txt = "Volume n/a" if np.isnan(rel) else f"Breakout volume {rel:.1f}x"
        else:
            recent = np.nanmean(v[-10:])
            older = np.nanmean(v[-60:-10])
            rel = recent / older if older and older > 0 else np.nan
            vol_ok = bool(not np.isnan(rel) and rel <= 0.9)
            vol_txt = "Volume n/a" if np.isnan(rel) else f"Volume {rel:.1f}x lately (quieter is better)"
    if fo is not None and idx is not None and len(idx) > 6:
        ch = oi_change(fo, pd.Timestamp(idx[-6]).normalize(), pd.Timestamp(idx[-1]).normalize())
        if ch:
            px, oi = ch
            oi_ok = bool((px > 0 and oi > 0) or (abs(px) < 0.02 and oi <= 0.03))
    where = (f"{weeks:.0f}-week range under a {rg['kind']}, {rg['touches']} touches, "
             f"{rg['height']:.0f}% deep")
    pos = ("broke out " + pd.Timestamp(idx[rg['broke_at']]).strftime("%a %d")
           if rg["broke_at"] is not None and idx is not None else
           f"{(rg['line_now'] - T['c'][-1]) / T['ind']['atr'][-1]:.1f} ranges under the ceiling")
    grade = "ABC"[2 - int(vol_ok) - int(oi_ok)] if fo is not None else ("A" if vol_ok else "B")
    return [{"symbol": row["symbol"], "close": row["close"], "trend": row["trend"],
             "states": row["states"], "setup": name, "stage": rg["stage"],
             "old": row.get("old"), "vk": row.get("vk"), "ext": row.get("ext"),
             "detail": " · ".join([where, pos, vol_txt]), "grade": grade,
             "trigger": round(rg["line_now"], 2),
             "stop": round(max(rg["low"], rg["line_now"] - 1.5 * float(T["ind"]["atr"][-1]))
                           * 0.998, 2),
             "target": round(rg["line_now"] * (1 + rg["height"] / 100), 2)}]


def extras(D):
    """Distance above the Daily 50 EMA in daily ranges, and the last 6 Daily momentum
    signs (for market breadth)."""
    if not D["ok"]:
        return {"ext": None, "d_pos": []}
    atr, e50 = D["ind"]["atr"][-1], D["ind"]["ema50"][-1]
    ext = None if not atr or np.isnan(atr) else round(float((D["c"][-1] - e50) / atr), 1)
    m = D["ind"]["mom"][-6:]
    return {"ext": ext, "d_pos": [] if np.isnan(m).any() else [bool(x > 0) for x in m]}


def market_turn(valid, nifty):
    """Is the market turning up, falling, or neither? Uses the share of stocks with Daily
    momentum above zero over the last 6 sessions, and Nifty's Daily state."""
    hist = [r["d_pos"] for r in valid if len(r.get("d_pos", [])) == 6]
    out = {"daily_bull_hist": None, "nifty": nifty, "turn": "Unknown"}
    if len(hist) < 20:
        return out
    pct = [round(100 * sum(h[i] for h in hist) / len(hist)) for i in range(6)]
    out["daily_bull_hist"] = pct
    chg, ns = pct[-1] - pct[-4], (nifty or {}).get("state")
    if chg >= CFG["turn_pts"] and pct[-1] >= pct[-2] and ns in ("Bottoming", "Bullish", None):
        out["turn"] = "Turning up"
    elif chg <= -CFG["turn_pts"] or ns == "Bearish":
        out["turn"] = "Falling"
    else:
        out["turn"] = "No clear turn"
    return out


def old_trade_status(wVal, dVal, dValPrev, dSqzOn, hVal, hValPrev):
    """TrendFire's original label, copied unchanged from get_trade_status()."""
    if any(pd.isna(x) for x in [wVal, dVal, dValPrev, hVal, hValPrev]):
        return "— Insufficient Data"
    if wVal > 0:
        if dVal < 0 and dVal >= dValPrev and dSqzOn: return "🏆 Prime Setup"
        elif dVal > 0 and dVal > dValPrev:           return "✅ Confirmed Bullish"
        elif dVal > 0 and dVal <= dValPrev:
            if hVal < 0 and hVal < hValPrev:         return "⚠️ Extended (Weakening)"
            elif hVal < 0 and hVal >= hValPrev:      return "🎯 Consolidating"
            elif hVal > 0 and hVal > hValPrev:       return "⚠️ Extended (Gaining)"
            else:                                    return "⚠️ Extended"
        elif dVal < 0 and dVal >= dValPrev:          return "⏳ Building Momentum"
        else:                                        return "❌ Avoid"
    return "❌ Avoid"


def find_pullback(h, l, c, atr, look_high, look_leg, min_bars):
    """Last up-leg and the pullback after it, or None."""
    n = len(c)
    if n < look_high + look_leg + 5:
        return None
    ih = n - look_high + int(np.argmax(h[-look_high:]))
    if n - 1 - ih < min_bars:
        return None
    lo0 = max(0, ih - look_leg)
    il = lo0 + int(np.argmin(l[lo0:ih + 1]))
    leg = h[ih] - l[il]
    if ih - il < 3 or leg <= 0 or np.isnan(atr[ih]) or leg < CFG["leg_min_atr"] * atr[ih]:
        return None
    ipl = ih + 1 + int(np.argmin(l[ih + 1:]))
    depth = (h[ih] - l[ipl]) / leg
    if not (CFG["depth_min"] <= depth <= CFG["depth_max"]) or c[-1] >= h[ih]:
        return None
    return {"ih": ih, "il": il, "ipl": ipl, "high": float(h[ih]),
            "low": float(l[ipl]), "depth": float(depth), "bars": n - 1 - ih}


def find_overspill(h, l, c, ind):
    """Uptrend (20 EMA over 50 EMA over 200 SMA) where price has come back into
    the 20-50 EMA zone, or spilled through it, and has not broken the trend."""
    n = len(c)
    e20, e50, s200, atr, mom = (ind[k] for k in ("ema20", "ema50", "sma200", "atr", "mom"))
    S = CFG["spill_bars"]
    if n < 215 or np.isnan(s200[-1]) or not atr[-1] > 0:
        return None
    # after a spill the 20 EMA can dip to the 50 EMA and the 50 EMA can flatten,
    # so both get a little room; the 200 SMA must stay clearly below
    if not (e20[-1] >= e50[-1] - atr[-1] and e50[-1] > s200[-1]
            and e50[-1] >= e50[-11] - 0.75 * atr[-1] and c[-1] > s200[-1]):
        return None
    touched = np.where(l[-S:] <= e20[-S:])[0]
    if not len(touched):
        return None
    last_touch = n - S + int(touched[-1])
    a = max(0, last_touch - 40)
    ih = a + int(np.argmax(h[a:last_touch + 1]))           # the high the dip started from
    if ih >= last_touch or not h[ih] >= e20[ih] + CFG["spill_from"] * atr[ih]:
        return None                                         # was not coming from strength
    ipl = ih + 1 + int(np.argmin(l[ih + 1:]))              # lowest point of the dip
    low = l[ipl]
    if low < e50[ipl] - CFG["spill_deep"] * atr[ipl] or low <= s200[ipl]:
        return None                                         # too deep: trend damaged
    a2 = max(0, ih - 40)
    il = a2 + int(np.argmin(l[a2:ih + 1]))
    if ih - il < 3 or h[ih] <= l[il]:
        return None
    if c[-1] > e20[-1]:                                     # back above the 20 EMA
        if n - 1 - last_touch > 3 or c[-1] - e20[-1] > atr[-1]:
            return None                                     # bounced a while ago; moved on
        stage = "Triggered" if c[-1] > c[-2] else "Watch"   # a down close is not a bounce yet
    else:
        stage = "Ready" if (c[-1] > h[-2] or mom[-1] > mom[-2]) else "Watch"
    return {"stage": stage, "level": float(e20[-1]), "low": float(low),
            "under50": float((e50[ipl] - low) / e50[ipl]),
            "il": il, "ih": ih, "ipl": ipl,
            "depth": float((h[ih] - low) / (h[ih] - l[il]))}


def turn_stage(h, c, mom, pb):
    """Watch (momentum still falling), Ready (just turned up), Triggered."""
    n = len(c)
    streak = 0
    for i in range(n - 1, 0, -1):
        if mom[i] > mom[i - 1]:
            streak += 1
        else:
            break
    if streak == 0:
        return "Watch", None
    s = max(n - streak, pb["ipl"])          # signal bar
    level = float(h[s])
    return ("Triggered" if np.any(c[s + 1:] > level) else "Ready"), level


def oi_change(fo, d0, d1):
    """(price change, OI change) from the F&O file between two dates, or None."""
    if fo is None or d0 is None:
        return None
    a = fo[fo["date"] >= d0]
    b = fo[fo["date"] <= d1]
    if not len(a) or not len(b) or a["date"].iloc[0] >= b["date"].iloc[-1]:
        return None
    r0, r1 = a.iloc[0], b.iloc[-1]
    if not (r0["fut_oi"] > 0 and r0["fut_close"] > 0):
        return None
    return (float(r1["fut_close"] / r0["fut_close"] - 1),
            float(r1["fut_oi"] / r0["fut_oi"] - 1))


def pct(x):
    return f"{x * 100:+.0f}%"


def grade_pullback(vol, dates, pb, fo):
    """Volume dry-up and OI behaviour for a pullback setup."""
    leg_v = np.nanmean(vol[pb["il"]:pb["ih"] + 1])
    pb_v = np.nanmean(vol[pb["ih"] + 1:])
    ratio = float(pb_v / leg_v) if leg_v and leg_v > 0 else np.nan
    vol_ok = bool(not np.isnan(ratio) and ratio <= CFG["vol_dry"])
    d_leg0, d_high, d_end = (pd.Timestamp(dates[i]).normalize()
                             for i in (pb["il"], pb["ih"], -1))
    leg = oi_change(fo, d_leg0, d_high)
    pull = oi_change(fo, d_high, d_end)
    warn = bool(pull and pull[0] <= CFG["px_warn"] and pull[1] >= CFG["oi_warn"])
    oi_ok = bool(not warn and leg and leg[0] > 0 and leg[1] > 0.01)
    bits = [f"Pullback {pb['depth'] * 100:.0f}%"]
    bits.append("Volume n/a" if np.isnan(ratio) else f"Volume {ratio:.1f}x in pullback")
    if leg or pull:
        oi = []
        if leg:
            oi.append(f"{pct(leg[1])} in up-leg")
        if pull:
            oi.append(f"{pct(pull[1])} in pullback")
        bits.append("OI " + ", ".join(oi))
    else:
        bits.append("OI n/a")
    return vol_ok, oi_ok, warn, " · ".join(bits)


def grade_squeeze(d, fo, sq_days, flip):
    c, v = d["Close"].values.astype(float), d["Volume"].values.astype(float)
    chg = np.diff(c[-11:])
    up, dn = v[-10:][chg > 0].sum(), v[-10:][chg < 0].sum()
    udr = float(up / dn) if dn > 0 else 3.0
    vol_ok = udr >= CFG["updown_vol"]
    oi5 = None
    if fo is not None and len(fo) >= 6:
        oi5 = oi_change(fo, fo["date"].iloc[-6], fo["date"].iloc[-1])
    warn = bool(oi5 and oi5[0] <= -0.02 and oi5[1] >= CFG["oi_warn"])
    oi_ok = bool(not warn and oi5 and oi5[1] >= CFG["oi_build"])
    bits = [f"Squeeze {sq_days} days" + (f", {flip} bars to flip" if flip else ""),
            f"Up/down volume {udr:.1f}x",
            f"OI {pct(oi5[1])} in 5 days" if oi5 else "OI n/a"]
    return vol_ok, oi_ok, warn, " · ".join(bits)


def to_weekly(d):
    return d.groupby(d.index.to_period("W-FRI")).agg(
        High=("High", "max"), Low=("Low", "min"), Close=("Close", "last"))


def to_monthly(d):
    return d.groupby(d.index.to_period("M")).agg(
        High=("High", "max"), Low=("Low", "min"), Close=("Close", "last"))


def slot_rvol(hr):
    """Hourly volume divided by the usual volume of that clock hour."""
    v = hr["Volume"].astype(float)
    slot = hr.index.strftime("%H:%M")
    base = v.groupby(slot).transform("mean")
    return (v / base.where(base > 0)).values


def analyse(sym, d, hr, fo):
    """d: completed daily bars. hr: completed hourly bars or None."""
    if d is None or len(d) < 80:
        return None
    D = tf_view(d, 3)
    if not D["ok"]:
        return None
    W = tf_view(to_weekly(d), 3)
    M = tf_view(to_monthly(d), 1)
    H = tf_view(hr, 3)
    close = float(D["c"][-1]) if not H["ok"] else float(H["c"][-1])
    cands = []

    # Trend Pullback: Monthly and Weekly impulse, Daily pullback
    if W["impulse"]:
        p = CFG["daily"]
        pb = find_pullback(D["h"], D["l"], D["c"], D["ind"]["atr"], **p)
        if pb:
            stage, level = turn_stage(D["h"], D["c"], D["ind"]["mom"], pb)
            g = grade_pullback(d["Volume"].values.astype(float), d.index, pb, fo)
            cands.append(("Trend Pullback", stage, level, pb["low"] * 0.998, g))

    # Swing Pullback: Daily impulse, Hourly pullback, Weekly not falling below zero
    if D["impulse"] and H["ok"] and W["state"] != "Bearish":
        p = CFG["hourly"]
        pb = find_pullback(H["h"], H["l"], H["c"], H["ind"]["atr"], **p)
        if pb:
            stage, level = turn_stage(H["h"], H["c"], H["ind"]["mom"], pb)
            g = grade_pullback(slot_rvol(hr), hr.index, pb, fo)
            cands.append(("Swing Pullback", stage, level, pb["low"] * 0.998, g))

    # Squeeze Break: Weekly up, Daily coiled and turning
    mom, sq = D["ind"]["mom"], D["ind"]["sq"]
    if W["ok"] and W["mom"] > 0 and sq[-5:].any():
        stage = None
        if mom[-1] <= 0:
            stage = "Ready" if mom[-1] > mom[-2] else "Watch"
        elif (mom[-4:-1] <= 0).any():
            stage = "Triggered"
        if stage:
            flip = None
            if stage == "Ready":
                flip = int(min(20, math.ceil(-mom[-1] / (mom[-1] - mom[-2]))))
            g = grade_squeeze(d, fo, int(sq[-20:].sum()), flip)
            level = float(D["h"][-5:].max()) if stage != "Watch" else None
            cands.append(("Squeeze Break", stage, level,
                          float(D["l"][-10:].min()) * 0.998, g))

    trend = {"Bullish": "With trend", "Topping": "With trend",
             "Bottoming": "Early turn", "Bearish": "Counter-trend"}.get(M["state"], "")
    show = lambda v: "Impulse" if v["impulse"] else v["state"]
    hm = H["ind"]["mom"] if H["ok"] else [np.nan, np.nan]
    row = {"symbol": sym, "close": round(close, 2), "trend": trend,
           "states": {"M": show(M), "W": show(W), "D": show(D), "H": show(H)},
           "w_bull": bool(W["ok"] and W["mom"] > 0),
           "m_bull": (bool(M["mom"] > 0) if M["ok"] else None),
           "old": old_trade_status(W["mom"], D["ind"]["mom"][-1], D["ind"]["mom"][-2],
                                   bool(D["ind"]["sq"][-1]), hm[-1], hm[-2])}
    row.update(extras(D))
    row["vk"] = vk_status(M, W, D, H)

    # Overspill: price back in the 20-50 EMA zone of an uptrend.
    #   Daily  overspill needs Weekly bullish (Monthly is the tag).
    #   Hourly overspill needs Weekly and Daily bullish.
    spills = []
    w_up = W["ok"] and W["mom"] > 0
    for name, T, vol, idx, gate in (
            ("Daily Overspill", D, d["Volume"].values.astype(float), d.index, w_up),
            ("Hourly Overspill", H, slot_rvol(hr) if H["ok"] else None,
             hr.index if H["ok"] else None, w_up and D["mom"] > 0)):
        if not (gate and T["ok"]):
            continue
        sp = find_overspill(T["h"], T["l"], T["c"], T["ind"])
        if not sp:
            continue
        vol_ok, oi_ok, warn, det = grade_pullback(vol, idx, sp, fo)
        where = (f"Spilled {sp['under50'] * 100:.1f}% under the 50 EMA" if sp["under50"] > 0
                 else "Dipped into the 20-50 EMA zone")
        spills.append({
            "symbol": sym, "close": row["close"], "trend": trend, "states": row["states"],
            "setup": name, "stage": sp["stage"], "old": row["old"], "ext": row["ext"],
            "vk": row["vk"],
            "detail": " · ".join([where] + det.split(" · ")[1:]),
            "grade": "Warning" if warn else "ABC"[2 - int(vol_ok) - int(oi_ok)],
            "trigger": round(sp["level"], 2), "stop": round(sp["low"] * 0.998, 2)})

    # Weekly Overspill: the weekly chart dips into its 20-50 EMA zone while Monthly is
    # bullish; the Daily turning up inside the zone is the entry signal.
    if M["ok"] and M["mom"] > 0 and W["ok"]:
        sp = find_overspill(W["h"], W["l"], W["c"], W["ind"])
        if sp:
            dm = D["ind"]["mom"]
            daily_up = bool(dm[-1] > dm[-2])
            stage = sp["stage"] if sp["stage"] == "Triggered" else (
                "Ready" if daily_up else "Watch")
            wk_vol = d["Volume"].astype(float).groupby(d.index.to_period("W-FRI")).sum()
            wk_dates = to_weekly(d).index.to_timestamp(how="end")
            vol_ok, oi_ok, warn, det = grade_pullback(wk_vol.values, wk_dates, sp, fo)
            pull = oi_change(fo, pd.Timestamp(wk_dates[sp["ih"]]).normalize(),
                             pd.Timestamp(wk_dates[-1]).normalize())
            if not oi_ok and pull and not warn and pull[1] <= 0.03:
                oi_ok = True        # up-leg older than the saved OI: a calm pullback counts
            where = (f"Weekly spilled {sp['under50'] * 100:.1f}% under the 50 EMA"
                     if sp["under50"] > 0 else "Weekly dipped into the 20-50 EMA zone")
            spills.append({
                "symbol": sym, "close": row["close"], "trend": trend, "states": row["states"],
                "setup": "Weekly Overspill", "stage": stage, "old": row["old"],
                "vk": row["vk"],
                "ext": row["ext"],
                "detail": " · ".join([where, "Daily turning up" if daily_up
                                      else "Daily still falling"] + det.split(" · ")[1:]),
                "grade": "Warning" if warn else "ABC"[2 - int(vol_ok) - int(oi_ok)],
                "trigger": round(sp["level"], 2), "stop": round(sp["low"] * 0.998, 2)})
    row["overspill"] = spills
    lines = range_rows(row, D, d["Volume"].values.astype(float), d.index, fo, "Daily Range")
    if H["ok"]:
        lines += range_rows(row, H, slot_rvol(hr), hr.index, fo, "Hourly Range")
    row["ranges"] = lines

    if cands:
        order = {"Ready": 0, "Triggered": 1, "Watch": 2}
        setup, stage, level, stop, (vol_ok, oi_ok, warn, detail) = \
            min(cands, key=lambda x: order[x[1]])
        row.update({
            "setup": setup, "stage": stage, "detail": detail,
            "grade": "Warning" if warn else "ABC"[2 - int(vol_ok) - int(oi_ok)],
            "trigger": None if level is None else round(level, 2),
            "stop": round(float(stop), 2)})
        return row

    # No setup: where is the stock in its move?
    atr, ema20 = D["ind"]["atr"][-1], D["ind"]["ema20"][-1]
    stage = None
    if D["mom"] > 0 and atr > 0 and (D["c"][-1] - ema20) / atr > CFG["stretch_atr"]:
        stage = "Stretched"
    elif D["impulse"]:
        stage = "Running"
    elif (D["state"] == "Topping" and W["ok"] and W["mom"] > 0
          and mom[-1] < mom[-2] < mom[-3] < mom[-4]):
        stage = "Fading"
    row.update({"setup": None, "stage": stage})
    return row


def build_result(rows, now, fo_asof, vix, notes, nifty=None):
    valid = [r for r in rows if r]
    turn = market_turn(valid, nifty)
    wb = [r["w_bull"] for r in valid]
    mb = [r["m_bull"] for r in valid if r["m_bull"] is not None]
    w_pct = round(100 * sum(wb) / len(wb)) if wb else None
    m_pct = round(100 * sum(mb) / len(mb)) if mb else None
    regime = ("Unknown" if w_pct is None else "Healthy" if w_pct >= CFG["healthy"]
              else "Weak" if w_pct < CFG["weak"] else "Mixed")
    g_rank = {"A": 0, "B": 1, "C": 2, "Warning": 3}
    t_rank = {"With trend": 0, "Early turn": 1, "": 2, "Counter-trend": 3}
    order = lambda r: (g_rank[r["grade"]], t_rank[r["trend"]], r["symbol"])
    spills = sorted([x for r in valid for x in r.pop("overspill", [])], key=order)
    ranges = sorted([x for r in valid for x in r.pop("ranges", [])], key=order)
    setups = sorted([r for r in valid if r["setup"]], key=order)
    others = {s: sorted(r["symbol"] for r in valid if not r["setup"] and r["stage"] == s)
              for s in ("Running", "Stretched", "Fading")}
    old_labels = {}
    for r in valid:
        if r.get("old"):
            old_labels.setdefault(r["old"], []).append(r["symbol"])
    old_labels = {k: sorted(v) for k, v in old_labels.items()}
    vk_labels = {}
    for r in valid:
        if r.get("vk"):
            vk_labels.setdefault(r["vk"]["final"], []).append(
                {"symbol": r["symbol"], "close": r["close"], "states": r["states"],
                 **{k: r["vk"][k] for k in ("signal", "ma", "advice", "daily", "hourly",
                                            "confirmed")}})
    vk_labels = {k: sorted(v, key=lambda x: x["symbol"]) for k, v in vk_labels.items()}
    for r in setups:
        r.pop("w_bull", None)
        r.pop("m_bull", None)
        r.pop("d_pos", None)
    return {"generated_ist": now.strftime("%Y-%m-%d %H:%M"),
            "oi_asof": fo_asof, "stocks": len(valid),
            "market": {"weekly_bull_pct": w_pct, "monthly_bull_pct": m_pct,
                       "vix": vix, "regime": regime, **turn},
            "rows": setups, "overspill": spills, "ranges": ranges, "others": others,
            "old_labels": old_labels, "vk_labels": vk_labels, "notes": notes}


# ------------------------------------------------------------------ network
def nse_session():
    import requests
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        s.get("https://www.nseindia.com", timeout=15)
    except Exception as e:
        print(f"[warn] NSE home page not reachable: {e}")
    return s


def nse_get(session, url, tries=3):
    for i in range(tries):
        try:
            r = session.get(url, timeout=40)
            if r.status_code == 200 and len(r.content) > 200:
                return r.content
            if r.status_code == 404:
                return None
        except Exception as e:
            print(f"[warn] {url} attempt {i + 1}: {e}")
        time.sleep(3 * (i + 1))
    return None


def parse_fo(raw):
    """F&O bhavcopy zip -> one row per stock: total futures OI, near-month price."""
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        df = pd.read_csv(z.open(z.namelist()[0]), low_memory=False)
    df.columns = [c.strip() for c in df.columns]
    df = df[df["FinInstrmTp"].astype(str).str.strip() == "STF"].copy()
    df["TckrSymb"] = df["TckrSymb"].astype(str).str.strip()
    for c in ("ClsPric", "SttlmPric", "OpnIntrst"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["XpryDt"] = pd.to_datetime(df["XpryDt"], errors="coerce")
    df["px"] = np.where(df["SttlmPric"] > 0, df["SttlmPric"], df["ClsPric"])
    df = df.sort_values("XpryDt")
    g = df.groupby("TckrSymb")
    return pd.DataFrame({"fut_oi": g["OpnIntrst"].sum(),
                         "fut_close": g["px"].first()}).reset_index() \
        .rename(columns={"TckrSymb": "symbol"})


def update_fo(session, now, notes):
    path = os.path.join(CFG["data_dir"], "fo_oi.csv.gz")
    hist = (pd.read_csv(path, parse_dates=["date"]) if os.path.exists(path)
            else pd.DataFrame(columns=["date", "symbol", "fut_oi", "fut_close"]))
    have = set(pd.to_datetime(hist["date"]).dt.normalize())
    today = pd.Timestamp(now.date())
    days = [today - pd.Timedelta(days=i) for i in range(CFG["fo_backfill_days"])]
    want = [d for d in days if d.weekday() < 5 and d not in have
            and (d < today or now.hour >= 18)]
    if len(have) >= 15:                       # routine run: only newer days
        want = [d for d in want if d > max(have)]
    new = []
    for d in sorted(want):
        raw = nse_get(session, FO_URL.format(d=d.strftime("%Y%m%d")))
        if raw is None:
            continue                          # holiday, or not published yet
        try:
            day = parse_fo(raw)
        except Exception as e:
            print(f"[warn] F&O file {d.date()} unreadable: {e}")
            continue
        day.insert(0, "date", d)
        new.append(day)
        time.sleep(1)
    if new:
        hist = pd.concat([hist] + new, ignore_index=True)
        hist["date"] = pd.to_datetime(hist["date"])
        cutoff = hist["date"].max() - pd.Timedelta(days=CFG["fo_keep_days"])
        hist = hist[hist["date"] >= cutoff].drop_duplicates(["date", "symbol"])
        hist.sort_values(["date", "symbol"]).to_csv(path, index=False)
    hist["date"] = pd.to_datetime(hist["date"])
    if not len(hist):
        raise RuntimeError("No F&O file could be downloaded from NSE.")
    return hist


def yf_download(tickers, **kw):
    import yfinance as yf
    for _ in range(3):
        try:
            data = yf.download(tickers, auto_adjust=False, group_by="ticker",
                               threads=True, progress=False, **kw)
            if data is not None and len(data):
                return data
        except Exception as e:
            print(f"[warn] yfinance: {e}")
        time.sleep(5)
    return None


def fetch_bars(symbols, **kw):
    out = {}
    for i in range(0, len(symbols), 60):
        chunk = symbols[i:i + 60]
        tickers = [s + ".NS" for s in chunk]
        data = yf_download(tickers, **kw)
        if data is None:
            continue
        for s, t in zip(chunk, tickers):
            try:
                df = data[t][["Open", "High", "Low", "Close", "Volume"]].dropna()
            except Exception:
                continue
            if len(df):
                idx = pd.to_datetime(df.index)
                if idx.tz is not None:
                    idx = idx.tz_convert("Asia/Kolkata").tz_localize(None)
                df.index = idx
                out[s] = df
    return out


def fetch_vix():
    try:
        data = yf_download(["^INDIAVIX"], period="5d", interval="1d")
        df = data["^INDIAVIX"] if isinstance(data.columns, pd.MultiIndex) else data
        return round(float(df["Close"].dropna().iloc[-1]), 1)
    except Exception:
        return None


def fetch_nifty(now):
    """Nifty 50 Daily TTM state from completed days."""
    try:
        data = yf_download(["^NSEI"], period="2y", interval="1d")
        df = (data["^NSEI"] if isinstance(data.columns, pd.MultiIndex) else data).dropna()
        last = pd.Timestamp(df.index[-1]).date()
        if last == now.date() and (now.hour, now.minute) < (15, 40):
            df = df.iloc[:-1]
        v = tf_view(df, 3)
        if not v["ok"]:
            return None
        return {"state": v["state"], "mom": round(float(v["mom"]), 1)}
    except Exception as e:
        print(f"[warn] Nifty not available: {e}")
        return None


def completed(daily, hourly, now):
    """Drop bars that are still forming."""
    naive = now.replace(tzinfo=None)
    closed = naive.hour * 60 + naive.minute >= 15 * 60 + 35
    d, h = {}, {}
    for s, df in daily.items():
        df = df.copy()
        df.index = df.index.normalize()
        if len(df) and df.index[-1].date() == naive.date() and not closed:
            df = df.iloc[:-1]
        d[s] = df
    for s, df in hourly.items():
        end = df.index + pd.Timedelta(minutes=60)
        end = end.where(df.index.strftime("%H:%M") != "15:15",
                        df.index + pd.Timedelta(minutes=15))
        h[s] = df[end <= naive]
    return d, h


def log_ready(res):
    path = os.path.join(CFG["data_dir"], "ready_log.csv")
    day = res["generated_ist"][:10]
    rows = [{"date": day, "seen_ist": res["generated_ist"], "symbol": r["symbol"],
             "setup": r["setup"], "trend": r["trend"], "grade": r["grade"],
             "close": r["close"], "trigger": r["trigger"], "stop": r["stop"]}
            for r in res["rows"] + res.get("overspill", []) if r["stage"] == "Ready"]
    if not rows:
        return
    new = pd.DataFrame(rows)
    if os.path.exists(path):
        old = pd.read_csv(path)
        new = pd.concat([old, new], ignore_index=True)
    new.drop_duplicates(["date", "symbol", "setup"], keep="first").to_csv(path, index=False)


def main_live():
    os.makedirs(CFG["data_dir"], exist_ok=True)
    now = dt.datetime.now(IST)
    notes = []
    fo = update_fo(nse_session(), now, notes)
    latest = fo["date"].max()
    symbols = sorted(fo.loc[fo["date"] == latest, "symbol"].unique())
    daily = fetch_bars(symbols, period="5y", interval="1d")
    if len(daily) < 50:
        raise RuntimeError(f"Daily prices for only {len(daily)} stocks; stopping.")
    hourly = fetch_bars(symbols, period="60d", interval="1h")
    if len(hourly) < 50:
        notes.append("Hourly prices unavailable: Swing Pullback not scanned this run.")
    daily, hourly = completed(daily, hourly, now)
    fo_by = {s: g.sort_values("date").reset_index(drop=True)
             for s, g in fo.groupby("symbol")}
    rows = []
    for s in symbols:
        try:
            rows.append(analyse(s, daily.get(s), hourly.get(s), fo_by.get(s)))
        except Exception as e:
            print(f"[warn] {s}: {e}")
    missing = len(symbols) - len([r for r in rows if r])
    if missing:
        notes.append(f"{missing} stocks skipped (too little price history or no data).")
    res = build_result(rows, now, latest.strftime("%Y-%m-%d"), fetch_vix(), notes,
                       fetch_nifty(now))
    with open(os.path.join(CFG["data_dir"], "setups.json"), "w") as f:
        json.dump(res, f, indent=1)
    log_ready(res)
    c = {}
    for r in res["rows"]:
        c[r["stage"]] = c.get(r["stage"], 0) + 1
    print(f"{res['stocks']} stocks | {c} | market {res['market']}")


# ---------------------------------------------------------------- selftest
def _bars(c, idx, noise=0.002, vol=None, seed=0):
    rng = np.random.default_rng(seed)
    c = np.asarray(c, float)
    return pd.DataFrame({
        "Open": c, "High": c * (1 + noise), "Low": c * (1 - noise), "Close": c,
        "Volume": vol if vol is not None else rng.integers(8e5, 1e6, len(c)).astype(float)},
        index=idx)


def _hour_index(days):
    out = []
    for d in days:
        out += [d + pd.Timedelta(hours=9, minutes=15) + pd.Timedelta(hours=k)
                for k in range(7)]
    return pd.DatetimeIndex(out)


def selftest():
    rng = np.random.default_rng(3)

    # 1) rolling regression equals a direct fit
    y = rng.normal(size=80).cumsum()
    r = rolling_linreg_end(y, 20)
    for i in (19, 40, 79):
        sl, ic = np.polyfit(np.arange(20), y[i - 19:i + 1], 1)
        assert abs(r[i] - (sl * 19 + ic)) < 1e-9

    # 2) the four states
    assert [state_name(*a) for a in ((2, 1), (1, 2), (-1, -2), (-2, -1))] == \
        ["Bullish", "Topping", "Bottoming", "Bearish"]

    # 3) stage from the momentum turn
    pb = {"ipl": 5}
    h = np.array([10, 10, 10, 10, 10, 9.0, 9.2, 9.4, 9.6])
    c = h - 0.1
    falling = np.array([5, 4, 3, 2, 1, 0, -1, -2, -3.0])
    assert turn_stage(h, c, falling, pb)[0] == "Watch"
    one_up = np.array([5, 4, 3, 2, 1, 0, -1, -2, -1.5])
    assert turn_stage(h, c, one_up, pb) == ("Ready", 9.6)
    up3 = np.array([5, 4, 3, 2, 1, 0, 0.5, 1.0, 1.5])
    assert turn_stage(h, c, up3, pb) == ("Triggered", 9.2)

    # 4) full pipeline on a made-up stock: long uptrend, hourly dip, then a turn
    days = pd.bdate_range(end="2026-11-13", periods=900)
    d_close = 100 * np.exp(np.cumsum(np.full(900, 0.0016) + rng.normal(0, 0.002, 900)))
    daily = _bars(d_close, days, noise=0.006)
    up = np.linspace(0, 0.07, 40)                      # hourly up-leg
    dip = up[-1] - np.linspace(0.004, 0.032, 12)       # pullback of ~45%
    turn = dip[-1] + np.linspace(0.002, 0.022, 10)     # recovery, below the old high
    shape = np.concatenate([np.zeros(280 - 62), up, dip, turn])
    hc = d_close[-1] * 0.95 * (1 + np.linspace(0, 0.05, 280)) * (1 + shape)
    vol = np.full(280, 9e5)
    vol[-22:] = 4e5                                    # volume dries up in the dip
    hourly = _bars(hc, _hour_index(days[-40:]), noise=0.0015, vol=vol)
    fo = pd.DataFrame({"date": days[-30:],
                       "fut_oi": np.concatenate([np.linspace(1e6, 1.25e6, 28),
                                                 [1.23e6, 1.21e6]]),
                       "fut_close": d_close[-30:]})
    seq = []
    for cut in range(10, -1, -1):                      # reveal the turn bar by bar
        r_ = analyse("DEMO", daily, hourly.iloc[:len(hourly) - cut], fo)
        assert r_["setup"] == "Swing Pullback", r_
        assert r_["trend"] == "With trend" and r_["states"]["D"] == "Impulse", r_
        assert r_["grade"] in ("A", "B") and r_["stop"] < r_["close"], r_
        assert (r_["trigger"] is None) == (r_["stage"] == "Watch"), r_
        seq.append(r_["stage"])
        if r_["stage"] == "Ready":
            row = r_
    print("stages as the turn unfolds:", " > ".join(seq))
    print(json.dumps(row, indent=1))
    # 5) order must be Watch, then Ready, then Triggered, never backwards
    rank = [("Watch", "Ready", "Triggered").index(x) for x in seq]
    assert rank == sorted(rank) and set(rank) == {0, 1, 2}, seq

    # 6) OI rising while price falls in the pullback -> Warning
    bad = fo.copy()
    bad.loc[bad.index[-2:], "fut_oi"] = [1.33e6, 1.40e6]
    bad.loc[bad.index[-2:], "fut_close"] = bad["fut_close"].iloc[-3] * np.array([0.99, 0.98])
    assert analyse("DEMO", daily, hourly, bad)["grade"] == "Warning"

    # 7) forming bars are dropped
    now = dt.datetime(2026, 11, 13, 11, 40, tzinfo=IST)
    dd, hh = completed({"X": daily}, {"X": hourly}, now)
    assert dd["X"].index[-1] == days[-2]
    assert hh["X"].index[-1] == pd.Timestamp("2026-11-13 10:15")

    # 8) result file shape, flat stock has no setup
    flat = _bars(100 + rng.normal(0, 0.3, 900), days)
    rows = [row, analyse("FLAT", flat, None, None), None]
    res = build_result(rows, now, "2026-11-12", 13.2, [])
    assert res["stocks"] == 2 and len(res["rows"]) == 1
    assert res["market"]["weekly_bull_pct"] in (50, 100)
    json.dumps(res)

    # 8b) overspill: uptrend, dip into the 20-50 EMA zone, bounce back over the 20 EMA
    def spill_case(dip_total, noise, n_=700):
        dd = pd.bdate_range(end="2026-11-13", periods=n_)
        up_ = np.cumsum(np.full(n_ - 16, 0.0028))
        dip_ = up_[-1] - np.linspace(0.012, dip_total, 7)
        bounce_ = dip_[-1] + np.linspace(0.012, 0.11, 9)
        return _bars(100 * np.exp(np.concatenate([up_, dip_, bounce_])), dd, noise=noise)

    def spill_trace(df):
        out = []
        for cut in range(16, -1, -1):
            T = tf_view(df.iloc[:len(df) - cut], 3)
            sp = find_overspill(T["h"], T["l"], T["c"], T["ind"])
            out.append("-" if sp is None else sp["stage"][0])
        return "".join(out)

    zone, deep, broken = spill_case(0.085, 0.006), spill_case(0.115, 0.012), spill_case(0.30, 0.012)
    tz, td, tb = spill_trace(zone), spill_trace(deep), spill_trace(broken)
    print("overspill traces:", tz, td, tb)
    for t in (tz, td):                 # Watch, then Ready, then Triggered, then it moves on
        core = t.strip("-")
        assert core == "W" * core.count("W") + "R" * core.count("R") + "T" * core.count("T"), t
        assert min(core.count(x) for x in "WRT") >= 1 and t.endswith("-"), t
    assert "R" not in tb and "T" not in tb, tb          # a trend-breaking fall never qualifies
    Tf = tf_view(flat, 3)
    assert find_overspill(Tf["h"], Tf["l"], Tf["c"], Tf["ind"]) is None
    at_ready = zone.iloc[:len(zone) - (16 - tz.index("R"))]
    ro = analyse("SPILL", at_ready, None, None)
    print(json.dumps(ro["overspill"], indent=1))
    assert [x["setup"] for x in ro["overspill"]] == ["Daily Overspill"], ro
    x = ro["overspill"][0]
    assert x["stage"] == "Ready" and x["stop"] < x["close"] <= x["trigger"], x
    res_o = build_result([ro], now, "2026-11-12", None, [])
    assert len(res_o["overspill"]) == 1 and "overspill" not in json.dumps(res_o["rows"])
    keep_dir, CFG["data_dir"] = CFG["data_dir"], __import__("tempfile").mkdtemp()
    log_ready(res_o)
    lg = pd.read_csv(os.path.join(CFG["data_dir"], "ready_log.csv"))
    CFG["data_dir"] = keep_dir
    assert lg["setup"].tolist() == ["Daily Overspill"], lg

    # 8c) weekly overspill: Monthly bullish, weekly dip into its 20-50 EMA zone, Daily turn
    wk = np.cumsum(np.full(270, 0.007))
    wk = np.concatenate([wk, wk[-1] - np.linspace(0.02, 0.15, 6)])
    wk = np.concatenate([wk, wk[-1] + np.linspace(0.02, 0.12, 6)])
    wdays = pd.bdate_range(end="2026-11-13", periods=len(wk) * 5)
    wc = 100 * np.exp(np.repeat(wk, 5) + np.tile(np.linspace(-0.004, 0.004, 5), len(wk)))
    wd = _bars(wc * (1 + np.random.default_rng(2).normal(0, 0.003, len(wc))), wdays, noise=0.006)
    wseq = []
    for cut in range(60, -1, -2):
        wo = [x for x in analyse("WK", wd.iloc[:len(wd) - cut], None, None)["overspill"]
              if x["setup"] == "Weekly Overspill"]
        wseq.append(wo[0]["stage"][0] if wo else "-")
    print("weekly overspill trace:", "".join(wseq))
    assert "R" in wseq and "T" in wseq and wseq.index("W") < wseq.index("R") < wseq.index("T")
    assert wseq[-1] == "-"                 # moved on after the bounce
    flat_m = analyse("WK", wd.iloc[:-30], None, None)
    assert all(x["stop"] < x["trigger"] for x in flat_m["overspill"])

    # 8d) old TrendFire labels: same branches as get_trade_status()
    nan = np.nan
    cases = [((1, -1, -2, True, 1, 0), "🏆 Prime Setup"), ((1, -1, -2, False, 1, 0), "⏳ Building Momentum"),
             ((1, 2, 1, False, 1, 0), "✅ Confirmed Bullish"), ((1, 2, 3, False, -2, -1), "⚠️ Extended (Weakening)"),
             ((1, 2, 3, False, -1, -2), "🎯 Consolidating"), ((1, 2, 3, False, 2, 1), "⚠️ Extended (Gaining)"),
             ((1, 2, 3, False, 1, 2), "⚠️ Extended"), ((1, -2, -1, True, 1, 0), "❌ Avoid"),
             ((-1, 2, 1, False, 1, 0), "❌ Avoid"), ((1, 2, 1, False, nan, 0), "— Insufficient Data")]
    for args, want in cases:
        assert old_trade_status(*args) == want, (args, want)
    assert ro["old"] in OLD_LABELS and res_o["old_labels"]
    assert res_o["overspill"] and all(x["old"] == ro["old"] for x in res_o["overspill"])

    # 8g) big ranges: falling ceiling + flat floor, then a breakout
    def wave(n, top0, top1, floor, period=14, seed=3):
        """Price swinging between a falling ceiling and a flat floor."""
        t = np.arange(n)
        top = top0 + (top1 - top0) * t / (n - 1)
        mid, amp = (top + floor) / 2, (top - floor) / 2
        return mid + amp * 0.97 * np.sin(2 * np.pi * t / period)
    pre = np.linspace(1500, 1700, 40)
    base = wave(84, 1820, 1760, 1700)
    days_r = pd.bdate_range(end="2026-11-13", periods=200)
    def run_range(tail):
        cc = np.concatenate([pre, base, tail])
        cc = np.concatenate([np.full(200 - len(cc), 1500.0), cc])[-200:]
        dd = _bars(cc, days_r, noise=0.006)
        T = tf_view(dd, 3)
        return find_range(T["h"], T["l"], T["c"], T["ind"]["atr"], CFG["range"]["daily"]), T
    rg, T = run_range(np.linspace(1712, 1745, 6))            # mid-range, ceiling ~1755
    assert rg and rg["kind"] == "falling trendline" and rg["touches"] >= 3, rg
    assert rg["stage"] in ("Watch", "Ready") and 1740 < rg["line_now"] < 1775, rg
    rg_up, _ = run_range(np.concatenate([np.linspace(1730, 1752, 4), [1790, 1805, 1812]]))
    assert rg_up and rg_up["stage"] == "Triggered" and rg_up["broke_at"] is not None, rg_up
    rg_old, _ = run_range(np.concatenate([[1790, 1810], np.linspace(1815, 1900, 12)]))
    assert rg_old is None                                    # broke out long ago / too far
    # a steady downtrend is not a range (floor keeps breaking)
    down = _bars(np.linspace(2000, 1400, 200) + 25 * np.sin(np.arange(200) / 2.2), days_r, 0.006)
    Td = tf_view(down, 3)
    assert find_range(Td["h"], Td["l"], Td["c"], Td["ind"]["atr"], CFG["range"]["daily"]) is None
    # a rising market has no falling/flat ceiling
    up = _bars(np.linspace(1000, 1600, 200) + 20 * np.sin(np.arange(200) / 2.2), days_r, 0.006)
    Tu = tf_view(up, 3)
    assert find_range(Tu["h"], Tu["l"], Tu["c"], Tu["ind"]["atr"], CFG["range"]["daily"]) is None
    cc = np.concatenate([pre, base, np.linspace(1730, 1752, 4), [1790, 1805, 1812]])
    cc = np.concatenate([np.full(200 - len(cc), 1500.0), cc])[-200:]
    vol = np.full(200, 1e6)
    vol[-3] = 3e6                                              # heavy breakout day
    ar = analyse("RNG", _bars(cc, days_r, noise=0.006, vol=vol), None, None)
    res_r = build_result([ar], dt.datetime(2026, 11, 13, 19, 45, tzinfo=IST), "2026-11-13",
                         None, [])
    rgr = [x for x in res_r["ranges"] if x["setup"] == "Daily Range"]
    assert rgr and rgr[0]["stage"] == "Triggered" and "Breakout volume 3.0x" in rgr[0]["detail"], rgr
    assert rgr[0]["grade"] == "A" and rgr[0]["stop"] < rgr[0]["trigger"] < rgr[0]["target"], rgr
    assert "falling trendline" in rgr[0]["detail"] and "ranges" not in json.dumps(res_r["rows"])
    print("range trace:", rg["kind"], rg["touches"], round(rg["line_now"], 1), rg["stage"],
          "|", rg_up["stage"], round(rg_up["line_now"], 1))

    # 8f) VK System words
    def T_(m, p, sq=False, e=(3.0, 2.0, 1.0)):
        return {"ok": True, "ind": {"mom": np.array([p, m]), "sq": np.array([sq, sq]),
                                    "ema20": np.array([e[0]]), "ema50": np.array([e[1]]),
                                    "sma200": np.array([e[2]])}}
    up, dn = (3.0, 2.0, 1.0), (1.0, 2.0, 3.0)
    v = vk_status(T_(5, 4), T_(4, 3), T_(2, 1), T_(-1, -2))
    assert v["final"] == "ALMOST READY (Bullish)" and v["hourly"].startswith("Dip on Hourly"), v
    assert v["signal"] == "ALL 3 ALIGNED BULLISH" and v["ma"] == "ONLY BUY", v
    assert vk_status(T_(5, 4), T_(4, 3), T_(2, 1), T_(1, 0.5))["confirmed"] == "CONFIRMED — Buy ON"
    v = vk_status(T_(5, 4), T_(4, 3), T_(-2, -3), T_(-1, -2))
    assert v["final"] == "COMING CLOSE TO READY (Bullish)" and v["advice"].startswith("Watchlist"), v
    v = vk_status(T_(-5, -4, e=dn), T_(-4, -3, e=dn), T_(-2, -1, e=dn), T_(-1, -0.5, e=dn))
    assert v["final"] == "SELL NOW" and v["ma"] == "ONLY SELL", v
    assert vk_status(T_(5, 4), T_(4, 3), T_(2, 1, sq=True), T_(1, 0))["advice"].startswith("PERFECT")
    assert vk_status({"ok": False}, T_(1, 0), T_(1, 0), T_(1, 0)) is None
    assert "vk" in ro and (ro["vk"] is None or ro["vk"]["final"] in VK_FINAL)
    assert "vk_labels" in res_o

    # 8e) market turn and extension
    mkr = lambda pos: {"d_pos": pos}
    up = [mkr([False] * 3 + [True] * 3)] * 15 + [mkr([False] * 6)] * 15
    t = market_turn(up, {"state": "Bottoming", "mom": -10})
    assert t["daily_bull_hist"] == [0, 0, 0, 50, 50, 50] and t["turn"] == "Turning up", t
    assert market_turn(up, {"state": "Bearish", "mom": -20})["turn"] == "Falling"
    down = [mkr([True] * 3 + [False] * 3)] * 30
    assert market_turn(down, None)["turn"] == "Falling"
    assert market_turn([mkr([True] * 6)] * 30, None)["turn"] == "No clear turn"
    assert market_turn(up[:5], None)["turn"] == "Unknown"
    assert isinstance(ro["ext"], float) and "d_pos" not in json.dumps(res_o["rows"])
    assert all("ext" in x for x in res_o["overspill"]) and "turn" in res_o["market"]

    # 9) F&O file reader: futures OI summed across expiries, near-month price
    recs = [{"FinInstrmTp": "STF", "TckrSymb": "ABC", "XpryDt": "2026-12-29",
             "ClsPric": 503.0, "SttlmPric": 503.0, "OpnIntrst": 3000},
            {"FinInstrmTp": "STF", "TckrSymb": "ABC", "XpryDt": "2026-11-24",
             "ClsPric": 500.0, "SttlmPric": 500.0, "OpnIntrst": 1000},
            {"FinInstrmTp": "IDF", "TckrSymb": "NIFTY", "XpryDt": "2026-11-24",
             "ClsPric": 25000.0, "SttlmPric": 25000.0, "OpnIntrst": 9}]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("x.csv", pd.DataFrame(recs).to_csv(index=False))
    day = parse_fo(buf.getvalue())
    assert day.to_dict("records") == [{"symbol": "ABC", "fut_oi": 4000, "fut_close": 500.0}]

    # 10) Ready log: one line per stock per day, first sighting kept
    import tempfile
    keep, CFG["data_dir"] = CFG["data_dir"], tempfile.mkdtemp()
    log_ready(res)
    log_ready(res)
    log = pd.read_csv(os.path.join(CFG["data_dir"], "ready_log.csv"))
    CFG["data_dir"] = keep
    assert len(log) == 1 and log["symbol"].iloc[0] == "DEMO", log
    print("\nSELFTEST PASSED")
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    selftest() if ap.parse_args().selftest else main_live()
