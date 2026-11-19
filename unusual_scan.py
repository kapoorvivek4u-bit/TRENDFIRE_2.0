#!/usr/bin/env python3
"""
TrendFire Setups - Unusual Activity scan (NSE F&O stocks).

Question it answers: in the last few hours or the last couple of days, is
something happening in this stock that is not normal FOR THIS STOCK?

Five kinds of evidence, each measured against the stock's own recent history:
  Volume    Daily volume vs the 20 days before the scan window.
            Hourly volume vs the SAME CLOCK HOUR on earlier days, so the busy
            opening hour is not mistaken for a spike.
            Patterns: absorption (big volume, small candle), dry-up then
            explosion, flush absorbed (sharp fall bought back).
  Range     A day whose range is 2x or more its normal range.
  Gap       An opening gap of one normal day's range or more that held.
  OI        A one-day change in futures open interest that is large for this
            stock, after removing the market-wide change (expiry effects).
  Delivery  Delivery % well above the stock's usual (only if the equity scan
            has saved NSE's delivery file).

Events from today count fully, yesterday 0.7, two days ago 0.5.

Outputs
  data/unusual.json    read by app.py

Usage
  python unusual_scan.py             # live run
  python unusual_scan.py --selftest  # offline logic check, no network
"""
import argparse
import datetime as dt
import importlib.machinery
import importlib.util
import json
import os

import numpy as np
import pandas as pd

UCFG = {
    "hourly_window": 14,          # completed hourly bars scanned (about 2 days)
    "daily_window": 3,            # completed daily bars scanned
    "hourly_min": 2.5,            # hourly volume vs the same hour's usual
    "daily_min": 2.0,             # daily volume vs usual
    "range_min": 2.0,             # day's range vs normal range
    "gap_min": 1.0,               # opening gap vs normal range
    "oi_floor": 0.04,             # smallest one-day OI change that can count
    "oi_mult": 2.5,               # ... and it must be this many times the stock's usual
    "deliv_mult": 1.4, "deliv_pts": 10.0,
    "oi_build_days": 7,           # OI build-up over this many sessions ...
    "oi_build_min": 0.15,         # ... at least this much above the market's change ...
    "oi_build_mult": 3.0,         # ... and this many times the stock's usual 7-session change
    "oi_build_flat": 0.04,        # price change under this = flat: big positions building quietly
    # short squeeze watch: a big short build-up day, shorts still in, price back above it
    "sq_look": 30,                # sessions searched for the short build-up day
    "sq_oi_jump": 0.08,           # OI rise that day, above the market's change ...
    "sq_px_drop": -0.015,         # ... while price fell at least this much
    "sq_kept": 0.6,               # share of that day's added OI still open now
    "sq_trapped": 0.03,           # price this far above that day's close = shorts trapped
    "sq_late": 0.15,              # ... further than this = the squeeze has mostly run
    "sq_cover": -0.02,            # OI change over the last 2 sessions that counts as covering
    "sq_history": 15,             # sessions of OI needed before the short day (new F&O stocks
                                  # build OI from zero, which is not a short build-up)
    "age_weight": [1.0, 0.7, 0.5],
    "min_score": 3.0,             # score needed to be listed
    "moved_atr": 2.0,             # price move over the window, in normal day-ranges, that counts as "moved"
}


def load_engine():
    """The F&O engine file, whatever name it was uploaded under."""
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


def age_of(day, cal):
    """Trading days between `day` and the newest day in `cal` (0 = newest)."""
    return int(sum(1 for x in cal if x > day))


def tier(x, cuts):
    return int(1 + sum(x >= c for c in cuts))


def sign(x, dead=0.0):
    return 1 if x > dead else -1 if x < -dead else 0


# ------------------------------------------------------------------ events
def hourly_events(hr, cal):
    """One event per day: unusual hourly volume, judged hour-for-hour."""
    W = UCFG["hourly_window"]
    if hr is None or len(hr) < W + 60:
        return []
    o, h, l, c, v = (hr[k].values.astype(float)
                     for k in ("Open", "High", "Low", "Close", "Volume"))
    slots = hr.index.strftime("%H:%M")
    pre = hr.iloc[:-W]
    base = pre["Volume"].astype(float).groupby(pre.index.strftime("%H:%M")) \
        .apply(lambda s: s.iloc[-20:].median())
    n0 = len(hr) - W
    pc = np.roll(c, 1)
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    atr = float(np.mean(tr[max(1, n0 - 35):n0]))
    by_day = {}
    for i in range(n0, len(hr)):
        b = base.get(slots[i], np.nan)
        if not (b > 0) or not (v[i] > 0):
            continue
        rv = v[i] / b
        if rv < UCFG["hourly_min"]:
            continue
        rng = h[i] - l[i]
        pos = (c[i] - l[i]) / rng if rng > 0 else 0.5
        pattern, side = None, 0
        if rv >= 3 and o[i] > 0 and (o[i] - l[i]) / o[i] >= 0.01 and pos >= 0.5:
            pattern, side = "flush absorbed", 1
        elif atr > 0 and rng < 0.7 * atr:
            pattern = "absorption"
        elif c[i] > o[i] and pos >= 0.6:
            side = 1
        elif c[i] < o[i] and pos <= 0.4:
            side = -1
        by_day.setdefault(hr.index[i].normalize(), []).append(
            (rv, slots[i], pattern, side))
    out = []
    for day, bars in by_day.items():
        age = age_of(day, cal)
        if age > 2:
            continue
        peak = max(bars)
        pats = sorted({b[2] for b in bars if b[2]})
        strength = min(4, tier(peak[0], (4, 6)) + (len(bars) >= 3) + bool(pats))
        net = sum(b[3] * b[0] for b in bars)
        text = (f"{len(bars)} hour{'s' if len(bars) > 1 else ''} of unusual volume, "
                f"peak {peak[0]:.1f}x at {peak[1]}")
        if pats:
            text += ", " + " and ".join(pats)
        out.append({"src": "volume_h", "age": age, "day": day, "strength": strength,
                    "side": sign(net, 0.5), "text": text})
    return out


def daily_events(d, cal):
    """Volume, range and gap events on the last few completed days."""
    W = UCFG["daily_window"]
    if d is None or len(d) < W + 40:
        return []
    o, h, l, c, v = (d[k].values.astype(float)
                     for k in ("Open", "High", "Low", "Close", "Volume"))
    n0 = len(d) - W
    base = float(np.median(v[n0 - 20:n0]))
    pc = np.roll(c, 1)
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    atr = float(np.mean(tr[n0 - 14:n0]))
    out = []
    if not (base > 0 and atr > 0):
        return out
    for i in range(n0, len(d)):
        day = d.index[i].normalize()
        age = age_of(day, cal)
        if age > 2:
            continue
        chg = c[i] / c[i - 1] - 1
        rng = h[i] - l[i]
        pos = (c[i] - l[i]) / rng if rng > 0 else 0.5
        move = sign(chg, 0.003)
        rv = v[i] / base
        if rv >= UCFG["daily_min"]:
            strength, side, extra = tier(rv, (3, 5)), move, ""
            if (rv >= 3 and (o[i] - l[i]) / o[i] >= 0.015 and pos >= 0.5
                    and c[i] > c[i - 10:i].min()):
                strength, side, extra = strength + 2, 1, ", flush absorbed"
            elif rv >= 2.5 and rng < 0.7 * atr:
                strength, side, extra = strength + 1, 0, ", absorption (small candle)"
            elif rv >= 2.5 and np.mean(v[i - 5:i]) < 0.65 * base:
                strength, extra = strength + 1, ", after a dry-up"
            out.append({"src": "volume_d", "age": age, "day": day, "strength": strength,
                        "side": side, "text": f"Day's volume {rv:.1f}x usual{extra}"})
        if tr[i] >= UCFG["range_min"] * atr:
            out.append({"src": "range", "age": age, "day": day,
                        "strength": tier(tr[i] / atr, (3,)), "side": move,
                        "text": f"Range {tr[i] / atr:.1f}x normal, closed {chg * 100:+.1f}%"})
        gap = o[i] - c[i - 1]
        if abs(gap) >= UCFG["gap_min"] * atr:
            held = l[i] > c[i - 1] if gap > 0 else h[i] < c[i - 1]
            if held:
                out.append({"src": "gap", "age": age, "day": day, "strength": 1,
                            "side": sign(gap),
                            "text": f"Gap {'up' if gap > 0 else 'down'} "
                                    f"{abs(gap) / c[i - 1] * 100:.1f}%, held"})
    return out


def oi_tables(fo):
    """One-day OI change per stock, minus that day's market-wide change."""
    oi = fo.pivot(index="date", columns="symbol", values="fut_oi").sort_index()
    px = fo.pivot(index="date", columns="symbol", values="fut_close").sort_index()
    chg = oi.pct_change(fill_method=None)
    rel = chg.sub(chg.median(axis=1), axis=0)
    N = UCFG["oi_build_days"]
    chN = oi / oi.shift(N) - 1
    relN = chN.sub(chN.median(axis=1), axis=0)
    return rel, chg, px.pct_change(fill_method=None), relN, chN, px / px.shift(N) - 1


def oi_events(sym, tables, cal):
    rel, chg, pxc = tables[:3]
    if sym not in rel.columns:
        return []
    r = rel[sym].dropna()
    if len(r) < 17:
        return []
    usual = float(r.iloc[:-2].abs().iloc[-60:].median())
    thr = max(UCFG["oi_floor"], UCFG["oi_mult"] * usual)
    out = []
    for day in r.index[-2:]:
        age = age_of(day, cal)
        x = float(r[day])
        if age > 2 or abs(x) < thr:
            continue
        p = float(pxc.at[day, sym]) if not np.isnan(pxc.at[day, sym]) else 0.0
        label = (("long build-up" if p >= 0 else "short build-up") if x > 0
                 else ("short covering" if p >= 0 else "long unwinding"))
        out.append({"src": "oi", "age": age, "day": day,
                    "strength": 2 if abs(x) >= 2 * thr else 1, "side": 1 if p >= 0 else -1,
                    "text": f"OI {float(chg.at[day, sym]) * 100:+.0f}% in a day, "
                            f"price {p * 100:+.1f}%: {label}"})
    return out


OI_KINDS = {  # (OI building?, price) -> (text, side, strength)
    (True, "flat"): ("big positions building, direction not clear yet", 0, 3),
    (True, "up"): ("steady long build-up", 1, 1),
    (True, "down"): ("steady short build-up", -1, 1),
    (False, "up"): ("shorts exiting (short covering), rally not backed by fresh buying", 1, 1),
    (False, "down"): ("longs exiting (long unwinding)", -1, 1),
    (False, "flat"): ("positions closing on both sides, interest fading", 0, 1),
}


def oi_build(sym, tables, cal):
    """OI change over several sessions (adjusted for the market) if it is unusual for this
    stock: dict(day, age, oi, px, building, text, side, strength), else None."""
    if len(tables) < 6:
        return None
    relN, chN, pxN = tables[3:6]
    N = UCFG["oi_build_days"]
    if sym not in relN.columns:
        return None
    r = relN[sym].dropna()
    if len(r) < N + 3:
        return None
    if chN[sym].first_valid_index() != chN.first_valid_index() and len(r) < N + 10:
        return None                              # new to F&O: OI is still building from zero
    day = r.index[-1]
    age = age_of(day, cal)
    usual = float(r.iloc[:-1].abs().median())
    x = float(r.iloc[-1])
    if age > 2 or abs(x) < max(UCFG["oi_build_min"], UCFG["oi_build_mult"] * usual):
        return None
    p = float(pxN.at[day, sym]) if not np.isnan(pxN.at[day, sym]) else 0.0
    move = "flat" if abs(p) <= UCFG["oi_build_flat"] else ("up" if p > 0 else "down")
    text, side, strength = OI_KINDS[(x > 0, move)]
    return {"day": day, "age": age, "oi": float(chN.at[day, sym]) * 100, "px": p * 100,
            "building": x > 0, "text": text, "side": side, "strength": strength}


def oi_build_events(sym, tables, cal):
    """OI building up, or positions exiting, over several sessions (not just one day)."""
    b = oi_build(sym, tables, cal)
    if not b:
        return []
    N = UCFG["oi_build_days"]
    return [{"src": "oi_build", "age": b["age"], "day": b["day"], "strength": b["strength"],
             "side": b["side"],
             "text": f"OI {b['oi']:+.0f}% in {N} sessions, price {b['px']:+.1f}%: {b['text']}"}]


def oi_moves(symbols, tables, cal):
    """Every stock with an unusual 7-session OI change, for the OI page (no score needed)."""
    out = []
    for s in symbols:
        b = oi_build(s, tables, cal)
        if b:
            out.append({"symbol": s, "oi": round(b["oi"], 1), "px": round(b["px"], 1),
                        "building": b["building"], "text": b["text"], "side": b["side"]})
    return sorted(out, key=lambda x: -abs(x["oi"]))


def squeeze_watch(symbols, fo, hourly=None, cal=None, setups=None):
    """Short squeeze watch: stocks where a big short build-up (OI jump, price down) is still
    open while price has come back above that day's close, so those shorts are losing.
      Watch          price 0-3% above the short day; shorts still in
      Shorts trapped price 3%+ above; shorts still in
      Squeeze on     shorts started covering: OI falling over the last 2 sessions, price up
      Ran already    price more than 15% above: most of the move is done"""
    C = UCFG
    oi = fo.pivot(index="date", columns="symbol", values="fut_oi").sort_index()
    px = fo.pivot(index="date", columns="symbol", values="fut_close").sort_index()
    ch = oi.pct_change(fill_method=None)
    rel = ch.sub(ch.median(axis=1), axis=0)
    pc = px.pct_change(fill_method=None)
    n = len(oi)
    if n < 6:
        return []
    out = []
    for s in symbols:
        if s not in oi.columns or np.isnan(oi[s].iloc[-1]) or np.isnan(px[s].iloc[-1]):
            continue
        best = None
        first = oi[s].first_valid_index()
        if first is None:
            continue
        # a stock that joined F&O after the file starts builds OI from zero: skip its
        # first sessions (stocks there from the start are fine)
        start_i = 0 if first == oi.index[0] else oi.index.get_loc(first) + C["sq_history"]
        for i in range(max(1, n - C["sq_look"], start_i), n - 1):
            r_, p_ = rel[s].iloc[i], pc[s].iloc[i]
            if not (r_ >= C["sq_oi_jump"] and p_ <= C["sq_px_drop"]):
                continue
            added = oi[s].iloc[i] - oi[s].iloc[i - 1]
            if not added > 0:
                continue
            if best is None or added >= best[1]:
                best = (i, added)
        if not best:
            continue
        i, added = best
        # shorts still open = the lowest OI since that day, not today's OI: if OI dipped
        # back to its old level in between (an expiry, a covering day), those shorts left,
        # and any OI added later is new money, maybe longs
        low = oi[s].iloc[i:].min()
        low_before = oi[s].iloc[i:max(i + 1, n - 2)].min()   # before the last 2 sessions
        kept = (min(oi[s].iloc[-1], low) - oi[s].iloc[i - 1]) / added
        kept_before = (low_before - oi[s].iloc[i - 1]) / added
        above = px[s].iloc[-1] / px[s].iloc[i] - 1
        if max(kept, kept_before) < C["sq_kept"] or above < 0:
            continue
        cover = float(rel[s].iloc[-2:].sum())
        p2 = px[s].iloc[-1] / px[s].iloc[-3] - 1
        if above > C["sq_late"]:
            stage = "Ran already"
        elif cover <= C["sq_cover"] and p2 > 0:
            stage = "Squeeze on"
        elif kept < C["sq_kept"]:
            continue                             # shorts mostly gone, without a clear squeeze
        elif above >= C["sq_trapped"]:
            stage = "Shorts trapped"
        else:
            stage = "Watch"
        notes = []
        if hourly is not None and cal is not None and hourly.get(s) is not None:
            ev = [e for e in hourly_events(hourly[s], cal) if "absorption" in e["text"]
                  or "flush absorbed" in e["text"]]
            if ev:
                notes.append(f"{len(ev)} day{'s' if len(ev) > 1 else ''} of absorption volume")
        for x in (setups or {}).get(s, []):
            notes.append(x)
        out.append({
            "symbol": s, "stage": stage, "close": round(float(px[s].iloc[-1]), 2),
            "short_day": oi.index[i].strftime("%Y-%m-%d"),
            "short_oi": round(float(ch[s].iloc[i]) * 100, 1),
            "short_px": round(float(pc[s].iloc[i]) * 100, 1),
            "short_close": round(float(px[s].iloc[i]), 2),
            "kept": round(float(max(kept, 0)) * 100), "above": round(float(above) * 100, 1),
            "cover": round(cover * 100, 1), "notes": notes})
    order = {"Squeeze on": 0, "Shorts trapped": 1, "Watch": 2, "Ran already": 3}
    return sorted(out, key=lambda x: (order[x["stage"]], x["above"]))


def log_squeeze(watch, asof):
    """One line per stock per day, so we can later check what happened next."""
    path = os.path.join(DATA_DIR, "squeeze_log.csv")
    new = pd.DataFrame([{"date": asof, "symbol": x["symbol"], "stage": x["stage"],
                         "close": x["close"], "above": x["above"]} for x in watch
                        if x["stage"] != "Ran already"])
    if new.empty:
        return
    old = pd.read_csv(path) if os.path.exists(path) else pd.DataFrame(columns=new.columns)
    seen = set(zip(old["date"].astype(str), old["symbol"]))
    new = new[[(d, s) not in seen for d, s in zip(new["date"], new["symbol"])]]
    pd.concat([old, new], ignore_index=True).to_csv(path, index=False)


def delivery_events(dl, cal):
    if dl is None or len(dl) < 23:
        return []
    dp = dl["deliv_per"].astype(float).values
    cl = dl["close"].astype(float).values
    out = []
    for k in (len(dl) - 2, len(dl) - 1):
        day = pd.Timestamp(dl["date"].iloc[k]).normalize()
        age = age_of(day, cal)
        base = np.nanmean(dp[k - 20:k])
        if age > 2 or not (base > 0) or np.isnan(dp[k]):
            continue
        if dp[k] >= UCFG["deliv_mult"] * base and dp[k] - base >= UCFG["deliv_pts"]:
            out.append({"src": "delivery", "age": age, "day": day, "strength": 1,
                        "side": sign(cl[k] / cl[k - 1] - 1, 0.003),
                        "text": f"Delivery {dp[k]:.0f}% vs {base:.0f}% usual"})
    return out


def score_events(events):
    """Total score, lean, and the events ordered newest and strongest first."""
    w = UCFG["age_weight"]
    best = {}
    for e in events:
        e["w"] = w[e["age"]] * e["strength"]
        best[e["src"]] = max(best.get(e["src"], 0.0), e["w"])
    vd, vh = best.pop("volume_d", 0.0), best.pop("volume_h", 0.0)
    score = max(vd, vh) + 0.5 * min(vd, vh) + sum(best.values())
    net = sum(e["side"] * e["w"] for e in events)
    bias = "Buying" if net >= 1 else "Selling" if net <= -1 else "Mixed"
    events = sorted(events, key=lambda e: (e["age"], -e["w"]))
    return round(float(score), 1), bias, events


def when_text(e):
    return e["day"].strftime("%a %d")


def analyse_unusual(sym, d, hr, tables, dl, cal):
    events = (hourly_events(hr, cal) + daily_events(d, cal)
              + (oi_events(sym, tables, cal) + oi_build_events(sym, tables, cal)
                 if tables else []) + delivery_events(dl, cal))
    if not events:
        return None
    score, bias, events = score_events(events)
    if score < UCFG["min_score"]:
        return None
    row = {"symbol": sym, "score": score, "bias": bias,
           "events": [{"when": when_text(e), "text": e["text"],
                       "side": e["side"]} for e in events[:5]]}
    if d is not None and len(d) >= 25:
        c = d["Close"].values.astype(float)
        last = float(hr["Close"].iloc[-1]) if hr is not None and len(hr) else float(c[-1])
        hi20 = float(d["High"].values[-21:-1].max())
        row["close"] = round(last, 2)
        W = UCFG["daily_window"]
        h_, l_ = d["High"].values.astype(float), d["Low"].values.astype(float)
        ref = float(c[-(W + 1)])
        pc = np.roll(c, 1)
        tr = np.maximum(h_ - l_, np.maximum(np.abs(h_ - pc), np.abs(l_ - pc)))
        atr = float(np.mean(tr[-(W + 14):-W]))
        move = last / ref - 1
        row["move_pct"] = round(move * 100, 1)
        row["moved"] = bool(abs(move) * ref >= UCFG["moved_atr"] * atr
                            or any(e["src"] in ("range", "gap") for e in events))
        row["vs_high"] = ("At a 20-day high" if last >= hi20
                          else f"{(1 - last / hi20) * 100:.1f}% below its 20-day high")
        D, Wk = E.tf_view(d, 3), E.tf_view(E.to_weekly(d), 3)
        row["states"] = {"W": Wk["state"], "D": D["state"]}
    return row


def trading_days(hourly, daily):
    days = set()
    for df in list(hourly.values())[:40]:
        days.update(df.index.normalize()[-40:])
    for df in list(daily.values())[:40]:
        days.update(df.index.normalize()[-6:])
    return sorted(days)[-6:]


def main_live():
    os.makedirs(DATA_DIR, exist_ok=True)
    now = dt.datetime.now(E.IST)
    notes = []
    fo_path = os.path.join(DATA_DIR, "fo_oi.csv.gz")
    if not os.path.exists(fo_path):
        raise RuntimeError("data/fo_oi.csv.gz not found. Run the 'Setups scan' "
                           "workflow once first; this scan reuses its F&O file.")
    fo = pd.read_csv(fo_path, parse_dates=["date"])
    symbols = sorted(fo.loc[fo["date"] == fo["date"].max(), "symbol"].unique())
    daily = E.fetch_bars(symbols, period="2y", interval="1d")
    hourly = E.fetch_bars(symbols, period="60d", interval="1h")
    if len(daily) < 50:
        raise RuntimeError(f"Daily prices for only {len(daily)} stocks; stopping.")
    if len(hourly) < 50:
        notes.append("Hourly prices unavailable: only daily events were scanned.")
    daily, hourly = E.completed(daily, hourly, now)
    cal = trading_days(hourly, daily)
    tables = oi_tables(fo)
    dl_by = {}
    dl_path = os.path.join(DATA_DIR, "equity_deliv.csv.gz")
    if os.path.exists(dl_path):
        dl = pd.read_csv(dl_path, parse_dates=["date"])
        dl = dl[dl["symbol"].isin(symbols) & (dl["series"] == "EQ")]
        dl_by = {s: g.sort_values("date") for s, g in dl.groupby("symbol")}
    else:
        notes.append("Delivery not checked: it uses the file the Equity scan saves.")
    setups = {}
    sp = os.path.join(DATA_DIR, "setups.json")
    if os.path.exists(sp):
        try:
            setups = {r["symbol"]: f"{r['setup']} · {r['stage']}"
                      for r in json.load(open(sp))["rows"]}
        except Exception:
            pass
    rows = []
    for s in symbols:
        try:
            r = analyse_unusual(s, daily.get(s), hourly.get(s), tables, dl_by.get(s), cal)
        except Exception as e:
            print(f"[warn] {s}: {e}")
            continue
        if r:
            if s in setups:
                r["setup"] = setups[s]
            rows.append(r)
    rows.sort(key=lambda r: (-r["score"], r["symbol"]))
    brk = {}
    if os.path.exists(sp):
        try:
            sj = json.load(open(sp))
            for r in sj.get("ranges", []):
                if r["stage"] in ("Ready", "Triggered"):
                    brk.setdefault(r["symbol"], []).append(f"{r['setup']} {r['stage']}")
        except Exception:
            pass
    squeeze = squeeze_watch(symbols, fo, hourly, cal, brk)
    try:
        log_squeeze(squeeze, fo["date"].max().strftime("%Y-%m-%d"))
    except Exception as e:
        print(f"[warn] squeeze log: {e}")
    res = {"generated_ist": now.strftime("%Y-%m-%d %H:%M"),
           "oi_asof": fo["date"].max().strftime("%Y-%m-%d"),
           "squeeze": squeeze,
           "stocks": len(symbols), "rows": rows, "notes": notes,
           "oi_days": UCFG["oi_build_days"], "oi_moves": oi_moves(symbols, tables, cal)}
    with open(os.path.join(DATA_DIR, "unusual.json"), "w") as f:
        json.dump(res, f, indent=1)
    print(f"{len(symbols)} stocks | {len(rows)} flagged | top: "
          + ", ".join(f"{r['symbol']} {r['score']}" for r in rows[:8]))


# ---------------------------------------------------------------- selftest
def _hourly(days, shape, base_px=500.0, seed=0):
    """Hourly bars with the usual intraday volume shape (busy open and close)."""
    rng = np.random.default_rng(seed)
    idx, vol = [], []
    for d_ in days:
        for k in range(7):
            idx.append(d_ + pd.Timedelta(hours=9, minutes=15) + pd.Timedelta(hours=k))
            vol.append(shape[k] * 1e5 * rng.uniform(0.9, 1.1))
    c = base_px * (1 + rng.normal(0, 0.0015, len(idx)).cumsum())
    return pd.DataFrame({"Open": c * 0.9995, "High": c * 1.002, "Low": c * 0.998,
                         "Close": c, "Volume": vol}, index=pd.DatetimeIndex(idx))


def _daily(days, seed=0):
    rng = np.random.default_rng(seed)
    c = 500 * (1 + rng.normal(0, 0.006, len(days)).cumsum())
    return pd.DataFrame({"Open": c * 0.999, "High": c * 1.008, "Low": c * 0.992,
                         "Close": c, "Volume": rng.uniform(0.9e6, 1.1e6, len(days))},
                        index=days)


def selftest():
    days = pd.bdate_range(end="2026-11-13", periods=300)
    cal = list(days[-6:])
    shape = [3.0, 1.2, 0.8, 0.7, 0.8, 1.3, 1.6]      # opening hour is 3-4x midday

    # 1) a perfectly ordinary stock must NOT be flagged, busy opening hour and all
    hr, d = _hourly(days[-40:], shape), _daily(days)
    assert hourly_events(hr, cal) == [] and daily_events(d, cal) == []
    assert analyse_unusual("QUIET", d, hr, None, None, cal) is None

    # 2) a midday hour at 5x its own usual IS flagged, as buying
    hr2 = hr.copy()
    k = len(hr2) - 4                                  # 12:15 bar of the last day
    hr2.iloc[k, hr2.columns.get_loc("Volume")] *= 5
    hr2.iloc[k, hr2.columns.get_loc("Open")] = hr2["Low"].iloc[k]
    hr2.iloc[k, hr2.columns.get_loc("Close")] = hr2["High"].iloc[k]
    ev = hourly_events(hr2, cal)
    assert len(ev) == 1 and ev[0]["age"] == 0 and ev[0]["side"] == 1, ev
    assert "peak" in ev[0]["text"] and "12:15" in ev[0]["text"], ev

    # 3) daily patterns: absorption yesterday, flush absorbed today
    d3 = d.copy()
    i = len(d3) - 2
    d3.iloc[i, d3.columns.get_loc("Volume")] *= 3
    mid = d3["Close"].iloc[i]
    d3.iloc[i, [d3.columns.get_loc(x) for x in ("High", "Low")]] = [mid * 1.001, mid * 0.999]
    j = len(d3) - 1
    px = d3["Close"].iloc[j - 1]
    d3.iloc[j, [d3.columns.get_loc(x) for x in ("Open", "High", "Low", "Close", "Volume")]] = \
        [px, px * 1.002, px * 0.96, px * 1.001, d3["Volume"].iloc[j] * 4]
    ev = {e["age"]: e for e in daily_events(d3, cal) if e["src"] == "volume_d"}
    assert "absorption" in ev[1]["text"] and ev[1]["side"] == 0, ev
    assert "flush absorbed" in ev[0]["text"] and ev[0]["side"] == 1, ev
    assert any(e["src"] == "range" for e in daily_events(d3, cal))

    # 4) OI: a market-wide drop (expiry) is ignored; one stock's own jump is caught
    rng = np.random.default_rng(1)
    rows = []
    for s in [f"S{n}" for n in range(30)] + ["JUMP"]:
        oi = 1e6 * np.cumprod(1 + rng.normal(0, 0.01, 40))
        oi[-2:] *= 0.80                               # every stock drops 20%
        if s == "JUMP":
            oi[-1] *= 1.15
        for dte, x in zip(days[-40:], oi):
            rows.append({"date": dte, "symbol": s, "fut_oi": x, "fut_close": 100.0})
    fo = pd.DataFrame(rows)
    fo.loc[(fo["symbol"] == "JUMP") & (fo["date"] == days[-1]), "fut_close"] = 102.0
    tables = oi_tables(fo)
    assert oi_events("S3", tables, cal) == []
    ev = oi_events("JUMP", tables, cal)
    assert len(ev) == 1 and "long build-up" in ev[0]["text"] and ev[0]["side"] == 1, ev

    # 4b) OI building over 7 sessions while price is flat: listed on its own
    rows = []
    for s in [f"S{n}" for n in range(30)] + ["QUIET"]:
        oi = 1e6 * np.cumprod(1 + rng.normal(0, 0.01, 40))
        if s == "QUIET":
            oi[-7:] *= np.linspace(1.04, 1.30, 7)       # +30% over the last 7 sessions
        for dte, x in zip(days[-40:], oi):
            rows.append({"date": dte, "symbol": s, "fut_oi": x, "fut_close": 100.0})
    tq = oi_tables(pd.DataFrame(rows))
    ev = oi_build_events("QUIET", tq, cal)
    assert len(ev) == 1 and "big positions building" in ev[0]["text"] and ev[0]["side"] == 0, ev
    assert "in 7 sessions" in ev[0]["text"], ev
    assert oi_build_events("S5", tq, cal) == []
    rows = []
    for s in [f"S{n}" for n in range(30)] + ["EXIT"]:
        oi = 1e6 * np.cumprod(1 + rng.normal(0, 0.01, 40))
        px_ = np.full(40, 100.0)
        if s == "EXIT":
            oi[-7:] *= np.linspace(0.96, 0.70, 7)       # -30% over 7 sessions
            px_[-7:] = np.linspace(101, 108, 7)          # while price rises
        for dte, x, pp in zip(days[-40:], oi, px_):
            rows.append({"date": dte, "symbol": s, "fut_oi": x, "fut_close": pp})
    te = oi_tables(pd.DataFrame(rows))
    ev = oi_build_events("EXIT", te, cal)
    assert len(ev) == 1 and "short covering" in ev[0]["text"] and ev[0]["side"] == 1, ev
    mv = oi_moves([f"S{n}" for n in range(30)] + ["EXIT"], te, cal)
    assert [m["symbol"] for m in mv] == ["EXIT"] and mv[0]["building"] is False, mv
    assert oi_moves(["QUIET", "S1"], tq, cal)[0]["building"] is True
    rq = analyse_unusual("QUIET", None, None, tq, None, cal)
    assert rq and rq["score"] >= 3 and rq["bias"] == "Mixed", rq

    # 4c) short squeeze watch
    rows = []
    for s in [f"S{n}" for n in range(20)] + ["TRAP", "COVER", "GONE"]:
        oi = 1e6 * np.cumprod(1 + rng.normal(0, 0.005, 40))
        px_ = 100 * np.cumprod(1 + rng.normal(0, 0.003, 40))
        if s in ("TRAP", "COVER", "GONE"):
            oi[25:] *= 1.20                              # day 25: OI +20% ...
            px_[25:] *= 0.95                             # ... while price falls 5%
            lift = {"TRAP": 1.10, "COVER": 1.10, "GONE": 1.30}[s]
            px_[30:] *= np.linspace(1, lift, 10)         # then price climbs back above
            if s == "COVER":
                oi[-2:] *= np.array([0.96, 0.92])        # shorts start covering
                px_[-2:] *= np.array([1.01, 1.02])
        for dte, x, pp in zip(days[-40:], oi, px_):
            rows.append({"date": dte, "symbol": s, "fut_oi": x, "fut_close": pp})
    sq = squeeze_watch([f"S{n}" for n in range(20)] + ["TRAP", "COVER", "GONE"],
                       pd.DataFrame(rows), setups={"TRAP": ["Daily Flag Ready"]})
    by_sq = {x["symbol"]: x for x in sq}
    assert set(by_sq) == {"TRAP", "COVER", "GONE"}, sq
    assert by_sq["TRAP"]["stage"] == "Shorts trapped" and by_sq["TRAP"]["kept"] >= 60, by_sq
    assert by_sq["COVER"]["stage"] == "Squeeze on", by_sq["COVER"]
    assert by_sq["GONE"]["stage"] == "Ran already" and sq[0]["symbol"] == "COVER", sq
    assert "Daily Flag Ready" in by_sq["TRAP"]["notes"]
    # shorts that left at an expiry (OI back to its old level) are not trapped any more
    rows2 = [r for r in rows if r["symbol"] != "TRAP"]
    for k_, (dte, r_) in enumerate(zip(days[-40:], [r for r in rows if r["symbol"] == "TRAP"])):
        x = r_["fut_oi"] * (0.80 if k_ == 28 else 1.0) * (1.25 if k_ > 28 else 1.0)
        rows2.append(dict(r_, fut_oi=x))
    sq2 = {x["symbol"]: x for x in squeeze_watch(["TRAP"], pd.DataFrame(rows2))}
    assert "TRAP" not in sq2, sq2

    # 5) delivery spike
    dl = pd.DataFrame({"date": days[-30:], "deliv_per": [40.0] * 29 + [65.0],
                       "close": [100.0] * 29 + [102.0]})
    ev = delivery_events(dl, cal)
    assert len(ev) == 1 and ev[0]["text"] == "Delivery 65% vs 40% usual" and ev[0]["side"] == 1

    # 6) scoring: older events count less, several kinds of evidence add up
    a = score_events([{"src": "volume_d", "age": 0, "strength": 2, "side": 1, "day": days[-1]}])
    b = score_events([{"src": "volume_d", "age": 2, "strength": 2, "side": 1, "day": days[-3]}])
    assert a[0] == 2.0 and b[0] == 1.0 and a[1] == "Buying"
    r = analyse_unusual("JUMP", d3, hr2, tables, dl, cal)
    print(json.dumps(r, indent=1))
    assert r["score"] > a[0] and r["bias"] == "Buying" and r["events"][0]["when"] == "Fri 13"
    assert set(r["states"]) == {"W", "D"} and "20-day high" in r["vs_high"]
    assert r["moved"] is True

    # 7) quiet case: absorption today plus an OI build-up, price barely moved
    dq = d.copy()
    i = len(dq) - 1
    dq.iloc[i, dq.columns.get_loc("Volume")] *= 3
    mid = dq["Close"].iloc[i - 1]
    dq.iloc[i, [dq.columns.get_loc(x) for x in ("Open", "High", "Low", "Close")]] = \
        [mid, mid * 1.001, mid * 0.999, mid * 1.0005]
    q = analyse_unusual("JUMP", dq, None, tables, None, cal)
    print(json.dumps(q, indent=1))
    assert q is not None and q["moved"] is False and abs(q["move_pct"]) < 2, q
    assert any("absorption" in e["text"] for e in q["events"])

    # 8) a single ordinary 2x volume day is no longer enough to be listed
    d1 = d.copy()
    d1.iloc[-1, d1.columns.get_loc("Volume")] *= 2.2
    assert analyse_unusual("ONE", d1, None, None, None, cal) is None
    print("\nSELFTEST PASSED")
    return r


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    selftest() if ap.parse_args().selftest else main_live()
