#!/usr/bin/env python3
"""
TradeReady - bulk and block deals (NSE), tagged by who is buying.

Every evening:
  1. downloads the day's bulk and block deals from NSE (and backfills the last
     ~40 days through NSE's history API the first time)
  2. keeps a rolling history in data/deals.csv.gz
  3. tags each buyer/seller: Mutual fund, Star investor, Insurance, or Other
  4. writes data/deals.json: recent deals, and per stock the 30-day picture
     (mutual fund net buying in Rs crore, star investor deals)

The site shows a "Deals:" line on every stock card that has fund or star activity,
and a Deals page.

  python deals_scan.py             # live run (GitHub Actions)
  python deals_scan.py --selftest  # offline check
"""
import argparse
import datetime as dt
import io
import json
import os
import re
import sys
import time

import pandas as pd

DATA_DIR = "data"
HIST = os.path.join(DATA_DIR, "deals.csv.gz")
OUT = os.path.join(DATA_DIR, "deals.json")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
DCFG = {
    "keep_days": 400,          # history kept
    "window_days": 30,         # the per-stock picture covers this many calendar days
    "recent_days": 10,         # the Deals page lists this many calendar days
    "backfill_days": 45,       # first run: history fetched through the NSE API
    "min_other_cr": 25,        # "Other" buyers/sellers are listed only above this size
}

URLS = {
    "csv_bulk": "https://nsearchives.nseindia.com/content/equities/bulk.csv",
    "csv_block": "https://nsearchives.nseindia.com/content/equities/block.csv",
    "snapshot": "https://www.nseindia.com/api/snapshot-capital-market-largedeal",
    "hist_bulk": "https://www.nseindia.com/api/historical/bulk-deals?from={a}&to={b}",
    "hist_block": "https://www.nseindia.com/api/historical/block-deals?from={a}&to={b}",
}

# Star investors as their names appear in exchange deal reports. Edit freely:
#   display name: [words that must ALL appear in the client name]
STARS = {
    "Jhunjhunwala (Rare)": [["JHUNJHUNWALA"], ["RARE", "ENTERPRISES"], ["RARE", "INVESTMENTS"]],
    "Ashish Kacholia": [["KACHOLIA"], ["BENGAL", "FINANCE", "INVESTMENT"]],
    "Vijay Kedia": [["VIJAY", "KEDIA"], ["KEDIA", "SECURITIES"]],
    "Dolly Khanna": [["DOLLY", "KHANNA"], ["RAJIV", "KHANNA"]],
    "Sunil Singhania (Abakkus)": [["SINGHANIA"], ["ABAKKUS"]],
    "Mukul Agrawal": [["MUKUL", "AGRAWAL"], ["PARAM", "CAPITAL"]],
    "Ashish Dhawan": [["ASHISH", "DHAWAN"]],
    "Radhakishan Damani": [["RADHAKISHAN", "DAMANI"], ["DERIVE", "TRADING"],
                           ["BRIGHT", "STAR", "INVESTMENTS"]],
    "Porinju Veliyath": [["PORINJU"], ["EQUITY", "INTELLIGENCE"]],
    "Akash / Vallabh Bhanshali": [["BHANSHALI"]],
    "Madhusudan Kela": [["KELA"], ["SINGULARITY"]],
    "Nemish Shah": [["NEMISH", "SHAH"]],
    "Anil Kumar Goel": [["ANIL", "KUMAR", "GOEL"]],
    "Satpal Khattar": [["KHATTAR"]],
    "Mohnish Pabrai": [["PABRAI"]],
    "Ramesh Damani": [["RAMESH", "DAMANI"]],
}
MF_WORDS = ["MUTUAL FUND", " MF ", " MF", "MF ", "ASSET MANAGEMENT", "AMC "]
INSURANCE_WORDS = ["INSURANCE", "LIFE INSURANCE CORPORATION", "ASSURANCE"]
AMC_SHORT = [  # tidy names for the page
    ("SBI", "SBI MF"), ("HDFC", "HDFC MF"), ("ICICI PRUDENTIAL", "ICICI Pru MF"),
    ("NIPPON", "Nippon MF"), ("KOTAK", "Kotak MF"), ("AXIS", "Axis MF"),
    ("ADITYA BIRLA", "ABSL MF"), ("MOTILAL OSWAL", "Motilal MF"), ("DSP", "DSP MF"),
    ("MIRAE", "Mirae MF"), ("UTI", "UTI MF"), ("TATA", "Tata MF"), ("EDELWEISS", "Edelweiss MF"),
    ("QUANT", "Quant MF"), ("HSBC", "HSBC MF"), ("INVESCO", "Invesco MF"),
    ("FRANKLIN", "Franklin MF"), ("CANARA ROBECO", "Canara Robeco MF"),
    ("BANDHAN", "Bandhan MF"), ("PGIM", "PGIM MF"), ("WHITEOAK", "WhiteOak MF"),
    ("BAJAJ FINSERV", "Bajaj MF"), ("SUNDARAM", "Sundaram MF"), ("PPFAS", "PPFAS MF"),
    ("PARAG PARIKH", "PPFAS MF"), ("UNION", "Union MF"), ("LIC MUTUAL", "LIC MF"),
    ("BARODA BNP", "Baroda BNP MF"), ("360 ONE", "360 ONE MF"), ("HELIOS", "Helios MF"),
]


def classify(client):
    """(category, short name) for a client name from a deal report."""
    c = " " + re.sub(r"[^A-Z0-9 ]", " ", str(client).upper()) + " "
    c = re.sub(r"\s+", " ", c)
    for star, alts in STARS.items():
        if any(all(f" {w} " in c for w in words) for words in alts):
            return "Star", star
    if any(w in c for w in MF_WORDS):
        for key, short in AMC_SHORT:
            if key in c:
                return "MF", short
        return "MF", str(client).title()[:40]
    if any(w in c for w in INSURANCE_WORDS):
        return "Insurance", "LIC" if "LIFE INSURANCE CORPORATION" in c else str(client).title()[:40]
    return "Other", str(client).title()[:40]


# ----------------------------------------------------------------- parsing
def _pick(cols, *keys):
    """The first column whose simplified name contains any of the keys."""
    simple = {c: re.sub(r"[^a-z]", "", str(c).lower()) for c in cols}
    for k in keys:
        for c, s in simple.items():
            if k in s:
                return c
    return None


def normalise(df, kind):
    """Any NSE bulk/block table (CSV or API JSON) -> date, symbol, kind, client, side,
    qty, price."""
    if df is None or not len(df):
        return pd.DataFrame(columns=["date", "symbol", "kind", "client", "side", "qty", "price"])
    cols = list(df.columns)
    c_date = _pick(cols, "date", "dtdate", "dt")
    c_sym = _pick(cols, "symbol")
    c_cli = _pick(cols, "clientname", "client")
    c_side = _pick(cols, "buysell", "buyorsell", "side")
    c_qty = _pick(cols, "quantitytraded", "qtytrd", "quantity", "qty")
    c_px = _pick(cols, "tradeprice", "wghtavg", "watp", "tpwatp", "price")
    need = [c_date, c_sym, c_cli, c_side, c_qty, c_px]
    if any(c is None for c in need):
        raise ValueError(f"unexpected columns: {cols}")
    out = pd.DataFrame({
        "date": pd.to_datetime(df[c_date].astype(str).str.strip(), dayfirst=True,
                               errors="coerce").dt.normalize(),
        "symbol": df[c_sym].astype(str).str.strip().str.upper(),
        "kind": kind,
        "client": df[c_cli].astype(str).str.strip(),
        "side": df[c_side].astype(str).str.strip().str.upper().str[0].map(
            {"B": "BUY", "S": "SELL"}),
        "qty": pd.to_numeric(df[c_qty].astype(str).str.replace(",", ""), errors="coerce"),
        "price": pd.to_numeric(df[c_px].astype(str).str.replace(",", ""), errors="coerce"),
    })
    return out.dropna(subset=["date", "side", "qty", "price"])


def parse_csv(raw, kind):
    df = pd.read_csv(io.BytesIO(raw), skipinitialspace=True)
    df.columns = [str(c).strip() for c in df.columns]
    return normalise(df, kind)


def parse_json(raw):
    """NSE API JSON: either {"data": [...]} (history) or the snapshot with
    BULK_DEALS_DATA / BLOCK_DEALS_DATA lists."""
    j = json.loads(raw)
    frames = []
    if isinstance(j, dict) and "data" in j and isinstance(j["data"], list):
        return [pd.DataFrame(j["data"])]
    for key, kind in (("BULK_DEALS_DATA", "bulk"), ("BLOCK_DEALS_DATA", "block")):
        if isinstance(j, dict) and isinstance(j.get(key), list):
            frames.append((kind, pd.DataFrame(j[key])))
    return frames


# ----------------------------------------------------------------- network
def fetch(session, now, have_dates, notes):
    from setups_scan import nse_get
    got = []
    for kind in ("bulk", "block"):
        raw = nse_get(session, URLS[f"csv_{kind}"])
        if raw:
            try:
                got.append(parse_csv(raw, kind))
            except Exception as e:
                print(f"[warn] {kind}.csv unreadable: {e}")
    if not got:
        raw = nse_get(session, URLS["snapshot"])
        if raw:
            try:
                for kind, df in parse_json(raw):
                    got.append(normalise(df, kind))
            except Exception as e:
                print(f"[warn] deals snapshot unreadable: {e}")
    if len(have_dates) < 10:                       # first runs: backfill through the API
        a = (now - dt.timedelta(days=DCFG["backfill_days"])).strftime("%d-%m-%Y")
        b = now.strftime("%d-%m-%Y")
        for kind in ("bulk", "block"):
            raw = nse_get(session, URLS[f"hist_{kind}"].format(a=a, b=b))
            if raw:
                try:
                    for df in parse_json(raw):
                        got.append(normalise(df if isinstance(df, pd.DataFrame) else df[1],
                                             kind))
                except Exception as e:
                    print(f"[warn] {kind} history unreadable: {e}")
            time.sleep(2)
    if not got:
        notes.append("NSE deal files could not be downloaded today; showing saved history.")
    return got


# ----------------------------------------------------------------- analysis
def summarise(hist, today, universe=None):
    hist = hist.copy()
    hist["value_cr"] = hist["qty"] * hist["price"] / 1e7
    tags = hist["client"].map(classify)
    hist["cat"] = [t[0] for t in tags]
    hist["who"] = [t[1] for t in tags]
    win = hist[hist["date"] >= today - pd.Timedelta(days=DCFG["window_days"])]
    by_stock = {}
    for sym, g in win.groupby("symbol"):
        if universe is not None and sym not in universe:
            pass                                   # keep all: equity page uses non-F&O too
        mf = g[g["cat"] == "MF"]
        star = g[g["cat"] == "Star"]
        ins = g[g["cat"] == "Insurance"]
        if not (len(mf) or len(star) or len(ins)):
            continue
        sgn = lambda d: (d["value_cr"] * d["side"].map({"BUY": 1, "SELL": -1})).sum()
        item = {"mf_net_cr": round(float(sgn(mf)), 1) if len(mf) else None,
                "mf_buyers": sorted(set(mf.loc[mf["side"] == "BUY", "who"])),
                "mf_sellers": sorted(set(mf.loc[mf["side"] == "SELL", "who"])),
                "ins_net_cr": round(float(sgn(ins)), 1) if len(ins) else None,
                "stars": [], "last": g["date"].max().strftime("%Y-%m-%d")}
        for (who, side), s in star.groupby(["who", "side"]):
            item["stars"].append({"who": who, "side": side,
                                  "value_cr": round(float(s["value_cr"].sum()), 1),
                                  "date": s["date"].max().strftime("%Y-%m-%d")})
        item["text"] = deal_text(item)
        by_stock[sym] = item
    rec = hist[hist["date"] >= today - pd.Timedelta(days=DCFG["recent_days"])]
    rec = rec[(rec["cat"] != "Other") | (rec["value_cr"] >= DCFG["min_other_cr"])]
    recent = [{"date": r.date.strftime("%Y-%m-%d"), "symbol": r.symbol, "kind": r.kind,
               "side": r.side, "who": r.who, "cat": r.cat, "value_cr": round(r.value_cr, 1),
               "price": round(r.price, 2)}
              for r in rec.sort_values(["date", "value_cr"], ascending=[False, False])
              .itertuples()]
    return by_stock, recent


def deal_text(item):
    """One line for a stock card."""
    parts = []
    if item["mf_net_cr"] is not None:
        names = item["mf_buyers"] if item["mf_net_cr"] >= 0 else item["mf_sellers"]
        verb = "bought" if item["mf_net_cr"] >= 0 else "sold"
        parts.append(f"Funds {verb} net Rs {abs(item['mf_net_cr']):,.0f} Cr"
                     + (f" ({', '.join(names[:3])}{'…' if len(names) > 3 else ''})" if names else ""))
    for s in item["stars"]:
        parts.append(f"{s['who']} {'bought' if s['side'] == 'BUY' else 'sold'} "
                     f"Rs {s['value_cr']:,.1f} Cr")
    if item["ins_net_cr"] is not None:
        parts.append(f"Insurance {'bought' if item['ins_net_cr'] >= 0 else 'sold'} "
                     f"Rs {abs(item['ins_net_cr']):,.0f} Cr")
    return " · ".join(parts)


def probe(session):
    """One-time sample of NSE's shareholding-pattern data (for the next step: quarterly
    star-investor holdings). Saved to data/probe/; nothing on the site uses it yet."""
    from setups_scan import nse_get
    d = os.path.join(DATA_DIR, "probe")
    if os.path.exists(d):
        return
    os.makedirs(d, exist_ok=True)
    for sym in ("TITAN", "JUBLPHARMA"):
        url = ("https://www.nseindia.com/api/corporate-share-holdings-master"
               f"?index=equities&symbol={sym}")
        raw = nse_get(session, url, tries=2)
        if raw:
            open(os.path.join(d, f"shp_{sym}.json"), "wb").write(raw[:400_000])
            try:
                first = json.loads(raw)[0]
                link = next((v for v in first.values() if isinstance(v, str)
                             and v.lower().endswith(".xml")), None)
                if link:
                    x = nse_get(session, link, tries=2)
                    if x:
                        open(os.path.join(d, f"shp_{sym}.xml"), "wb").write(x[:2_000_000])
            except Exception as e:
                print(f"[warn] probe {sym}: {e}")
    print("probe saved:", os.listdir(d))


def main_live():
    sys.path.insert(0, os.getcwd())
    from setups_scan import nse_session
    os.makedirs(DATA_DIR, exist_ok=True)
    now = dt.datetime.now(IST)
    notes = []
    cols = ["date", "symbol", "kind", "client", "side", "qty", "price"]
    hist = (pd.read_csv(HIST, parse_dates=["date"]) if os.path.exists(HIST)
            else pd.DataFrame(columns=cols))
    have = set(pd.to_datetime(hist["date"]).dt.normalize())
    session = nse_session()
    new = fetch(session, now, have, notes)
    try:
        probe(session)
    except Exception as e:
        print(f"[warn] probe failed: {e}")
    if new:
        hist = pd.concat([hist] + new, ignore_index=True)
    hist["date"] = pd.to_datetime(hist["date"])
    if not len(hist):
        raise RuntimeError("No bulk/block deals could be downloaded from NSE.")
    hist = (hist.drop_duplicates(["date", "symbol", "kind", "client", "side", "qty", "price"])
            .sort_values(["date", "symbol"]))
    hist = hist[hist["date"] >= hist["date"].max() - pd.Timedelta(days=DCFG["keep_days"])]
    hist[cols].to_csv(HIST, index=False)
    today = pd.Timestamp(now.date())
    by_stock, recent = summarise(hist, today)
    res = {"generated_ist": now.strftime("%Y-%m-%d %H:%M"),
           "asof": hist["date"].max().strftime("%Y-%m-%d"),
           "days": int(hist["date"].nunique()), "stocks": by_stock, "recent": recent,
           "notes": notes}
    with open(OUT, "w") as f:
        json.dump(res, f, indent=1)
    print(f"{len(hist)} deals over {res['days']} days | {len(by_stock)} stocks with fund/star "
          f"activity | {len(recent)} recent")


# ----------------------------------------------------------------- selftest
def selftest():
    # 1) CSV in NSE's layout (padded headers, Rs, commas)
    csv = ("Date ,Symbol ,Security Name ,Client Name ,Buy/Sell ,Quantity Traded ,"
           "Trade Price / Wght. Avg. Price ,Remarks\n"
           "13-Nov-2026,MEESHO,Meesho Ltd,MOTILAL OSWAL MUTUAL FUND,BUY,\"10,000,000\",129.54,-\n"
           "13-Nov-2026,MEESHO,Meesho Ltd,HDFC MUTUAL FUND,BUY,2684000,129.6,-\n"
           "13-Nov-2026,ABC,Abc Ltd,ASHISH KACHOLIA,BUY,500000,240,-\n"
           "13-Nov-2026,ABC,Abc Ltd,GRAVITON RESEARCH CAPITAL LLP,SELL,500000,240,-\n"
           "12-Nov-2026,XYZ,Xyz Ltd,SBI MUTUAL FUND,SELL,1000000,500,-\n")
    df = parse_csv(csv.encode(), "bulk")
    assert len(df) == 5 and df["qty"].iloc[0] == 10_000_000 and df["side"].iloc[3] == "SELL", df
    assert df["date"].iloc[0] == pd.Timestamp("2026-11-13")
    # 2) API JSON shapes
    js = json.dumps({"data": [{"BD_DT_DATE": "14-Nov-2026", "BD_SYMBOL": "PQR",
                               "BD_SCRIP_NAME": "Pqr", "BD_CLIENT_NAME": "LIFE INSURANCE "
                               "CORPORATION OF INDIA", "BD_BUY_SELL": "BUY",
                               "BD_QTY_TRD": 2000000, "BD_TP_WATP": 400.5,
                               "BD_REMARKS": "-"}]}).encode()
    j1 = normalise(parse_json(js)[0], "block")
    assert len(j1) == 1 and j1["symbol"].iloc[0] == "PQR" and j1["price"].iloc[0] == 400.5
    snap = json.dumps({"BULK_DEALS_DATA": [{"date": "16-Nov-2026", "symbol": "LMN",
                                            "name": "Lmn", "clientName": "DOLLY KHANNA",
                                            "buySell": "SELL", "qty": "100000",
                                            "watp": "88.1"}], "BLOCK_DEALS_DATA": []}).encode()
    kinds = parse_json(snap)
    assert kinds[0][0] == "bulk" and normalise(kinds[0][1], "bulk")["side"].iloc[0] == "SELL"
    # 3) who is who
    assert classify("MOTILAL OSWAL MUTUAL FUND") == ("MF", "Motilal MF")
    assert classify("Nippon India Mutual Fund A/c Nippon India Small Cap Fund")[0] == "MF"
    assert classify("RARE ENTERPRISES") == ("Star", "Jhunjhunwala (Rare)")
    assert classify("Ashish Rameshchandra Kacholia")[0] == "Star"
    assert classify("LIFE INSURANCE CORPORATION OF INDIA") == ("Insurance", "LIC")
    assert classify("GRAVITON RESEARCH CAPITAL LLP")[0] == "Other"
    # 4) per-stock picture and card text
    hist = pd.concat([df, j1, normalise(kinds[0][1], "bulk")], ignore_index=True)
    by, recent = summarise(hist, pd.Timestamp("2026-11-16"))
    m = by["MEESHO"]
    assert m["mf_net_cr"] == round((10_000_000 * 129.54 + 2_684_000 * 129.6) / 1e7, 1), m
    assert m["mf_buyers"] == ["HDFC MF", "Motilal MF"] and "Funds bought net Rs 164 Cr" in m["text"]
    assert by["ABC"]["stars"][0]["who"] == "Ashish Kacholia" and "Kacholia bought" in by["ABC"]["text"]
    assert by["XYZ"]["mf_net_cr"] == -50.0 and "Funds sold" in by["XYZ"]["text"]
    assert "Insurance bought" in by["PQR"]["text"] and "Dolly Khanna sold" in by["LMN"]["text"]
    assert "GRAVITON" not in json.dumps(by)
    assert recent[0]["date"] == "2026-11-16" and all(r["cat"] != "Other" for r in recent)
    old = hist.copy()
    old["date"] = pd.Timestamp("2026-08-01")
    by2, rec2 = summarise(old, pd.Timestamp("2026-11-16"))
    assert by2 == {} and rec2 == []
    print("SELFTEST PASSED")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    selftest() if ap.parse_args().selftest else main_live()
