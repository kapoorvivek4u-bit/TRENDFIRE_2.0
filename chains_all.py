#!/usr/bin/env python3
"""
TradeReady - Option screener: reads the option chain of every F&O stock (hourly in market hours
and once after the close), works out what option writers are doing, and
  1) writes data/chain_screen.json (one line per stock: signals, bias, PCR, range, max pain, IV)
  2) sends every chain to the site, so tapping any F&O stock opens its chain at once.

Signals (same rules as the stock panel):
  cover    call writers covering just above the price (call OI falling, premiums rising)
  cwrite   fresh call writing just above the price (sellers capping the upside)
  cbuy     calls being bought above the price
  pstrong  fresh put writing just under the price (sellers defending it)
  pexit    put writers leaving under the price (support weakening)
  pbuy     puts being bought under the price

  python chains_all.py
"""
import datetime as dt
import importlib.util
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, file))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main():
    import pandas as pd
    C = _load("chain_scan", "chain_scan.py")
    I = _load("intraday_scan", "intraday_scan.py")
    now = dt.datetime.now(IST)
    fo = pd.read_csv(os.path.join(DATA, "fo_oi.csv.gz"), parse_dates=["date"])
    syms = sorted(fo.loc[fo["date"] == fo["date"].max(), "symbol"])
    print(f"{len(syms)} F&O stocks")
    nse, rows, raw, fails = I.NSE(), [], [], []
    for i, s in enumerate(syms):
        if i and i % 70 == 0:
            nse = I.NSE()                                  # fresh cookies now and then
        c = None
        for attempt in range(2):
            try:
                c = C.raw_chain(nse, s)
            except Exception as ex:
                print(s, "failed:", ex)
            if c:
                break
            time.sleep(2)
        time.sleep(0.35)
        if not c:
            fails.append(s)
            continue
        raw.append(c)
        try:
            rows.append(C.read_chain(c))
        except Exception as ex:
            print(s, "read failed:", ex)
        if len(raw) >= 50:
            C.post(raw); raw = []
    if raw:
        C.post(raw)
    res = {"generated_ist": now.strftime("%Y-%m-%d %H:%M"), "checked": len(rows), "failed": fails, "rows": rows}
    with open(os.path.join(DATA, "chain_screen.json"), "w") as f:
        json.dump(res, f, separators=(",", ":"), allow_nan=False, default=str)
    print(f"chains: {len(rows)} read, {len(fails)} failed")
    if len(rows) < 20:
        sys.exit(1)


if __name__ == "__main__":
    main()
