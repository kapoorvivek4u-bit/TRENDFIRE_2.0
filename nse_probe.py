"""One-off check: which free NSE live endpoints answer from GitHub's servers. Prints sizes and keys only."""
import json, time, requests
s = requests.Session()
s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
                  "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9", "Referer": "https://www.nseindia.com/option-chain"})
for u in ["https://www.nseindia.com", "https://www.nseindia.com/option-chain"]:
    try: r = s.get(u, timeout=20); print("warm", u, r.status_code, len(r.text))
    except Exception as e: print("warm fail", u, e)
    time.sleep(1)
urls = {
 "oc_idx_old": "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY",
 "oc_contract_info": "https://www.nseindia.com/api/option-chain-contract-info?symbol=NIFTY",
 "oc_v3_idx": "https://www.nseindia.com/api/option-chain-v3?type=Indices&symbol=NIFTY",
 "oc_eq_old": "https://www.nseindia.com/api/option-chain-equities?symbol=SBIN",
 "oi_spurts": "https://www.nseindia.com/api/live-analysis-oi-spurts-underlyings",
 "oi_spurts_contracts": "https://www.nseindia.com/api/live-analysis-oi-spurts-contracts",
 "fo_stocks": "https://www.nseindia.com/api/equity-stockIndices?index=SECURITIES%20IN%20F%26O",
 "nifty50": "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%2050",
 "deriv_quote": "https://www.nseindia.com/api/quote-derivative?symbol=SBIN",
 "most_active_fut": "https://www.nseindia.com/api/live-analysis-variations?index=gainers",
 "allindices": "https://www.nseindia.com/api/allIndices",
}
for k, u in urls.items():
    try:
        r = s.get(u, timeout=25); t = r.text
        info = ""
        try:
            j = r.json()
            if isinstance(j, dict): info = "keys=" + ",".join(list(j.keys())[:12])
            if k == "oc_contract_info": print("  expiries:", str(j)[:300])
            if k == "oi_spurts": print("  sample:", json.dumps(j)[:600])
            if k == "fo_stocks": print("  sample:", json.dumps(j.get("data", [])[:2])[:700])
            if k == "deriv_quote": print("  sample:", json.dumps(j)[:500])
        except Exception: info = "not json: " + t[:80].replace("\n", " ")
        print(k, r.status_code, len(t), info)
    except Exception as e:
        print(k, "FAIL", e)
    time.sleep(1.5)
# if v3 needs expiry, try with the first expiry
try:
    ci = s.get(urls["oc_contract_info"], timeout=25).json()
    ex = (ci.get("expiryDates") or [None])[0]
    if ex:
        r = s.get(f"https://www.nseindia.com/api/option-chain-v3?type=Indices&symbol=NIFTY&expiry={ex}", timeout=25)
        j = r.json(); print("oc_v3_with_expiry", r.status_code, len(r.text), list(j.keys()), json.dumps(j)[:600])
except Exception as e:
    print("v3 expiry try failed", e)
import yfinance as yf
t0 = time.time(); d = yf.download(["SBIN.NS", "RELIANCE.NS", "^NSEI", "^NSEBANK"], period="2d", interval="5m", progress=False, group_by="ticker")
print("yf 5m rows", len(d), "last", d.index[-1] if len(d) else None, "secs", round(time.time() - t0, 1))
