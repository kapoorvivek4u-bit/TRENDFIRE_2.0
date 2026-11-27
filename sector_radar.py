"""Sector radar: which sectors look set to do better (or worse) than Nifty over the next 1-5 days.

Runs in the evening after the sector and option-chain scans (radar.yml). It reads files the site already
makes and scores each sector 0-100 from six readings:

  rotation   30%  momentum on the rotation chart and its stage (Improving best, Lagging worst)
  writers    25%  option writers in the sector's stocks: bullish vs bearish chains, puts strong, calls covered
  trend      15%  share of stocks in Daily and Weekly impulse
  early      15%  share of stocks showing up early in the scans (Most scanned, "early" hits)
  oi         10%  5-day futures OI: long build-up vs short build-up
  breadth     5%  share of stocks up over 5 days

Then it sorts sectors into four groups (Gaining steam, Possible turnaround, Leading but tired, Avoid) with the
reasons in plain words. Every evening's call is logged to data/radar_log.csv and checked later against what each
sector actually did against Nifty 1, 3 and 5 trading days on (from the sector "day" move in sectors.json), so the
page can show its own hit rate. Sector and market level only: no stock picks.
"""
import csv, json, os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
LOG = os.path.join(D, "radar_log.csv")
IST = timezone(timedelta(hours=5, minutes=30))
W = {"rotation": .30, "writers": .25, "trend": .15, "early": .15, "oi": .10, "breadth": .05}


def load(name, default=None):
    try:
        with open(os.path.join(D, name)) as f:
            return json.load(f)
    except Exception:
        return default


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def score_sectors():
    site, secj, chains = load("site.json", {}), load("sectors.json", {}), load("chain_screen.json", {})
    sec_of = {r["s"]: r.get("sec") for r in site.get("mtf", [])}
    A = defaultdict(Counter)
    for r in site.get("mtf", []):
        g = A[r.get("sec")]
        g["n"] += 1
        g["dimp"] += r.get("D") == "Impulse"
        g["wimp"] += r.get("W") == "Impulse"
        oi, d5 = r.get("oi5") or 0, r.get("d5") or 0
        g["lbu"] += oi > 3 and d5 > 0
        g["sbu"] += oi > 3 and d5 < 0
        g["up5"] += d5 > 0
    for s, v in (site.get("hits") or {}).items():
        if v.get("e"):
            A[sec_of.get(s)]["early"] += 1
    for r in chains.get("rows", []):
        g, b, sig = A[sec_of.get(r["s"])], r.get("bias", ""), r.get("sig") or []
        g["cn"] += 1
        g["cbull"] += b in ("Bullish", "Mildly bullish")
        g["cbear"] += b in ("Bearish", "Mildly bearish")
        g["cover"] += "cover" in sig
        g["pstrong"] += "pstrong" in sig
        g["cwrite"] += "cwrite" in sig

    rows_c = chains.get("rows", [])                           # market-wide writer mood, so each sector is judged against it
    mkt_bull = sum(r.get("bias", "") in ("Bullish", "Mildly bullish") for r in rows_c) / max(1, len(rows_c))
    mkt_bear = sum(r.get("bias", "") in ("Bearish", "Mildly bearish") for r in rows_c) / max(1, len(rows_c))
    out = []
    for x in secj.get("sectors", []):
        name, g = x["name"], A.get(x["name"])
        if not g or not g["n"]:
            continue
        n, cn = g["n"], max(1, g["cn"])
        mom = (x.get("mom") or 0) * 100                       # % momentum on the rotation chart
        stage = x.get("stage", "")
        trail = x.get("trail") or []
        was = trail[-6]["stage"] if len(trail) >= 6 else (trail[0]["stage"] if trail else stage)
        stage_pts = {"Improving": .15, "Leading": .05, "Weakening": -.10, "Lagging": -.05}.get(stage, 0)
        parts = {
            "rotation": clamp(.5 + mom / 8 + stage_pts),
            "writers": clamp(.5 + ((g["cbull"] / cn - mkt_bull) - (g["cbear"] / cn - mkt_bear)) / 2 + (g["cover"] + g["pstrong"] * .5) / cn * .25),
            "trend": clamp((g["dimp"] / n) * .5 + (g["wimp"] / n) * .5),
            "early": clamp((g["early"] / n) / .6),
            "oi": clamp(.5 + (g["lbu"] - g["sbu"]) / n),
            "breadth": g["up5"] / n,
        }
        score = round(sum(parts[k] * W[k] for k in W) * 100)
        bull_w, bear_w = g["cbull"] / cn, g["cbear"] / cn
        wr_bull, wr_bear = bull_w >= max(.5, mkt_bull + .15), bear_w >= max(.6, mkt_bear + .15)   # clearly more than the market
        why = []
        if mom > 1:
            why.append(f"momentum rising on the rotation chart ({was} → {stage})" if was != stage else f"momentum rising ({stage})")
        elif mom < -1:
            why.append(f"momentum falling ({stage})")
        if g["early"] >= max(2, n * .3):
            why.append(f"{g['early']} of {n} stocks early in the scans")
        if wr_bull:
            why.append(f"option writers bullish in {g['cbull']} of {cn} (market {round(mkt_bull * 100)}%)")
        elif wr_bear:
            why.append(f"option writers bearish in {g['cbear']} of {cn} (market {round(mkt_bear * 100)}%)")
        if g["cover"] >= 2:
            why.append(f"call writers covering in {g['cover']}")
        if g["wimp"] / n >= .4:
            why.append(f"{round(g['wimp'] / n * 100)}% in Weekly impulse")
        if g["lbu"] >= 2 and g["lbu"] > g["sbu"]:
            why.append(f"long build-up in {g['lbu']}")
        if g["sbu"] >= 2 and g["sbu"] > g["lbu"]:
            why.append(f"short build-up in {g['sbu']}")

        if not why:
            why.append("weak on most readings" if score < 40 else "no strong signal either way")
        if stage in ("Improving", "Leading") and mom > 0 and score >= 55 and not wr_bear:
            group = "Gaining steam"
        elif stage in ("Lagging", "Weakening") and wr_bull:
            group = "Possible turnaround"
        elif stage in ("Leading", "Weakening") and (wr_bear or mom < 0):
            group = "Leading but tired"
        elif score < 40:
            group = "Avoid"
        else:
            group = "Neutral"
        out.append({"name": name, "score": score, "group": group, "stage": stage, "was": was, "mom": round(mom, 2),
                    "n": n, "why": why[:4], "parts": {k: round(v * 100) for k, v in parts.items()},
                    "writers": {"bull": g["cbull"], "bear": g["cbear"], "of": g["cn"]}, "early": g["early"]})
    out.sort(key=lambda r: -r["score"])
    score_sectors.market = {"writers_bull": round(mkt_bull * 100), "writers_bear": round(mkt_bear * 100)}
    for i, r in enumerate(out, 1):
        r["rank"] = i
    return out, secj, chains


def trail_moves(secj):
    """sector -> list of (date, relative day move vs Nifty in %), oldest first."""
    return {x["name"]: [(t["date"], t.get("day") or 0) for t in (x.get("trail") or [])] for x in secj.get("sectors", [])}


def update_log(today, rows, secj):
    old = []
    if os.path.exists(LOG):
        with open(LOG) as f:
            old = list(csv.DictReader(f))
    old = [r for r in old if r["date"] != today]
    for r in rows:
        old.append({"date": today, "sector": r["name"], "score": r["score"], "rank": r["rank"], "group": r["group"], "r1": "", "r3": "", "r5": ""})
    moves = trail_moves(secj)
    for r in old:                                            # fill in what happened next, once those days exist
        m = moves.get(r["sector"]) or []
        after = [d for dt, d in m if dt > r["date"]]
        for k, h in (("r1", 1), ("r3", 3), ("r5", 5)):
            if r[k] == "" and len(after) >= h:
                r[k] = round(sum(after[:h]), 2)
    with open(LOG, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "sector", "score", "rank", "group", "r1", "r3", "r5"])
        w.writeheader()
        w.writerows(old)
    return old


def track(log):
    """How the calls did: top 3 vs bottom 3 each day, and each group's hit rate (beat Nifty or not)."""
    by_day = defaultdict(list)
    for r in log:
        by_day[r["date"]].append(r)
    res = {}
    for k in ("r1", "r3", "r5"):
        wins = tot = 0
        for d, rs in by_day.items():
            done = [r for r in rs if r[k] != ""]
            if len(done) < 6:
                continue
            done.sort(key=lambda r: int(r["rank"]))
            top = sum(float(r[k]) for r in done[:3]) / 3
            bot = sum(float(r[k]) for r in done[-3:]) / 3
            tot += 1
            wins += top > bot
        res[f"top3_vs_bottom3_{k}"] = {"days": tot, "wins": wins, "rate": round(wins / tot * 100) if tot else None}
    groups = {}
    for gname, want in (("Gaining steam", 1), ("Possible turnaround", 1), ("Leading but tired", -1), ("Avoid", -1)):
        g = {}
        for k in ("r1", "r3", "r5"):
            done = [float(r[k]) for r in log if r["group"] == gname and r[k] != ""]
            hit = sum(1 for v in done if v * want > 0)
            g[k] = {"calls": len(done), "hits": hit, "rate": round(hit / len(done) * 100) if done else None}
        groups[gname] = g
    res["groups"] = groups
    res["since"] = min(by_day) if by_day else None
    return res


def main():
    now = datetime.now(IST)
    rows, secj, chains = score_sectors()
    if not rows:
        print("No sector data.")
        return
    today = (secj.get("asof") or now.strftime("%Y-%m-%d"))[:10]
    log = update_log(today, rows, secj)
    out = {"generated_ist": now.strftime("%Y-%m-%d %H:%M"), "asof": today, "chains_at": (chains.get("rows") or [{}])[0].get("at"),
           "weights": W, "market": getattr(score_sectors, "market", {}), "sectors": rows, "track": track(log)}
    with open(os.path.join(D, "radar.json"), "w") as f:
        json.dump(out, f, separators=(",", ":"))
    for r in rows:
        print(f"{r['rank']:2} {r['name']:24} {r['score']:3} {r['group']:20} {'; '.join(r['why'])}")
    print("track:", json.dumps(out["track"]["top3_vs_bottom3_r3"]))


if __name__ == "__main__":
    main()
