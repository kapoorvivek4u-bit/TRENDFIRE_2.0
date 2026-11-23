"""Intraday + BTST scanner for TradeReady (admin only).

Runs every 15 minutes in market hours from GitHub Actions. Free data:
  - yfinance 5-minute candles for every F&O stock and Nifty / Bank Nifty (a few minutes late)
  - NSE live OI spurts (intraday futures + options OI change per stock)
  - NSE option chain (Nifty, Bank Nifty, and the top stock candidates)

The result is NOT written to this public repository. It is sent straight to the site
(tradeready.in/api/ingest/intraday) and kept in Cloudflare, where only the admin can read it.
"""
import datetime as dt
import json
import math
import os
import sys
import time

import numpy as np
import pandas as pd
import requests

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
DATA = "data"
SITE = os.environ.get("SITE_URL", "https://tradeready.in")
KEY = os.environ.get("INGEST_KEY", "")


def r1(x, n=1):
    try:
        x = float(x)
        return None if math.isnan(x) or math.isinf(x) else round(x, n)
    except (TypeError, ValueError):
        return None


def jload(name):
    try:
        with open(os.path.join(DATA, name)) as f:
            return json.load(f)
    except Exception:
        return {}


# ------------------------------------------------------------------ NSE session
class NSE:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.nseindia.com/option-chain"})
        try:
            self.s.get("https://www.nseindia.com/option-chain", timeout=20)
        except Exception as ex:
            print("nse warm-up failed:", ex)

    def get(self, path, tries=2):
        for i in range(tries):
            try:
                r = self.s.get("https://www.nseindia.com/api/" + path, timeout=25)
                if r.ok and r.text.strip() not in ("", "{}"):
                    return r.json()
            except Exception as ex:
                print("nse", path, "failed:", ex)
            time.sleep(1.5 * (i + 1))
        return None


# ------------------------------------------------------------------ option chain
def option_chain(nse, symbol, kind="Indices", near=8):
    info = nse.get(f"option-chain-contract-info?symbol={requests.utils.quote(symbol)}")
    exps = (info or {}).get("expiryDates") or []
    if not exps:
        return None
    rows = []
    for exp in exps[:2]:                              # on expiry day after the close, the next expiry
        j = nse.get(f"option-chain-v3?type={kind}&symbol={requests.utils.quote(symbol)}&expiry={exp}")
        rows = ((j or {}).get("records") or {}).get("data") or []
        if rows and any((d.get("CE") or {}).get("openInterest") for d in rows):
            break
    if not rows:
        return None
    spot = None
    tab = {}
    for d in rows:
        k = d.get("strikePrice")
        for side in ("CE", "PE"):
            o = d.get(side)
            if not o:
                continue
            spot = spot or o.get("underlyingValue")
            t = tab.setdefault(k, {"k": k})
            t[side] = {"oi": o.get("openInterest") or 0, "chg": o.get("changeinOpenInterest") or 0,
                       "ltp": o.get("lastPrice") or 0, "iv": o.get("impliedVolatility") or 0,
                       "vol": o.get("totalTradedVolume") or 0}
    ks = sorted(tab)
    if not ks or not spot:
        return None
    ce = {k: tab[k].get("CE", {}) for k in ks}
    pe = {k: tab[k].get("PE", {}) for k in ks}
    tot_ce, tot_pe = sum(v.get("oi", 0) for v in ce.values()), sum(v.get("oi", 0) for v in pe.values())
    chg_ce, chg_pe = sum(v.get("chg", 0) for v in ce.values()), sum(v.get("chg", 0) for v in pe.values())
    atm = min(ks, key=lambda k: abs(k - spot))
    pain = min(ks, key=lambda K: sum(ce[s].get("oi", 0) * max(0, K - s) + pe[s].get("oi", 0) * max(0, s - K) for s in ks))
    above = [k for k in ks if k >= spot]
    below = [k for k in ks if k <= spot]
    top = lambda d, keys, f, n=3: [{"k": k, "v": d[k].get(f, 0)} for k in sorted(keys, key=lambda k: -d[k].get(f, 0))[:n] if d[k].get(f, 0) > 0]
    i = ks.index(atm)
    band = ks[max(0, i - near): i + near + 1]
    straddle = (ce[atm].get("ltp") or 0) + (pe[atm].get("ltp") or 0)
    iv = [x for x in (ce[atm].get("iv"), pe[atm].get("iv")) if x]
    out = {
        "symbol": symbol, "expiry": exp, "spot": r1(spot, 2), "atm": atm,
        "pcr": r1(tot_pe / tot_ce, 2) if tot_ce else None,
        "chg_pcr": r1(chg_pe / chg_ce, 2) if chg_ce > 0 and chg_pe > 0 else None,
        "chg_ce": chg_ce, "chg_pe": chg_pe, "max_pain": pain,
        "res": top(ce, above, "oi"), "sup": top(pe, below, "oi"),
        "ce_add": top(ce, ks, "chg"), "pe_add": top(pe, ks, "chg"),
        "straddle": r1(straddle, 2), "move_pct": r1(straddle / spot * 100, 2) if spot else None,
        "iv": r1(sum(iv) / len(iv), 1) if iv else None,
        "rows": [{"k": k, "ce_oi": ce[k].get("oi", 0), "ce_chg": ce[k].get("chg", 0), "ce_ltp": ce[k].get("ltp", 0),
                  "pe_oi": pe[k].get("oi", 0), "pe_chg": pe[k].get("chg", 0), "pe_ltp": pe[k].get("ltp", 0)} for k in band],
    }
    out["read"] = chain_read(out)
    return out


def chain_read(c):
    """Plain-words reading of an option chain."""
    words = []
    if c["pcr"] is not None:
        words.append(f"PCR {c['pcr']}: " + ("puts heavier, writers lean bullish" if c["pcr"] >= 1.2 else
                                               "calls heavier, writers lean bearish" if c["pcr"] <= 0.8 else "balanced"))
    if c["res"]:
        words.append(f"Call wall {c['res'][0]['k']:g}")
    if c["sup"]:
        words.append(f"put base {c['sup'][0]['k']:g}")
    if c["chg_ce"] and c["chg_pe"]:
        if c["chg_pe"] > 1.5 * c["chg_ce"] > 0:
            words.append("today more puts written (support building)")
        elif c["chg_ce"] > 1.5 * c["chg_pe"] > 0:
            words.append("today more calls written (resistance building)")
    words.append(f"max pain {c['max_pain']:g}")
    if c["move_pct"]:
        words.append(f"ATM straddle prices a ±{c['move_pct']}% move")
    return "; ".join(words)


# ------------------------------------------------------------------ candles
def candles(symbols):
    import yfinance as yf
    tick = {s: s + ".NS" for s in symbols}
    tick.update({"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK"})
    out = {}
    names = list(tick.values())
    for i in range(0, len(names), 80):
        part = names[i:i + 80]
        try:
            d = yf.download(part, period="5d", interval="5m", progress=False, group_by="ticker", threads=True, auto_adjust=False)
        except Exception as ex:
            print("yfinance failed:", ex)
            continue
        for s, t in tick.items():
            if t in part and t in d.columns.get_level_values(0):
                df = d[t].dropna(subset=["Close"])
                if len(df):
                    df.index = df.index.tz_convert(IST) if df.index.tz is not None else df.index.tz_localize("UTC").tz_convert(IST)
                    out[s] = df
    return out


def squeeze_15m(df):
    """TTM-style squeeze on 15-minute bars: on now / fired in the last 2 bars, and momentum direction."""
    b = df.resample("15min", label="right", closed="right").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}).dropna()
    if len(b) < 25:
        return None
    c, h, l = b.Close, b.High, b.Low
    ma, sd = c.rolling(20).mean(), c.rolling(20).std()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(20).mean()
    on = ((ma + 2 * sd) < (ma + 1.5 * atr)) & ((ma - 2 * sd) > (ma - 1.5 * atr))
    mid = (h.rolling(20).max() + l.rolling(20).min()) / 2
    mom = c - (mid + ma) / 2
    fired = bool(on.iloc[-3] or on.iloc[-2]) and not bool(on.iloc[-1])
    rising = mom.iloc[-1] > mom.iloc[-2]
    return {"on": bool(on.iloc[-1]), "fired": fired, "dir": 1 if mom.iloc[-1] > 0 else -1, "rising": bool(rising)}


def read_stock(s, df, nifty_chg):
    days = sorted({d.date() for d in df.index})
    if len(days) < 2:
        return None
    today, prev = days[-1], days[:-1]
    t = df[df.index.date == today]
    p = df[df.index.date == prev[-1]]
    if len(t) < 2 or not len(p):
        return None
    pc = float(p.Close.iloc[-1])
    c, hi, lo, op = float(t.Close.iloc[-1]), float(t.High.max()), float(t.Low.min()), float(t.Open.iloc[0])
    tp = (t.High + t.Low + t.Close) / 3
    v = t.Volume.replace(0, np.nan)
    vwap = float((tp * v).sum() / v.sum()) if v.sum() and not np.isnan(v.sum()) else None
    vw_series = (tp * v).cumsum() / v.cumsum()
    o = t.iloc[:6]                                   # first 30 minutes
    orh, orl = float(o.High.max()), float(o.Low.min())
    orb = 1 if c > orh and len(t) > 6 else -1 if c < orl and len(t) > 6 else 0
    n = len(t)
    past = [df[df.index.date == d].Volume.iloc[:n].sum() for d in prev[-4:]]
    past = [x for x in past if x > 0]
    rvol = float(t.Volume.sum() / np.mean(past)) if past and t.Volume.sum() else None
    chg = (c / pc - 1) * 100
    reclaim = vwap is not None and c > vwap and bool((t.Close.iloc[-4:-1] < vw_series.iloc[-4:-1]).any())
    lose = vwap is not None and c < vwap and bool((t.Close.iloc[-4:-1] > vw_series.iloc[-4:-1]).any())
    pos = (c - lo) / (hi - lo) if hi > lo else 0.5
    sq = squeeze_15m(df)
    return {"s": s, "c": r1(c, 2), "chg": r1(chg, 2), "rs": r1(chg - nifty_chg, 2) if nifty_chg is not None else None,
            "gap": r1((op / pc - 1) * 100, 2), "hi": r1(hi, 2), "lo": r1(lo, 2), "pc": r1(pc, 2),
            "vwap": r1(vwap, 2), "above_vwap": vwap is not None and c > vwap, "reclaim": reclaim, "lose": lose,
            "orh": r1(orh, 2), "orl": r1(orl, 2), "orb": orb, "rvol": r1(rvol, 2), "pos": r1(pos, 2),
            "sq": sq, "spark": [r1(x, 2) for x in t.Close.iloc[-40:].tolist()], "date": str(today), "_t": t, "_vw": vw_series}


def bars(x):
    """Today's 5-minute candles in a compact form for the cards: slot (0 = 9:15), OHLC, volume, VWAP."""
    t, vw = x.get("_t"), x.get("_vw")
    if t is None or not len(t):
        return None
    slot = [int(((d.hour * 60 + d.minute) - (9 * 60 + 15)) // 5) for d in t.index]
    return {"i": slot, "o": [r1(v, 2) for v in t.Open], "h": [r1(v, 2) for v in t.High], "l": [r1(v, 2) for v in t.Low],
            "c": [r1(v, 2) for v in t.Close], "v": [int(v) if v == v else 0 for v in t.Volume],
            "vw": [r1(v, 2) for v in vw] if vw is not None and t.Volume.sum() > 0 else None}


# ------------------------------------------------------------------ scoring
def score(x, oi, ctx):
    tags_l, tags_s = [], []
    L = S = 0.0
    if x["above_vwap"]:
        L += 2; tags_l.append("Above VWAP")
    else:
        S += 2; tags_s.append("Below VWAP")
    if x["reclaim"]:
        L += 1; tags_l.append("VWAP reclaim")
    if x["lose"]:
        S += 1; tags_s.append("Lost VWAP")
    if x["orb"] == 1:
        L += 2; tags_l.append("Above 30-min range")
    elif x["orb"] == -1:
        S += 2; tags_s.append("Below 30-min range")
    rs = x["rs"] or 0
    if rs > 0:
        L += min(rs, 3); tags_l.append(f"RS {rs:+.1f}% vs Nifty")
    else:
        S += min(-rs, 3); tags_s.append(f"RS {rs:+.1f}% vs Nifty")
    if (x["rvol"] or 0) >= 1.5:
        L += 1.5 if x["chg"] > 0 else 0; S += 1.5 if x["chg"] < 0 else 0
        (tags_l if x["chg"] > 0 else tags_s).append(f"Volume {x['rvol']:.1f}x")
    sq = x["sq"] or {}
    if sq.get("fired"):
        if sq["dir"] > 0:
            L += 2; tags_l.append("15-min squeeze fired up")
        else:
            S += 2; tags_s.append("15-min squeeze fired down")
    elif sq.get("on"):
        (tags_l if sq.get("dir", 0) > 0 else tags_s).append("15-min squeeze on")
    if (x["pos"] or 0) >= 0.85:
        L += 1; tags_l.append("Near day high")
    elif (x["pos"] or 1) <= 0.15:
        S += 1; tags_s.append("Near day low")
    o = oi.get(x["s"])
    if o and o.get("oi") is not None:
        if o["oi"] >= 3 and x["chg"] > 0:
            L += 2; tags_l.append(f"Long build-up OI {o['oi']:+.0f}%")
        elif o["oi"] >= 3 and x["chg"] < 0:
            S += 2; tags_s.append(f"Short build-up OI {o['oi']:+.0f}%")
        elif o["oi"] <= -3 and x["chg"] > 0:
            L += 1; tags_l.append(f"Short covering OI {o['oi']:+.0f}%")
        elif o["oi"] <= -3 and x["chg"] < 0:
            S += 1; tags_s.append(f"Long unwinding OI {o['oi']:+.0f}%")
    sec = ctx["sectors"].get(x["s"], {})
    if sec.get("stage") in ("Leading", "Improving"):
        L += 1; tags_l.append(f"{sec.get('sector')} {sec['stage'].lower()}")
    elif sec.get("stage") in ("Lagging", "Weakening"):
        S += 1; tags_s.append(f"{sec.get('sector')} {sec['stage'].lower()}")
    st = ctx["states"].get(x["s"], {})
    if st.get("D") in ("Impulse", "Bullish"):
        L += 1; tags_l.append(f"Daily {st['D'].lower()}")
    elif st.get("D") in ("Bearish", "Topping"):
        S += 1; tags_s.append(f"Daily {st['D'].lower()}")
    return round(L, 1), round(S, 1), tags_l, tags_s


def btst(x, oi, ctx):
    """Score for holding overnight: strong close, money coming in, trend behind it."""
    o = oi.get(x["s"]) or {}
    st = ctx["states"].get(x["s"], {})
    lng = [("Close in top 15% of the day", (x["pos"] or 0) >= 0.85, 2), ("Above VWAP", x["above_vwap"], 1.5),
           ("Beat Nifty by 1%+", (x["rs"] or 0) >= 1, 1.5), ("Volume 1.3x+", (x["rvol"] or 0) >= 1.3, 1),
           ("OI rising with price", (o.get("oi") or 0) >= 2 and x["chg"] > 0, 2), ("Above 30-min range", x["orb"] == 1, 1),
           ("Daily trend up", st.get("D") in ("Impulse", "Bullish"), 1), ("Weekly trend up", st.get("W") in ("Impulse", "Bullish", "Bottoming"), 0.5)]
    sht = [("Close in bottom 15% of the day", (x["pos"] or 1) <= 0.15, 2), ("Below VWAP", not x["above_vwap"], 1.5),
           ("Lagged Nifty by 1%+", (x["rs"] or 0) <= -1, 1.5), ("Volume 1.3x+", (x["rvol"] or 0) >= 1.3, 1),
           ("OI rising as price falls", (o.get("oi") or 0) >= 2 and x["chg"] < 0, 2), ("Below 30-min range", x["orb"] == -1, 1),
           ("Daily trend down", st.get("D") in ("Bearish", "Topping"), 1), ("Weekly trend down", st.get("W") in ("Bearish", "Topping"), 0.5)]
    f = lambda rules: (round(sum(w for _, ok, w in rules if ok), 1), [t for t, ok, _ in rules if ok])
    return f(lng), f(sht)


# ------------------------------------------------------------------ index bias: Nifty / Bank Nifty
HEAVY = {"NIFTY": ["HDFCBANK", "RELIANCE", "ICICIBANK", "INFY", "BHARTIARTL", "TCS", "LT", "ITC", "SBIN", "AXISBANK"],
         "BANKNIFTY": ["HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK", "INDUSINDBK"]}


def members(name):
    try:
        return [x.strip() for x in open(os.path.join(DATA, name)).read().split()[1:] if x.strip()]
    except Exception:
        return []


def index_bias(key, ix, chain, rows, cd):
    """Checklist and score for an index, from its own chart, its stocks and its option writers."""
    if not ix:
        return None
    mem = set(members("nifty50.csv" if key == "NIFTY" else "banknifty.csv"))
    st = [x for x in rows if x["s"] in mem]
    by = {x["s"]: x for x in rows}
    bull, bear = [], []

    def add(ok_bull, ok_bear, txt_bull, txt_bear, w=1):
        if ok_bull:
            bull.append((txt_bull, w))
        elif ok_bear:
            bear.append((txt_bear, w))

    add(ix["orb"] == 1, ix["orb"] == -1, "Above 30-min range", "Below 30-min range", 2)
    add((ix["pos"] or 0) >= 0.7, (ix["pos"] or 1) <= 0.3, "Trading in the top third of the day", "Trading in the bottom third of the day")
    if st:
        av = sum(1 for x in st if x["above_vwap"]) / len(st) * 100
        up = sum(1 for x in st if x["chg"] > 0) / len(st) * 100
        add(av >= 60, av <= 40, f"{av:.0f}% of its stocks above VWAP", f"Only {av:.0f}% of its stocks above VWAP", 2)
        add(up >= 60, up <= 40, f"{up:.0f}% of its stocks up", f"Only {up:.0f}% of its stocks up")
    hv = [by[s] for s in HEAVY[key] if s in by]
    if hv:
        hup = sum(1 for x in hv if x["chg"] > 0 and x["above_vwap"])
        hdn = sum(1 for x in hv if x["chg"] < 0 and not x["above_vwap"])
        add(hup >= len(hv) * 0.6, hdn >= len(hv) * 0.6, f"{hup} of {len(hv)} heavyweights up and above VWAP",
            f"{hdn} of {len(hv)} heavyweights down and below VWAP", 2)
    if chain:
        if chain.get("pcr") is not None:
            add(chain["pcr"] >= 1.1, chain["pcr"] <= 0.8, f"PCR {chain['pcr']}: put writers in control", f"PCR {chain['pcr']}: call writers in control")
        cc, cp = chain.get("chg_ce") or 0, chain.get("chg_pe") or 0
        add(cp > 1.3 * max(cc, 0) and cp > 0, cc > 1.3 * max(cp, 0) and cc > 0, "More puts written today than calls (support building)",
            "More calls written today than puts (resistance building)", 2)
        mp = chain.get("max_pain")
        if mp and ix.get("c"):
            add(ix["c"] > mp, ix["c"] < mp, f"Above max pain {mp:g}", f"Below max pain {mp:g}")
    sq = squeeze_15m(cd[key]) if key in cd else None
    if sq and sq.get("fired"):
        add(sq["dir"] > 0, sq["dir"] < 0, "15-min squeeze fired up", "15-min squeeze fired down", 2)
    elif sq and sq.get("on"):
        bull.append(("15-min squeeze on: a move is building", 0)) if sq["dir"] > 0 else bear.append(("15-min squeeze on: a move is building", 0))
    b, r = sum(w for _, w in bull), sum(w for _, w in bear)
    tot = b + r
    score = round(50 + 50 * (b - r) / tot) if tot else 50                 # 0 = fully bearish, 100 = fully bullish
    word = "Bullish" if score >= 65 else "Bearish" if score <= 35 else "Mixed"
    return {"score": score, "word": word, "bull": [t for t, _ in bull], "bear": [t for t, _ in bear],
            "heavy": [{"s": x["s"], "chg": x["chg"], "vw": x["above_vwap"]} for x in hv]}


# ------------------------------------------------------------------ main
def main(force=False):
    now = dt.datetime.now(IST)
    mins = now.hour * 60 + now.minute
    if not force and (now.weekday() >= 5 or not (9 * 60 + 10 <= mins <= 15 * 60 + 40)):
        print("Outside market hours, nothing to do.")
        return
    site = jload("site.json")
    syms = sorted(site.get("closes") or {})
    sj = jload("sectors.json")
    fo = jload("setups.json")
    states = {}
    for k in ("rows", "overspill", "ranges"):
        for r in fo.get(k) or []:
            if isinstance(r, dict) and r.get("states"):
                states.setdefault(r["symbol"], r["states"])
    for x in sj.get("strongest_fo") or []:
        states.setdefault(x["symbol"], x.get("states") or {})
    ctx = {"sectors": sj.get("map", {}), "states": states}
    ban = set((site.get("heads_up") or {}).get("ban") or [])
    results = (site.get("heads_up") or {}).get("results") or {}

    t0 = time.time()
    cd = candles(syms)
    print(f"candles: {len(cd)} symbols in {time.time() - t0:.0f}s")
    idx = {}
    for k in ("NIFTY", "BANKNIFTY"):
        if k in cd:
            x = read_stock(k, cd[k], 0)
            if x:
                idx[k] = x
    nchg = (idx.get("NIFTY") or {}).get("chg")

    nse = NSE()
    oi = {}
    sp = nse.get("live-analysis-oi-spurts-underlyings")
    for d in (sp or {}).get("data", []):
        if d.get("prevOI"):
            oi[d["symbol"]] = {"oi": r1((d["latestOI"] / d["prevOI"] - 1) * 100), "vol": d.get("volume"), "px": d.get("underlyingValue")}
    oi_time = (sp or {}).get("timestamp")
    print(f"oi spurts: {len(oi)} symbols at {oi_time}")

    rows = []
    for s in syms:
        if s not in cd:
            continue
        x = read_stock(s, cd[s], nchg)
        if not x:
            continue
        x["L"], x["S"], x["tl"], x["ts"] = score(x, oi, ctx)
        (x["bl"], x["btl"]), (x["bs"], x["bts"]) = btst(x, oi, ctx)
        x["oi"] = (oi.get(s) or {}).get("oi")
        x["sec"] = (ctx["sectors"].get(s) or {}).get("sector")
        x["ban"] = s in ban
        x["res"] = results.get(s)
        rows.append(x)
    session = max((x["date"] for x in rows), default=None)
    rows = [x for x in rows if x["date"] == session]

    chains = {}
    for k in ("NIFTY", "BANKNIFTY"):
        try:
            c = option_chain(nse, k, "Indices")
            if c:
                chains[k] = c
        except Exception as ex:
            print("chain", k, "failed:", ex)
    ok = [x for x in rows if not x["ban"]]
    longs = sorted(ok, key=lambda x: -x["L"])[:15]
    shorts = sorted(ok, key=lambda x: -x["S"])[:15]
    bl = sorted([x for x in ok if x["bl"] >= 5 and not x["res"]], key=lambda x: -x["bl"])[:10]
    bs = sorted([x for x in ok if x["bs"] >= 5 and not x["res"]], key=lambda x: -x["bs"])[:10]
    for x in {x["s"]: x for x in (longs[:4] + shorts[:4] + bl[:3] + bs[:2])}.values():
        try:
            c = option_chain(nse, x["s"], "Equity", near=5)
            if c:
                chains[x["s"]] = c
        except Exception as ex:
            print("chain", x["s"], "failed:", ex)

    for k in ("NIFTY", "BANKNIFTY"):
        try:
            if k in idx:
                idx[k]["bias"] = index_bias(k, idx[k], chains.get(k), rows, cd)
        except Exception as ex:
            print("bias", k, "failed:", ex)

    # live OI build-up board: every stock with an OI read and a price read
    board = {"long": [], "short": [], "cover": [], "unwind": []}
    for x in rows:
        o = x["oi"]
        if o is None or abs(o) < 1:
            continue
        q = ("long" if x["chg"] > 0 else "short") if o > 0 else ("cover" if x["chg"] > 0 else "unwind")
        board[q].append({"s": x["s"], "oi": o, "chg": x["chg"], "sec": x["sec"]})
    for q in board:
        board[q].sort(key=lambda r: -abs(r["oi"]))
        board[q] = board[q][:25]

    up = sum(1 for x in rows if x["chg"] > 0)
    final = mins >= 15 * 60 + 10
    out = {"at": now.strftime("%Y-%m-%d %H:%M"), "session": session, "oi_time": oi_time, "final": final,
           "breadth": {"up": up, "down": len(rows) - up, "above_vwap": sum(1 for x in rows if x["above_vwap"]),
                       "n": len(rows)},
           "index": idx, "chains": chains, "board": board,
           "longs": longs, "shorts": shorts, "btst": bl, "stbt": bs,
           "prices": {x["s"]: x["c"] for x in rows}}
    shown = {x["s"] for x in longs + shorts + bl + bs}
    for x in rows + list(idx.values()):
        if x["s"] in shown or x["s"] in idx:
            x["bars"] = bars(x)
        x.pop("_t", None); x.pop("_vw", None)
    body = json.dumps(out, separators=(",", ":"), allow_nan=False, default=str)
    print(f"built: {len(rows)} stocks, {len(chains)} chains, {len(body) // 1024} KB, longs {[x['s'] for x in longs[:5]]}, btst {[x['s'] for x in bl[:5]]}")
    if os.environ.get("DRY_RUN"):
        with open("intraday_preview.json", "w") as f:
            f.write(body)
        return
    if not KEY:
        print("INGEST_KEY is not set; not sending.")
        sys.exit(1)
    r = requests.post(f"{SITE}/api/ingest/intraday", data=body, timeout=60,
                      headers={"content-type": "application/json", "x-ingest-key": KEY})
    print("sent to site:", r.status_code, r.text[:200])
    if not r.ok:
        sys.exit(1)


if __name__ == "__main__":
    main(force="--force" in sys.argv)
