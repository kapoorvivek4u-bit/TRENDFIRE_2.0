#!/usr/bin/env python3
"""
TradeReady - star investors' quarterly stakes (1% or more), from NSE shareholding filings.

Every listed company files its shareholding pattern each quarter. Holders with 1% or more
are named, with their %. This scan reads those filings for all NSE companies, keeps every
named holder at 1%+, and picks out the star investors (the list in deals_scan.py).

The first fill is large (about 2,300 filings a quarter), so each run works for a fixed
time and saves its progress; runs every 3 hours finish the latest two quarters in about a
day and the 8-quarter history over the following days. After that, each run only picks
up new filings.

Files:
  data/stars/holders.csv.gz   every named holder at 1%+ (symbol, quarter, filed, name, pct, shares)
  data/stars/progress.json    what has been read for each company
  data/stars.json             what the site shows

  python stars_scan.py             # live run (GitHub Actions)
  python stars_scan.py --selftest  # offline check
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
DIR = os.path.join(DATA_DIR, "stars")
HOLD = os.path.join(DIR, "holders.csv.gz")
PROG = os.path.join(DIR, "progress.json")
OUT = os.path.join(DATA_DIR, "stars.json")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
SCFG = {
    "quarters": 8,             # history kept
    "minutes": 40,             # work per run, then save and stop
    "per_symbol": 2,           # filings read per company per run (latest first)
    "late_days": 60,           # a quarter not filed this long after it ended counts as missing
    "pause": 0.25,             # seconds between NSE requests
    "move_pp": 0.01,           # smaller changes count as unchanged
}
MASTER = "https://www.nseindia.com/api/corporate-share-holdings-master?index=equities&symbol={s}"
EQUITY_LIST = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
AMBIGUOUS = {"Anil Kumar Goel", "Ramesh Damani", "Nemish Shah"}   # common names: may be someone else


# ----------------------------------------------------------------- star names
def star_rules():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from deals_scan import STARS
    return STARS


def star_of(name, rules):
    """Display name of the star investor a holder name belongs to, or None.
    Every word of one alternative must appear as a whole word."""
    c = " " + re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]", " ", str(name).upper())) + " "
    for star, alts in rules.items():
        if any(all(f" {w} " in c for w in words) for words in alts):
            return star
    return None


# ----------------------------------------------------------------- parsing
def parse_xbrl(x):
    """Named holders from one shareholding filing: list of (category, name, pct, shares).
    The name sits in context D_<id>; its numbers in context <id>. Percentages are fractions."""
    if isinstance(x, bytes):
        x = x.decode("utf-8", "ignore")
    out = []
    for ctx, name in re.findall(
            r'<in-bse-shp:NameOfTheShareholder contextRef="D_([^"]+)">([^<]+)<', x):
        def num(tag):
            m = re.search(r'<in-bse-shp:%s contextRef="%s"[^>]*>([^<]*)<'
                          % (tag, re.escape(ctx)), x)
            try:
                return float(m.group(1)) if m else None
            except ValueError:
                return None
        pct, sh = num("ShareholdingAsAPercentageOfTotalNumberOfShares"), num("NumberOfShares")
        cat = re.sub(r"_Context\d+$", "", ctx)
        cat = re.sub(r"^DetailsOfSharesHeldBy", "", cat)
        out.append((cat[:60], " ".join(name.split()), None if pct is None else round(pct * 100, 4),
                    None if sh is None else int(sh)))
    return out


def quarter_of(s):
    try:
        return pd.Timestamp(dt.datetime.strptime(str(s).strip().title(), "%d-%b-%Y")).date()
    except Exception:
        return None


def filings(master_json):
    """{quarter end: (xbrl url, filed date)} from the NSE list, latest version per quarter."""
    out = {}
    for f in master_json if isinstance(master_json, list) else []:
        q, url = quarter_of(f.get("date")), f.get("xbrl")
        if not q or not url or not str(url).lower().endswith(".xml"):
            continue
        filed = quarter_of(str(f.get("submissionDate") or f.get("broadcastDate") or "")[:11])
        stamp = str(f.get("broadcastDate") or "")
        if q not in out or stamp > out[q][2]:
            out[q] = (url, filed, stamp)
    return {q: (u, f) for q, (u, f, _) in out.items()}


def quarter_ends(today, n):
    """The last n quarter ends on or before yesterday, newest first."""
    ends, y = [], today.year
    while len(ends) < n:
        for m, d in ((12, 31), (9, 30), (6, 30), (3, 31)):
            q = dt.date(y, m, d)
            if q < today and len(ends) < n:
                ends.append(q)
        y -= 1
    return ends


# ----------------------------------------------------------------- work queue
def work_queue(symbols, prog, targets, today):
    """Companies with something left to read, most useful first."""
    todo = []
    for s in symbols:
        p = prog.get(s, {})
        need = [q for q in targets if q.isoformat() not in p.get("q", {})]
        if not need or p.get("checked") == today.isoformat():
            continue
        newest = targets.index(need[0])
        todo.append((newest, p.get("checked", ""), s))
    return [s for _, _, s in sorted(todo)]


def read_company(sym, get, prog, targets, today, rows, per_symbol):
    """Read up to per_symbol missing filings for one company. Returns False if NSE did
    not answer (so the company is tried again next run)."""
    raw = get(MASTER.format(s=sym))
    if raw is None:
        return False
    try:
        fl = filings(json.loads(raw))
    except Exception:
        fl = {}
    p = prog.setdefault(sym, {"q": {}})
    p["checked"] = today.isoformat()
    first = min(fl) if fl else None
    done = 0
    for q in targets:
        key = q.isoformat()
        if key in p["q"]:
            continue
        if q in fl:
            if done >= per_symbol:
                continue
            x = get(fl[q][0])
            if x is None:
                continue
            for cat, name, pct, sh in parse_xbrl(x):
                if pct is not None and pct >= 1.0:
                    rows.append({"symbol": sym, "quarter": key,
                                 "filed": fl[q][1].isoformat() if fl[q][1] else "",
                                 "category": cat, "name": name, "pct": pct, "shares": sh})
            p["q"][key] = "ok"
            done += 1
        elif (today - q).days > SCFG["late_days"] or (first and q < first):
            p["q"][key] = "none"
    return True


# ----------------------------------------------------------------- summary
def summarise(hold, prog, rules, now):
    """What the site shows: star stakes, their change vs the previous quarter, trend."""
    filed_q = {}
    for s, p in prog.items():
        for q, st in p.get("q", {}).items():
            if st == "ok":
                filed_q.setdefault(q, set()).add(s)
    quarters = sorted(filed_q)
    counts = {q: len(v) for q, v in filed_q.items()}
    if not quarters:
        return {"generated_ist": now.strftime("%Y-%m-%d %H:%M"), "quarter": None, "rows": [],
                "map": {}, "investors": {}, "coverage": {}}
    big = max(counts.values())
    full = [q for q in quarters if counts[q] >= 0.6 * big]
    Q = full[-1] if full else quarters[-1]
    P = max([q for q in quarters if q < Q], default=None)
    h = hold.copy()
    h["star"] = [star_of(n, rules) for n in h["name"]]
    h = h[h["star"].notna()]
    agg = (h.groupby(["star", "symbol", "quarter"])
           .agg(pct=("pct", "sum"), shares=("shares", "sum"), filed=("filed", "max"),
                held_as=("name", lambda v: ", ".join(sorted(set(v)))))
           .reset_index())
    rows, mp, inv = [], {}, {}
    for (star, sym), g in agg.groupby(["star", "symbol"]):
        g = g.set_index("quarter")
        trend = [round(float(g["pct"].get(q, 0.0)), 2) if sym in filed_q.get(q, ()) else None
                 for q in quarters]
        has_q, has_p = sym in filed_q.get(Q, ()), P is not None and sym in filed_q.get(P, ())
        now_pct = float(g["pct"][Q]) if Q in g.index else None
        before = float(g["pct"][P]) if P is not None and P in g.index else None
        if now_pct is not None:
            if before is None:
                change = "New ≥1%" if has_p else "Held"
            else:
                d = now_pct - before
                change = ("Increased" if d > SCFG["move_pp"] else
                          "Reduced" if d < -SCFG["move_pp"] else "Unchanged")
        elif before is not None and has_q:
            change = "Under 1% / exit"
        else:
            continue                                   # not filed yet for Q, or older history only
        run_start = None
        for i in range(quarters.index(Q), -1, -1):
            t = trend[i]
            if t is None or t < 1.0:
                break
            run_start = i
        since = (None if run_start is None else
                 ("≤ " if run_start == 0 else "") +
                 dt.date.fromisoformat(quarters[run_start]).strftime("%b %Y"))
        last = g.loc[Q] if Q in g.index else g.loc[P]
        r = {"investor": star, "ambiguous": star in AMBIGUOUS, "symbol": sym, "change": change,
             "pct_now": None if now_pct is None else round(now_pct, 2),
             "pct_before": None if before is None else round(before, 2),
             "delta": (round(now_pct - before, 2) if now_pct is not None and before is not None
                       else None),
             "trend": trend, "since": since,
             "shares": None if now_pct is None else int(last["shares"] or 0),
             "filed": str(last["filed"]), "held_as": last["held_as"]}
        rows.append(r)
        if now_pct is not None:
            inv[star] = inv.get(star, 0) + 1
        tag = (f"{star} {now_pct:.2f}%" + (" NEW" if change == "New ≥1%" else
               f" ({r['delta']:+.2f} pp)" if change in ("Increased", "Reduced") else "")
               if now_pct is not None else f"{star} exited (was {before:.2f}%)")
        mp.setdefault(sym, []).append(tag)
    order = {"New ≥1%": 0, "Increased": 1, "Reduced": 2, "Under 1% / exit": 3,
             "Unchanged": 4, "Held": 5}
    rows.sort(key=lambda r: (order[r["change"]], -(abs(r["delta"] or 0)), -(r["pct_now"] or 0)))
    return {"generated_ist": now.strftime("%Y-%m-%d %H:%M"), "quarter": Q, "previous": P,
            "quarters": quarters, "coverage": {q: counts[q] for q in quarters},
            "rows": rows, "map": {s: " · ".join(v) for s, v in mp.items()},
            "investors": dict(sorted(inv.items(), key=lambda x: -x[1]))}


# ----------------------------------------------------------------- live
def universe(get):
    syms = set()
    raw = get(EQUITY_LIST)
    if raw:
        try:
            df = pd.read_csv(io.BytesIO(raw), skipinitialspace=True)
            df.columns = [c.strip().upper() for c in df.columns]
            syms |= set(df.loc[df["SERIES"].astype(str).str.strip().isin(["EQ", "BE", "BZ"]),
                               "SYMBOL"].astype(str).str.strip())
            name_col = next((c for c in df.columns if "NAME" in c), None)
            if name_col:                                  # company names, for the site's search
                (pd.DataFrame({"symbol": df["SYMBOL"].astype(str).str.strip(),
                               "name": df[name_col].astype(str).str.strip()})
                 .drop_duplicates("symbol")
                 .to_csv(os.path.join(DATA_DIR, "names.csv"), index=False))
        except Exception as e:
            print(f"[warn] equity list unreadable: {e}")
    for path, col in ((os.path.join(DATA_DIR, "equity_deliv.csv.gz"), "symbol"),
                      (os.path.join(DATA_DIR, "fo_oi.csv.gz"), "symbol")):
        if os.path.exists(path):
            try:
                syms |= set(pd.read_csv(path, usecols=[col])[col].astype(str))
            except Exception:
                pass
    return sorted(syms)


def main_live():
    sys.path.insert(0, os.getcwd())
    from setups_scan import nse_session, nse_get
    os.makedirs(DIR, exist_ok=True)
    now = dt.datetime.now(IST)
    today = now.date()
    session = nse_session()
    calls = {"n": 0}

    def get(url):
        calls["n"] += 1
        if calls["n"] % 150 == 0:                       # refresh NSE cookies now and then
            try:
                session.get("https://www.nseindia.com", timeout=15)
            except Exception:
                pass
        time.sleep(SCFG["pause"])
        return nse_get(session, url, tries=2)

    prog = json.load(open(PROG)) if os.path.exists(PROG) else {}
    hold = (pd.read_csv(HOLD) if os.path.exists(HOLD) else
            pd.DataFrame(columns=["symbol", "quarter", "filed", "category", "name", "pct", "shares"]))
    targets = quarter_ends(today, SCFG["quarters"])
    syms = universe(get)
    queue = work_queue(syms, prog, targets, today)
    print(f"{len(syms)} companies | {len(queue)} with filings left to read")
    rows, done, fails, start = [], 0, 0, time.time()
    for s in queue:
        if time.time() - start > SCFG["minutes"] * 60:
            break
        if read_company(s, get, prog, targets, today, rows, SCFG["per_symbol"]):
            done += 1
        else:
            fails += 1
            if fails >= 25 and done == 0:
                print("[error] NSE is not answering; stopping this run.")
                break
    if rows:
        hold = pd.concat([hold, pd.DataFrame(rows)], ignore_index=True)
    keep = {q.isoformat() for q in targets}
    hold = hold[hold["quarter"].astype(str).isin(keep)].drop_duplicates(
        ["symbol", "quarter", "category", "name", "shares"])
    hold.to_csv(HOLD, index=False)
    with open(PROG, "w") as f:
        json.dump(prog, f)
    res = summarise(hold, prog, star_rules(), now)
    res["companies"] = len(syms)
    res["left"] = sum(1 for s in syms if any(q.isoformat() not in prog.get(s, {}).get("q", {})
                                             for q in targets))
    res["latest_done"] = sum(1 for s in syms
                             if targets[0].isoformat() in prog.get(s, {}).get("q", {}))
    with open(OUT, "w") as f:
        json.dump(res, f, indent=1)
    print(f"read {done} companies ({fails} no answer), {len(rows)} holders at 1%+ | "
          f"quarter {res.get('quarter')} | {len(res['rows'])} star stakes | {res['left']} left")


# ----------------------------------------------------------------- selftest
def selftest():
    here = os.path.dirname(os.path.abspath(__file__))
    rules = star_rules()
    # 1) names
    assert star_of("REKHA JHUNJHUNWALA", rules) == "Jhunjhunwala (Rare)"
    assert star_of("ABAKKUS FLEXI CAP FUND", rules) == "Sunil Singhania (Abakkus)"
    assert star_of("Mukul Mahavir Agrawal", rules) == "Mukul Agrawal"
    assert star_of("KEDIA SECURITIES PRIVATE LIMITED", rules) == "Vijay Kedia"
    assert star_of("EQUITY INTELLIGENCE INDIA PRIVATE LIMITED", rules) == "Porinju Veliyath"
    assert star_of("ANIL KUMAR GOEL, SEEMA GOEL", rules) == "Anil Kumar Goel"
    assert star_of("MAKELA HOLDINGS", rules) is None and star_of("TATA SONS", rules) is None

    # 2) a filing in NSE's XBRL layout (trimmed from a real one)
    def fake_xbrl(holders):
        parts = ['<?xml version="1.0"?><xbrli:xbrl>']
        for i, (cat, name, frac, sh) in enumerate(holders):
            c = f"{cat}_Context{i}"
            parts += [f'<in-bse-shp:NameOfTheShareholder contextRef="D_{c}">{name}'
                      f'</in-bse-shp:NameOfTheShareholder>',
                      f'<in-bse-shp:NumberOfShares contextRef="{c}" decimals="INF" '
                      f'unitRef="shares">{sh}</in-bse-shp:NumberOfShares>',
                      f'<in-bse-shp:ShareholdingAsAPercentageOfTotalNumberOfShares contextRef="{c}"'
                      f' decimals="4" unitRef="pure">{frac}</in-bse-shp:'
                      f'ShareholdingAsAPercentageOfTotalNumberOfShares>']
        return "".join(parts + ["</xbrli:xbrl>"]).encode()
    ind = "DetailsOfSharesHeldByResidentIndividualShareholdersHoldingNominalShareCapitalInExcessOfRsTwoLakh"
    x = fake_xbrl([(ind, "REKHA JHUNJHUNWALA", "0.0424", 37643895),
                   (ind, "REKHA JHUNJHUNWALA", "0.0107", 9540575),
                   ("MutualFundsOrUTI", "UTI-FLEXI CAP FUND", "0.0108", 9549187)])
    p = parse_xbrl(x)
    assert len(p) == 3 and p[0][1] == "REKHA JHUNJHUNWALA" and p[0][2] == 4.24 and p[2][0] == "MutualFundsOrUTI", p
    real = os.path.join(here, DATA_DIR, "probe", "shp_TITAN.xml")
    if os.path.exists(real):                          # the real filing, when the repo has it
        rk = sum(t[2] for t in parse_xbrl(open(real, "rb").read()) if "JHUNJHUNWALA" in t[1])
        assert abs(rk - 5.31) < 0.01, rk

    # 3) the NSE list of filings
    master = [{"date": "30-SEP-2026", "xbrl": "https://x/q3.xml", "submissionDate": "15-OCT-2026",
               "broadcastDate": "15-OCT-2026 15:59:39"},
              {"date": "30-SEP-2026", "xbrl": "https://x/q3r.xml", "submissionDate": "20-OCT-2026",
               "broadcastDate": "20-OCT-2026 10:00:00"},
              {"date": "30-JUN-2026", "xbrl": "https://x/q2.xml", "submissionDate": "14-JUL-2026",
               "broadcastDate": "14-JUL-2026 11:00:00"}]
    fl = filings(master)
    assert fl[dt.date(2026, 9, 30)][0] == "https://x/q3r.xml" and len(fl) == 2, fl
    today = dt.date(2026, 11, 16)
    tg = quarter_ends(today, 8)
    assert tg[0] == dt.date(2026, 9, 30) and tg[-1] == dt.date(2024, 12, 31) and len(tg) == 8

    # 4) reading companies: two filings per run, latest first; gaps become "none"
    q3 = fake_xbrl([(ind, "ASHISH KACHOLIA", "0.0384", 1269832),
                    (ind, "DOLLY KHANNA", "0.0105", 777842)])
    q2 = fake_xbrl([(ind, "ASHISH KACHOLIA", "0.0174", 575000),
                    (ind, "SUNIL SINGHANIA", "0.0120", 400000)])
    q1 = fake_xbrl([(ind, "ASHISH KACHOLIA", "0.0150", 500000)])
    srv = {MASTER.format(s="STOVE"): json.dumps(
               [{"date": "30-SEP-2026", "xbrl": "u3.xml", "submissionDate": "14-OCT-2026"},
                {"date": "30-JUN-2026", "xbrl": "u2.xml", "submissionDate": "14-JUL-2026"},
                {"date": "31-MAR-2026", "xbrl": "u1.xml", "submissionDate": "14-APR-2026"}]).encode(),
           "u3.xml": q3, "u2.xml": q2, "u1.xml": q1}
    get = srv.get
    prog, rows = {}, []
    assert work_queue(["STOVE"], prog, tg, today) == ["STOVE"]
    assert read_company("STOVE", get, prog, tg, today, rows, 2)
    st = prog["STOVE"]["q"]
    assert st["2026-09-30"] == st["2026-06-30"] == "ok" and "2026-03-31" not in st, st
    assert st["2025-12-31"] == "none"                             # before its first filing
    assert work_queue(["STOVE"], prog, tg, today) == []           # checked today
    later = today + dt.timedelta(days=1)
    assert read_company("STOVE", get, prog, tg, later, rows, 2)
    st = prog["STOVE"]["q"]
    assert st["2026-03-31"] == "ok" and st["2025-12-31"] == "none" and len(st) == 8, st
    assert not read_company("NOPE", get, prog, tg, later, rows, 2)  # NSE silent: retried later

    # 5) the summary: new, increased, exit, trend, since
    hold = pd.DataFrame(rows)
    now = dt.datetime(2026, 11, 16, 21, 0, tzinfo=IST)
    res = summarise(hold, prog, rules, now)
    assert res["quarter"] == "2026-09-30" and res["previous"] == "2026-06-30", res
    by = {(r["investor"], r["change"]): r for r in res["rows"]}
    k = by[("Ashish Kacholia", "Increased")]
    assert k["pct_now"] == 3.84 and k["pct_before"] == 1.74 and k["delta"] == 2.1, k
    assert k["since"] == "≤ Mar 2026" and k["trend"][-3:] == [1.5, 1.74, 3.84], k
    assert by[("Dolly Khanna", "New ≥1%")]["since"] == "Sep 2026"
    assert by[("Sunil Singhania (Abakkus)", "Under 1% / exit")]["pct_now"] is None
    assert "Ashish Kacholia 3.84% (+2.10 pp)" in res["map"]["STOVE"]
    assert "Dolly Khanna 1.05% NEW" in res["map"]["STOVE"]
    assert res["investors"]["Ashish Kacholia"] == 1 and res["coverage"]["2026-09-30"] == 1
    assert [r["change"] for r in res["rows"]][0] == "New ≥1%"
    print("SELFTEST PASSED")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    selftest() if ap.parse_args().selftest else main_live()
