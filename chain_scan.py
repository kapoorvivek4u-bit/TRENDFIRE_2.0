#!/usr/bin/env python3
"""
TradeReady - one stock's option chain, fetched on demand.

The site calls this (GitHub Actions, workflow chain.yml) when someone opens a stock whose chain
it does not have yet. It reads the NSE option chain for the nearest expiry (or the one asked for)
and sends the strikes around the money to the site, which shows the table and explains it.

  python chain_scan.py SBIN              # nearest expiry
  python chain_scan.py SBIN 29-Dec-2026  # a given expiry
  python chain_scan.py --selftest
"""
import datetime as dt
import importlib.util
import json
import os
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
SITE = os.environ.get("SITE_URL", "https://tradeready.in")
KEY = os.environ.get("INGEST_KEY", "")
INDICES = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, file))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def side(o):
    if not o:
        return None
    return {"oi": o.get("openInterest") or 0, "chg": o.get("changeinOpenInterest") or 0, "ltp": o.get("lastPrice") or 0,
            "lc": round(o.get("change") or 0, 2), "iv": o.get("impliedVolatility") or 0, "vol": o.get("totalTradedVolume") or 0}


def raw_chain(nse, symbol, expiry=None, band=12):
    """The chain in the site's format: strikes around the money, both sides."""
    q = requests.utils.quote(symbol)
    info = nse.get(f"option-chain-contract-info?symbol={q}")
    exps = (info or {}).get("expiryDates") or []
    if not exps:
        return None
    exp = expiry if expiry in exps else exps[0]
    kind = "Indices" if symbol in INDICES else "Equity"
    j = nse.get(f"option-chain-v3?type={kind}&symbol={q}&expiry={exp}")
    data = ((j or {}).get("records") or {}).get("data") or []
    if not data and expiry is None and len(exps) > 1:          # expiry day after the close
        exp = exps[1]
        j = nse.get(f"option-chain-v3?type={kind}&symbol={q}&expiry={exp}")
        data = ((j or {}).get("records") or {}).get("data") or []
    spot, rows = None, {}
    for d in data:
        k = d.get("strikePrice")
        if k is None:
            continue
        for s in ("CE", "PE"):
            if d.get(s):
                spot = spot or d[s].get("underlyingValue")
        rows[k] = {"k": k, "ce": side(d.get("CE")), "pe": side(d.get("PE"))}
    if not rows or not spot:
        return None
    ks = sorted(rows)
    atm = min(ks, key=lambda k: abs(k - spot))
    i = ks.index(atm)
    keep = ks[max(0, i - band): i + band + 1]
    tot = lambda f, sd: sum((rows[k][sd] or {}).get(f, 0) for k in ks)
    return {"s": symbol, "spot": spot, "expiry": exp, "expiries": exps[:4], "atm": atm,
            "at": dt.datetime.now(IST).strftime("%Y-%m-%d %H:%M"),
            "tot": {"ce_oi": tot("oi", "ce"), "pe_oi": tot("oi", "pe"), "ce_chg": tot("chg", "ce"), "pe_chg": tot("chg", "pe")},
            "all": [[k, (rows[k]["ce"] or {}).get("oi", 0), (rows[k]["pe"] or {}).get("oi", 0)] for k in ks],   # for max pain
            "rows": [rows[k] for k in keep]}


def post(chains):
    if not KEY:
        print("INGEST_KEY is not set; not sending.")
        return False
    r = requests.post(f"{SITE}/api/ingest/chain", data=json.dumps({"chains": chains}, separators=(",", ":")), timeout=60,
                      headers={"content-type": "application/json", "x-ingest-key": KEY})
    print("sent to site:", r.status_code, r.text[:200])
    return r.ok


def main(args):
    I = _load("intraday_scan", "intraday_scan.py")
    sym = args[0].upper().strip()
    exp = args[1] if len(args) > 1 else None
    nse = I.NSE()
    c = None
    for _ in range(2):
        c = raw_chain(nse, sym, exp)
        if c:
            break
        time.sleep(2)
    if not c:
        print("no chain for", sym)
        post([{"s": sym, "error": "NSE did not return an option chain for this stock.", "at": dt.datetime.now(IST).strftime("%Y-%m-%d %H:%M")}])
        sys.exit(1)
    print(f"{sym}: {len(c['rows'])} strikes, expiry {c['expiry']}, spot {c['spot']}")
    post([c])


def selftest():
    class Fake:
        def get(self, path):
            if "contract-info" in path:
                return {"expiryDates": ["29-Dec-2026", "26-Jan-2027"]}
            return {"records": {"data": [{"strikePrice": k,
                                          "CE": {"openInterest": 100, "changeinOpenInterest": 5, "lastPrice": 10, "change": -1, "impliedVolatility": 20, "totalTradedVolume": 9, "underlyingValue": 101},
                                          "PE": {"openInterest": 200, "changeinOpenInterest": 15, "lastPrice": 8, "change": 1, "impliedVolatility": 22, "totalTradedVolume": 7, "underlyingValue": 101}}
                                         for k in range(60, 141, 2)]}}
    c = raw_chain(Fake(), "ABC")
    assert c and c["atm"] == 100 and len(c["rows"]) == 25 and c["tot"]["pe_oi"] == 200 * 41, c
    print("selftest ok")


if __name__ == "__main__":
    selftest() if "--selftest" in sys.argv else main(sys.argv[1:])
