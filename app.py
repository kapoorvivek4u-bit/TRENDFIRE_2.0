"""TrendFire Setups - F&O and Equity views. Reads the scan results; does no scanning itself."""
import datetime as dt
import html
import json
import os

import urllib.error
import urllib.request

import streamlit as st

PAGES = {
    "F&O": {"file": os.path.join("data", "setups.json"), "workflow": "Setups scan",
            "setups": ["All", "Squeeze Break", "Swing Pullback", "Trend Pullback",
                       "Overspill"],
            "who": "F&O stocks"},
    "Equity": {"file": os.path.join("data", "equity.json"), "workflow": "Equity scan",
               "setups": ["All", "Squeeze Break", "Trend Pullback"],
               "who": "these midcaps and smallcaps"},
    "Unusual": {"F&O": {"file": os.path.join("data", "unusual.json"),
                        "workflow": "Unusual scan"},
                "Equity": {"file": os.path.join("data", "unusual_equity.json"),
                           "workflow": "Equity scan"}},
    "Breakout": {"file": os.path.join("data", "breakout.json"), "workflow": "Equity scan"},
    "Sectors": {"file": os.path.join("data", "sectors.json"), "workflow": "Sector scan"},
}
ALL_FILES = ([PAGES[k]["file"] for k in ("F&O", "Equity", "Breakout", "Sectors")]
             + [v["file"] for v in PAGES["Unusual"].values()])
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
OWNER = "Vivek Kapoor"          # name shown beside the Refresh button
INK, MUTED, RULE = "#1a1a18", "#5c5c57", "#b9b9b0"
GREEN, RED, BLUE = "#0f6b4f", "#b42318", "#1d5fa8"
TREND_COLOR = {"With trend": GREEN, "Early turn": BLUE, "Counter-trend": RED}
BIAS_COLOR = {"Buying": GREEN, "Selling": RED, "Mixed": MUTED}
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"

st.set_page_config(page_title="Setups", layout="centered")
st.markdown(f"""<style>
.block-container {{max-width: 480px; padding: 1.2rem 1rem 3rem;}}
#MainMenu, footer, header {{visibility: hidden;}}
.tf-grid {{display: grid; grid-template-columns: minmax(0, 1fr); column-gap: 36px;
  border-bottom: 1px solid {RULE};}}
@media (min-width: 900px) {{
  .block-container {{max-width: 1180px; padding: 1.6rem 2rem 3rem;}}
  .tf-grid {{grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));}}
  .tf-small {{font-size: 13px !important;}}
  .tf-set, .tf-ev {{font-size: 14px !important;}}
}}
.tf-row {{border-top: 1px solid {RULE}; padding: 11px 0; line-height: 1.45;}}
.tf-top {{display: flex; justify-content: space-between; align-items: center;}}
.tf-sym {{font-family: {MONO}; font-size: 17px; font-weight: 600; color: {INK} !important;}}
.tf-grade {{min-width: 28px; height: 28px; box-sizing: border-box; border-radius: 5px;
  font-weight: 700; font-size: 15px; display: flex; align-items: center;
  justify-content: center; padding: 0 6px;}}
.tf-set {{font-size: 13px; font-weight: 600; color: {INK};}}
.tf-small {{font-size: 12px; color: {MUTED};}}
.tf-lvl {{display: flex; justify-content: space-between; font-family: {MONO};
  font-size: 13px; font-weight: 500; color: {INK};}}
.tf-ev {{font-size: 13px; color: {INK}; padding-left: 10px; margin: 2px 0;
  border-left: 3px solid {RULE};}}
.tf-names a {{font-family: {MONO}; font-size: 13px; color: {INK} !important;}}
</style>""", unsafe_allow_html=True)


@st.cache_data(ttl=120)
def load(path, mtime):
    with open(path) as f:
        return json.load(f)


def chart_link(sym):
    tv = sym.replace("&", "_").replace("-", "_")
    return f"https://www.tradingview.com/chart/?symbol=NSE:{html.escape(tv)}"


def money(x):
    if x is None:
        return ""
    return f"{x:,.1f}" if x >= 1000 else f"{x:,.2f}"


def grade_badge(g):
    if g == "A":
        style = f"background:{GREEN};color:#fff"
    elif g == "Warning":
        style = f"background:{RED};color:#fff;font-size:12px"
    else:
        style = f"border:2px solid {INK};color:{INK}"
    return f'<span class="tf-grade" style="{style}">{html.escape(g)}</span>'


UNUSUAL = {}          # symbol -> its row on the Unusual page, for the market in view
SECTORS = {}          # symbol -> {"sector", "stage"} from the sector scan
STAGE_COLOR = {"Leading": GREEN, "Improving": BLUE, "Weakening": "#8a5300", "Lagging": RED}


def load_sector_tags():
    path = PAGES["Sectors"]["file"]
    if not os.path.exists(path):
        return {}
    try:
        return load(path, os.path.getmtime(path)).get("map", {})
    except Exception:
        return {}


def sector_html(sym):
    t = SECTORS.get(sym)
    if not t or not t.get("sector"):
        return ""
    stage = t.get("stage") or ""
    tail = (f' · <span style="color:{STAGE_COLOR.get(stage, MUTED)};font-weight:600">'
            f'{html.escape(stage)}</span>' if stage else "")
    return f'<div class="tf-small">Sector: {html.escape(t["sector"])}{tail}</div>'


def load_unusual_map(market):
    path = PAGES["Unusual"][market]["file"]
    if not os.path.exists(path):
        return {}
    try:
        return {r["symbol"]: r for r in load(path, os.path.getmtime(path))["rows"]}
    except Exception:
        return {}


def row_html(r):
    e = html.escape
    trend = r.get("trend") or "Monthly n/a"
    tcol = TREND_COLOR.get(trend, MUTED)
    states = " · ".join(f"{k} {e(v)}" for k, v in r["states"].items())
    detail = e(r.get("detail", ""))
    if r["grade"] == "Warning":
        why = r.get("warning_text") or "OI rose while price fell: short build-up"
        detail += f'<br><span style="color:{RED};font-weight:600">{e(why)}</span>'
    if r.get("trigger") is None:
        left = "No trigger yet"
    elif r["stage"] == "Triggered":
        left = f"Triggered above {money(r['trigger'])}"
    else:
        left = f"Above {money(r['trigger'])}"
    if r.get("also"):
        detail += (f'<br><span style="color:{GREEN};font-weight:600">Also grade A: '
                   f'{e("; ".join(r["also"]))}</span>')
    u = UNUSUAL.get(r["symbol"])
    if u:
        ev = u["events"][0]
        detail += (f'<br><span style="color:{INK}">Unusual ({u["score"]:.1f}, '
                   f'{e(u["bias"].lower())}): {e(ev["when"])} · {e(ev["text"])}</span>')
    return (
        '<div class="tf-row"><div class="tf-top">'
        f'<a class="tf-sym" href="{chart_link(r["symbol"])}" target="_blank">'
        f'{e(r["symbol"])}</a>{grade_badge(r["grade"])}</div>'
        f'<div class="tf-set">{e(r["setup"])} · '
        f'<span style="color:{tcol}">{e(trend)}</span></div>'
        f'<div class="tf-small">{states}</div>{sector_html(r["symbol"])}'
        f'<div class="tf-small">{detail}</div>'
        f'<div class="tf-lvl"><span>{left}</span>'
        + (f'<span style="color:{RED}">Stop {money(r["stop"])}</span>'
           if r.get("stop") is not None else
           f'<span style="color:{MUTED}">Stop set on breakout</span>')
        + '</div></div>')


def show_rows(rows):
    st.markdown('<div class="tf-grid">' + "".join(row_html(r) for r in rows) + '</div>',
                unsafe_allow_html=True)


def names_html(names):
    links = ", ".join(f'<a href="{chart_link(n)}" target="_blank">{html.escape(n)}</a>'
                      for n in names)
    return f'<div class="tf-names">{links}</div>'


def unusual_row(r):
    e = html.escape
    bcol = BIAS_COLOR.get(r["bias"], MUTED)
    side_col = {1: GREEN, -1: RED, 0: RULE}
    events = "".join(
        f'<div class="tf-ev" style="border-left-color:{side_col.get(ev["side"], RULE)}">'
        f'<span style="color:{MUTED}">{e(ev["when"])}</span> · {e(ev["text"])}</div>'
        for ev in r["events"])
    ctx = []
    if r.get("states"):
        ctx.append(" · ".join(f"{k} {e(v)}" for k, v in r["states"].items()))
    if r.get("move_pct") is not None:
        ctx.append(f"Price {r['move_pct']:+.1f}% over the last 3 sessions"
                   + ("" if r.get("moved") else " (quiet)"))
    if r.get("vs_high"):
        ctx.append(e(r["vs_high"]))
    if r.get("setup"):
        ctx.append("On Setups: " + e(r["setup"]))
    t = SECTORS.get(r["symbol"])
    if t and t.get("sector"):
        ctx.append(f"Sector: {e(t['sector'])}"
                   + (f" · <span style=\"color:{STAGE_COLOR.get(t['stage'], MUTED)};"
                      f"font-weight:600\">{e(t['stage'])}</span>" if t.get("stage") else ""))
    return (
        '<div class="tf-row"><div class="tf-top">'
        f'<a class="tf-sym" href="{chart_link(r["symbol"])}" target="_blank">'
        f'{e(r["symbol"])}</a><span class="tf-grade" style="border:2px solid {INK};'
        f'color:{INK};font-family:{MONO}">{r["score"]:.1f}</span></div>'
        f'<div class="tf-set"><span style="color:{bcol}">{e(r["bias"])}</span>'
        + (f' · {money(r["close"])}' if r.get("close") else "") + '</div>'
        f'{events}<div class="tf-small" style="margin-top:4px">{"<br>".join(ctx)}</div></div>')


def render_unusual(cfg):
    market = st.radio("Stocks", list(cfg), horizontal=True, label_visibility="collapsed",
                      key="unusual_market", format_func=lambda x: f"{x} stocks")
    P, equity = cfg[market], market == "Equity"
    path = P["file"]
    if not os.path.exists(path):
        st.title("Unusual activity")
        st.info(f"No scan results yet. Run the '{P['workflow']}' workflow once from the "
                "GitHub Actions tab.")
        return
    res = load(path, os.path.getmtime(path))
    gen = dt.datetime.strptime(res["generated_ist"], "%Y-%m-%d %H:%M")
    age_days = (dt.datetime.now(IST).replace(tzinfo=None) - gen).days
    st.markdown(
        f'<div style="display:flex;justify-content:space-between;align-items:baseline;'
        f'border-bottom:2px solid {INK};padding-bottom:10px">'
        f'<span style="font-family:Georgia,serif;font-size:26px;font-weight:700;color:{INK}">'
        f'Unusual activity</span><span class="tf-small">Scanned {gen.strftime("%a %d %b")} · '
        f'{gen.strftime("%H:%M")}</span></div>', unsafe_allow_html=True)
    if age_days >= 3:
        st.warning(f"These results are {age_days} days old. Check the Actions tab on "
                   "GitHub for a failed run.")
    lean = st.radio("Lean", ["All", "Quiet", "Buying", "Selling", "Mixed"], horizontal=True,
                    label_visibility="collapsed", key="unusual_lean")
    quiet = [r for r in res["rows"] if r.get("moved") is False]
    rows = (res["rows"] if lean == "All" else quiet if lean == "Quiet"
            else [r for r in res["rows"] if r["bias"] == lean])
    total = len(res["rows"])
    headline = ("Nothing unusual right now." if total == 0 else
                f"{total} of {res['stocks']} stocks show something unusual.")
    st.markdown(
        f'<div style="font-family:Georgia,serif;font-size:22px;font-weight:600;'
        f'color:{INK};margin:6px 0 6px">{headline}</div>'
        f'<div style="font-size:14px;color:{INK};line-height:1.5">'
        + ("Covers the last three sessions, on Daily bars only."
           if equity else "Covers the last few hours and the last two to three sessions.")
        + f' Strongest first. {len(quiet)} of them have not made a big price move yet: '
        'tap Quiet to see those.</div>', unsafe_allow_html=True)
    with st.expander("What the words mean"):
        st.markdown("""
**What counts as unusual.** Every check compares the stock with its own recent history.
- **Day's volume 3.1x usual**: the day's volume against the 20 days before.
- **Hours of unusual volume**: each hour against the same clock hour on earlier days, so the busy opening hour is not counted as a spike.
- **Absorption**: very high volume with a small candle. Someone is taking the other side without moving price.
- **After a dry-up**: volume was unusually quiet for five days, then jumped.
- **Flush absorbed**: a sharp fall on very high volume that was bought back by the close.
- **Range 2.4x normal**: the day's range against its usual range.
- **Gap, held**: an opening gap as large as a normal day's range that was not filled.
- **OI in a day**: a one-day change in futures open interest that is large for this stock, after removing the market-wide change.
- **Delivery vs usual**: delivery share well above the stock's normal.

**The number on the right** is the score. Different kinds of evidence add up. The latest session counts fully, the one before 0.7, and the one before that 0.5. A stock needs 3 or more to be listed.

**Quiet**: unusual volume, OI, or delivery while price has NOT yet made a big move (a small change over the last 3 sessions, with no wide-range day or gap). These are the early ones. Stocks outside Quiet have already moved.

**Buying, Selling, Mixed**: which way the events lean. A green bar beside an event leans to buying, red to selling, grey is neutral.

**M, W and D** are Monthly, Weekly and Daily momentum: Bullish, Topping, Bearish, or Bottoming.

The Equity view uses Daily bars only, so it has no hourly or OI events.

This page says something is happening. It does not say the move will continue. Check the chart.
""")
    if rows:
        st.markdown('<div class="tf-grid">' + "".join(unusual_row(r) for r in rows[:15])
                    + '</div>', unsafe_allow_html=True)
        if len(rows) > 15:
            with st.expander(f"{len(rows) - 15} more, weaker"):
                st.markdown('<div class="tf-grid">' + "".join(unusual_row(r) for r in rows[15:])
                            + '</div>', unsafe_allow_html=True)
    elif total:
        st.caption("None in this view.")
    for note in res.get("notes", []):
        st.caption(f"Note: {note}")
    if equity:
        st.caption(
            f"{res['stocks']} midcaps and smallcaps outside F&O scanned, as of "
            f"{res['oi_asof']}. Daily volume, range, gap and delivery only: no hourly "
            "data and no OI. Updated once a day after the close. Thresholds are starting "
            "values, not tested ones. Watchlist only, not investment advice.")
    else:
        st.caption(
            f"{res['stocks']} F&O stocks scanned. OI as of the F&O file for "
            f"{res['oi_asof']}. Refreshes about once an hour in market hours. Thresholds "
            "are starting values, not tested ones. Watchlist only, not investment advice.")


def render_breakout(P):
    path = P["file"]
    if not os.path.exists(path):
        st.title("Breakout")
        st.info(f"No scan results yet. Run the '{P['workflow']}' workflow once from the "
                "GitHub Actions tab.")
        return
    res = load(path, os.path.getmtime(path))
    UNUSUAL.update(load_unusual_map("Equity"))
    gen = dt.datetime.strptime(res["generated_ist"], "%Y-%m-%d %H:%M")
    age_days = (dt.datetime.now(IST).replace(tzinfo=None) - gen).days
    st.markdown(
        f'<div style="display:flex;justify-content:space-between;align-items:baseline;'
        f'border-bottom:2px solid {INK};padding-bottom:10px">'
        f'<span style="font-family:Georgia,serif;font-size:26px;font-weight:700;color:{INK}">'
        f'Breakout</span><span class="tf-small">Scanned {gen.strftime("%a %d %b")} · '
        f'{gen.strftime("%H:%M")}</span></div>', unsafe_allow_html=True)
    if age_days >= 3:
        st.warning(f"These results are {age_days} days old. Check the Actions tab on "
                   "GitHub for a failed run.")
    broke, setting = res["broke"], res["setting"]
    n = len(broke)
    headline = ("No stock broke out of a base in the last 3 sessions." if n == 0 else
                "1 stock broke out of a base in the last 3 sessions." if n == 1 else
                f"{n} stocks broke out of a base in the last 3 sessions.")
    sub = (f"{len(setting)} more {'is' if len(setting) == 1 else 'are'} within 3% of "
           "the breakout level.")
    m = res.get("market") or {}
    if m.get("regime") == "Weak":
        sub += (f" Weak market: {m['weekly_bull_pct']}% of these stocks are bullish on "
                "Weekly, and breakouts fail more often in a market like this.")
    st.markdown(
        f'<div style="font-family:Georgia,serif;font-size:22px;font-weight:600;'
        f'color:{INK};margin:6px 0 6px">{headline}</div>'
        f'<div style="font-size:14px;color:{INK};line-height:1.5">{html.escape(sub)}</div>',
        unsafe_allow_html=True)
    with st.expander("What the words mean"):
        st.markdown("""
**Base breakout**: a strong stock rests in a narrow range (the base), then closes above the top of it on heavy volume. You are buying strength, not a dip.

**To be listed, a stock must be**
- above its rising 50-day average, which is above the 200-day, and within 20% of its 52-week high;
- in the top 30% of this group on 3 and 6 month returns (**RS 70 or more**);
- in a base no deeper than 20% over the last 15 days.

**Broke out**: closed above the base on at least 1.5x normal volume, in the upper half of the day's range, within the last 3 sessions, and has stayed above the level since. Dropped if it is more than 6% above the level or the stop is more than 8% away.

**Setting up**: has not broken out, but is within 3% of the level. "Above 1,234" is the level to watch.

**Stop**: just below the low of the breakout day. For Setting up there is no stop until it breaks out.

**Grade**: A, B or C from relative strength, how tight the base is, volume drying up in the base, breakout volume, and delivery.

**M, W, D** and the trend tag mean the same as on the Equity page.

The main risk is a failed breakout: price falls back into the base. That happens more often in weak markets.
""")
    if broke:
        show_rows(broke)
    st.markdown(f'<div class="tf-small" style="letter-spacing:.08em;text-transform:uppercase;'
                f'font-weight:600;margin-top:18px">Not broken out yet</div>',
                unsafe_allow_html=True)
    with st.expander(f"Setting up · {len(setting)}"):
        if setting:
            show_rows(setting)
        else:
            st.caption("None.")
    for note in res.get("notes", []):
        st.caption(f"Note: {note}")
    st.caption(
        f"{res['stocks']} midcaps and smallcaps outside F&O scanned, as of {res['oi_asof']}. "
        "Updated once a day after the close. Thresholds are starting values, not tested "
        "ones. Smallcaps can hit circuits and gap, so a stop may not be honoured. "
        "Watchlist only, not investment advice.")


def render_alist():
    """Every grade A row on the site, in one place."""
    sources = (("F&O", PAGES["F&O"]["file"], ("rows", "overspill")),
               ("Equity", PAGES["Equity"]["file"], ("rows",)),
               ("Equity", PAGES["Breakout"]["file"], ("broke", "setting")))
    found, stamps, markets = [], {}, {}
    for market, path, keys in sources:
        if not os.path.exists(path):
            continue
        res = load(path, os.path.getmtime(path))
        stamps.setdefault(market, res["generated_ist"])
        if res.get("market"):
            markets.setdefault(market, res["market"])
        for k in keys:
            found += [dict(r, market=market) for r in res.get(k, []) if r.get("grade") == "A"]
    for mk in ("F&O", "Equity"):
        UNUSUAL.update(load_unusual_map(mk))
    st.markdown(
        f'<div style="display:flex;justify-content:space-between;align-items:baseline;'
        f'border-bottom:2px solid {INK};padding-bottom:10px">'
        f'<span style="font-family:Georgia,serif;font-size:26px;font-weight:700;color:{INK}">'
        f'A list</span><span class="tf-small">'
        + html.escape("Scanned: " + " · ".join(
            f"{k} " + dt.datetime.strptime(v, "%Y-%m-%d %H:%M").strftime("%a %d %b %H:%M")
            for k, v in stamps.items()) if stamps else "no scans yet")
        + '</span></div>', unsafe_allow_html=True)
    if not stamps:
        st.info("No scan results yet. Run the scans once from the GitHub Actions tab.")
        return
    stage_rank = {"Ready": 0, "Triggered": 1, "Watch": 2}
    t_rank = {"With trend": 0, "Early turn": 1, "": 2, "Counter-trend": 3}
    merged = {}
    for r in sorted(found, key=lambda r: stage_rank.get(r["stage"], 9)):
        key = (r["market"], r["symbol"])
        if key in merged:
            merged[key]["also"].append(f'{r["setup"]} · {r["stage"]}')
        else:
            merged[key] = dict(r, setup=f'{r["market"]} · {r["setup"]}', also=[])
    rows = sorted(merged.values(),
                  key=lambda r: (-len(r["also"]), t_rank.get(r.get("trend") or "", 2), r["symbol"]))
    by = {k: [r for r in rows if r["stage"] == k] for k in stage_rank}
    n = len(by["Ready"])
    headline = ("No grade A stock is Ready right now." if n == 0 else
                "1 grade A stock is Ready." if n == 1 else f"{n} grade A stocks are Ready.")
    line = " ".join(f"{k}: {m['regime'].lower()} market, {m['weekly_bull_pct']}% bullish on Weekly."
                    for k, m in markets.items() if m.get("weekly_bull_pct") is not None)
    st.markdown(
        f'<div style="font-family:Georgia,serif;font-size:22px;font-weight:600;'
        f'color:{INK};margin:6px 0 6px">{headline}</div>'
        f'<div style="font-size:14px;color:{INK};line-height:1.5">{html.escape(line)}</div>',
        unsafe_allow_html=True)
    with st.expander("What this page is"):
        st.markdown("""
Every stock graded **A** anywhere on the site, gathered here: the F&O setups (Squeeze, Swing, Trend), Overspill, the Equity setups, and Breakout.

- **A** means the price pattern qualified and both supporting checks passed: volume and OI for F&O, volume and delivery for Equity. On Breakout it is the top score band.
- Each row starts with where it comes from, such as "F&O · Hourly Overspill".
- A stock that is grade A on two setups is shown once, with an "Also grade A" line, and is listed first.
- **Ready** is the entry stage. **Triggered** has already turned. **Watch** has not turned yet.
- For Breakout, Ready means close to its breakout level, and Triggered means it has broken out.
- The Unusual page is not part of this list, because it uses scores, not grades. Its line still appears on a row when the stock shows unusual activity.

Grade A is not a guarantee. The grade is as of the last close, and every threshold is a starting value.
""")
    if by["Ready"]:
        show_rows(by["Ready"])
    st.markdown(f'<div class="tf-small" style="letter-spacing:.08em;text-transform:uppercase;'
                f'font-weight:600;margin-top:18px">Other stages</div>', unsafe_allow_html=True)
    for stage in ("Triggered", "Watch"):
        with st.expander(f"{stage} · {len(by[stage])}"):
            if by[stage]:
                show_rows(by[stage])
            else:
                st.caption("None.")
    st.caption("Grade A rows from F&O Setups, Overspill, Equity Setups and Breakout. "
               "Watchlist only, not investment advice.")


def render_sectors(P):
    path = P["file"]
    if not os.path.exists(path):
        st.title("Sectors")
        st.info(f"No scan results yet. Run the '{P['workflow']}' workflow once from the "
                "GitHub Actions tab.")
        return
    res = load(path, os.path.getmtime(path))
    e = html.escape
    gen = dt.datetime.strptime(res["generated_ist"], "%Y-%m-%d %H:%M")
    age_days = (dt.datetime.now(IST).replace(tzinfo=None) - gen).days
    st.markdown(
        f'<div style="display:flex;justify-content:space-between;align-items:baseline;'
        f'border-bottom:2px solid {INK};padding-bottom:10px">'
        f'<span style="font-family:Georgia,serif;font-size:26px;font-weight:700;color:{INK}">'
        f'Sectors</span><span class="tf-small">Scanned {gen.strftime("%a %d %b")} · '
        f'{gen.strftime("%H:%M")}</span></div>', unsafe_allow_html=True)
    if age_days >= 3:
        st.warning(f"These results are {age_days} days old. Check the Actions tab on "
                   "GitHub for a failed run.")
    secs = res["sectors"]
    lead = [x["name"] for x in secs if x["stage"] == "Leading"]
    headline = ("No sector is Leading right now." if not lead else
                f"1 sector is Leading: {lead[0]}." if len(lead) == 1 else
                f"{len(lead)} sectors are Leading: {', '.join(lead)}.")
    nf = res["nifty"]
    st.markdown(
        f'<div style="font-family:Georgia,serif;font-size:22px;font-weight:600;'
        f'color:{INK};margin:6px 0 6px">{e(headline)}</div>'
        f'<div style="font-size:14px;color:{INK};line-height:1.5">Nifty is '
        f'{nf["r1"]:+.1f}% over 1 month and {nf["r3"]:+.1f}% over 3 months. Each sector '
        'below is measured against it, using that sector\'s F&amp;O stocks.</div>',
        unsafe_allow_html=True)
    with st.expander("What the words mean"):
        st.markdown("""
**The four stages** compare a sector with Nifty.
- **Leading**: beating Nifty, and the lead is growing.
- **Weakening**: still beating Nifty, but the lead is shrinking.
- **Lagging**: trailing Nifty, and falling further behind.
- **Improving**: trailing Nifty, but catching up.

Sectors usually move round in that order: Improving, Leading, Weakening, Lagging. Improving is often the early sign of the next leader.

**How it is measured.** Each sector's F&O stocks are averaged with equal weight. That line is divided by Nifty. Above its own 50-day average means the sector is beating Nifty. The change in that over the last 10 days says whether the lead is growing or shrinking.

**1M and 3M** are the sector's return over 1 and 3 months, minus Nifty's.

**Wk bull** is the share of the sector's F&O stocks with Weekly momentum above zero.

**Top stocks** in each sector are its strongest by return against Nifty over 1 and 3 months, blended. A green line shows when a stock is also on another page today.

**Sector names** come from NSE's Nifty 500 list. Banks and PSU Banks are split out of Finance. Sectors with fewer than 3 F&O stocks are listed at the bottom, not ranked.

The sector line on every stock row elsewhere on the site comes from this page.
""")
    def pct(x):
        return "n/a" if x is None else f"{x:+.1f}%"
    head = ("<tr><th style='text-align:left'>Sector</th><th style='text-align:left'>Stage</th>"
            "<th>1M</th><th>3M</th><th>Wk bull</th></tr>")
    body = "".join(
        f"<tr><td>{e(x['name'])}</td>"
        f"<td style='color:{STAGE_COLOR[x['stage']]};font-weight:600'>{x['stage']}</td>"
        f"<td class='n'>{pct(x['rs1'])}</td><td class='n'>{pct(x['rs3'])}</td>"
        f"<td class='n'>{x['wk_bull']}%</td></tr>" for x in secs)
    st.markdown(
        f"<style>.tf-sec{{width:100%;border-collapse:collapse;font-size:13.5px;color:{INK}}}"
        f".tf-sec th{{font-size:11px;letter-spacing:.06em;text-transform:uppercase;"
        f"color:{MUTED};font-weight:600;text-align:right;padding:6px 4px;"
        f"border-bottom:2px solid {INK}}}"
        f".tf-sec td{{padding:7px 4px;border-bottom:1px solid {RULE}}}"
        f".tf-sec td.n{{text-align:right;font-family:{MONO}}}</style>"
        f"<div style='overflow-x:auto;margin:6px 0 4px'><table class='tf-sec'>{head}{body}"
        "</table></div>", unsafe_allow_html=True)

    on_site = {}
    for key in ("F&O", "Equity"):
        p_ = PAGES[key]["file"]
        if os.path.exists(p_):
            r_ = load(p_, os.path.getmtime(p_))
            for row in r_.get("rows", []) + r_.get("overspill", []):
                if row.get("stage") in ("Ready", "Triggered", "Watch"):
                    on_site.setdefault(row["symbol"], []).append(
                        f'{row["setup"]} · {row["stage"]} ({row["grade"]})')
    st.markdown(f'<div class="tf-small" style="letter-spacing:.08em;text-transform:uppercase;'
                f'font-weight:600;margin-top:18px">Top stocks by sector</div>',
                unsafe_allow_html=True)
    for x in secs:
        label = (f"{x['name']} · {x['stage']} · 1M {pct(x['rs1'])} vs Nifty · "
                 f"{x['n']} stocks")
        with st.expander(label, expanded=x["stage"] == "Leading"):
            rows_html = []
            for t in x["top"]:
                tags = on_site.get(t["symbol"])
                rows_html.append(
                    '<div class="tf-row"><div class="tf-top">'
                    f'<a class="tf-sym" href="{chart_link(t["symbol"])}" target="_blank">'
                    f'{e(t["symbol"])}</a><span class="tf-lvl">{money(t["close"])}</span></div>'
                    f'<div class="tf-small">W {e(t["states"]["W"])} · D {e(t["states"]["D"])}'
                    f' · 1M {pct(t["rs1"])} · 3M {pct(t["rs3"])} vs Nifty</div>'
                    + (f'<div class="tf-small" style="color:{GREEN};font-weight:600">'
                       f'On the site: {e("; ".join(tags))}</div>' if tags else "")
                    + '</div>')
            st.markdown(f'<div class="tf-small">{x["wk_bull"]}% of its stocks bullish on '
                        f'Weekly, {x["d_bull"]}% on Daily.</div>'
                        '<div class="tf-grid">' + "".join(rows_html) + '</div>',
                        unsafe_allow_html=True)
    if res.get("unranked"):
        k = len(res["unranked"])
        with st.expander(f"Not ranked · {k} small group{'' if k == 1 else 's'}"):
            st.markdown("".join(
                f'<div class="tf-small" style="margin:4px 0"><b>{e(u["name"])}</b> '
                f'({u["n"]}): {names_html(u["members"])}</div>' for u in res["unranked"]),
                unsafe_allow_html=True)
    for note in res.get("notes", []):
        st.caption(f"Note: {note}")
    st.caption(f"F&O stocks, Daily bars as of {res['asof']}. Updated once a day after the "
               "close. Stages are relative to Nifty, not a forecast. Watchlist only, not "
               "investment advice.")


SCANS = {                      # what the button can start -> workflow file in .github/workflows
    "F&O setups": "scan.yml",
    "Equity, Breakout, Equity unusual": "equity.yml",
    "Unusual (F&O)": "unusual.yml",
    "Sectors": "sector.yml",
}


def secret(name, default=None):
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default


def github(method, path, body=None):
    """Call the GitHub API with the token kept in the app's secrets. (status, json)"""
    api = secret("GH_API", "https://api.github.com")
    req = urllib.request.Request(
        f"{api}/repos/{secret('GH_REPO', 'kapoorvivek4u-bit/TRENDFIRE_2.0')}{path}",
        method=method, data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {secret('GH_TOKEN')}",
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "tradeready"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        return e.code, {}
    except Exception:
        return 0, {}


@st.cache_data(ttl=20)
def last_run(workflow):
    code, data = github("GET", f"/actions/workflows/{workflow}/runs?per_page=1")
    runs = data.get("workflow_runs") or []
    if code != 200 or not runs:
        return None
    r = runs[0]
    when = dt.datetime.strptime(r["created_at"], "%Y-%m-%dT%H:%M:%SZ") \
        .replace(tzinfo=dt.timezone.utc).astimezone(IST)
    state = {"success": "finished", "failure": "failed", "cancelled": "cancelled"}.get(
        r.get("conclusion"), "running" if r.get("status") != "completed" else r.get("conclusion"))
    return f"{state}, started {when.strftime('%a %d %b %H:%M')}"


def scan_panel():
    if not secret("GH_TOKEN"):
        st.caption("To start scans from here, add GH_TOKEN, GH_REPO and SCAN_PIN to the "
                   "app's secrets on Streamlit. Until then, use the Actions tab on GitHub.")
        return
    which = st.selectbox("Scan", list(SCANS), key="scan_pick")
    pin = st.text_input("PIN", type="password", key="scan_pin")
    if st.button("Start scan", key="scan_go", type="primary"):
        if str(pin) != str(secret("SCAN_PIN", "")) or not secret("SCAN_PIN"):
            st.error("Wrong PIN.")
        else:
            code, _ = github("POST", f"/actions/workflows/{SCANS[which]}/dispatches",
                             {"ref": "main"})
            if code == 204:
                last_run.clear()
                st.success(f"{which} scan started. It takes about 2 to 10 minutes. The "
                           "page reloads by itself when the new results arrive.")
            else:
                st.error({401: "GitHub refused the token. It may have expired: make a new "
                               "one and update GH_TOKEN.",
                          403: "The token is not allowed to start workflows. Give it "
                               "'Actions: Read and write' on this repository.",
                          404: f"GitHub could not find .github/workflows/{SCANS[which]} in "
                               "the repo, or the token cannot see the repo.",
                          422: "That workflow cannot be started by hand."}.get(
                    code, f"Could not reach GitHub (code {code}). Try again shortly."))
    for name, wf in SCANS.items():
        info = last_run(wf)
        st.caption(f"{name}: {info}" if info else f"{name}: no run found")


def file_stamp():
    return tuple(os.path.getmtime(p) if os.path.exists(p) else 0 for p in ALL_FILES)


@st.fragment(run_every="120s")
def watch_for_new_scans():
    """Every two minutes, reload the page if any scan has saved new results."""
    now, seen = file_stamp(), st.session_state.get("file_stamp")
    st.session_state["file_stamp"] = now
    if seen is not None and seen != now:
        st.rerun()


page = st.radio("Market", ["A list"] + list(PAGES), horizontal=True,
                label_visibility="collapsed")
with st.container(horizontal=True, horizontal_alignment="distribute",
                  vertical_alignment="center"):
    if st.button("Refresh"):
        st.cache_data.clear()
    with st.popover("Run scan"):
        scan_panel()
    st.markdown(f'<div style="font-family:Georgia,serif;font-size:18px;font-weight:600;'
                f'color:{INK};text-align:right">{html.escape(OWNER)}</div>',
                unsafe_allow_html=True)
watch_for_new_scans()
SECTORS.update(load_sector_tags())
if page == "Sectors":
    render_sectors(PAGES[page])
    st.stop()
if page == "A list":
    render_alist()
    st.stop()
if page == "Unusual":
    render_unusual(PAGES[page])
    st.stop()
if page == "Breakout":
    render_breakout(PAGES[page])
    st.stop()
P = PAGES[page]
DATA, EQUITY = P["file"], page == "Equity"
if not os.path.exists(DATA):
    st.title(f"{page} Setups")
    st.info(f"No scan results yet. Run the '{P['workflow']}' workflow once from the "
            "GitHub Actions tab.")
    st.stop()

res = load(DATA, os.path.getmtime(DATA))
UNUSUAL.update(load_unusual_map(page))
m = res["market"]
gen = dt.datetime.strptime(res["generated_ist"], "%Y-%m-%d %H:%M")
age_days = (dt.datetime.now(IST).replace(tzinfo=None) - gen).days

st.markdown(
    f'<div style="display:flex;justify-content:space-between;align-items:baseline;'
    f'border-bottom:2px solid {INK};padding-bottom:10px">'
    f'<span style="font-family:Georgia,serif;font-size:26px;font-weight:700;color:{INK}">'
    f'{html.escape(page)} Setups</span><span class="tf-small">Scanned {gen.strftime("%a %d %b")} · '
    f'{gen.strftime("%H:%M")}</span></div>', unsafe_allow_html=True)
if age_days >= 3:
    st.warning(f"These results are {age_days} days old. Check the Actions tab on "
               "GitHub for a failed run.")

choice = st.radio("Setup", P["setups"], horizontal=True, label_visibility="collapsed",
                  format_func=lambda x: x.split()[0], key=f"setup_{page}")
SPILL = choice == "Overspill"
if SPILL:
    rows = res.get("overspill", [])
    if "overspill" not in res:
        st.caption("Overspill needs the updated F&O scan file. It will appear after the "
                   "next scan with that file.")
else:
    rows = [r for r in res["rows"] if choice == "All" or r["setup"] == choice]
ready = [r for r in rows if r["stage"] == "Ready"]
held = []
if m["regime"] == "Weak":
    held = [r for r in ready if r["grade"] != "A"]
    ready = [r for r in ready if r["grade"] == "A"]

n = len(ready)
headline = ("No stocks are Ready right now." if n == 0 else
            "1 stock is Ready." if n == 1 else f"{n} stocks are Ready.")
if n == 0 and held:
    headline = (f"No grade A stock is Ready. {len(held)} lower-grade "
                f"{'one is' if len(held) == 1 else 'ones are'} listed below.")
market = "Market data unavailable."
if m["weekly_bull_pct"] is not None:
    market = (f"{m['regime']} market: {m['weekly_bull_pct']}% of {P['who']} are "
              f"bullish on Weekly")
    if m["monthly_bull_pct"] is not None:
        market += f" and {m['monthly_bull_pct']}% on Monthly"
    market += "." + (f" VIX is {m['vix']}." if m.get("vix") else "")
bar = m["weekly_bull_pct"] or 0
st.markdown(
    f'<div style="font-family:Georgia,serif;font-size:22px;font-weight:600;'
    f'color:{INK};margin:6px 0 6px">{headline}</div>'
    f'<div style="font-size:14px;color:{INK};line-height:1.5">{html.escape(market)}</div>'
    f'<div style="height:6px;border-radius:3px;background:#dcdcd4;margin:8px 0 4px">'
    f'<div style="height:6px;border-radius:3px;width:{bar}%;background:{GREEN}"></div></div>',
    unsafe_allow_html=True)

SPILL_DEF = "" if EQUITY else (
    "- **Overspill** (its own list): in an uptrend, with the 20 EMA over the 50 EMA over "
    "the 200 SMA, price comes back into the 20-50 EMA zone, sometimes spilling under the "
    "50 EMA, then reclaims the 20 EMA. **Daily Overspill** needs Weekly bullish. "
    "**Hourly Overspill** needs Weekly and Daily bullish. Watch means price is in the "
    "zone, Ready means it has started to turn inside the zone, and Triggered means it "
    "closed back above the 20 EMA. 'Above' is the 20 EMA level, and the stop is under "
    "the low of the spill. A fall of more than 3 normal ranges under the 50 EMA, or "
    "to the 200 SMA, is treated as trend damage and dropped.\n")
SWING_DEF = ("- **Swing** (Swing Pullback): Daily is in an impulse and Hourly has dipped. "
             "The aim is to join a moving stock on a short dip. Suits 3 to 5 day holds.\n")
TREND_DEF = (
    "- **Trend** (Trend Pullback): Weekly is in an impulse and Daily has dipped. Monthly "
    "does not decide whether a stock is listed here. It only sets the tag, so check the "
    "tag. Suits holds of one to three weeks.")
if EQUITY:
    GRADE_DEF = """**Grade (volume and delivery, as of the last close)**
- **A**: volume and delivery both support the setup.
- **B**: one of the two supports it.
- **C**: neither does.
- **Warning**: volume in the dip is heavier than in the up-move. That points to selling pressure."""
    THIRD_DEF = ("- **Delivery 55% vs 40% before**: delivery share in the last 10 days "
                 "against the 30 days before. Higher is better.")
    TF_DEF = "**M, W, D (Monthly, Weekly, Daily momentum)**"
else:
    GRADE_DEF = """**Grade (volume and OI, as of the last close)**
- **A**: volume and OI both support the setup.
- **B**: one of the two supports it.
- **C**: neither does.
- **Warning**: OI rose while price fell in the dip. That means fresh shorts."""
    THIRD_DEF = ("- **OI +4% in up-leg, +1% in pullback**: change in futures open "
                 "interest in each part.")
    TF_DEF = "**M, W, D, H (Monthly, Weekly, Daily, Hourly momentum)**"

with st.expander("What the words mean"):
    st.markdown(f"""
**Setups (the buttons at the top)**
- **Squeeze** (Squeeze Break): Weekly is bullish. Daily is coiled in a squeeze with momentum below zero. The aim is to catch it before it breaks out.
{"" if EQUITY else SWING_DEF}{TREND_DEF}
{SPILL_DEF}

**Stages (where the stock is)**
- **Watch**: the dip or coil is still in progress. Momentum is still falling. No trigger yet.
- **Ready**: momentum has just turned up. This is the entry stage.
- **Triggered**: price has already closed above the trigger level. The turn is confirmed, so you are a little late.
- **Running**: in an impulse with no dip to buy.
- **Stretched**: far above its 20-day average. Chasing here is risky.
- **Fading**: was bullish, and Daily momentum has fallen for several days.

**Trend tag (from Monthly)**
- **With trend**: Monthly momentum is above zero.
- **Early turn**: Monthly is below zero but rising.
- **Counter-trend**: Monthly is below zero and falling. Smaller size or skip.
- **Monthly n/a**: a recent listing without enough history for a Monthly reading.

{GRADE_DEF}

{TF_DEF}
- **Bullish**: above zero and rising.
- **Topping**: above zero and falling.
- **Bearish**: below zero and falling.
- **Bottoming**: below zero and rising.
- **Impulse**: Bullish, with price above a rising 20 EMA and the 20 EMA above the 50.

**The numbers on each row**
- **Pullback 41%**: the dip has given back 41% of the last up-move. The page accepts 20% to 75%.
- **Volume 0.6x in pullback**: volume in the dip is 0.6 times the volume in the up-move. Lower is better.
{THIRD_DEF}
- **Above 1,904**: the trigger. A close above this level confirms the turn.
- **Stop**: just below the low of the dip.
""")

if ready:
    show_rows(ready)
if held:
    with st.expander(f"{len(held)} more Ready, hidden in a weak market (not grade A)"):
        show_rows(held)

st.markdown(f'<div class="tf-small" style="letter-spacing:.08em;text-transform:uppercase;'
            f'font-weight:600;margin-top:18px">Other stages</div>', unsafe_allow_html=True)
for stage in ("Triggered", "Watch"):
    group = [r for r in rows if r["stage"] == stage]
    with st.expander(f"{stage} · {len(group)}"):
        if group:
            show_rows(group)
        else:
            st.caption("None.")
if choice == "All":
    for stage in ("Running", "Stretched", "Fading"):
        names = res["others"].get(stage, [])
        with st.expander(f"{stage} · {len(names)}"):
            if names:
                st.markdown(names_html(names), unsafe_allow_html=True)
            else:
                st.caption("None.")

for note in res.get("notes", []):
    st.caption(f"Note: {note}")
if EQUITY:
    st.caption(
        f"{res['stocks']} stocks scanned: midcaps and smallcaps outside F&O with average "
        f"turnover above Rs 10 crore. Grade uses volume and delivery as of {res['oi_asof']}. "
        "Updated once a day after the close. Smallcaps can hit circuits and gap, so a "
        "stop may not be honoured. Watchlist only, not investment advice.")
else:
    st.caption(
        f"{res['stocks']} F&O stocks scanned. Grade uses volume and OI as of the F&O file "
        f"for {res['oi_asof']}. Daily, Weekly and Monthly use completed days; Hourly uses "
        "completed bars and refreshes about once an hour. Ready sightings are saved for "
        "the scorecard. Watchlist only, not investment advice.")
