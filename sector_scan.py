#!/usr/bin/env python3
"""
TrendFire Setups - sector scan (NSE F&O stocks, Daily bars).

For each sector:
  stage   : Leading | Improving | Weakening | Lagging
            from the sector's relative strength against Nifty:
              RS ratio    = sector line vs its own 50-day average (above 1 = outperforming)
              RS momentum = change in that ratio over the last 10 days
  returns : 1-month and 3-month return minus Nifty's
  breadth : share of the sector's F&O stocks bullish on Weekly and on Daily
  top     : its strongest F&O stocks by relative strength

The sector "index" is the sector's own F&O stocks, equal-weighted, so every
sector is measured the same way, including ones with no NSE sector index.

Sectors come from the Industry column of NSE's Nifty 500 list. Financial
Services is split into Banks, PSU Banks and Finance using NSE's bank index lists.
The map also covers midcaps and smallcaps, so Equity rows get a sector too.

Outputs
  data/sectors.json    read by app.py
  data/sector_map.csv  saved copy of the sector list, used if NSE cannot be reached

Usage
  python sector_scan.py             # live run
  python sector_scan.py --selftest  # offline logic check, no network
"""
import argparse
import datetime as dt
import importlib.machinery
import importlib.util
import io
import json
import os

import numpy as np
import pandas as pd

SCFG = {
    "min_members": 3,             # fewer F&O stocks than this: listed, not ranked
    "rs_avg": 20,                 # RS ratio = RS line vs its 20-day average
    "mom_days": 5,                # RS momentum = change in the ratio over 5 days
    "periods": {"rw": 5, "r2w": 10, "r1": 21},   # 1 week, 2 weeks, 1 month (trading days)
    "top_n": 5,
    "trail_days": 10,             # day-by-day history shown for each sector
    "dir_days": 5,                # direction = change in strength vs Nifty over this many days
    "dir_flat_pct": 0.75,         # smaller changes count as sideways
    "max_daily_move": 0.25,       # daily returns beyond this are treated as data errors
}
NAMES = {
    "Automobile and Auto Components": "Auto",
    "Fast Moving Consumer Goods": "FMCG",
    "Oil Gas & Consumable Fuels": "Oil & Gas",
    "Media Entertainment & Publication": "Media",
    "Information Technology": "IT",
    "Telecommunication": "Telecom",
    "Financial Services": "Finance",
}
BASE = ["https://nsearchives.nseindia.com/content/indices/",
        "https://www.niftyindices.com/IndexConstituent/"]
LISTS = {"all": "ind_nifty500list.csv", "banks": "ind_niftybanklist.csv",
         "psu": "ind_niftypsubanklist.csv", "pharma": "ind_niftypharmalist.csv"}
STAGE_ORDER = {"Leading": 0, "Improving": 1, "Weakening": 2, "Lagging": 3}


def load_engine():
    for name in ("setups_scan.py",):
        if os.path.exists(name) and "def tf_view(" in open(name, encoding="utf-8").read():
            loader = importlib.machinery.SourceFileLoader("tf_engine", name)
            spec = importlib.util.spec_from_loader("tf_engine", loader)
            mod = importlib.util.module_from_spec(spec)
            loader.exec_module(mod)
            return mod
    raise RuntimeError("Engine file not found. The F&O scan file (setups_scan.py) must "
                       "be in the same folder.")


E = load_engine()
DATA_DIR = E.CFG["data_dir"]


# ------------------------------------------------------------------ sectors
def parse_index_list(raw):
    df = pd.read_csv(io.BytesIO(raw))
    df.columns = [c.strip() for c in df.columns]
    out = pd.DataFrame({"symbol": df["Symbol"].astype(str).str.strip()})
    if "Company Name" in df.columns:
        out["name"] = df["Company Name"].astype(str).str.strip()
    if "Industry" in df.columns:
        out["industry"] = df["Industry"].astype(str).str.strip()
    return out


def build_map(all_df, banks=None, psu=None, pharma=None):
    """symbol -> sector name, with Finance split into Banks / PSU Banks / Finance, and
    Pharma split out of Healthcare (hospitals and diagnostics stay in Healthcare)."""
    m = {}
    banks, psu, pharma = set(banks or []), set(psu or []), set(pharma or [])
    for s, ind in zip(all_df["symbol"], all_df["industry"]):
        name = NAMES.get(ind, ind)
        if s in psu:
            name = "PSU Banks"
        elif s in banks:
            name = "Banks"
        elif s in pharma:
            name = "Pharma"
        m[s] = name
    return m


def load_sector_map(session, notes):
    path = os.path.join(DATA_DIR, "sector_map.csv")
    got = {}
    for key, fname in LISTS.items():
        for base in BASE:
            raw = E.nse_get(session, base + fname, tries=2)
            if raw is None:
                continue
            try:
                got[key] = parse_index_list(raw)
                break
            except Exception as e:
                print(f"[warn] {fname} unreadable: {e}")
    if "all" in got and len(got["all"]) >= 400:
        if "banks" not in got:
            notes.append("Bank list unavailable: banks are grouped under Finance.")
        m = build_map(got["all"],
                      got["banks"]["symbol"].tolist() if "banks" in got else None,
                      got["psu"]["symbol"].tolist() if "psu" in got else None,
                      got["pharma"]["symbol"].tolist() if "pharma" in got else None)
        if "pharma" not in got:
            notes.append("Pharma list unavailable: pharma stays inside Healthcare.")
        names = (dict(zip(got["all"]["symbol"], got["all"]["name"]))
                 if "name" in got["all"].columns else {})
        pd.DataFrame({"symbol": list(m), "sector": list(m.values()),
                      "name": [names.get(k, "") for k in m]}).to_csv(path, index=False)
        return m
    if os.path.exists(path):
        notes.append("Sector list not refreshed today; using the saved copy.")
        df = pd.read_csv(path)
        return dict(zip(df["symbol"], df["sector"]))
    raise RuntimeError("Sector list could not be downloaded from NSE and no saved copy "
                       "exists yet.")


# ------------------------------------------------------------------ logic
def sector_index(closes):
    """Equal-weighted sector line from its members' closes (DataFrame, one column each)."""
    r = closes.pct_change(fill_method=None)
    r = r.where(r.abs() <= SCFG["max_daily_move"])
    return (1 + r.mean(axis=1, skipna=True).fillna(0)).cumprod()


def rotation(idx, nifty):
    """(stage, rs_ratio, rs_momentum) of a sector line against Nifty."""
    n = nifty.reindex(idx.index).ffill()
    rs = idx / n
    ratio = rs / rs.rolling(SCFG["rs_avg"]).mean()
    mom = ratio - ratio.shift(SCFG["mom_days"])
    r, m = float(ratio.iloc[-1]), float(mom.iloc[-1])
    if np.isnan(r) or np.isnan(m):
        return None, None, None
    stage = ("Leading" if r >= 1 and m >= 0 else "Weakening" if r >= 1 else
             "Improving" if m >= 0 else "Lagging")
    return stage, round(r, 4), round(m, 4)


def stage_of(r, m):
    if np.isnan(r) or np.isnan(m):
        return None
    return ("Leading" if r >= 1 and m >= 0 else "Weakening" if r >= 1 else
            "Improving" if m >= 0 else "Lagging")


def trail(idx, nifty, d_pos):
    """Last few sessions of a sector, one entry per day: stage, strength vs Nifty, that
    day's move vs Nifty, and the share of its stocks with Daily TTM above zero."""
    n = nifty.reindex(idx.index).ffill()
    rs = idx / n
    ratio = rs / rs.rolling(SCFG["rs_avg"]).mean()
    mom = ratio - ratio.shift(SCFG["mom_days"])
    day_rel = (idx.pct_change() - n.pct_change()) * 100
    k = SCFG["trail_days"]
    out = []
    for i in range(len(idx) - k, len(idx)):
        if i < 1:
            continue
        out.append({"date": idx.index[i].strftime("%Y-%m-%d"),
                    "stage": stage_of(float(ratio.iloc[i]), float(mom.iloc[i])),
                    "rs": round(float(rs.iloc[i] / rs.iloc[len(idx) - k - 1]) * 100 - 100, 2)
                    if len(idx) > k else None,
                    "day": None if np.isnan(day_rel.iloc[i]) else round(float(day_rel.iloc[i]), 2),
                    "d_bull": d_pos[i - len(idx)] if d_pos and len(d_pos) >= len(idx) - i else None})
    D = SCFG["dir_days"]
    chg = float(rs.iloc[-1] / rs.iloc[-1 - D] - 1) * 100 if len(rs) > D else np.nan
    direction = ("Sideways" if np.isnan(chg) or abs(chg) < SCFG["dir_flat_pct"] else
                 "Rising" if chg > 0 else "Falling")
    return out, direction, None if np.isnan(chg) else round(chg, 2)


def ret(series, days):
    s = series.dropna()
    return float(s.iloc[-1] / s.iloc[-1 - days] - 1) if len(s) > days else np.nan


def sector_ohlc(daily, members):
    """Equal-weighted sector line plus high/low bands, so TTM can be read on it."""
    closes = pd.DataFrame({m: daily[m]["Close"] for m in members}).sort_index().iloc[-260:]
    idx = sector_index(closes)
    hi = pd.DataFrame({m: daily[m]["High"] / daily[m]["Close"] for m in members})
    lo = pd.DataFrame({m: daily[m]["Low"] / daily[m]["Close"] for m in members})
    hi, lo = hi.reindex(idx.index).mean(axis=1), lo.reindex(idx.index).mean(axis=1)
    bars = pd.DataFrame({"Open": idx, "High": idx * hi.fillna(1), "Low": idx * lo.fillna(1),
                         "Close": idx, "Volume": 0.0})
    return idx, bars


def analyse_sectors(daily, smap, nifty, symbols):
    """Rank sectors among the F&O stocks in `symbols`. Returns are against Nifty."""
    P = SCFG["periods"]
    nr = {k: ret(nifty, d) for k, d in P.items()}
    rel = lambda series: {k: (None if np.isnan(ret(series, d)) else
                              round((ret(series, d) - nr[k]) * 100, 1)) for k, d in P.items()}
    per_stock = {}
    for s in symbols:
        d = daily.get(s)
        if d is None or len(d) < 120:
            continue
        D, W = E.tf_view(d, 3), E.tf_view(E.to_weekly(d), 3)
        per_stock[s] = {
            "symbol": s, "close": round(float(d["Close"].iloc[-1]), 2), **rel(d["Close"]),
            "states": {"W": "Impulse" if W["impulse"] else W["state"],
                       "D": "Impulse" if D["impulse"] else D["state"]},
            "w_bull": bool(W["ok"] and W["mom"] > 0), "d_bull": bool(D["ok"] and D["mom"] > 0),
            "d_hist": ([bool(x > 0) for x in D["ind"]["mom"][-SCFG["trail_days"]:]]
                       if D["ok"] else [])}
    groups = {}
    for s in per_stock:
        groups.setdefault(smap.get(s, "Unclassified"), []).append(s)
    ranked, unranked = [], []
    for name, members in groups.items():
        if len(members) < SCFG["min_members"] or name == "Unclassified":
            unranked.append({"name": name, "n": len(members), "members": sorted(members)})
            continue
        idx, bars = sector_ohlc(daily, members)
        stage, ratio, mom = rotation(idx, nifty)
        if stage is None:
            unranked.append({"name": name, "n": len(members), "members": sorted(members)})
            continue
        T = E.tf_view(bars, 3)
        st_ = [per_stock[s] for s in members]
        k = SCFG["trail_days"]
        hist = [x["d_hist"] for x in st_ if len(x["d_hist"]) == k]
        d_pos = ([round(100 * np.mean([h[i] for h in hist])) for i in range(k)]
                 if hist else [])
        days, direction, chg5 = trail(idx, nifty, d_pos)
        score = lambda x: (x["rw"] or 0) * 0.4 + (x["r2w"] or 0) * 0.3 + (x["r1"] or 0) * 0.3
        top = sorted(st_, key=score, reverse=True)      # all members, strongest first
        ranked.append({
            "name": name, "stage": stage, "n": len(members), **rel(idx),
            "idx_d": T["state"] if T["ok"] else None,
            "wk_bull": round(100 * np.mean([x["w_bull"] for x in st_])),
            "d_bull": round(100 * np.mean([x["d_bull"] for x in st_])),
            "ratio": ratio, "mom": mom, "trail": days, "direction": direction, "chg5": chg5,
            "top": [{k_: v for k_, v in x.items() if k_ not in ("w_bull", "d_bull", "d_hist")}
                    for x in top]})
    ranked.sort(key=lambda x: (STAGE_ORDER[x["stage"]], -(x["rw"] or 0)))
    unranked.sort(key=lambda x: x["name"])
    return ranked, unranked, {k: round(v * 100, 1) for k, v in nr.items()}


def main_live():
    os.makedirs(DATA_DIR, exist_ok=True)
    now = dt.datetime.now(E.IST)
    notes = []
    fo_path = os.path.join(DATA_DIR, "fo_oi.csv.gz")
    if not os.path.exists(fo_path):
        raise RuntimeError("data/fo_oi.csv.gz not found. Run the 'Setups scan' workflow "
                           "once first; this scan reuses its F&O list.")
    fo = pd.read_csv(fo_path, parse_dates=["date"])
    symbols = sorted(fo.loc[fo["date"] == fo["date"].max(), "symbol"].unique())
    smap = load_sector_map(E.nse_session(), notes)
    daily = E.fetch_bars(symbols, period="2y", interval="1d")
    if len(daily) < 50:
        raise RuntimeError(f"Daily prices for only {len(daily)} stocks; stopping.")
    daily, _ = E.completed(daily, {}, now)
    data = E.yf_download(["^NSEI"], period="2y", interval="1d")
    if data is None:
        raise RuntimeError("Could not load Nifty from yfinance.")
    nd = data["^NSEI"] if isinstance(data.columns, pd.MultiIndex) else data
    nifty = nd["Close"].dropna()
    nifty.index = pd.to_datetime(nifty.index).tz_localize(None).normalize()
    ranked, unranked, nret = analyse_sectors(daily, smap, nifty, symbols)
    stage_of = {s["name"]: s["stage"] for s in ranked}
    tag_map = {sym: {"sector": sec, "stage": stage_of.get(sec, "")} for sym, sec in smap.items()}
    for s in symbols:
        tag_map.setdefault(s, {"sector": "Unclassified", "stage": ""})
    asof = max(df.index[-1] for df in daily.values()).strftime("%Y-%m-%d")
    res = {"generated_ist": now.strftime("%Y-%m-%d %H:%M"), "asof": asof,
           "nifty": nret, "sectors": ranked, "unranked": unranked,
           "map": tag_map, "notes": notes}
    with open(os.path.join(DATA_DIR, "sectors.json"), "w") as f:
        json.dump(res, f, indent=1)
    print(f"{len(ranked)} sectors ranked | "
          + ", ".join(f"{s['name']} {s['stage']}" for s in ranked))


# ---------------------------------------------------------------- selftest
def selftest():
    rng = np.random.default_rng(9)
    days = pd.bdate_range(end="2026-11-13", periods=320)
    nifty = pd.Series(100 * np.exp(np.cumsum(np.full(320, 0.0004))), index=days)

    def stock(edge, seed):
        r = np.random.default_rng(seed).normal(0, 0.006, 320) + 0.0004 + edge
        return E._bars(100 * np.exp(np.cumsum(r)), days, noise=0.005)

    t = np.arange(320)
    accel_up = np.where(t > 295, 0.0006 * (t - 295) / 3, -0.0004)      # late, growing outperformance
    accel_dn = np.where(t > 295, -0.0006 * (t - 295) / 3, 0.0004)      # late, growing underperformance
    daily, smap = {}, {}
    for i in range(5):
        daily[f"MET{i}"], smap[f"MET{i}"] = stock(accel_up, i), "Metals & Mining"
        daily[f"IT{i}"], smap[f"IT{i}"] = stock(accel_dn, 10 + i), "IT"
    for i in range(2):
        daily[f"TINY{i}"], smap[f"TINY{i}"] = stock(0.0, 20 + i), "Media"
    daily["LOST"] = stock(0.0, 30)                                      # not in the sector list
    ranked, unranked, nret = analyse_sectors(daily, smap, nifty, list(daily))
    print(json.dumps({"ranked": [{k: s[k] for k in ("name", "stage", "rw", "r2w", "r1", "idx_d",
                                                    "d_bull", "wk_bull", "ratio", "mom")}
                                 for s in ranked],
                      "unranked": unranked, "nifty": nret}, indent=1))
    by = {s["name"]: s for s in ranked}
    assert by["Metals & Mining"]["stage"] == "Leading", by
    assert by["IT"]["stage"] == "Lagging", by
    assert by["Metals & Mining"]["rw"] > 0 > by["IT"]["rw"]
    mt = by["Metals & Mining"]
    assert len(mt["trail"]) == SCFG["trail_days"] and mt["direction"] == "Rising", mt["trail"]
    assert by["IT"]["direction"] == "Falling" and mt["trail"][-1]["stage"] == mt["stage"]
    assert all(t["d_bull"] is not None for t in mt["trail"]) and "d_hist" not in json.dumps(mt["top"])
    assert by["Metals & Mining"]["idx_d"] in ("Bullish", "Topping", "Bearish", "Bottoming")
    assert [s["name"] for s in ranked] == ["Metals & Mining", "IT"]
    assert {u["name"] for u in unranked} == {"Media", "Unclassified"}
    top = by["Metals & Mining"]["top"]
    assert len(top) == 5 and top[0]["rw"] is not None and "w_bull" not in top[0]

    # quadrant rule
    idx = pd.Series(np.linspace(1, 1.3, 120), index=days[-120:])
    assert rotation(idx, nifty)[0] in ("Leading", "Weakening")
    assert rotation(idx.iloc[::-1].set_axis(idx.index), nifty)[0] in ("Lagging", "Improving")

    # NSE list parsing and the bank split
    csv = ("Company Name,Industry,Symbol,Series,ISIN Code\n"
           "HDFC Bank Ltd.,Financial Services,HDFCBANK,EQ,X\n"
           "State Bank of India,Financial Services,SBIN,EQ,X\n"
           "Bajaj Finance Ltd.,Financial Services,BAJFINANCE,EQ,X\n"
           "Infosys Ltd.,Information Technology,INFY,EQ,X\n"
           "Maruti Suzuki,Automobile and Auto Components,MARUTI,EQ,X\n").encode()
    csv2 = csv.decode() + ("Sun Pharma,Healthcare,SUNPHARMA,EQ,X\n"
                           "Apollo Hospitals,Healthcare,APOLLOHOSP,EQ,X\n")
    m = build_map(parse_index_list(csv2.encode()), ["HDFCBANK", "SBIN"], ["SBIN"], ["SUNPHARMA"])
    assert m == {"HDFCBANK": "Banks", "SBIN": "PSU Banks", "BAJFINANCE": "Finance",
                 "INFY": "IT", "MARUTI": "Auto", "SUNPHARMA": "Pharma",
                 "APOLLOHOSP": "Healthcare"}, m
    assert build_map(parse_index_list(csv))["HDFCBANK"] == "Finance"
    json.dumps(ranked)
    print("\nSELFTEST PASSED")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    selftest() if ap.parse_args().selftest else main_live()
