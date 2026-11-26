#!/usr/bin/env python3
"""
TradeReady - VK Experimentals: four new scanners, run every evening after the equity scan.

  1. Chup-chap kharidari (delivery absorption)
     Price flat (-1% to +1.5%) while delivery value is 2x+ its 20-day average and delivery % is 55+.
     Big money buying without pushing the price. Days with a bulk/block deal are flagged.
  2. RS lead (relative strength new high before price)
     The stock / Nifty line is at a 52-week high, while the price itself is still 4%+ under
     its own 52-week high. Leaders show up here before they break out.
  3. Squeeze + cheap volatility ("saste options")
     Daily squeeze ON for 5+ days and 20-day historical volatility in the bottom 25% of its
     1-year range. ATM option IV is saved every day for these stocks; once a stock has 40+ days
     of IV history, its IV rank is shown too (until then HV stands in for IV).
  4. Expiry short squeeze
     Shorts built up earlier in the series (OI up while price fell), price now holding above
     its 20 EMA and rising, near-month OI being closed rather than rolled, futures premium
     turning positive. Live in the last 5 sessions before the monthly expiry; other days it lists
     candidates only.

Files
  data/experimental.json   what the site shows
  data/fo_series.csv.gz    near/next-month futures OI, futures and spot price per stock per day
  data/iv_hist.csv.gz      daily ATM IV for squeeze stocks
  data/lab_log.csv         every pick, to check later how they did

  python experimental_scan.py             # live run
  python experimental_scan.py --selftest  # offline logic check
"""
import datetime as dt
import importlib.util
import io
import json
import os
import sys
import time
import zipfile

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
FO_URL = "https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{d}_F_0000.csv.zip"


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


# ------------------------------------------------------------------ data
def fo_series(E, session, now, back=30):
    """Near- and next-month futures OI, near futures price and spot, per stock per day."""
    path = os.path.join(DATA, "fo_series.csv.gz")
    hist = pd.read_csv(path, parse_dates=["date", "near_exp"]) if os.path.exists(path) else pd.DataFrame()
    have = set(pd.to_datetime(hist["date"]).dt.normalize()) if len(hist) else set()
    today = pd.Timestamp(now.date())
    want = [today - pd.Timedelta(days=i) for i in range(back)]
    want = [d for d in want if d.weekday() < 5 and d not in have and (d < today or now.hour >= 18)]
    new = []
    for d in sorted(want):
        raw = E.nse_get(session, FO_URL.format(d=d.strftime("%Y%m%d")), tries=2)
        if raw is None:
            continue
        try:
            new.append(parse_series(raw, d))
        except Exception as ex:
            print("fo series", d.date(), "unreadable:", ex)
        time.sleep(0.8)
    if new:
        hist = pd.concat([hist] + new, ignore_index=True)
        hist["date"] = pd.to_datetime(hist["date"])
        hist = hist[hist["date"] >= today - pd.Timedelta(days=90)]
        hist.sort_values(["date", "symbol"]).to_csv(path, index=False, compression="gzip")
    print(f"fo series: {hist['date'].nunique() if len(hist) else 0} days ({len(new)} new)")
    return hist


def parse_series(raw, d):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        df = pd.read_csv(z.open(z.namelist()[0]), low_memory=False)
    df.columns = [c.strip() for c in df.columns]
    df = df[df["FinInstrmTp"].astype(str).str.strip() == "STF"].copy()
    df["symbol"] = df["TckrSymb"].astype(str).str.strip()
    for c in ("ClsPric", "SttlmPric", "OpnIntrst", "UndrlygPric"):
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    df["XpryDt"] = pd.to_datetime(df["XpryDt"], errors="coerce")
    df["px"] = np.where(df["SttlmPric"] > 0, df["SttlmPric"], df["ClsPric"])
    rows = []
    for s, g in df.sort_values("XpryDt").groupby("symbol"):
        g = g.reset_index(drop=True)
        rows.append({"date": d, "symbol": s, "near_exp": g.loc[0, "XpryDt"], "near_oi": g.loc[0, "OpnIntrst"],
                     "next_oi": g.loc[1, "OpnIntrst"] if len(g) > 1 else 0, "fut": g.loc[0, "px"],
                     "spot": g.loc[0, "UndrlygPric"]})
    return pd.DataFrame(rows)


def deals_today(days=3):
    try:
        d = pd.read_csv(os.path.join(DATA, "deals.csv.gz"))
        d["date"] = pd.to_datetime(d["date"])
        return set(d.loc[d["date"] >= d["date"].max() - pd.Timedelta(days=days), "symbol"].astype(str))
    except Exception:
        return set()


# ------------------------------------------------------------------ 1. delivery absorption
def delivery_absorption(dl, universe, deal_syms, fo_syms):
    """dl: date, symbol, close, turnover_lacs, deliv_per (NSE cash bhavcopy history)."""
    out = []
    dl = dl[dl["symbol"].isin(universe)].copy()
    dl["dv"] = dl["turnover_lacs"] * dl["deliv_per"] / 100            # delivery value, Rs lakh
    last = dl["date"].max()
    for s, g in dl.groupby("symbol"):
        g = g.sort_values("date")
        if len(g) < 22 or g["date"].iloc[-1] != last:
            continue
        base = g["dv"].iloc[-22:-1].mean()
        if not base or base <= 0 or g["turnover_lacs"].iloc[-21:-1].mean() < 500:   # under Rs 5 Cr a day: too thin
            continue
        c, pc = g["close"].iloc[-1], g["close"].iloc[-2]
        chg = (c / pc - 1) * 100
        ratio = g["dv"].iloc[-1] / base
        dp = g["deliv_per"].iloc[-1]
        # quiet days in the last 5: delivery 1.5x+ with price within -1%..+1.5%
        tail = g.iloc[-6:]
        chgs = tail["close"].pct_change().iloc[1:] * 100
        quiet = int(sum((tail["dv"].iloc[1:] / base >= 1.5) & chgs.between(-1, 1.5)))
        if not (ratio >= 2 and dp >= 55 and -1 <= chg <= 1.5):
            continue
        five = (c / g["close"].iloc[-6] - 1) * 100 if len(g) >= 6 else None
        score = min(ratio, 5) * 10 + (dp - 55) * 0.6 + quiet * 6 - (8 if s in deal_syms else 0)
        out.append({"s": s, "c": r1(c, 2), "chg": r1(chg, 2), "ratio": r1(ratio), "dp": r1(dp), "dp20": r1(g["deliv_per"].iloc[-21:-1].mean()),
                    "quiet": quiet, "d5": r1(five), "cr": r1(g["dv"].iloc[-1] / 100, 1), "deal": s in deal_syms,
                    "fo": s in fo_syms, "score": r1(score)})
    return sorted(out, key=lambda x: -x["score"])[:40]


# ------------------------------------------------------------------ 2. RS lead
def rs_lead(daily, nifty, fo_syms):
    out = []
    n = nifty["Close"].dropna()
    for s, df in daily.items():
        c = df["Close"].dropna()
        if len(c) < 200:
            continue
        j = c.index.intersection(n.index)
        if len(j) < 200:
            continue
        cc, nn = c.loc[j].iloc[-250:], n.loc[j].iloc[-250:]
        rs = cc / nn
        rs_hi = rs.max()
        px_hi = cc.max()
        new_hi_days = int((rs.iloc[-5:] >= rs.iloc[:-5].max() * 0.999).sum())     # RS at a new high in the last 5 days
        below = (cc.iloc[-1] / px_hi - 1) * 100
        if new_hi_days == 0 or rs.iloc[-1] < rs_hi * 0.99 or below > -4:
            continue
        nifty_dd = (nn.iloc[-1] / nn.max() - 1) * 100
        r3 = (cc.iloc[-1] / cc.iloc[-63] - 1) * 100 if len(cc) > 63 else None
        n3 = (nn.iloc[-1] / nn.iloc[-63] - 1) * 100 if len(nn) > 63 else None
        sma200 = cc.rolling(200).mean().iloc[-1]
        score = 50 + min(new_hi_days, 5) * 4 + max(-15, min(15, ((r3 or 0) - (n3 or 0)))) + (10 if cc.iloc[-1] > sma200 else -10) \
            - abs(below + 8) * 0.8                                         # sweet spot about 6-10% under the high
        out.append({"s": s, "c": r1(cc.iloc[-1], 2), "below": r1(below), "rs_days": new_hi_days,
                    "r3": r1(r3), "n3": r1(n3), "nifty_dd": r1(nifty_dd), "a200": bool(cc.iloc[-1] > sma200),
                    "spark": [r1(x, 4) for x in (rs.iloc[-60:] / rs.iloc[-60]).tolist()], "fo": s in fo_syms, "score": r1(score)})
    return sorted(out, key=lambda x: -x["score"])[:40]


# ------------------------------------------------------------------ 3. squeeze + cheap volatility
def squeeze_cheap(E, daily, fo_syms, iv_hist):
    out = []
    for s, df in daily.items():
        if s not in fo_syms or len(df) < 260:
            continue
        ind = E.indicators(df["High"].values.astype(float), df["Low"].values.astype(float), df["Close"].values.astype(float))
        sq = ind["sq"]
        days = 0
        for v in sq[::-1]:
            if not v:
                break
            days += 1
        if days < 5:
            continue
        ret = np.log(df["Close"]).diff()
        hv = ret.rolling(20).std() * np.sqrt(252) * 100
        hv1y = hv.iloc[-252:].dropna()
        if len(hv1y) < 200:
            continue
        hv_now = hv.iloc[-1]
        hv_rank = (hv_now - hv1y.min()) / (hv1y.max() - hv1y.min()) * 100 if hv1y.max() > hv1y.min() else 50
        if hv_rank > 25:
            continue
        w = E.tf_view(E.to_weekly(df), 3)
        if w["ok"] and w["state"] == "Bearish":
            continue
        ivs = iv_hist[iv_hist["symbol"] == s].sort_values("date") if len(iv_hist) else pd.DataFrame()
        iv_now = ivs["iv"].iloc[-1] if len(ivs) else None
        iv_rank = None
        if len(ivs) >= 40 and ivs["iv"].max() > ivs["iv"].min():
            iv_rank = (iv_now - ivs["iv"].min()) / (ivs["iv"].max() - ivs["iv"].min()) * 100
        mom, prev = ind["mom"][-1], ind["mom"][-2]
        score = 60 - hv_rank + min(days, 20) + (10 if mom > prev else 0) + (8 if w["ok"] and w["mom"] > 0 else 0) \
            - ((iv_rank - 25) * 0.6 if iv_rank is not None and iv_rank > 25 else 0)
        out.append({"s": s, "c": r1(df["Close"].iloc[-1], 2), "sq_days": days, "hv": r1(hv_now), "hv_rank": r1(hv_rank, 0),
                    "iv": r1(iv_now), "iv_rank": r1(iv_rank, 0), "iv_days": int(len(ivs)), "turning": bool(mom > prev),
                    "w": "Impulse" if w["impulse"] else w["state"], "score": r1(score)})
    return sorted(out, key=lambda x: -x["score"])[:30]


def collect_iv(I, syms, now):
    """Save today's ATM IV for these stocks (NSE option chain)."""
    path = os.path.join(DATA, "iv_hist.csv.gz")
    hist = pd.read_csv(path, parse_dates=["date"]) if os.path.exists(path) else pd.DataFrame(columns=["date", "symbol", "iv"])
    today = pd.Timestamp(now.date())
    have = set(hist.loc[hist["date"] == today, "symbol"]) if len(hist) else set()
    todo = [s for s in syms if s not in have][:45]
    if not todo:
        return hist
    try:
        nse = I.NSE()
    except Exception as ex:
        print("iv: NSE session failed:", ex)
        return hist
    rows = []
    for s in todo:
        try:
            c = I.option_chain(nse, s, "Equity", near=1)
            if c and c.get("iv"):
                rows.append({"date": today, "symbol": s, "iv": c["iv"]})
        except Exception as ex:
            print("iv", s, "failed:", ex)
        time.sleep(0.6)
    if rows:
        hist = pd.concat([hist, pd.DataFrame(rows)], ignore_index=True)
        hist["date"] = pd.to_datetime(hist["date"])
        hist = hist[hist["date"] >= today - pd.Timedelta(days=400)]
        hist.to_csv(path, index=False, compression="gzip")
    print(f"iv: {len(rows)} of {len(todo)} saved")
    return hist


# ------------------------------------------------------------------ 4. expiry short squeeze
def expiry_squeeze(ser, daily, fo_syms, now):
    if ser is None or not len(ser):
        return {"window": False, "rows": [], "note": "Futures series history not ready yet."}
    ser = ser.copy()
    ser["date"] = pd.to_datetime(ser["date"])
    last = ser["date"].max()
    cur = ser[ser["date"] == last]
    exp = cur["near_exp"].mode()
    exp = pd.Timestamp(exp.iloc[0]) if len(exp) else None
    bdays = int(np.busday_count(last.date(), exp.date())) if exp is not None else None
    window = bdays is not None and 0 <= bdays <= 5
    rows = []
    for s, g in ser[ser["date"] >= last - pd.Timedelta(days=45)].groupby("symbol"):
        g = g.sort_values("date")
        if s not in fo_syms or len(g) < 12 or g["date"].iloc[-1] != last:
            continue
        g = g[g["near_exp"] == g["near_exp"].iloc[-1]]              # this series only
        if len(g) < 8:
            continue
        df = daily.get(s)
        if df is None or len(df) < 30:
            continue
        c = df["Close"]
        ema20 = c.ewm(span=20, adjust=False).mean().iloc[-1]
        # earlier in the series: OI built while price fell (short build-up)
        mid = len(g) - 4
        built = (g["near_oi"].iloc[mid] / g["near_oi"].iloc[0] - 1) * 100 if g["near_oi"].iloc[0] else 0
        fell = (g["fut"].iloc[mid] / g["fut"].iloc[0] - 1) * 100
        # last 3 sessions: price up, near OI down (covering), premium turning positive
        px3 = (g["fut"].iloc[-1] / g["fut"].iloc[-4] - 1) * 100
        oi3 = (g["near_oi"].iloc[-1] / g["near_oi"].iloc[-4] - 1) * 100 if g["near_oi"].iloc[-4] else 0
        tot = g["near_oi"].iloc[-1] + g["next_oi"].iloc[-1]
        roll = g["next_oi"].iloc[-1] / tot * 100 if tot else None
        basis = (g["fut"].iloc[-1] / g["spot"].iloc[-1] - 1) * 100 if g["spot"].iloc[-1] else None
        basis0 = (g["fut"].iloc[-4] / g["spot"].iloc[-4] - 1) * 100 if g["spot"].iloc[-4] else None
        if not (built >= 8 and fell <= 0 and px3 > 0 and c.iloc[-1] > ema20):
            continue
        score = min(built, 40) * 0.8 + min(px3, 8) * 3 + (max(0, -oi3) * 0.8) + ((basis - basis0) * 20 if basis is not None and basis0 is not None else 0) \
            + ((40 - roll) * 0.4 if roll is not None and window else 0)
        rows.append({"s": s, "c": r1(c.iloc[-1], 2), "built": r1(built), "fell": r1(fell), "px3": r1(px3), "oi3": r1(oi3),
                     "roll": r1(roll), "basis": r1(basis, 2), "basis0": r1(basis0, 2), "score": r1(score)})
    return {"window": window, "expiry": exp.strftime("%Y-%m-%d") if exp is not None else None, "days_left": bdays,
            "rows": sorted(rows, key=lambda x: -x["score"])[:25],
            "note": None if window else "Not expiry week: these are candidates; the setup matters in the last 5 sessions."}


# ------------------------------------------------------------------ run
def log_picks(res, now):
    path = os.path.join(DATA, "lab_log.csv")
    day = now.strftime("%Y-%m-%d")
    log = pd.read_csv(path) if os.path.exists(path) else pd.DataFrame(columns=["date"])
    if (log["date"].astype(str) == day).any():
        return
    rows = [{"date": day, "scanner": k, "rank": i + 1, "symbol": x["s"], "close": x["c"], "score": x["score"]}
            for k, key in (("delivery", "delivery"), ("rs_lead", "rs_lead"), ("squeeze_iv", "squeeze_iv"))
            for i, x in enumerate(res[key][:10])] + \
           [{"date": day, "scanner": "expiry", "rank": i + 1, "symbol": x["s"], "close": x["c"], "score": x["score"]}
            for i, x in enumerate(res["expiry"]["rows"][:10])]
    if rows:
        pd.concat([log, pd.DataFrame(rows)], ignore_index=True).to_csv(path, index=False)


def main():
    E = _load("setups_scan", "setups_scan.py")
    I = _load("intraday_scan", "intraday_scan.py")
    now = dt.datetime.now(IST)
    session = E.nse_session()
    fo = pd.read_csv(os.path.join(DATA, "fo_oi.csv.gz"), parse_dates=["date"])
    fo_syms = set(fo.loc[fo["date"] == fo["date"].max(), "symbol"])
    try:
        with open(os.path.join(DATA, "site.json")) as f:
            levels = json.load(f).get("levels") or {}
    except Exception:
        levels = {}
    universe = sorted(fo_syms | set(levels))
    print(f"universe: {len(universe)} stocks ({len(fo_syms)} F&O)")

    dl = pd.read_csv(os.path.join(DATA, "equity_deliv.csv.gz"), parse_dates=["date"])
    dl = dl[dl["series"].astype(str).str.strip() == "EQ"]
    deliv = delivery_absorption(dl, set(universe), deals_today(), fo_syms)
    print("delivery absorption:", len(deliv))

    daily = E.fetch_bars(universe, period="2y", interval="1d")
    nd = E.yf_download(["^NSEI"], period="2y", interval="1d")
    nifty = (nd["^NSEI"] if isinstance(nd.columns, pd.MultiIndex) else nd).dropna()
    nifty.index = pd.to_datetime(nifty.index).tz_localize(None) if nifty.index.tz is not None else pd.to_datetime(nifty.index)
    if now.hour < 16:                                     # completed days only
        cut = pd.Timestamp(now.date())
        daily = {s: d[d.index < cut] for s, d in daily.items()}
        nifty = nifty[nifty.index < cut]
    print(f"daily bars: {len(daily)} stocks")
    rs = rs_lead(daily, nifty, fo_syms)
    print("rs lead:", len(rs))

    iv_path = os.path.join(DATA, "iv_hist.csv.gz")
    iv_hist = pd.read_csv(iv_path, parse_dates=["date"]) if os.path.exists(iv_path) else pd.DataFrame(columns=["date", "symbol", "iv"])
    sq = squeeze_cheap(E, daily, fo_syms, iv_hist)
    tracked = sorted(set(x["s"] for x in sq) | (set(iv_hist["symbol"]) if len(iv_hist) else set()))
    iv_hist = collect_iv(I, tracked, now)
    sq = squeeze_cheap(E, daily, fo_syms, iv_hist)        # again, with today's IV
    print("squeeze + cheap vol:", len(sq))

    ser = fo_series(E, session, now)
    ex = expiry_squeeze(ser, daily, fo_syms, now)
    print("expiry squeeze:", len(ex["rows"]), "window" if ex["window"] else "")

    res = {"generated_ist": now.strftime("%Y-%m-%d %H:%M"), "deliv_asof": str(dl["date"].max().date()),
           "delivery": deliv, "rs_lead": rs, "squeeze_iv": sq, "expiry": ex,
           "nifty_dd": rs[0]["nifty_dd"] if rs else None}
    with open(os.path.join(DATA, "experimental.json"), "w") as f:
        json.dump(res, f, separators=(",", ":"), allow_nan=False, default=str)
    log_picks(res, now)
    print("wrote data/experimental.json")


# ------------------------------------------------------------------ offline check
def selftest():
    idx = pd.bdate_range("2025-01-01", periods=300)
    rng = np.random.default_rng(1)
    nifty = pd.DataFrame({"Close": 100 * np.cumprod(1 + rng.normal(0, 0.008, 300))}, index=idx)
    # leader: beats Nifty, RS at a high, price 7% under its peak
    lead = nifty["Close"] * np.linspace(1, 1.6, 300)
    lead.iloc[-40:] = lead.iloc[-40:] * 0.93 * (nifty["Close"].iloc[-40:].max() / nifty["Close"].iloc[-40:])
    nifty.iloc[-40:, 0] = nifty["Close"].iloc[-40:] * np.linspace(1, 0.88, 40)
    lead = lead.copy()
    daily = {"LEAD": pd.DataFrame({"Close": lead, "High": lead * 1.01, "Low": lead * 0.99}, index=idx)}
    r = rs_lead(daily, nifty, {"LEAD"})
    assert r and r[0]["s"] == "LEAD" and r[0]["below"] <= -4, r
    # delivery absorption
    days = pd.bdate_range("2026-10-01", periods=25)
    dl = pd.DataFrame({"date": np.repeat(days, 1), "symbol": "ABC", "close": 100.0, "turnover_lacs": 1000.0, "deliv_per": 40.0})
    dl.loc[dl.index[-1], ["turnover_lacs", "deliv_per", "close"]] = [2500.0, 65.0, 100.5]
    a = delivery_absorption(dl, {"ABC"}, set(), {"ABC"})
    assert a and a[0]["s"] == "ABC" and a[0]["ratio"] >= 2, a
    dl.loc[dl.index[-1], "close"] = 104.0                    # price ran: not quiet buying
    assert not delivery_absorption(dl, {"ABC"}, set(), set())
    # expiry squeeze
    sd = pd.bdate_range("2026-10-27", periods=20)
    exp = pd.Timestamp("2026-11-24")
    oi = np.r_[np.linspace(100, 140, 16), [135, 128, 120, 112]]
    px = np.r_[np.linspace(100, 94, 16), [96, 98, 100, 102]]
    ser = pd.DataFrame({"date": sd, "symbol": "SQZ", "near_exp": exp, "near_oi": oi, "next_oi": 20, "fut": px, "spot": px - 0.1})
    c = pd.Series(np.r_[np.full(40, 90.0), px], index=pd.bdate_range("2026-08-01", periods=60))
    e = expiry_squeeze(ser, {"SQZ": pd.DataFrame({"Close": c})}, {"SQZ"}, dt.datetime(2026, 11, 23))
    assert e["rows"] and e["rows"][0]["s"] == "SQZ" and e["window"], e
    print("selftest ok")


if __name__ == "__main__":
    selftest() if "--selftest" in sys.argv else main()
