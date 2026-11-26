#!/usr/bin/env python3
"""
TradeReady - Options check for the strongest stocks (calls only).

For the stocks the site already likes (Top 5 positional, 5+ scan hits, grade-A Ready/Triggered
F&O setups) it reads the NSE option chain and asks whether the options market agrees:

  1. Put writing below    the biggest put OI sits within 5% under the price and puts are being added there
  2. Calls unwinding      call OI within 5% above the price fell today (resistance getting weaker)
  3. PCR supportive       total put OI / call OI is 1 or more, or today more puts were added than calls
  4. IV not expensive     ATM IV is at most 1.3x the stock's 20-day historical volatility
  5. Max pain             max pain is at or above the price (pull up into expiry)

3 of 5 or more: it picks one call, ATM or one strike in the money (delta closest to 0.6), in the
current month if more than 10 sessions are left, else next month, with enough open interest and
volume to get in and out. Premium, lot, cost per lot, breakeven, and Black-Scholes estimates of the
premium at the setup's target and stop are added.

Output: data/options.json (shown only to the admin and full-access users).
Also adds each stock's ATM IV to data/iv_hist.csv.gz so IV history keeps growing.

  python options_scan.py             # live run
  python options_scan.py --selftest  # offline check
"""
import datetime as dt
import importlib.util
import io
import json
import math
import os
import sys
import time

import numpy as np
import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
LOTS_URL = "https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv"
RATE = 0.065


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


# ------------------------------------------------------------------ Black-Scholes (calls)
def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_call(S, K, T, iv, r=RATE):
    """Call price and delta. T in years, iv as a fraction."""
    if T <= 0 or iv <= 0:
        return max(0.0, S - K), (1.0 if S > K else 0.0)
    d1 = (math.log(S / K) + (r + iv * iv / 2) * T) / (iv * math.sqrt(T))
    d2 = d1 - iv * math.sqrt(T)
    return S * _ncdf(d1) - K * math.exp(-r * T) * _ncdf(d2), _ncdf(d1)


# ------------------------------------------------------------------ data
def lot_sizes(session):
    try:
        r = session.get(LOTS_URL, timeout=30)
        df = pd.read_csv(io.StringIO(r.text))
        df.columns = [str(c).strip().upper() for c in df.columns]
        sym = next(c for c in df.columns if c == "SYMBOL")
        months = [c for c in df.columns if c not in ("UNDERLYING", "SYMBOL")]
        out = {}
        for _, row in df.iterrows():
            s = str(row[sym]).strip()
            for c in months:
                v = pd.to_numeric(str(row[c]).strip(), errors="coerce")
                if pd.notna(v) and v > 0:
                    out[s] = int(v)
                    break
        print(f"lot sizes: {len(out)}")
        return out
    except Exception as ex:
        print("lot sizes failed:", ex)
        return {}


def pick_expiry(exps, today):
    """Current month if more than 10 sessions are left, else the next one."""
    ds = []
    for e in exps:
        try:
            ds.append((dt.datetime.strptime(e, "%d-%b-%Y").date(), e))
        except Exception:
            continue
    ds = sorted(d for d in ds if d[0] >= today)
    if not ds:
        return None, None
    left = int(np.busday_count(today, ds[0][0]))
    if left <= 10 and len(ds) > 1:
        return ds[1][1], int(np.busday_count(today, ds[1][0]))
    return ds[0][1], left


def chain(nse, symbol, today):
    info = nse.get(f"option-chain-contract-info?symbol={requests.utils.quote(symbol)}")
    exp, left = pick_expiry((info or {}).get("expiryDates") or [], today)
    if not exp:
        return None
    j = nse.get(f"option-chain-v3?type=Equity&symbol={requests.utils.quote(symbol)}&expiry={exp}")
    rows = ((j or {}).get("records") or {}).get("data") or []
    tab, spot = {}, None
    for d in rows:
        k = d.get("strikePrice")
        for side in ("CE", "PE"):
            o = d.get(side)
            if not o or k is None:
                continue
            spot = spot or o.get("underlyingValue")
            tab.setdefault(k, {})[side] = {"oi": o.get("openInterest") or 0, "chg": o.get("changeinOpenInterest") or 0,
                                           "ltp": o.get("lastPrice") or 0, "iv": o.get("impliedVolatility") or 0,
                                           "vol": o.get("totalTradedVolume") or 0}
    if not tab or not spot:
        return None
    return {"expiry": exp, "left": left, "spot": float(spot), "tab": tab}


def hv20(df):
    ret = np.log(df["Close"]).diff()
    return float(ret.iloc[-20:].std() * math.sqrt(252) * 100)


# ------------------------------------------------------------------ the check
def assess(c, hv, lot, setup):
    """c: chain() result. hv: 20-day historical vol in %. setup: {target, stop} or {}."""
    tab, S = c["tab"], c["spot"]
    ks = sorted(tab)
    ce = {k: tab[k].get("CE", {}) for k in ks}
    pe = {k: tab[k].get("PE", {}) for k in ks}
    below = [k for k in ks if S * 0.95 <= k <= S]
    above = [k for k in ks if S < k <= S * 1.05]
    checks = []
    # 1 put writing below
    if below:
        kp = max(below, key=lambda k: pe[k].get("oi", 0))
        add = sum(pe[k].get("chg", 0) for k in below)
        ok = pe[kp].get("oi", 0) > 0 and add > 0
        checks.append({"t": f"Put writing below: biggest put OI at {kp:g}, puts {'added' if add > 0 else 'cut'} under the price today", "ok": bool(ok), "k": kp})
    else:
        checks.append({"t": "Put writing below: no strikes within 5%", "ok": False})
    # 2 calls unwinding above
    cchg = sum(ce[k].get("chg", 0) for k in above)
    checks.append({"t": f"Calls above the price {'unwinding' if cchg < 0 else 'being added'} today (OI {cchg:+,.0f})", "ok": bool(above and cchg < 0)})
    # 3 PCR
    tce, tpe = sum(v.get("oi", 0) for v in ce.values()), sum(v.get("oi", 0) for v in pe.values())
    ace, ape = sum(max(0, v.get("chg", 0)) for v in ce.values()), sum(max(0, v.get("chg", 0)) for v in pe.values())
    pcr = tpe / tce if tce else None
    checks.append({"t": f"PCR {pcr:.2f}" + (", more puts added than calls today" if ape > ace else "") if pcr else "PCR n/a",
                   "ok": bool(pcr and (pcr >= 1 or ape > ace))})
    # 4 IV vs HV
    atm = min(ks, key=lambda k: abs(k - S))
    ivs = [x for x in (ce[atm].get("iv"), pe[atm].get("iv")) if x]
    iv = sum(ivs) / len(ivs) if ivs else None
    ratio = iv / hv if iv and hv else None
    checks.append({"t": f"ATM IV {iv:.1f}% vs 20-day move {hv:.1f}%" + (f" ({ratio:.2f}x)" if ratio else "") if iv and hv else "IV n/a",
                   "ok": bool(ratio and ratio <= 1.3)})
    # 5 max pain
    pain = min(ks, key=lambda K: sum(ce[s].get("oi", 0) * max(0, K - s) + pe[s].get("oi", 0) * max(0, s - K) for s in ks))
    checks.append({"t": f"Max pain {pain:g} ({(pain / S - 1) * 100:+.1f}% from the price)", "ok": bool(pain >= S * 0.99)})
    passed = sum(1 for x in checks if x["ok"])
    out = {"spot": r1(S, 2), "expiry": c["expiry"], "left": c["left"], "pcr": r1(pcr, 2), "iv": r1(iv), "hv": r1(hv),
           "max_pain": pain, "checks": checks, "passed": passed, "idea": None, "warn": []}
    if passed < 3:
        return out
    # the call: ATM or 1 strike ITM, delta closest to 0.6, liquid
    T = max(c["left"], 1) / 252
    cands = []
    itm = [k for k in ks if k <= S]
    for k in ([max(itm)] if itm else []) + [atm] + ([sorted(itm)[-2]] if len(itm) > 1 else []):
        o = ce.get(k) or {}
        if not o.get("ltp") or o.get("oi", 0) <= 0:
            continue
        kiv = (o.get("iv") or iv or hv) / 100
        _, delta = bs_call(S, k, T, kiv)
        liquid = o.get("oi", 0) * (lot or 1) > 0 and o.get("vol", 0) > 0
        cands.append((abs(delta - 0.6) + (0 if liquid else 1), k, o, kiv, delta, liquid))
    if not cands:
        out["warn"].append("No liquid call near the money")
        return out
    _, k, o, kiv, delta, liquid = min(cands)
    prem = float(o["ltp"])
    idea = {"strike": k, "ltp": r1(prem, 2), "iv": r1(kiv * 100), "delta": r1(delta, 2), "oi": int(o.get("oi", 0)), "vol": int(o.get("vol", 0)),
            "lot": lot, "cost": r1(prem * lot, 0) if lot else None, "breakeven": r1(k + prem, 2), "liquid": liquid}
    tgt, stp = (setup or {}).get("target"), (setup or {}).get("stop")
    if tgt and tgt > S:
        p, _ = bs_call(tgt, k, max(T - 5 / 252, 1 / 252), kiv)          # about a week to get there
        idea.update({"tgt_px": r1(tgt, 2), "tgt_prem": r1(p, 2), "tgt_pct": r1((p / prem - 1) * 100, 0)})
    if stp and stp < S:
        p, _ = bs_call(stp, k, max(T - 2 / 252, 1 / 252), kiv)
        idea.update({"stop_px": r1(stp, 2), "stop_prem": r1(p, 2), "stop_pct": r1((p / prem - 1) * 100, 0)})
    if c["left"] < 10:
        out["warn"].append(f"Only {c['left']} sessions to expiry: time decay is fast")
    if not liquid:
        out["warn"].append("Thin trading in this strike: use limit orders")
    out["idea"] = idea
    return out


# ------------------------------------------------------------------ run
def candidates(site):
    out = {}
    for p in (site.get("top5") or {}).get("picks") or []:
        out.setdefault(p["symbol"], {"why": [], "target": p.get("target"), "stop": p.get("stop"), "setup": p.get("setup")})["why"].append("Top 5")
    fo = set((m["s"] for m in site.get("mtf") or []))
    for s, h in (site.get("hits") or {}).items():
        if h.get("n", 0) >= 5 and s in fo:
            out.setdefault(s, {"why": []})["why"].append(f"🔥{h['n']} scans")
    for r in site.get("ready") or []:
        if r.get("market") == "F&O" and r.get("grade") == "A" and r.get("stage") in ("Ready", "Triggered"):
            c = out.setdefault(r["symbol"], {"why": []})
            c["why"].append(f"{r['setup']} {r['stage']} (A)")
            c.setdefault("target", r.get("target")); c.setdefault("stop", r.get("stop")); c.setdefault("setup", r.get("setup"))
    ban = set((site.get("heads_up") or {}).get("ban") or [])
    return {s: v for s, v in out.items() if s not in ban}


def main():
    E = _load("setups_scan", "setups_scan.py")
    I = _load("intraday_scan", "intraday_scan.py")
    now = dt.datetime.now(IST)
    with open(os.path.join(DATA, "site.json")) as f:
        site = json.load(f)
    cand = candidates(site)
    syms = list(cand)[:35]
    print(f"candidates: {len(cand)}, checking {len(syms)}")
    nse = I.NSE()
    lots = lot_sizes(nse.s)
    daily = E.fetch_bars(syms, period="3mo", interval="1d")
    results = (site.get("heads_up") or {}).get("results") or {}
    rows, ivrows = [], []
    for s in syms:
        try:
            c = chain(nse, s, now.date())
        except Exception as ex:
            print(s, "chain failed:", ex); c = None
        time.sleep(0.6)
        if not c:
            continue
        hv = hv20(daily[s]) if s in daily and len(daily[s]) > 21 else None
        a = assess(c, hv, lots.get(s), cand[s])
        if s in results:
            a["warn"].append(f"Results on {results[s]}: IV can collapse after the numbers")
        if a.get("iv"):
            ivrows.append({"date": pd.Timestamp(now.date()), "symbol": s, "iv": a["iv"]})
        rows.append({"s": s, "why": cand[s]["why"], "setup": cand[s].get("setup"), **a})
    rows.sort(key=lambda r: (-(r["idea"] is not None), -r["passed"], r["s"]))
    path = os.path.join(DATA, "iv_hist.csv.gz")
    if ivrows:
        hist = pd.read_csv(path, parse_dates=["date"]) if os.path.exists(path) else pd.DataFrame(columns=["date", "symbol", "iv"])
        hist = pd.concat([hist, pd.DataFrame(ivrows)], ignore_index=True)
        hist["date"] = pd.to_datetime(hist["date"])
        hist = hist.drop_duplicates(["date", "symbol"], keep="last")
        hist.to_csv(path, index=False, compression="gzip")
    res = {"generated_ist": now.strftime("%Y-%m-%d %H:%M"), "checked": len(rows), "ideas": sum(1 for r in rows if r["idea"]), "rows": rows}
    with open(os.path.join(DATA, "options.json"), "w") as f:
        json.dump(res, f, separators=(",", ":"), allow_nan=False, default=str)
    print(f"options: {len(rows)} checked, {res['ideas']} with a call idea")


def selftest():
    p, d = bs_call(100, 100, 30 / 365, 0.3)
    assert 3 < p < 4 and 0.5 < d < 0.6, (p, d)
    tab = {}
    for k in range(90, 111, 2):
        tab[k] = {"CE": {"oi": 1000 + (k - 90) * 50, "chg": -50 if k > 100 else 10, "ltp": max(0.5, 100 - k + 3), "iv": 28, "vol": 500},
                  "PE": {"oi": 3000 if k == 98 else 800, "chg": 200 if k <= 100 else 0, "ltp": max(0.5, k - 100 + 3), "iv": 30, "vol": 400}}
    c = {"expiry": "29-Dec-2026", "left": 18, "spot": 100.4, "tab": tab}
    a = assess(c, 26.0, 500, {"target": 108, "stop": 96})
    assert a["passed"] >= 3 and a["idea"] and a["idea"]["strike"] in (98, 100), a
    assert a["idea"]["tgt_prem"] > a["idea"]["ltp"] > a["idea"]["stop_prem"], a["idea"]
    assert pick_expiry(["30-Nov-2026", "29-Dec-2026"], dt.date(2026, 11, 26))[0] == "29-Dec-2026"
    assert pick_expiry(["29-Dec-2026", "26-Jan-2027"], dt.date(2026, 11, 26))[0] == "29-Dec-2026"
    print("selftest ok")


if __name__ == "__main__":
    selftest() if "--selftest" in sys.argv else main()
