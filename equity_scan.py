#!/usr/bin/env python3
"""
TrendFire Setups - equity scan (midcaps and smallcaps outside F&O).

Uses the same engine as the F&O scan, on Daily bars only:
  setup : Squeeze Break | Trend Pullback (Weekly impulse + Daily dip; Monthly is a tag)
          plus a separate list: Weekly Overspill | Daily Overspill
  stage : Watch | Ready | Triggered   (else Running | Stretched | Fading)
  trend : With trend | Early turn | Counter-trend
  grade : A | B | C | Warning   (volume + delivery %, last close)

Data
  Stock list : Nifty Midcap 150 + Smallcap 250 from NSE's index files, minus F&O
               stocks. If those files cannot be read, every liquid non-F&O stock.
  Delivery, turnover, series : NSE daily cash bhavcopy (data/equity_deliv.csv.gz)
  Prices     : yfinance daily bars (completed days only)

Outputs
  data/equity.json            read by app.py
  data/equity_ready_log.csv   every Ready sighting, for the later scorecard

Usage
  python equity_scan.py             # live run
  python equity_scan.py --selftest  # offline logic check, no network
"""
import argparse
import datetime as dt
import importlib.machinery
import importlib.util
import io
import json
import math
import os
import time

import numpy as np
import pandas as pd

ECFG = {
    "min_turnover_cr": 10.0,      # 20-day average daily turnover on NSE
    "min_price": 50.0,
    "deliv_lift": 1.05,           # last 10 days' delivery % vs the 30 days before
    "heavy_dip": 1.3,             # dip volume vs up-leg volume that earns a Warning
    "backfill_days": 75, "keep_days": 150,
    # base breakout
    "bo_base_bars": 15,           # length of the base, in days
    "bo_max_depth": 0.20,         # base no deeper than this
    "bo_min_rvol": 1.5,           # breakout-day volume vs 50-day average
    "bo_max_ext": 0.06,           # not more than this above the breakout level
    "bo_max_risk": 0.08,          # stop no further than this
    "bo_near": 0.03,              # "setting up" = within this of the breakout level
    "bo_min_rs": 0.70,            # relative-strength percentile in the group
    "bo_days": 3,                 # breakouts from the last N sessions are listed
}
CASH_URL = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{d}.csv"
INDEX_URLS = [
    ["https://nsearchives.nseindia.com/content/indices/ind_niftymidcap150list.csv",
     "https://nsearchives.nseindia.com/content/indices/ind_niftysmallcap250list.csv"],
    ["https://www.niftyindices.com/IndexConstituent/ind_niftymidcap150list.csv",
     "https://www.niftyindices.com/IndexConstituent/ind_niftysmallcap250list.csv"],
]
FUND_WORDS = ("BEES", "ETF", "LIQUID", "GOLD", "SILVER", "NIFTY", "SENSEX", "GILT",
              "INVIT", "REIT")


def load_engine():
    """The F&O engine file, whatever name it was uploaded under."""
    for name in ("setups_scan", "setups_scan.py", "setups scan.py"):
        if os.path.exists(name) and "def tf_view(" in open(name, encoding="utf-8").read():
            loader = importlib.machinery.SourceFileLoader("tf_engine", name)
            spec = importlib.util.spec_from_loader("tf_engine", loader)
            mod = importlib.util.module_from_spec(spec)
            loader.exec_module(mod)
            return mod
    raise RuntimeError("Engine file not found. The F&O scan file (setups_scan) must "
                       "be in the same folder.")


def load_unusual():
    """The Unusual scan file, if it is in the repo, whatever its name."""
    for name in ("unusual_scan.py", "unusual scan.py", "unusual_scan"):
        if os.path.exists(name) and "def daily_events(" in open(name, encoding="utf-8").read():
            loader = importlib.machinery.SourceFileLoader("tf_unusual", name)
            spec = importlib.util.spec_from_loader("tf_unusual", loader)
            mod = importlib.util.module_from_spec(spec)
            loader.exec_module(mod)
            return mod
    return None


E = load_engine()
CFG = E.CFG


def equity_unusual(U, symbols, daily, dl_by, setups):
    """Unusual activity on Daily bars only: volume patterns, range, gap, delivery."""
    cal = U.trading_days({}, daily)
    rows = []
    for s in symbols:
        d = daily.get(s)
        try:
            r = U.analyse_unusual(s, d, None, None, dl_by.get(s), cal)
        except Exception as e:
            print(f"[warn] unusual {s}: {e}")
            continue
        if not r:
            continue
        if "states" in r:
            r["states"] = {"M": E.tf_view(E.to_monthly(d), 1)["state"], **r["states"]}
        if s in setups:
            r["setup"] = setups[s]
        rows.append(r)
    rows.sort(key=lambda r: (-r["score"], r["symbol"]))
    return rows


# ------------------------------------------------------------------- logic
def delivery_check(dl):
    """(passes, text) for delivery % in the last 10 days vs the 30 before."""
    if dl is None or len(dl) < 25:
        return False, "Delivery n/a"
    d = dl["deliv_per"].astype(float).values
    recent, base = np.nanmean(d[-10:]), np.nanmean(d[-40:-10])
    if not (base > 0) or np.isnan(recent):
        return False, "Delivery n/a"
    return bool(recent >= ECFG["deliv_lift"] * base), \
        f"Delivery {recent:.0f}% vs {base:.0f}% before"


OLD_EQUITY_LABELS = ["🏆 Prime Setup", "✅ Confirmed Bullish", "⏳ Building Momentum",
                     "⚠️ Extended", "❌ Avoid", "— Insufficient Data"]


def old_equity_status(W, D):
    """TrendFire's F&O label rule without Hourly: the Hourly-based Extended variants
    and Consolidating all become plain Extended."""
    if not (W["ok"] and D["ok"]) or len(D["ind"]["mom"]) < 2:
        return "— Insufficient Data"
    wVal, dVal, dValPrev = W["mom"], D["ind"]["mom"][-1], D["ind"]["mom"][-2]
    dSqzOn = bool(D["ind"]["sq"][-1])
    if any(pd.isna(x) for x in [wVal, dVal, dValPrev]):
        return "— Insufficient Data"
    if wVal > 0:
        if dVal < 0 and dVal >= dValPrev and dSqzOn: return "🏆 Prime Setup"
        elif dVal > 0 and dVal > dValPrev:           return "✅ Confirmed Bullish"
        elif dVal > 0 and dVal <= dValPrev:          return "⚠️ Extended"
        elif dVal < 0 and dVal >= dValPrev:          return "⏳ Building Momentum"
    return "❌ Avoid"


def analyse_equity(sym, d, dl):
    """d: completed daily bars. dl: this stock's delivery history or None."""
    if d is None or len(d) < 80:
        return None
    D = E.tf_view(d, 3)
    if not D["ok"]:
        return None
    W = E.tf_view(E.to_weekly(d), 3)
    M = E.tf_view(E.to_monthly(d), 1)
    vol = d["Volume"].values.astype(float)
    deliv_ok, deliv_txt = delivery_check(dl)
    cands = []

    # Weekly impulse + Daily dip. Monthly is NOT a gate here: it only sets the tag,
    # so recent listings and stocks whose Monthly has paused are still listed.
    if W["impulse"]:
        pb = E.find_pullback(D["h"], D["l"], D["c"], D["ind"]["atr"], **CFG["daily"])
        if pb:
            stage, level = E.turn_stage(D["h"], D["c"], D["ind"]["mom"], pb)
            leg_v = np.nanmean(vol[pb["il"]:pb["ih"] + 1])
            ratio = float(np.nanmean(vol[pb["ih"] + 1:]) / leg_v) if leg_v > 0 else np.nan
            vol_ok = bool(not np.isnan(ratio) and ratio <= CFG["vol_dry"])
            warn = bool(not np.isnan(ratio) and ratio >= ECFG["heavy_dip"])
            detail = " · ".join([
                f"Pullback {pb['depth'] * 100:.0f}%",
                "Volume n/a" if np.isnan(ratio) else f"Volume {ratio:.1f}x in pullback",
                deliv_txt])
            cands.append(("Trend Pullback", stage, level, pb["low"] * 0.998,
                          vol_ok, warn, detail,
                          "Volume in the dip is heavier than in the up-move"))

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
            chg = np.diff(D["c"][-11:])
            up, dn = vol[-10:][chg > 0].sum(), vol[-10:][chg < 0].sum()
            udr = float(up / dn) if dn > 0 else 3.0
            detail = " · ".join([
                f"Squeeze {int(sq[-20:].sum())} days" + (f", {flip} bars to flip" if flip else ""),
                f"Up/down volume {udr:.1f}x", deliv_txt])
            level = float(D["h"][-5:].max()) if stage != "Watch" else None
            cands.append(("Squeeze Break", stage, level, float(D["l"][-10:].min()) * 0.998,
                          udr >= CFG["updown_vol"], False, detail, ""))

    trend = {"Bullish": "With trend", "Topping": "With trend",
             "Bottoming": "Early turn", "Bearish": "Counter-trend"}.get(M["state"], "")
    show = lambda v: "Impulse" if v["impulse"] else v["state"]
    row = {"symbol": sym, "close": round(float(D["c"][-1]), 2), "trend": trend,
           "states": {"M": show(M), "W": show(W), "D": show(D)},
           "w_bull": bool(W["ok"] and W["mom"] > 0),
           "m_bull": (bool(M["mom"] > 0) if M["ok"] else None),
           "old": old_equity_status(W, D)}
    row["overspill"] = equity_overspill(row, d, M, W, D, deliv_ok, deliv_txt)
    if cands:
        order = {"Ready": 0, "Triggered": 1, "Watch": 2}
        setup, stage, level, stop, vol_ok, warn, detail, warn_txt = \
            min(cands, key=lambda x: order[x[1]])
        row.update({
            "setup": setup, "stage": stage, "detail": detail,
            "grade": "Warning" if warn else "ABC"[2 - int(vol_ok) - int(deliv_ok)],
            "trigger": None if level is None else round(level, 2),
            "stop": round(float(stop), 2)})
        if warn:
            row["warning_text"] = warn_txt
        return row
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


def clamp(x, lo=0.0, hi=1.0):
    return float(max(lo, min(hi, x)))


def rs_percentiles(daily):
    """Rank of each stock's blended 3-month and 6-month return within the group."""
    rows = {}
    for s, df in daily.items():
        c = df["Close"].values
        if len(c) >= 130:
            rows[s] = 0.5 * (c[-1] / c[-64] - 1) + 0.5 * (c[-1] / c[-127] - 1)
    return pd.Series(rows, dtype=float).rank(pct=True)


def analyse_breakout(sym, d, rs, dl):
    """A strong stock leaving a tight base on volume, or about to. None otherwise."""
    if d is None or len(d) < 210 or rs is None or np.isnan(rs) or rs < ECFG["bo_min_rs"]:
        return None
    h, l, c, v = (d[k].values.astype(float) for k in ("High", "Low", "Close", "Volume"))
    n, B = len(c), ECFG["bo_base_bars"]
    sma50, sma50_old, sma200 = c[-50:].mean(), c[-70:-20].mean(), c[-200:].mean()
    high252 = h[-252:].max()
    if not (c[-1] > sma50 > sma200 and sma50 > sma50_old and c[-1] >= 0.8 * high252):
        return None

    def base(i):                       # the B days before day i
        bh, bl = h[i - B:i].max(), l[i - B:i].min()
        vol50 = v[i - 50:i].mean()
        return bh, (bh - bl) / bh, vol50, (v[i - 10:i].mean() / vol50 if vol50 > 0 else np.nan)

    deliv_ok, deliv_txt = delivery_check(dl)
    p_common = lambda depth, dry: (
        25 * clamp((rs - 0.7) / 0.3) + 15 * clamp((c[-1] / high252 - 0.8) / 0.2)
        + 15 * clamp((0.20 - depth) / 0.12) + 10 * clamp((1 - dry) / 0.4)
        + (10 if deliv_ok else 0))
    M, W, D = E.tf_view(E.to_monthly(d), 1), E.tf_view(E.to_weekly(d), 3), E.tf_view(d, 3)
    trend = {"Bullish": "With trend", "Topping": "With trend",
             "Bottoming": "Early turn", "Bearish": "Counter-trend"}.get(M["state"], "")
    show = lambda x: "Impulse" if x["impulse"] else x["state"]
    row = {"symbol": sym, "close": round(float(c[-1]), 2), "trend": trend,
           "setup": "Base Breakout", "states": {"M": show(M), "W": show(W), "D": show(D)}}

    for k in range(ECFG["bo_days"]):   # did it break out in the last few sessions?
        i = n - 1 - k
        bh, depth, vol50, dry = base(i)
        rng = h[i] - l[i]
        pos = (c[i] - l[i]) / rng if rng > 0 else 0.0
        rvol = v[i] / vol50 if vol50 > 0 else 0.0
        if not (depth <= ECFG["bo_max_depth"] and c[i] > bh >= c[i - 1]
                and rvol >= ECFG["bo_min_rvol"] and pos >= 0.5):
            continue
        stop = min(l[i], l[i - 1]) * 0.995
        if ((c[i + 1:] <= bh).any() or c[-1] / bh - 1 > ECFG["bo_max_ext"]
                or not 0 < (c[-1] - stop) / c[-1] <= ECFG["bo_max_risk"]):
            return None                # failed, extended, or stop too far
        score = p_common(depth, dry) + 20 * clamp((rvol - 1) / 2) + 5 * clamp((pos - 0.5) / 0.5)
        row.update({
            "stage": "Triggered", "score": round(score, 1),
            "grade": "A" if score >= 70 else "B" if score >= 55 else "C",
            "trigger": round(float(bh), 2), "stop": round(float(stop), 2),
            "detail": " · ".join([
                f"Broke out {d.index[i].strftime('%a %d')}", f"Volume {rvol:.1f}x",
                f"Base {depth * 100:.0f}% deep", f"RS {rs * 100:.0f}", deliv_txt])})
        return row

    bh, depth, vol50, dry = base(n)    # not broken out: is it close to the level?
    gap = 1 - c[-1] / bh
    if depth <= ECFG["bo_max_depth"] and 0 <= gap <= ECFG["bo_near"]:
        score = p_common(depth, dry) * 100 / 75
        row.update({
            "stage": "Ready", "score": round(score, 1),
            "grade": "A" if score >= 70 else "B" if score >= 55 else "C",
            "trigger": round(float(bh), 2), "stop": None,
            "detail": " · ".join([
                f"{gap * 100:.1f}% below the breakout level", f"Base {depth * 100:.0f}% deep",
                f"Volume in base {dry:.1f}x usual", f"RS {rs * 100:.0f}", deliv_txt])})
        return row
    return None


def breakout_scan(symbols, daily, dl_by):
    rs = rs_percentiles(daily)
    rows = []
    for s in symbols:
        try:
            r = analyse_breakout(s, daily.get(s), rs.get(s), dl_by.get(s))
        except Exception as e:
            print(f"[warn] breakout {s}: {e}")
            continue
        if r:
            rows.append(r)
    rank = {"A": 0, "B": 1, "C": 2}
    rows.sort(key=lambda r: (rank[r["grade"]], -r["score"], r["symbol"]))
    return ([r for r in rows if r["stage"] == "Triggered"],
            [r for r in rows if r["stage"] == "Ready"])


def equity_overspill(row, d, M, W, D, deliv_ok, deliv_txt):
    """Weekly Overspill (Monthly bullish, Daily turn as entry) and Daily Overspill
    (Weekly bullish). Grade: volume drying up in the dip, and delivery."""
    out = []
    vol_d = d["Volume"].values.astype(float)
    vol_w = d["Volume"].astype(float).groupby(d.index.to_period("W-FRI")).sum().values
    dm = D["ind"]["mom"]
    for name, T, vol, gate in (
            ("Weekly Overspill", W, vol_w, M["ok"] and M["mom"] > 0),
            ("Daily Overspill", D, vol_d, W["ok"] and W["mom"] > 0)):
        if not (gate and T["ok"]):
            continue
        sp = E.find_overspill(T["h"], T["l"], T["c"], T["ind"])
        if not sp:
            continue
        stage, extra = sp["stage"], []
        if name == "Weekly Overspill":
            up = bool(dm[-1] > dm[-2])
            if stage != "Triggered":
                stage = "Ready" if up else "Watch"
            extra = ["Daily turning up" if up else "Daily still falling"]
        leg_v = np.nanmean(vol[sp["il"]:sp["ih"] + 1]) if len(vol) > sp["ih"] else np.nan
        ratio = (float(np.nanmean(vol[sp["ih"] + 1:]) / leg_v)
                 if leg_v and leg_v > 0 else np.nan)
        vol_ok = bool(not np.isnan(ratio) and ratio <= CFG["vol_dry"])
        warn = bool(not np.isnan(ratio) and ratio >= ECFG["heavy_dip"])
        tf = "Weekly" if name.startswith("Weekly") else "Daily"
        where = (f"{tf} spilled {sp['under50'] * 100:.1f}% under the 50 EMA"
                 if sp["under50"] > 0 else f"{tf} dipped into the 20-50 EMA zone")
        r = {k: row[k] for k in ("symbol", "close", "trend", "states", "old")}
        r.update({
            "setup": name, "stage": stage,
            "detail": " · ".join([where] + extra + [
                "Volume n/a" if np.isnan(ratio) else f"Volume {ratio:.1f}x in pullback",
                deliv_txt]),
            "grade": "Warning" if warn else "ABC"[2 - int(vol_ok) - int(deliv_ok)],
            "trigger": round(sp["level"], 2), "stop": round(sp["low"] * 0.998, 2)})
        if warn:
            r["warning_text"] = "Volume in the dip is heavier than in the up-move"
        out.append(r)
    return out


def pick_universe(hist, index_syms, fo_syms, notes):
    """Liquid, normally traded stocks outside F&O."""
    latest = hist["date"].max()
    last = hist[hist["date"] == latest]
    normal = set(last.loc[last["series"] == "EQ", "symbol"])
    recent = hist[hist["date"] >= sorted(hist["date"].unique())[-20:][0]]
    g = recent.groupby("symbol")
    turnover_cr = g["turnover_lacs"].mean() / 100.0
    price = g["close"].last()
    ok = set(turnover_cr.index[(turnover_cr >= ECFG["min_turnover_cr"])
                               & (price >= ECFG["min_price"])])
    if index_syms:
        base = set(index_syms)
    else:
        base = {s for s in ok if not any(w in s for w in FUND_WORDS)}
        notes.append("Index lists unavailable: scanning every liquid stock outside "
                     "F&O, not only Midcap 150 and Smallcap 250.")
    return sorted((base & ok & normal) - set(fo_syms))


# ------------------------------------------------------------------ network
def parse_cash(raw):
    df = pd.read_csv(io.BytesIO(raw))
    df.columns = [c.strip() for c in df.columns]
    for c in ("SYMBOL", "SERIES"):
        df[c] = df[c].astype(str).str.strip()
    num = lambda c: pd.to_numeric(df[c].astype(str).str.strip(), errors="coerce")
    out = pd.DataFrame({"symbol": df["SYMBOL"], "series": df["SERIES"],
                        "close": num("CLOSE_PRICE"), "turnover_lacs": num("TURNOVER_LACS"),
                        "deliv_per": num("DELIV_PER")})
    return out[out["series"].isin(["EQ", "BE", "BZ"])].reset_index(drop=True)


def update_cash(session, now):
    path = os.path.join(CFG["data_dir"], "equity_deliv.csv.gz")
    cols = ["date", "symbol", "series", "close", "turnover_lacs", "deliv_per"]
    hist = (pd.read_csv(path, parse_dates=["date"]) if os.path.exists(path)
            else pd.DataFrame(columns=cols))
    have = set(pd.to_datetime(hist["date"]).dt.normalize())
    today = pd.Timestamp(now.date())
    days = [today - pd.Timedelta(days=i) for i in range(ECFG["backfill_days"])]
    want = [d for d in days if d.weekday() < 5 and d not in have
            and (d < today or now.hour >= 18)]
    if len(have) >= 30:
        want = [d for d in want if d > max(have)]
    new = []
    for d in sorted(want):
        raw = E.nse_get(session, CASH_URL.format(d=d.strftime("%d%m%Y")))
        if raw is None:
            continue
        try:
            day = parse_cash(raw)
        except Exception as e:
            print(f"[warn] cash file {d.date()} unreadable: {e}")
            continue
        day.insert(0, "date", d)
        new.append(day)
        time.sleep(1)
    if new:
        hist = pd.concat([hist] + new, ignore_index=True)
        hist["date"] = pd.to_datetime(hist["date"])
        cutoff = hist["date"].max() - pd.Timedelta(days=ECFG["keep_days"])
        hist = hist[hist["date"] >= cutoff].drop_duplicates(["date", "symbol"])
        hist.sort_values(["date", "symbol"]).to_csv(path, index=False)
    hist["date"] = pd.to_datetime(hist["date"])
    if not len(hist):
        raise RuntimeError("No cash bhavcopy could be downloaded from NSE.")
    return hist


def load_index_list(session):
    path = os.path.join(CFG["data_dir"], "equity_index_list.csv")
    for group in INDEX_URLS:
        syms = []
        for url in group:
            raw = E.nse_get(session, url, tries=2)
            if raw is None:
                syms = []
                break
            try:
                df = pd.read_csv(io.BytesIO(raw))
                df.columns = [c.strip() for c in df.columns]
                syms += df["Symbol"].astype(str).str.strip().tolist()
            except Exception:
                syms = []
                break
        if len(syms) >= 300:
            pd.DataFrame({"symbol": sorted(set(syms))}).to_csv(path, index=False)
            return sorted(set(syms))
    if os.path.exists(path):
        return pd.read_csv(path)["symbol"].tolist()
    return []


def fo_symbols():
    path = os.path.join(CFG["data_dir"], "fo_oi.csv.gz")
    if not os.path.exists(path):
        return []
    fo = pd.read_csv(path, parse_dates=["date"])
    return fo.loc[fo["date"] == fo["date"].max(), "symbol"].unique().tolist()


def log_ready(res):
    path = os.path.join(CFG["data_dir"], "equity_ready_log.csv")
    rows = [{"date": res["generated_ist"][:10], "symbol": r["symbol"],
             "setup": r["setup"], "trend": r["trend"], "grade": r["grade"],
             "close": r["close"], "trigger": r["trigger"], "stop": r["stop"]}
            for r in res["rows"] + res.get("overspill", []) if r["stage"] == "Ready"]
    if not rows:
        return
    new = pd.DataFrame(rows)
    if os.path.exists(path):
        new = pd.concat([pd.read_csv(path), new], ignore_index=True)
    new.drop_duplicates(["date", "symbol", "setup"], keep="first").to_csv(path, index=False)


def main_live():
    os.makedirs(CFG["data_dir"], exist_ok=True)
    now = dt.datetime.now(E.IST)
    notes = []
    session = E.nse_session()
    hist = update_cash(session, now)
    symbols = pick_universe(hist, load_index_list(session), fo_symbols(), notes)
    if len(symbols) < 30:
        raise RuntimeError(f"Only {len(symbols)} stocks passed the filters; stopping.")
    daily = E.fetch_bars(symbols, period="5y", interval="1d")
    if len(daily) < 30:
        raise RuntimeError(f"Daily prices for only {len(daily)} stocks; stopping.")
    daily, _ = E.completed(daily, {}, now)
    dl_by = {s: g.sort_values("date") for s, g in hist.groupby("symbol")}
    rows = []
    for s in symbols:
        try:
            rows.append(analyse_equity(s, daily.get(s), dl_by.get(s)))
        except Exception as e:
            print(f"[warn] {s}: {e}")
    missing = len(symbols) - len([r for r in rows if r])
    if missing:
        notes.append(f"{missing} stocks skipped (too little price history or no data).")
    res = E.build_result(rows, now, hist["date"].max().strftime("%Y-%m-%d"), None, notes)
    with open(os.path.join(CFG["data_dir"], "equity.json"), "w") as f:
        json.dump(res, f, indent=1)
    log_ready(res)
    c = {}
    for r in res["rows"]:
        c[r["stage"]] = c.get(r["stage"], 0) + 1
    print(f"{res['stocks']} stocks | {c} | market {res['market']}")

    broke, setting = breakout_scan(symbols, daily, dl_by)
    with open(os.path.join(CFG["data_dir"], "breakout.json"), "w") as f:
        json.dump({"generated_ist": res["generated_ist"], "oi_asof": res["oi_asof"],
                   "stocks": len(symbols), "market": res["market"],
                   "broke": broke, "setting": setting, "notes": []}, f, indent=1)
    print(f"breakout: {len(broke)} broke out, {len(setting)} setting up")

    U = load_unusual()
    if U is None:
        print("[info] unusual scan file not in the repo; equity unusual list skipped.")
        return
    setups = {r["symbol"]: f"{r['setup']} · {r['stage']}" for r in res["rows"]}
    urows = equity_unusual(U, symbols, daily, dl_by, setups)
    with open(os.path.join(CFG["data_dir"], "unusual_equity.json"), "w") as f:
        json.dump({"generated_ist": res["generated_ist"], "oi_asof": res["oi_asof"],
                   "stocks": len(symbols), "rows": urows, "notes": []}, f, indent=1)
    print(f"unusual: {len(urows)} flagged | top: "
          + ", ".join(f"{r['symbol']} {r['score']}" for r in urows[:8]))


# ---------------------------------------------------------------- selftest
def selftest():
    rng = np.random.default_rng(5)
    days = pd.bdate_range(end="2026-11-13", periods=900)

    # 1) a made-up stock: long uptrend, a daily dip, then a turn revealed day by day
    up = np.linspace(0, 0.16, 30)
    dip = up[-1] - np.linspace(0.006, 0.07, 9)
    turn = dip[-1] + np.linspace(0.005, 0.038, 8)   # stays below the old high
    shape = np.concatenate([np.zeros(900 - 47), up, dip, turn])
    close = 100 * np.exp(np.cumsum(np.full(900, 0.0012))) * (1 + shape)
    vol = np.full(900, 9e5)
    vol[-17:] = 4e5
    d = E._bars(close, days, noise=0.006, vol=vol)
    dl = pd.DataFrame({"date": days[-50:], "deliv_per":
                       np.concatenate([np.full(40, 40.0), np.full(10, 55.0)])})
    seq, ready = [], None
    for cut in range(8, -1, -1):
        r = analyse_equity("DEMO", d.iloc[:len(d) - cut], dl)
        assert r["setup"] == "Trend Pullback", r
        assert r["trend"] == "With trend" and set(r["states"]) == {"M", "W", "D"}, r
        assert (r["trigger"] is None) == (r["stage"] == "Watch"), r
        seq.append(r["stage"])
        if r["stage"] == "Ready":
            ready = r
    print("stages as the turn unfolds:", " > ".join(seq))
    print(json.dumps(ready, indent=1))
    rank = [("Watch", "Ready", "Triggered").index(x) for x in seq]
    assert rank == sorted(rank) and 1 in rank, seq
    assert ready["grade"] == "A" and "Delivery 55% vs 40% before" in ready["detail"]
    assert ready["stop"] < ready["close"]

    # 2) heavy volume in the dip -> Warning with its own wording
    heavy = d.copy()
    heavy.iloc[-17:, heavy.columns.get_loc("Volume")] = 1.4e6
    cut = len(seq) - 1 - seq.index("Ready")
    w = analyse_equity("DEMO", heavy.iloc[:len(d) - cut], dl)
    assert w["grade"] == "Warning" and "heavier" in w["warning_text"], w

    # 3) no delivery history -> at most grade B
    assert analyse_equity("DEMO", d.iloc[:len(d) - cut], None)["grade"] == "B"

    # 3b) Monthly no longer gates Trend Pullback: a recent listing still qualifies
    young = d.iloc[-300:]
    y = analyse_equity("YOUNG", young.iloc[:len(young) - cut], dl)
    print("recent listing:", y["setup"], "|", y["stage"], "| tag:", repr(y["trend"]),
          "| M:", y["states"]["M"])
    assert y["setup"] == "Trend Pullback" and y["states"]["M"] == "No data" and y["trend"] == ""
    #     ... and so does a stock recovering from a long fall (Monthly not yet an impulse)
    fall = 300 * np.exp(np.cumsum(np.full(900, -0.0012)))
    rec = d["Close"].values[-220:] / d["Close"].values[-220] * fall[679]
    turn_c = np.concatenate([fall[:680], rec])
    t = E._bars(turn_c, days, noise=0.006, vol=vol)
    tr_ = analyse_equity("TURN", t.iloc[:len(t) - cut], dl)
    print("after a long fall:", tr_["setup"], "|", tr_["stage"], "| tag:", tr_["trend"],
          "| M:", tr_["states"]["M"])
    assert tr_["setup"] == "Trend Pullback" and tr_["states"]["M"] != "Impulse", tr_

    # 3c) overspill on equity: Weekly (Monthly bullish, Daily turn) and Daily
    wk = np.cumsum(np.full(270, 0.007))
    wk = np.concatenate([wk, wk[-1] - np.linspace(0.02, 0.15, 6)])
    wk = np.concatenate([wk, wk[-1] + np.linspace(0.02, 0.12, 6)])
    wdays = pd.bdate_range(end="2026-11-13", periods=len(wk) * 5)
    wc = 100 * np.exp(np.repeat(wk, 5) + np.tile(np.linspace(-0.004, 0.004, 5), len(wk)))
    wv = np.full(len(wc), 1e6)
    wv[-60:] = 5e5                         # quieter in the dip
    wd = E._bars(wc * (1 + np.random.default_rng(2).normal(0, 0.003, len(wc))), wdays,
                 noise=0.006, vol=wv)
    seen = {}
    for cut in range(60, -1, -2):
        for x in analyse_equity("GRAPH", wd.iloc[:len(wd) - cut], dl)["overspill"]:
            seen.setdefault(x["setup"], []).append(x["stage"])
            if x["setup"] == "Weekly Overspill" and x["stage"] == "Ready":
                wready = x
    print("equity overspill stages:", {k: "".join(v_[0] for v_ in val) for k, val in seen.items()})
    print(json.dumps(wready, indent=1))
    assert {"Watch", "Ready", "Triggered"} <= set(seen["Weekly Overspill"])
    assert wready["grade"] == "A" and "Delivery 55% vs 40% before" in wready["detail"]
    assert "Daily turning up" in wready["detail"] and wready["stop"] < wready["trigger"]
    res_e = E.build_result([analyse_equity("GRAPH", wd.iloc[:-20], dl)],
                           dt.datetime(2026, 11, 13, 19, 45, tzinfo=E.IST), "2026-11-13", None, [])
    assert res_e["overspill"] and "overspill" not in json.dumps(res_e["rows"])
    assert res_e["old_labels"] and all(x["old"] in OLD_EQUITY_LABELS for x in res_e["overspill"])
    mk = lambda w, d, dp, sq: ({"ok": True, "mom": w},
                               {"ok": True, "ind": {"mom": [dp, d], "sq": [sq, sq]}})
    for args, want in [((1, -1, -2, True), "🏆 Prime Setup"), ((1, -1, -2, False), "⏳ Building Momentum"),
                       ((1, 2, 1, False), "✅ Confirmed Bullish"), ((1, 2, 3, False), "⚠️ Extended"),
                       ((1, -2, -1, True), "❌ Avoid"), ((-1, 2, 1, False), "❌ Avoid")]:
        assert old_equity_status(*mk(*args)) == want, (args, want)

    # 4) cash bhavcopy reader (NSE pads its columns with spaces)
    csv = ("SYMBOL, SERIES, DATE1, CLOSE_PRICE, TURNOVER_LACS, DELIV_QTY, DELIV_PER\n"
           "ABC, EQ, 13-Nov-2026, 512.5, 2500.0, 1000, 48.2\n"
           "XYZ, BE, 13-Nov-2026, 90.0, 1500.0, 500, 100.0\n"
           "BOND, N1, 13-Nov-2026, 1000.0, 10.0, -, -\n")
    day = parse_cash(csv.encode())
    assert day["symbol"].tolist() == ["ABC", "XYZ"] and day["deliv_per"].iloc[0] == 48.2

    # 5) universe: liquid, normal series, not F&O; fund names dropped in the fallback
    rows = []
    for s, ser, px, to in [("MIDCO", "EQ", 300, 2500), ("THINCO", "EQ", 300, 200),
                           ("T2TCO", "BE", 300, 2500), ("FNOCO", "EQ", 300, 9000),
                           ("PENNY", "EQ", 12, 2500), ("NIFTYBEES", "EQ", 250, 9000),
                           ("OTHER", "EQ", 500, 3000)]:
        for dte in days[-25:]:
            rows.append({"date": dte, "symbol": s, "series": ser, "close": px,
                         "turnover_lacs": to, "deliv_per": 40.0})
    hist = pd.DataFrame(rows)
    notes = []
    assert pick_universe(hist, ["MIDCO", "THINCO", "T2TCO", "FNOCO", "PENNY"],
                         ["FNOCO"], notes) == ["MIDCO"] and not notes
    assert pick_universe(hist, [], ["FNOCO"], notes) == ["MIDCO", "OTHER"] and notes

    # 6) result file shape
    res = E.build_result([ready, None], dt.datetime(2026, 11, 13, 19, 45, tzinfo=E.IST),
                         "2026-11-13", None, [])
    assert res["stocks"] == 1 and len(res["rows"]) == 1
    json.dumps(res)

    # 6b) base breakout: strong stock, tight base, then a close above it on volume
    n_ = 400
    bdays = pd.bdate_range(end="2026-11-13", periods=n_)
    trend_ = 100 * np.exp(np.cumsum(np.full(n_ - 18, 0.003)))
    base_ = trend_[-1] * (1 + 0.01 * np.sin(np.arange(15)))
    bo_ = np.array([base_.max() * 1.03, base_.max() * 1.035, base_.max() * 1.04])
    cl = np.concatenate([trend_, base_, bo_])
    vv = np.full(n_, 1e6)
    vv[-18:-3] = 6e5
    vv[-3] = 3e6
    b = E._bars(cl, bdays, noise=0.004, vol=vv)
    b.iloc[-3, b.columns.get_loc("Low")] = cl[-3] * 0.985
    r0 = analyse_breakout("BO", b.iloc[:-2], 0.92, dl)       # the breakout day itself
    r2 = analyse_breakout("BO", b, 0.92, dl)                 # two days later, still holding
    print(json.dumps(r2, indent=1))
    for r_ in (r0, r2):
        assert r_ and r_["stage"] == "Triggered" and r_["stop"] < r_["trigger"] < r_["close"], r_
    assert r0["detail"].startswith("Broke out Wed 11") and r2["detail"] == r0["detail"]
    setup_ = analyse_breakout("BO", b.iloc[:-3], 0.92, dl)   # the day before: setting up
    assert setup_ and setup_["stage"] == "Ready" and setup_["stop"] is None, setup_
    assert setup_["trigger"] >= setup_["close"]
    assert analyse_breakout("BO", b, 0.55, dl) is None       # weak relative strength
    failed = b.copy()
    failed.iloc[-1, failed.columns.get_loc("Close")] = base_.max() * 0.99
    assert analyse_breakout("BO", failed, 0.92, dl) is None  # fell back into the base
    ext = b.copy()
    ext.iloc[-1, [ext.columns.get_loc(x) for x in ("High", "Close")]] = base_.max() * 1.12
    assert analyse_breakout("BO", ext, 0.92, dl) is None     # too far above the level
    assert analyse_breakout("FLAT", E._bars(np.full(n_, 100.0), bdays), 0.92, dl) is None
    group = {"BO": b}
    for q_ in range(4):                # four weaker stocks so BO ranks at the top
        group[f"W{q_}"] = E._bars(100 * np.exp(np.cumsum(np.full(n_, -0.0005 * (q_ + 1)))), bdays)
    br, st_ = breakout_scan(list(group), group, {"BO": dl})
    assert [r_["symbol"] for r_ in br] == ["BO"] and st_ == [], (br, st_)

    # 7) unusual activity on daily bars only (needs the unusual scan file alongside)
    U = load_unusual()
    if U is None:
        print("unusual scan file not found here; step 7 skipped")
    else:
        quiet = U._daily(days, seed=2)
        loud = U._daily(days, seed=3)
        j = len(loud) - 1
        px = loud["Close"].iloc[j - 1]
        loud.iloc[j, [loud.columns.get_loc(x) for x in ("Open", "High", "Low", "Close", "Volume")]] = \
            [px, px * 1.002, px * 0.96, px * 1.001, loud["Volume"].iloc[j] * 4]
        dlv = pd.DataFrame({"date": days[-30:], "deliv_per": [40.0] * 29 + [65.0],
                            "close": [100.0] * 29 + [102.0]})
        out = equity_unusual(U, ["QUIET", "LOUD"], {"QUIET": quiet, "LOUD": loud},
                             {"LOUD": dlv}, {"LOUD": "Trend Pullback · Watch"})
        print(json.dumps(out, indent=1))
        assert [r["symbol"] for r in out] == ["LOUD"], out
        assert list(out[0]["states"]) == ["M", "W", "D"] and out[0]["setup"].startswith("Trend")
        texts = " | ".join(e["text"] for e in out[0]["events"])
        assert "flush absorbed" in texts and "Delivery 65% vs 40% usual" in texts
        assert "hour" not in texts and "OI" not in texts
    print("\nSELFTEST PASSED")
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    selftest() if ap.parse_args().selftest else main_live()
