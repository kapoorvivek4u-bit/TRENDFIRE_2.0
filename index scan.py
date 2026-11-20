#!/usr/bin/env python3
"""
TradeReady index scan: Nifty, Bank Nifty and Midcap.

For each index:
  chart     MWDH TTM, VK System, setups and patterns (same engine as the stocks)
  futures   index futures OI: today and over 5 sessions (long build-up, short covering ...)
  options   from NSE's F&O file, nearest expiry: PCR (and its last sessions), max pain,
            the strikes with the most call OI (resistance) and put OI (support),
            and where the most new OI was added today
  board     the index's stocks with their VK, setup and OI reading

Runs at the end of the F&O scan (setups_scan.py) and never stops it if something fails.
Writes data/index.json and keeps a small history in data/index_oi.csv.gz.

  python index_scan.py             # live run
  python index_scan.py --selftest  # offline check, no network
"""
import argparse
import datetime as dt
import importlib.machinery
import importlib.util
import io
import json
import os
import sys
import time
import zipfile

import numpy as np
import pandas as pd


def load_engine():
    here = os.path.dirname(os.path.abspath(__file__))
    for name in (os.path.join(here, "setups_scan.py"), "setups_scan.py"):
        if os.path.exists(name) and "def tf_view(" in open(name, encoding="utf-8").read():
            loader = importlib.machinery.SourceFileLoader("tf_engine_idx", name)
            spec = importlib.util.spec_from_loader("tf_engine_idx", loader)
            mod = importlib.util.module_from_spec(spec)
            loader.exec_module(mod)
            return mod
    raise RuntimeError("setups_scan.py must be in the same folder.")


E = sys.modules.get("__main__") if hasattr(sys.modules.get("__main__"), "tf_view") else None
E = E or load_engine()
DATA = E.CFG["data_dir"]
INDEXES = [   # key, name, yahoo tickers to try, NSE F&O symbol, constituents list file
    ("NIFTY", "Nifty 50", ["^NSEI"], "NIFTY", "nifty50"),
    ("BANKNIFTY", "Bank Nifty", ["^NSEBANK"], "BANKNIFTY", "banknifty"),
    ("MIDCAP", "Midcap Select", ["NIFTY_MID_SELECT.NS", "^NSEMDCP50"], "MIDCPNIFTY", "midcap"),
]
LISTS = {"nifty50": "ind_nifty50list.csv", "banknifty": "ind_niftybanklist.csv",
         "midcap": "ind_niftymidcapselect_list.csv"}
ICFG = {"hist_days": 15, "chain_pct": 0.08, "top_strikes": 3, "oi_days": 5,
        "oi_move": 0.03, "px_flat": 0.01}


# ------------------------------------------------------------------ NSE F&O file
def _col(df, *names):
    low = {c.lower(): c for c in df.columns}
    for n in names:
        if n.lower() in low:
            return low[n.lower()]
    return None


def parse_index_fo(raw, symbols=("NIFTY", "BANKNIFTY", "MIDCPNIFTY")):
    """F&O file -> (futures: {sym: (oi, near price)}, options: DataFrame of index options
    with symbol, expiry, strike, type CE/PE, oi, chg_oi, under)."""
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        df = pd.read_csv(z.open(z.namelist()[0]), low_memory=False)
    df.columns = [c.strip() for c in df.columns]
    c_tp, c_sym = _col(df, "FinInstrmTp", "INSTRUMENT"), _col(df, "TckrSymb", "SYMBOL")
    c_exp, c_k = _col(df, "XpryDt", "EXPIRY_DT"), _col(df, "StrkPric", "STRIKE_PR")
    c_opt, c_oi = _col(df, "OptnTp", "OPTION_TYP"), _col(df, "OpnIntrst", "OPEN_INT")
    c_chg, c_cls = _col(df, "ChngInOpnIntrst", "CHG_IN_OI"), _col(df, "ClsPric", "CLOSE")
    c_set, c_und = _col(df, "SttlmPric", "SETTLE_PR"), _col(df, "UndrlygPric")
    df[c_sym] = df[c_sym].astype(str).str.strip()
    df[c_tp] = df[c_tp].astype(str).str.strip()
    df = df[df[c_sym].isin(symbols)].copy()
    for c in (c_k, c_oi, c_chg, c_cls, c_set, c_und):
        if c:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["exp"] = pd.to_datetime(df[c_exp], errors="coerce")
    fut = df[df[c_tp].isin(["IDF", "FUTIDX"])].sort_values("exp")
    futures = {}
    for s, g in fut.groupby(c_sym):
        px = g[c_set] if c_set else g[c_cls]
        px = np.where(px > 0, px, g[c_cls])
        futures[s] = (float(g[c_oi].sum()), float(px[0]))
    opt = df[df[c_tp].isin(["IDO", "OPTIDX"])]
    options = pd.DataFrame({
        "symbol": opt[c_sym], "expiry": opt["exp"], "strike": opt[c_k],
        "type": opt[c_opt].astype(str).str.strip().str.upper(), "oi": opt[c_oi],
        "chg_oi": opt[c_chg] if c_chg else 0.0,
        "under": opt[c_und] if c_und else np.nan}).dropna(subset=["strike", "oi"])
    return futures, options


def chain_summary(opt, spot, today):
    """Nearest expiry on or after today: PCR, max pain, biggest call/put OI strikes,
    biggest new OI today, and the strike ladder near spot."""
    if opt is None or not len(opt):
        return None
    fut_exp = opt[opt["expiry"] >= pd.Timestamp(today)]
    if not len(fut_exp):
        return None
    exp = fut_exp["expiry"].min()
    o = fut_exp[fut_exp["expiry"] == exp]
    ce = o[o["type"] == "CE"].groupby("strike")[["oi", "chg_oi"]].sum()
    pe = o[o["type"] == "PE"].groupby("strike")[["oi", "chg_oi"]].sum()
    if not len(ce) or not len(pe):
        return None
    strikes = np.array(sorted(set(ce.index) | set(pe.index)), float)
    ce_oi = ce["oi"].reindex(strikes, fill_value=0).values
    pe_oi = pe["oi"].reindex(strikes, fill_value=0).values
    # max pain: the expiry price where option buyers' total payoff is smallest
    pain = [float(np.sum(np.maximum(k - strikes, 0) * ce_oi)
                  + np.sum(np.maximum(strikes - k, 0) * pe_oi)) for k in strikes]
    max_pain = float(strikes[int(np.argmin(pain))])
    near = (strikes >= spot * (1 - ICFG["chain_pct"])) & (strikes <= spot * (1 + ICFG["chain_pct"]))
    ce_n = ce.reindex(strikes[near]).fillna(0)
    pe_n = pe.reindex(strikes[near]).fillna(0)
    n = ICFG["top_strikes"]
    res_k = ce_n[ce_n.index >= spot].sort_values("oi", ascending=False).head(n)
    sup_k = pe_n[pe_n.index <= spot].sort_values("oi", ascending=False).head(n)
    adds = []
    for typ, t in (("CE", ce_n), ("PE", pe_n)):
        for k, row in t.sort_values("chg_oi", ascending=False).head(2).iterrows():
            if row["chg_oi"] > 0:
                adds.append({"strike": float(k), "type": typ, "chg_oi": float(row["chg_oi"])})
    adds.sort(key=lambda x: -x["chg_oi"])
    ladder = [{"strike": float(k), "ce": float(ce_n.loc[k, "oi"]), "pe": float(pe_n.loc[k, "oi"])}
              for k in strikes[near]]
    if len(ladder) > 21:                                 # keep ~21 strikes around spot
        i = int(np.argmin([abs(x["strike"] - spot) for x in ladder]))
        ladder = ladder[max(0, i - 10):i + 11]
    tot_ce, tot_pe = float(ce["oi"].sum()), float(pe["oi"].sum())
    return {"expiry": exp.strftime("%Y-%m-%d"), "pcr": round(tot_pe / tot_ce, 2) if tot_ce else None,
            "max_pain": max_pain,
            "resistance": [{"strike": float(k), "oi": float(r["oi"])} for k, r in res_k.iterrows()],
            "support": [{"strike": float(k), "oi": float(r["oi"])} for k, r in sup_k.iterrows()],
            "new_oi": adds[:3], "ladder": ladder, "tot_ce": tot_ce, "tot_pe": tot_pe}


def oi_word(oi_chg, px_chg):
    """Futures OI reading from OI and price change."""
    if abs(oi_chg) < ICFG["oi_move"]:
        return "No big change", 0
    if oi_chg > 0:
        if abs(px_chg) < ICFG["px_flat"]:
            return "Big positions building", 0
        return ("Long build-up", 1) if px_chg > 0 else ("Short build-up", -1)
    return ("Short covering", 1) if px_chg > 0 else ("Long unwinding", -1)


def futures_reading(hist, sym):
    """hist: DataFrame date, symbol, fut_oi, fut_close, pcr. Today and over oi_days."""
    g = hist[hist["symbol"] == sym].sort_values("date")
    if len(g) < 2:
        return None
    out = {"oi": float(g["fut_oi"].iloc[-1]), "price": float(g["fut_close"].iloc[-1]),
           "date": pd.Timestamp(g["date"].iloc[-1]).strftime("%Y-%m-%d")}
    for key, k in (("day", 1), ("week", min(ICFG["oi_days"], len(g) - 1))):
        oc = g["fut_oi"].iloc[-1] / g["fut_oi"].iloc[-1 - k] - 1
        pc = g["fut_close"].iloc[-1] / g["fut_close"].iloc[-1 - k] - 1
        word, side = oi_word(oc, pc)
        out[key] = {"sessions": int(k), "oi_pct": round(float(oc) * 100, 1),
                    "px_pct": round(float(pc) * 100, 2), "word": word, "side": side}
    out["pcr_hist"] = [None if pd.isna(x) else round(float(x), 2)
                       for x in g["pcr"].tail(ICFG["oi_days"] + 1)] if "pcr" in g else []
    return out


# ------------------------------------------------------------------ chart side
def index_chart(key, name, d, hr):
    """Setups, patterns and VK for one index, from the stock engine."""
    row = E.analyse(key, d, hr, None)
    if not row:
        return None
    D = E.tf_view(d, 3)
    ind = D["ind"]
    ma = ("Bullish" if ind["ema20"][-1] > ind["ema50"][-1] > ind["sma200"][-1] else
          "Bearish" if ind["ema20"][-1] < ind["ema50"][-1] < ind["sma200"][-1] else "Messy")
    c = d["Close"].astype(float)
    setups = [dict(r) for r in ([row] if row.get("setup") else []) + row.get("overspill", [])
              + row.get("ranges", [])]
    for r in setups:
        for k_ in ("w_bull", "m_bull", "d_pos", "overspill", "ranges", "range_break"):
            r.pop(k_, None)
    return {"key": key, "name": name, "close": round(float(c.iloc[-1]), 2),
            "chg1": round(float(c.iloc[-1] / c.iloc[-2] - 1) * 100, 2),
            "chg5": round(float(c.iloc[-1] / c.iloc[-6] - 1) * 100, 2),
            "states": row["states"], "vk": row.get("vk"), "old": row.get("old"),
            "ma": ma, "squeeze": bool(ind["sq"][-1]), "ext": row.get("ext"),
            "stage": row.get("stage"), "spark": [round(float(x), 2) for x in c.tail(60)],
            "setups": setups}


def board(members, setups_res, fo):
    """The index's stocks: VK, best setup, 5-session OI reading."""
    vk = {x["symbol"]: k for k, xs in setups_res.get("vk_labels", {}).items() for x in xs}
    best = {}
    rank = {"Ready": 0, "Triggered": 1, "Watch": 2}
    for r in (setups_res.get("rows", []) + setups_res.get("overspill", [])
              + setups_res.get("ranges", [])):
        b = best.get(r["symbol"])
        if b is None or (rank.get(r["stage"], 9), r["grade"]) < (rank.get(b["stage"], 9), b["grade"]):
            best[r["symbol"]] = r
    out = []
    for s in members:
        g = fo[fo["symbol"] == s].sort_values("date") if fo is not None else pd.DataFrame()
        o = None
        if len(g) > ICFG["oi_days"]:
            oc = g["fut_oi"].iloc[-1] / g["fut_oi"].iloc[-1 - ICFG["oi_days"]] - 1
            pc = g["fut_close"].iloc[-1] / g["fut_close"].iloc[-1 - ICFG["oi_days"]] - 1
            w, side = oi_word(oc, pc)
            o = {"oi_pct": round(float(oc) * 100, 1), "px_pct": round(float(pc) * 100, 1),
                 "word": w, "side": side}
        b = best.get(s)
        out.append({"symbol": s, "vk": vk.get(s),
                    "setup": f'{b["setup"]} · {b["stage"]}' if b else None,
                    "grade": b["grade"] if b else None, "oi": o})
    order = {"BUY NOW": 0, "ALMOST READY (Bullish)": 1, "COMING CLOSE TO READY (Bullish)": 2,
             "NO SIGNAL": 3, None: 4, "COMING CLOSE TO READY (Bearish)": 5,
             "ALMOST READY (Bearish)": 6, "SELL NOW": 7}
    return sorted(out, key=lambda x: (order.get(x["vk"], 4), x["symbol"]))


# ------------------------------------------------------------------ live run
def load_members(session, key):
    path = os.path.join(DATA, f"{key}.csv")
    for base in ("https://nsearchives.nseindia.com/content/indices/",
                 "https://www.niftyindices.com/IndexConstituent/"):
        raw = E.nse_get(session, base + LISTS[key], tries=2) if session else None
        if raw is None:
            continue
        try:
            df = pd.read_csv(io.BytesIO(raw))
            col = _col(df, "Symbol")
            syms = df[col].astype(str).str.strip().tolist()
            if len(syms) >= 5:
                pd.DataFrame({"symbol": syms}).to_csv(path, index=False)
                return syms
        except Exception as e:
            print(f"[warn] {LISTS[key]}: {e}")
    if os.path.exists(path):
        return pd.read_csv(path)["symbol"].tolist()
    return []


def update_history(session, now):
    """Index futures OI and PCR per day (small file), plus the latest day's options."""
    path = os.path.join(DATA, "index_oi.csv.gz")
    hist = (pd.read_csv(path, parse_dates=["date"]) if os.path.exists(path)
            else pd.DataFrame(columns=["date", "symbol", "fut_oi", "fut_close", "pcr"]))
    have = set(pd.to_datetime(hist["date"]).dt.normalize())
    today = pd.Timestamp(now.date())
    days = [today - pd.Timedelta(days=i) for i in range(ICFG["hist_days"] + 8)]
    days = sorted(d for d in days if d.weekday() < 5 and (d < today or now.hour >= 18))
    latest_opt, latest_day = None, None
    new = []
    for d in days:
        if d in have and d != days[-1]:
            continue
        raw = E.nse_get(session, E.FO_URL.format(d=d.strftime("%Y%m%d")))
        if raw is None:
            continue
        try:
            fut, opt = parse_index_fo(raw)
        except Exception as e:
            print(f"[warn] index F&O {d.date()}: {e}")
            continue
        for s, (oi, px) in fut.items():
            cs = chain_summary(opt[opt["symbol"] == s], px, d)
            new.append({"date": d, "symbol": s, "fut_oi": oi, "fut_close": px,
                        "pcr": cs["pcr"] if cs else np.nan})
        latest_opt, latest_day = opt, d
        time.sleep(1)
    if new:
        hist = pd.concat([hist, pd.DataFrame(new)], ignore_index=True)
        hist["date"] = pd.to_datetime(hist["date"])
        hist = hist.drop_duplicates(["date", "symbol"], keep="last").sort_values(["date", "symbol"])
        hist = hist[hist["date"] >= hist["date"].max() - pd.Timedelta(days=60)]
        hist.to_csv(path, index=False)
    hist["date"] = pd.to_datetime(hist["date"])
    return hist, latest_opt, latest_day


def run(now=None, session=None):
    now = now or dt.datetime.now(E.IST)
    session = session or E.nse_session()
    hist, opt, opt_day = update_history(session, now)
    try:
        setups_res = json.load(open(os.path.join(DATA, "setups.json")))
    except Exception:
        setups_res = {}
    fo_path = os.path.join(DATA, "fo_oi.csv.gz")
    fo = pd.read_csv(fo_path, parse_dates=["date"]) if os.path.exists(fo_path) else None
    out = {"generated_ist": now.strftime("%Y-%m-%d %H:%M"),
           "oi_asof": opt_day.strftime("%Y-%m-%d") if opt_day is not None else None,
           "indexes": []}
    for key, name, tickers, fo_sym, list_key in INDEXES:
        d = hr = None
        for t in tickers:
            data = E.yf_download([t], period="5y", interval="1d")
            if data is None:
                continue
            df = (data[t] if isinstance(data.columns, pd.MultiIndex) else data)
            df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
            if len(df) > 250:
                d = df
                hd = E.yf_download([t], period="60d", interval="1h")
                if hd is not None:
                    h_ = hd[t] if isinstance(hd.columns, pd.MultiIndex) else hd
                    h_ = h_[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
                    idx = pd.to_datetime(h_.index)
                    if idx.tz is not None:
                        idx = idx.tz_convert("Asia/Kolkata").tz_localize(None)
                    h_.index = idx
                    hr = h_
                break
        item = {"key": key, "name": name}
        if d is not None:
            idx = pd.to_datetime(d.index)
            d.index = idx.tz_localize(None) if idx.tz is not None else idx
            dd, hh = E.completed({key: d}, {key: hr} if hr is not None else {}, now)
            d = dd[key]
            d["Volume"] = d["Volume"].replace(0, np.nan).fillna(1.0)
            hr = hh.get(key)
            if hr is not None:
                hr["Volume"] = hr["Volume"].replace(0, np.nan).fillna(1.0)
            try:
                item.update(index_chart(key, name, d, hr) or {})
            except Exception as e:
                print(f"[warn] {key} chart: {e}")
        item["futures"] = futures_reading(hist, fo_sym)
        if opt is not None:
            spot = item.get("close") or (item["futures"] or {}).get("price")
            if spot:
                item["options"] = chain_summary(opt[opt["symbol"] == fo_sym], spot, opt_day)
        members = load_members(session, list_key)
        item["board"] = board(members, setups_res, fo) if members else []
        out["indexes"].append(item)
    with open(os.path.join(DATA, "index.json"), "w") as f:
        json.dump(out, f, indent=1)
    print("index scan:", ", ".join(f'{x["key"]} {x.get("close")}' for x in out["indexes"]))
    return out


# ------------------------------------------------------------------ selftest
def _fo_zip(day_oi, spot=24000.0, chg=1.0):
    rows = [{"FinInstrmTp": "IDF", "TckrSymb": "NIFTY", "XpryDt": "2026-11-24",
             "StrkPric": "", "OptnTp": "", "ClsPric": spot + 20, "SttlmPric": spot + 20,
             "OpnIntrst": day_oi, "ChngInOpnIntrst": 0, "UndrlygPric": spot},
            {"FinInstrmTp": "STF", "TckrSymb": "SBIN", "XpryDt": "2026-11-24", "StrkPric": "",
             "OptnTp": "", "ClsPric": 900, "SttlmPric": 900, "OpnIntrst": 5, "ChngInOpnIntrst": 0,
             "UndrlygPric": 899}]
    for k in range(23000, 25001, 100):
        ce = 1000 + max(0, 3000 - abs(k - 24500) * 10) * chg      # call wall at 24500
        pe = 1000 + max(0, 3000 - abs(k - 23500) * 10) * chg      # put wall at 23500
        for typ, oi in (("CE", ce), ("PE", pe)):
            rows.append({"FinInstrmTp": "IDO", "TckrSymb": "NIFTY", "XpryDt": "2026-11-24",
                         "StrkPric": k, "OptnTp": typ, "ClsPric": 10, "SttlmPric": 10,
                         "OpnIntrst": oi, "ChngInOpnIntrst": oi * 0.1, "UndrlygPric": spot})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("x.csv", pd.DataFrame(rows).to_csv(index=False))
    return buf.getvalue()


def selftest():
    fut, opt = parse_index_fo(_fo_zip(1_000_000))
    assert set(fut) == {"NIFTY"} and fut["NIFTY"][0] == 1_000_000, fut
    assert set(opt["type"]) == {"CE", "PE"} and len(opt) == 42, opt.shape
    cs = chain_summary(opt, 24000.0, dt.date(2026, 11, 20))
    assert cs["resistance"][0]["strike"] == 24500 and cs["support"][0]["strike"] == 23500, cs
    assert 23500 <= cs["max_pain"] <= 24500 and cs["pcr"] and cs["new_oi"], cs
    assert all(23000 <= x["strike"] <= 25000 for x in cs["ladder"])
    assert oi_word(0.06, 0.004)[0] == "Big positions building"
    assert oi_word(0.06, 0.02) == ("Long build-up", 1) and oi_word(-0.05, 0.02)[0] == "Short covering"
    assert oi_word(-0.05, -0.02)[0] == "Long unwinding" and oi_word(0.01, 0.05)[0] == "No big change"
    days = pd.bdate_range(end="2026-11-20", periods=8)
    hist = pd.DataFrame({"date": days, "symbol": "NIFTY",
                         "fut_oi": np.linspace(1e6, 1.2e6, 8), "fut_close": np.linspace(24000, 24600, 8),
                         "pcr": np.linspace(0.8, 1.1, 8)})
    fr = futures_reading(hist, "NIFTY")
    assert fr["week"]["word"] == "Long build-up" and fr["week"]["sessions"] == 5, fr
    assert len(fr["pcr_hist"]) == 6 and fr["pcr_hist"][-1] == 1.1
    # chart side on a synthetic index
    dd = pd.bdate_range(end="2026-11-20", periods=1300)
    c = 20000 * np.exp(np.cumsum(np.random.default_rng(2).normal(0.0004, 0.008, 1300)))
    d = E._bars(c, dd, noise=0.004)
    ic = index_chart("NIFTY", "Nifty 50", d, None)
    assert ic and ic["key"] == "NIFTY" and len(ic["spark"]) == 60 and ic["ma"] in ("Bullish", "Bearish", "Messy")
    assert ic["states"]["M"] != "No data" and ic["vk"] is not None, ic["states"]
    json.dumps(ic)
    # board
    sres = {"vk_labels": {"BUY NOW": [{"symbol": "SBIN"}], "SELL NOW": [{"symbol": "PNB"}]},
            "rows": [{"symbol": "SBIN", "setup": "Swing Pullback", "stage": "Ready", "grade": "A"}],
            "overspill": [], "ranges": []}
    fo = pd.DataFrame({"date": list(days) * 2, "symbol": ["SBIN"] * 8 + ["PNB"] * 8,
                       "fut_oi": list(np.linspace(1e6, 1.2e6, 8)) + list(np.linspace(1e6, 0.9e6, 8)),
                       "fut_close": list(np.linspace(800, 840, 8)) + list(np.linspace(100, 95, 8))})
    bd = board(["PNB", "SBIN"], sres, fo)
    assert [x["symbol"] for x in bd] == ["SBIN", "PNB"] and bd[0]["oi"]["word"] == "Long build-up", bd
    assert bd[1]["oi"]["word"] == "Long unwinding" and bd[0]["setup"] == "Swing Pullback · Ready"
    print("ladder:", len(cs["ladder"]), "pcr", cs["pcr"], "max pain", cs["max_pain"],
          "| chart:", ic["states"], ic["vk"]["final"] if ic["vk"] else None, len(ic["setups"]))
    print("SELFTEST PASSED")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    selftest() if ap.parse_args().selftest else run()
