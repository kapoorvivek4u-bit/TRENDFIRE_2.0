"""FII footprint for TradeReady (data/fii.json). Runs after the deals scan, once a day in the evening.

NSE does not publish which stocks FIIs bought each day, so this puts together what is published:
  1. FII positions in F&O (NSE participant-wise open interest, daily): stock futures, index futures, index options.
  2. Bulk and block deals in F&O stocks done by foreign institutions, picked out by name (data/deals.csv.gz).
  3. A stock-wise estimate: futures OI rising with price over 5 and 10 sessions, with delivery above its usual level.
     This is institution-style buying, not confirmed FII buying, and the site labels it that way.
"""
import datetime as dt
import io
import json
import os
import re
import time

import pandas as pd
import requests

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
DATA = "data"
PART_URL = "https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_{d}.csv"
PART_PATH = os.path.join(DATA, "fii_participant.csv")

# Names of foreign institutions as they appear in NSE bulk/block deal files.
FII_NAMES = [
    "goldman sachs", "morgan stanley", "societe generale", "bnp paribas", "citigroup", "citibank", "nomura",
    "jp morgan", "jpmorgan", "merrill lynch", "bofa securities", "barclays", "ubs ", "credit suisse", "hsbc",
    "deutsche bank", "clsa", "macquarie", "jefferies", "vanguard", "blackrock", "fidelity", "ishares",
    "government of singapore", "monetary authority of singapore", "abu dhabi investment", "kuwait investment",
    "qatar investment", "saudi", "norges bank", "copthall mauritius", "segantii", "integrated core strategies",
    "wellington", "capital group", "smallcap world fund", "new world fund", "t. rowe", "t rowe", "aberdeen",
    "schroder", "franklin templeton investment funds", "nordea", "ghisallo", "eastspring", "matthews", "polunin",
    "ashoka india opportunities", "vikasa india", "elara india opportunities", "california public employees",
    "florida retirement", "teacher retirement", "stichting", "pinebridge", "lazard", "first state",
    "stewart investors", "baillie gifford", "amundi", "allianz global", "massachusetts mutual", "ontario teachers",
    "canada pension", "cpp investment", "caisse de depot", "temasek", "bank of america",
]
# High-frequency and proprietary traders are also foreign, but their bulk deals are same-day trading, not buying: left out.
NOT_FII = re.compile(r"\b(mutual fund|mf|life insurance|general insurance|pension fund of india|epfo|nps trust|trustee company)\b", re.I)


def is_fii(name):
    n = " " + str(name or "").lower() + " "
    if NOT_FII.search(n):
        return False
    return any(k in n for k in FII_NAMES)


def nse():
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
                      "Accept": "*/*", "Referer": "https://www.nseindia.com/"})
    try:
        s.get("https://www.nseindia.com/option-chain", timeout=20)
    except Exception:
        pass
    return s


# ------------------------------------------------------------------ 1. participant-wise OI
COLS = {"Future Index Long": "if_l", "Future Index Short": "if_s", "Future Stock Long": "sf_l", "Future Stock Short": "sf_s",
        "Option Index Call Long": "ic_l", "Option Index Put Long": "ip_l", "Option Index Call Short": "ic_s", "Option Index Put Short": "ip_s",
        "Option Stock Call Long": "sc_l", "Option Stock Put Long": "sp_l", "Option Stock Call Short": "sc_s", "Option Stock Put Short": "sp_s"}


def fetch_participant(s, day):
    r = s.get(PART_URL.format(d=day.strftime("%d%m%Y")), timeout=30)
    if not r.ok or "Client Type" not in r.text:
        return None
    lines = r.text.splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("Client Type"))
    df = pd.read_csv(io.StringIO("\n".join(lines[start:])))
    df.columns = [c.strip() for c in df.columns]
    df["Client Type"] = df["Client Type"].astype(str).str.strip()
    out = []
    for _, row in df.iterrows():
        who = row["Client Type"]
        if who not in ("FII", "DII", "Pro", "Client"):
            continue
        rec = {"date": day.isoformat(), "who": who}
        for c, k in COLS.items():
            if c in df.columns:
                rec[k] = int(str(row[c]).replace(",", "").strip() or 0)
        out.append(rec)
    return out


def update_participant(s, today):
    hist = pd.read_csv(PART_PATH) if os.path.exists(PART_PATH) else pd.DataFrame(columns=["date", "who"])
    have = set(hist["date"].astype(str))
    new, day, tries = [], today, 0
    while tries < 25 and len(set(hist["date"].astype(str)) | {r["date"] for r in new}) < 25:
        if day.weekday() < 5 and day.isoformat() not in have:
            try:
                rows = fetch_participant(s, day)
                if rows:
                    new += rows
            except Exception as ex:
                print("participant", day, "failed:", ex)
            time.sleep(0.6)
        day -= dt.timedelta(days=1)
        tries += 1
    if new:
        hist = pd.concat([hist, pd.DataFrame(new)], ignore_index=True)
    hist = hist.drop_duplicates(["date", "who"], keep="last").sort_values(["date", "who"])
    hist = hist[hist["date"].astype(str) >= (today - dt.timedelta(days=60)).isoformat()]
    hist.to_csv(PART_PATH, index=False)
    print(f"participant OI: {hist['date'].nunique()} sessions, {len(new)} new rows")
    return hist


def participant_view(hist):
    f = hist[hist["who"] == "FII"].sort_values("date").tail(15)
    if f.empty:
        return None
    rows = []
    for _, r in f.iterrows():
        def side(l, s_):
            L, S = float(r.get(l) or 0), float(r.get(s_) or 0)
            return {"long": int(L), "short": int(S), "net": int(L - S), "long_pct": round(100 * L / (L + S), 1) if L + S else None}
        rows.append({"date": str(r["date"]), "sf": side("sf_l", "sf_s"), "if": side("if_l", "if_s"),
                     "calls": {"long": int(r.get("ic_l") or 0), "short": int(r.get("ic_s") or 0)},
                     "puts": {"long": int(r.get("ip_l") or 0), "short": int(r.get("ip_s") or 0)}})
    last, prev5 = rows[-1], rows[-6] if len(rows) >= 6 else rows[0]
    read = []
    d_sf = last["sf"]["net"] - prev5["sf"]["net"]
    read.append(f"Stock futures: FIIs are {last['sf']['long_pct']}% long; net {'added' if d_sf > 0 else 'cut'} "
                f"{abs(d_sf):,} contracts over {len(rows) - rows.index(prev5) - 1} sessions.")
    d_if = last["if"]["net"] - prev5["if"]["net"]
    read.append(f"Index futures: {last['if']['long_pct']}% long; net {'added' if d_if > 0 else 'cut'} {abs(d_if):,} contracts.")
    return {"rows": rows, "read": read}


# ------------------------------------------------------------------ 2. FII deals in F&O stocks
def fii_deals(fo, today, days=30):
    path = os.path.join(DATA, "deals.csv.gz")
    if not os.path.exists(path):
        return []
    d = pd.read_csv(path)
    d["date"] = pd.to_datetime(d["date"])
    d = d[(d["date"] >= pd.Timestamp(today - dt.timedelta(days=days))) & d["symbol"].isin(fo)]
    d = d[d["client"].map(is_fii)]
    if d.empty:
        return []
    d["value_cr"] = d["qty"] * d["price"] / 1e7
    out = []
    for sym, g in d.groupby("symbol"):
        buy, sell = g[g.side == "BUY"].value_cr.sum(), g[g.side == "SELL"].value_cr.sum()
        out.append({"symbol": sym, "buy_cr": round(buy, 1), "sell_cr": round(sell, 1), "net_cr": round(buy - sell, 1),
                    "last": g["date"].max().strftime("%Y-%m-%d"),
                    "deals": [{"date": r.date.strftime("%Y-%m-%d"), "who": str(r.client).title()[:60], "side": r.side,
                               "kind": r.kind, "value_cr": round(r.value_cr, 1), "price": r.price}
                              for r in g.sort_values("date", ascending=False).head(8).itertuples()]})
    out.sort(key=lambda x: -abs(x["net_cr"]))
    return out


# ------------------------------------------------------------------ 3. stock-wise estimate
def footprint(fo, sectors):
    oi = pd.read_csv(os.path.join(DATA, "fo_oi.csv.gz"))
    dl = pd.read_csv(os.path.join(DATA, "equity_deliv.csv.gz"))
    dl = dl[dl["series"] == "EQ"]
    oi_p = oi.pivot(index="date", columns="symbol", values="fut_oi").sort_index()
    px_p = oi.pivot(index="date", columns="symbol", values="fut_close").sort_index()
    dv = dl.pivot_table(index="date", columns="symbol", values="deliv_per").sort_index()
    tv = dl.pivot_table(index="date", columns="symbol", values="turnover_lacs").sort_index()
    asof = str(oi_p.index[-1])
    acc, dist = [], []
    for s in fo:
        if s not in oi_p or s not in px_p:
            continue
        o, p = oi_p[s].dropna(), px_p[s].dropna()
        if len(o) < 11 or len(p) < 11:
            continue
        oi5, oi10 = (o.iloc[-1] / o.iloc[-6] - 1) * 100, (o.iloc[-1] / o.iloc[-11] - 1) * 100
        px5, px10 = (p.iloc[-1] / p.iloc[-6] - 1) * 100, (p.iloc[-1] / p.iloc[-11] - 1) * 100
        up_days = int((o.diff().iloc[-5:] > 0).sum())
        d5 = d20 = t5 = t20 = None
        if s in dv:
            dd = dv[s].dropna()
            if len(dd) >= 25:
                d5, d20 = dd.iloc[-5:].mean(), dd.iloc[-25:-5].mean()
        if s in tv:
            tt = tv[s].dropna()
            if len(tt) >= 25:
                t5, t20 = tt.iloc[-5:].mean(), tt.iloc[-25:-5].mean()
        dlv_up = d5 is not None and d20 and d5 >= d20 * 1.1
        turn_up = t5 is not None and t20 and t5 >= t20 * 1.2
        row = {"symbol": s, "sector": (sectors.get(s) or {}).get("sector"), "oi5": round(oi5, 1), "oi10": round(oi10, 1),
               "px5": round(px5, 1), "px10": round(px10, 1), "oi_up_days": up_days,
               "deliv5": round(d5, 1) if d5 is not None else None, "deliv20": round(d20, 1) if d20 else None,
               "turn_x": round(t5 / t20, 2) if t5 and t20 else None}
        why = []
        if oi5 >= 3 and px5 > 0:
            sc = min(oi5, 30) / 3 + min(px5, 10) + up_days
            if dlv_up:
                sc += 3; why.append(f"delivery {d5:.0f}% vs usual {d20:.0f}%")
            if turn_up:
                sc += 2; why.append(f"turnover {t5 / t20:.1f}x usual")
            if oi10 >= 5 and px10 > 0:
                sc += 2; why.append("10-session build-up too")
            why.insert(0, f"futures OI {oi5:+.0f}% with price {px5:+.1f}% in 5 sessions; OI rose {up_days} of 5 days")
            acc.append({**row, "score": round(sc, 1), "why": why})
        elif oi5 >= 3 and px5 < 0:
            sc = min(oi5, 30) / 3 + min(-px5, 10) + up_days
            if dlv_up:
                sc += 3; why.append(f"delivery {d5:.0f}% vs usual {d20:.0f}% (selling with delivery)")
            if turn_up:
                sc += 2; why.append(f"turnover {t5 / t20:.1f}x usual")
            why.insert(0, f"futures OI {oi5:+.0f}% with price {px5:+.1f}% in 5 sessions; OI rose {up_days} of 5 days")
            dist.append({**row, "score": round(sc, 1), "why": why})
    acc.sort(key=lambda x: -x["score"]); dist.sort(key=lambda x: -x["score"])
    return asof, acc[:30], dist[:30]


def main():
    now = dt.datetime.now(IST)
    site = json.load(open(os.path.join(DATA, "site.json")))
    fo = sorted(site.get("closes") or {})
    sectors = (json.load(open(os.path.join(DATA, "sectors.json"))).get("map") or {}) if os.path.exists(os.path.join(DATA, "sectors.json")) else {}
    out = {"generated_ist": now.strftime("%Y-%m-%d %H:%M"), "notes": []}
    try:
        out["participant"] = participant_view(update_participant(nse(), now.date()))
    except Exception as ex:
        out["notes"].append(f"FII F&O positions not available: {ex}")
        print("participant failed:", ex)
    try:
        out["deals"] = fii_deals(fo, now.date())
    except Exception as ex:
        out["deals"] = []; print("deals failed:", ex)
    try:
        out["oi_asof"], out["accumulation"], out["distribution"] = footprint(fo, sectors)
    except Exception as ex:
        out["accumulation"] = out["distribution"] = []; print("footprint failed:", ex)
    with open(os.path.join(DATA, "fii.json"), "w") as f:
        json.dump(out, f, separators=(",", ":"), default=str)
    print(f"fii.json: participant {'yes' if out.get('participant') else 'no'}, {len(out['deals'])} FII-deal stocks, "
          f"{len(out['accumulation'])} accumulation, {len(out['distribution'])} distribution")


if __name__ == "__main__":
    main()
