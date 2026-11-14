#!/usr/bin/env python3
"""
TrendFire Setups - scan engine (NSE F&O stocks).

One engine for all four timeframes. Each stock gets:
  setup  : Squeeze Break | Swing Pullback | Trend Pullback
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
    "stretch_atr": 3.0,                                # close this many ATRs above EMA20
    "healthy": 60, "weak": 40,                         # % of stocks bullish on Weekly
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
            "ema50": s.ewm(span=50, adjust=False).mean().values}


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
    if M["impulse"] and W["impulse"]:
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
    row = {"symbol": sym, "close": round(close, 2), "trend": trend,
           "states": {"M": show(M), "W": show(W), "D": show(D), "H": show(H)},
           "w_bull": bool(W["ok"] and W["mom"] > 0),
           "m_bull": (bool(M["mom"] > 0) if M["ok"] else None)}

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


def build_result(rows, now, fo_asof, vix, notes):
    valid = [r for r in rows if r]
    wb = [r["w_bull"] for r in valid]
    mb = [r["m_bull"] for r in valid if r["m_bull"] is not None]
    w_pct = round(100 * sum(wb) / len(wb)) if wb else None
    m_pct = round(100 * sum(mb) / len(mb)) if mb else None
    regime = ("Unknown" if w_pct is None else "Healthy" if w_pct >= CFG["healthy"]
              else "Weak" if w_pct < CFG["weak"] else "Mixed")
    g_rank = {"A": 0, "B": 1, "C": 2, "Warning": 3}
    t_rank = {"With trend": 0, "Early turn": 1, "": 2, "Counter-trend": 3}
    setups = sorted([r for r in valid if r["setup"]],
                    key=lambda r: (g_rank[r["grade"]], t_rank[r["trend"]], r["symbol"]))
    others = {s: sorted(r["symbol"] for r in valid if not r["setup"] and r["stage"] == s)
              for s in ("Running", "Stretched", "Fading")}
    for r in setups:
        r.pop("w_bull", None)
        r.pop("m_bull", None)
    return {"generated_ist": now.strftime("%Y-%m-%d %H:%M"),
            "oi_asof": fo_asof, "stocks": len(valid),
            "market": {"weekly_bull_pct": w_pct, "monthly_bull_pct": m_pct,
                       "vix": vix, "regime": regime},
            "rows": setups, "others": others, "notes": notes}


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
            for r in res["rows"] if r["stage"] == "Ready"]
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
    res = build_result(rows, now, latest.strftime("%Y-%m-%d"), fetch_vix(), notes)
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
