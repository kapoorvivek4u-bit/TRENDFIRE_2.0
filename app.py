"""TrendFire Setups - F&O and Equity views. Reads the scan results; does no scanning itself."""
import datetime as dt
import html
import json
import os

import urllib.error
import urllib.request

import re

import pandas as pd
import streamlit as st

PAGES = {
    "F&O": {"file": os.path.join("data", "setups.json"), "workflow": "Setups scan",
            "setups": ["All", "Squeeze Break", "Swing Pullback", "Trend Pullback",
                       "Overspill", "Range", "Old labels", "VK"],
            "who": "F&O stocks"},
    "Equity": {"file": os.path.join("data", "equity.json"), "workflow": "Equity scan",
               "setups": ["All", "Squeeze Break", "Trend Pullback", "Overspill", "Range", "Old labels"],
               "who": "these midcaps and smallcaps"},
    "Unusual": {"F&O": {"file": os.path.join("data", "unusual.json"),
                        "workflow": "Unusual scan"},
                "Equity": {"file": os.path.join("data", "unusual_equity.json"),
                           "workflow": "Equity scan"}},
    "Breakout": {"file": os.path.join("data", "breakout.json"), "workflow": "Equity scan"},
    "Sectors": {"file": os.path.join("data", "sectors.json"), "workflow": "Sector scan"},
}
DEALS_FILE = os.path.join("data", "deals.json")
STARS_FILE = os.path.join("data", "stars.json")
ALL_FILES = ([DEALS_FILE, STARS_FILE] + [PAGES[k]["file"] for k in ("F&O", "Equity", "Breakout", "Sectors")]
             + [v["file"] for v in PAGES["Unusual"].values()])
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
OWNER = "Vivek Kapoor"          # name shown as the faded watermark
INK, MUTED, RULE = "#1a1a18", "#5c5c57", "#b9b9b0"
GREEN, RED, BLUE = "#0f6b4f", "#b42318", "#1d5fa8"
AMBER_WL = "#b9a77a"          # watchlist star (empty)
TREND_COLOR = {"With trend": GREEN, "Early turn": BLUE, "Counter-trend": RED}
AMBER = "#a15c00"
VK_ORDER = ["BUY NOW", "ALMOST READY (Bullish)", "COMING CLOSE TO READY (Bullish)",
            "SELL NOW", "ALMOST READY (Bearish)", "COMING CLOSE TO READY (Bearish)", "NO SIGNAL"]
VK_COLOR = {"BUY NOW": GREEN, "ALMOST READY (Bullish)": GREEN,
            "COMING CLOSE TO READY (Bullish)": AMBER, "SELL NOW": RED,
            "ALMOST READY (Bearish)": RED, "COMING CLOSE TO READY (Bearish)": AMBER}
VK_DEF = """Your VK System (GHOST OF YOTEI) dashboard, worked out for every F&O stock with the same rules as the Pine Script. The buttons are the final trade status (the bottom-right cell of the top table).

**MA trend** per timeframe: Bullish = 20 EMA > 50 EMA > 200 SMA; Bearish = the reverse; otherwise Messy.
**HTF bullish** = Weekly and Daily MA trend Bullish and Daily TTM above zero (bearish is the mirror).

- **BUY NOW**: HTF bullish and Hourly TTM above zero.
- **ALMOST READY (Bullish)**: HTF bullish, Hourly TTM below zero (dip on Hourly, buy on turn).
- **COMING CLOSE TO READY (Bullish)**: M, W, D MA trends all Bullish, Weekly TTM above zero, Daily TTM still below zero.
- **SELL NOW / ALMOST READY (Bearish) / COMING CLOSE TO READY (Bearish)**: the same, bearish.
- **NO SIGNAL**: none of these.

Each card also shows the VK SIGNAL (ALL 3 ALIGNED, W+D, CASCADE FORMING), the MA signal (ONLY BUY, Wait...), KYA KARNA HAI, and the Daily, Hourly and Confirmed remarks. Confirmed = the last 2 completed hourly bars both above zero.

Small differences from TradingView are possible: the site uses completed bars only (TradingView also reads the bar still forming), there is no 15-minute row, and Monthly moving averages use about 5 years of history."""
EXT_MAX = 4.0     # daily ranges above the 50 EMA that count as Extended (same as the scan)


def is_extended(r):
    return r.get("ext") is not None and r["ext"] >= EXT_MAX


def cautious(m):
    """Weak breadth or a falling market: hide extended Ready stocks."""
    return m.get("regime") == "Weak" or m.get("turn") == "Falling"


def turn_html(m, label=""):
    t, h, n = m.get("turn"), m.get("daily_bull_hist"), m.get("nifty")
    if not t or t == "Unknown" or not h:
        return ""
    col, head, tail = {
        "Turning up": (GREEN, "Market turning up.", "Setups are favoured."),
        "Falling": (RED, "Market still falling.", "Stay light. Extended stocks are hidden."),
        "No clear turn": (AMBER, "No clear turn yet.", "Trade small and only near value."),
    }[t]
    nifty = f" Nifty Daily is {n['state']}." if n else ""
    return (f'<div style="font-size:14px;line-height:1.5;margin-top:4px">'
            f'<span style="color:{col};font-weight:700">{html.escape(label)}{head}</span> '
            f'Stocks with Daily momentum above zero, last 6 sessions: '
            f'<b>{" → ".join(str(x) for x in h)}%</b>.'
            f'{html.escape(nifty)} {tail}</div>')
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
a.tf-wl {{text-decoration: none !important; color: {AMBER_WL} !important; font-size: 17px;
  margin-left: 6px; line-height: 1;}}
a.tf-wl.on {{color: #c98a00 !important;}}
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


LIVE_STAGES = ("Ready", "Triggered")      # coloured badge only when the stock is tradeable


def quality_word(setup):
    """What the grade measures for this kind of setup."""
    s = str(setup or "")
    return ("Coil quality" if s.startswith("Squeeze") else
            "Base quality" if "Breakout" in s or "Range" in s else "Dip quality")


def grade_badge(g, stage=None):
    live = stage is None or stage in LIVE_STAGES
    if not live:                                       # Watch: not tradeable yet, so grey
        style = f"border:2px solid {RULE};color:{MUTED}"
    elif g == "A":
        style = f"background:{GREEN};color:#fff"
    elif g == "Warning":
        style = f"background:{RED};color:#fff;font-size:12px"
    else:
        style = f"border:2px solid {INK};color:{INK}"
    tip = "Not tradeable yet: the grade turns colour at Ready" if not live else ""
    return (f'<span class="tf-grade" title="{tip}" style="{style}">{html.escape(g)}</span>')


def quality_html(r):
    g = r.get("grade")
    if not g:
        return ""
    stage = r.get("stage")
    col = (MUTED if stage not in LIVE_STAGES else
           GREEN if g == "A" else RED if g == "Warning" else INK)
    tail = "" if stage in LIVE_STAGES else " · not tradeable until Ready"
    return (f' · <span style="color:{col};font-weight:600">{quality_word(r.get("setup"))} '
            f'{html.escape(g)}</span><span style="color:{MUTED};font-weight:400">{tail}</span>')


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


DEALS = {}            # symbol -> 30-day fund/star deal picture from the deals scan


def load_deals():
    if not os.path.exists(DEALS_FILE):
        return {}
    try:
        return load(DEALS_FILE, os.path.getmtime(DEALS_FILE)).get("stocks", {})
    except Exception:
        return {}


def deals_html(sym):
    d = DEALS.get(sym)
    if not d or not d.get("text"):
        return ""
    buy = ((d.get("mf_net_cr") or 0) + (d.get("ins_net_cr") or 0)
           + sum(x["value_cr"] * (1 if x["side"] == "BUY" else -1) for x in d.get("stars", [])))
    col = GREEN if buy >= 0 else RED
    return (f'<div class="tf-small" style="color:{col};font-weight:600">Deals (30d): '
            f'{html.escape(d["text"])}</div>')


STAR_MAP = {}         # symbol -> "Kacholia 3.84% (+2.10 pp) · ..." from the star scan


def load_star_map():
    if not os.path.exists(STARS_FILE):
        return {}
    try:
        return load(STARS_FILE, os.path.getmtime(STARS_FILE)).get("map", {})
    except Exception:
        return {}


def stars_html(sym):
    t = STAR_MAP.get(sym)
    if not t:
        return ""
    return (f'<div class="tf-small" style="color:{BLUE};font-weight:600">Stars: '
            f'{html.escape(t)}</div>')


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
    if r.get("ext") is not None:
        x = r["ext"]
        where = f"{abs(x):.1f} ranges {'above' if x >= 0 else 'below'} the 50 EMA"
        detail += (f'<br><span style="color:{RED};font-weight:600">Extended: {where}</span>'
                   if is_extended(r) else f'<br><span style="color:{MUTED}">{where}</span>')
    if r.get("target"):
        detail += (f'<br><span style="color:{GREEN}">Target {money(r["target"])} '
                   f'(range depth added to the breakout level)</span>')
    if r.get("old"):
        detail += f'<br><span style="color:{MUTED}">Old: {e(r["old"])}</span>'
    if r.get("vk"):
        v = r["vk"]
        detail += (f'<br><span style="color:{VK_COLOR.get(v["final"], MUTED)};font-weight:600">'
                   f'VK: {e(v["final"])}</span><span style="color:{MUTED}"> · {e(v["ma"])}</span>')
    u = UNUSUAL.get(r["symbol"])
    if u:
        ev = u["events"][0]
        detail += (f'<br><span style="color:{INK}">Unusual ({u["score"]:.1f}, '
                   f'{e(u["bias"].lower())}): {e(ev["when"])} · {e(ev["text"])}</span>')
    return (
        '<div class="tf-row"><div class="tf-top">'
        f'<span><a class="tf-sym" href="{chart_link(r["symbol"])}" target="_blank">'
        f'{e(r["symbol"])}</a>{wl_link(r["symbol"])}</span>{grade_badge(r["grade"], r.get("stage"))}</div>'
        f'<div class="tf-set">{e(r["setup"])} · '
        f'<span style="color:{tcol}">{e(trend)}</span>{quality_html(r)}</div>'
        f'<div class="tf-small">{states}</div>{sector_html(r["symbol"])}'
        f'{deals_html(r["symbol"])}{stars_html(r["symbol"])}'
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
        f'<span><a class="tf-sym" href="{chart_link(r["symbol"])}" target="_blank">'
        f'{e(r["symbol"])}</a>{wl_link(r["symbol"])}</span><span class="tf-grade" style="border:2px solid {INK};'
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
    sources = (("F&O", PAGES["F&O"]["file"], ("rows", "overspill", "ranges")),
               ("Equity", PAGES["Equity"]["file"], ("rows", "overspill", "ranges")),
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
    ext_held = [r for r in by["Ready"]
                if is_extended(r) and cautious(markets.get(r["market"], {}))]
    by["Ready"] = [r for r in by["Ready"] if r not in ext_held]
    n = len(by["Ready"])
    headline = ("No grade A stock is Ready right now." if n == 0 else
                "1 grade A stock is Ready." if n == 1 else f"{n} grade A stocks are Ready.")
    line = " ".join(f"{k}: {m['regime'].lower()} market, {m['weekly_bull_pct']}% bullish on Weekly."
                    for k, m in markets.items() if m.get("weekly_bull_pct") is not None)
    st.markdown(
        f'<div style="font-family:Georgia,serif;font-size:22px;font-weight:600;'
        f'color:{INK};margin:6px 0 6px">{headline}</div>'
        f'<div style="font-size:14px;color:{INK};line-height:1.5">{html.escape(line)}</div>'
        + turn_html(markets.get("F&O", {}), "F&O: ") + turn_html(markets.get("Equity", {}), "Equity: "),
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
    if ext_held:
        with st.expander(f"{len(ext_held)} more Ready, hidden (extended) while the market is weak"):
            show_rows(ext_held)
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


def sec_id(name):
    return "sec-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


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
    new = "rw" in nf
    if not new:
        st.caption("The faster sector view needs the updated sector scan file. It will "
                   "appear after the next Sector scan.")
    nifty_txt = (f'Nifty is {nf["rw"]:+.1f}% over 1 week, {nf["r2w"]:+.1f}% over 2 weeks and '
                 f'{nf["r1"]:+.1f}% over 1 month.' if new else
                 f'Nifty is {nf["r1"]:+.1f}% over 1 month and {nf["r3"]:+.1f}% over 3 months.')
    st.markdown(
        f'<div style="font-family:Georgia,serif;font-size:22px;font-weight:600;'
        f'color:{INK};margin:6px 0 6px">{e(headline)}</div>'
        f'<div style="font-size:14px;color:{INK};line-height:1.5">{nifty_txt} Each sector '
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

**How it is measured.** Each sector's F&O stocks are averaged with equal weight. That line is divided by Nifty. Above its own 20-day average means the sector is beating Nifty. The change in that over the last 5 days says whether the lead is growing or shrinking. Built for 3 to 10 day trades, so a turn shows within a few days.

**1W, 2W, 1M** are the sector's return over 1 week, 2 weeks and 1 month, minus Nifty's. +1.5% means it beat Nifty by 1.5%.

**Direction** is how the sector's strength against Nifty changed over the last 5 sessions: **▲ Rising** (gaining on Nifty by more than 0.75%), **▼ Falling**, or **▶ Sideways**. The **10-day trend** line is that strength, day by day (up = beating Nifty). **Stage, last 10 days** shows one dot per session, oldest on the left, coloured by that day's stage (green Leading, blue Improving, brown Weakening, red Lagging); each sector's section below has the same days as a table.

**Index D** is the Daily TTM state of the sector's own line (the equal-weight index above): Bullish, Topping, Bearish or Bottoming, the same words as on a chart.

**D bull** is the share of the sector's F&O stocks with Daily TTM above zero. It moves first.

**Wk bull** is the same on Weekly TTM. It moves slowly and shows the bigger trend. D bull rising while Wk bull is low is an early turn.

**Top stocks** in each sector are its strongest against Nifty over 1 week, 2 weeks and 1 month, blended (the latest week counts most). A green line shows when a stock is also on another page today.

**Sector names** come from NSE's Nifty 500 list. Banks and PSU Banks are split out of Finance, and Pharma (NSE's Nifty Pharma list) out of Healthcare, so Healthcare means hospitals, diagnostics and the like. Sectors with fewer than 3 F&O stocks are listed at the bottom, not ranked.

The sector line on every stock row elsewhere on the site comes from this page.
""")
    def pct(x):
        return "n/a" if x is None else f"{x:+.1f}%"
    STATE_COL = {"Bullish": GREEN, "Topping": AMBER, "Bearish": RED, "Bottoming": BLUE}
    def dots(x):
        tr = x.get("trail") or []
        return "".join(
            f"<span title='{e(t['date'])}: {e(t['stage'] or 'n/a')}' style='display:inline-block;"
            f"width:9px;height:9px;border-radius:50%;margin-right:2px;background:"
            f"{STAGE_COLOR.get(t['stage'], RULE)}'></span>" for t in tr)

    def direction(x):
        d = x.get("direction")
        if not d:
            return "<td></td>"
        sym_, col = {"Rising": ("▲", GREEN), "Falling": ("▼", RED)}.get(d, ("▶", MUTED))
        chg = x.get("chg5")
        return (f"<td style='color:{col};font-weight:600;white-space:nowrap'>{sym_} {d}"
                + (f"<div class='tf-small' style='font-weight:400'>{chg:+.1f}% in 5d</div>"
                   if chg is not None else "") + "</td>")
    has_trail = any(x.get("trail") for x in secs)
    if new:
        head = ("<tr><th style='text-align:left'>Sector</th><th style='text-align:left'>Stage</th>"
                + ("<th style='text-align:left'>Direction</th><th style='text-align:left'>"
                   "10-day trend</th><th style='text-align:left'>Stage, last 10 days</th>"
                   if has_trail else "")
                + "<th>1W</th><th>2W</th><th>1M</th><th style='text-align:left;padding-left:16px'>Index D</th>"
                "<th>D bull</th><th>Wk bull</th></tr>")
        body = "".join(
            f"<tr><td><a class='tf-jump' href='#{sec_id(x['name'])}'>{e(x['name'])}</a></td>"
            f"<td style='color:{STAGE_COLOR[x['stage']]};font-weight:600'>{x['stage']}</td>"
            + ((direction(x) + f"<td>{spark([t.get('rs') for t in x.get('trail', [])], 80, 20)}</td>"
                f"<td style='white-space:nowrap'>{dots(x)}</td>") if has_trail else "")
            + f"<td class='n'>{pct(x['rw'])}</td><td class='n'>{pct(x['r2w'])}</td>"
            f"<td class='n'>{pct(x['r1'])}</td>"
            f"<td style='padding-left:16px;color:{STATE_COL.get(x.get('idx_d'), MUTED)};font-weight:600'>"
            f"{e(x.get('idx_d') or 'n/a')}</td>"
            f"<td class='n'>{x['d_bull']}%</td><td class='n'>{x['wk_bull']}%</td></tr>"
            for x in secs)
    else:
        head = ("<tr><th style='text-align:left'>Sector</th><th style='text-align:left'>Stage</th>"
                "<th>1M</th><th>3M</th><th>Wk bull</th></tr>")
        body = "".join(
            f"<tr><td><a class='tf-jump' href='#{sec_id(x['name'])}'>{e(x['name'])}</a></td>"
            f"<td style='color:{STAGE_COLOR[x['stage']]};font-weight:600'>{x['stage']}</td>"
            f"<td class='n'>{pct(x['rs1'])}</td><td class='n'>{pct(x['rs3'])}</td>"
            f"<td class='n'>{x['wk_bull']}%</td></tr>" for x in secs)
    st.markdown(
        f"<style>.tf-sec{{width:100%;border-collapse:collapse;font-size:13.5px;color:{INK}}}"
        f".tf-sec th{{font-size:11px;letter-spacing:.06em;text-transform:uppercase;"
        f"color:{MUTED};font-weight:600;text-align:right;padding:6px 4px;"
        f"border-bottom:2px solid {INK}}}"
        f".tf-sec td{{padding:7px 4px;border-bottom:1px solid {RULE}}}"
        f".tf-sec td.n{{text-align:right;font-family:{MONO}}}"
        f".tf-jump{{color:{INK};font-weight:600;text-decoration:underline;"
        f"text-decoration-color:{RULE};text-underline-offset:3px}}"
        f".tf-secblock{{scroll-margin-top:70px}}</style>"
        f"<div class='tf-small' style='margin-top:6px'>Tap a sector name to jump to its "
        f"top stocks.</div>"
        f"<div id='sec-table' class='tf-secblock' style='overflow-x:auto;margin:6px 0 4px'>"
        f"<table class='tf-sec'>{head}{body}"
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
        label = (f"{x['name']} · {x['stage']} · "
                 + (f"1W {pct(x['rw'])}" if new else f"1M {pct(x['rs1'])}")
                 + f" vs Nifty · {x['n']} stocks")
        rows_html = []
        for t in x["top"]:
            tags = on_site.get(t["symbol"])
            rows_html.append(
                '<div class="tf-row"><div class="tf-top">'
                f'<span><a class="tf-sym" href="{chart_link(t["symbol"])}" target="_blank">'
                f'{e(t["symbol"])}</a>{wl_link(t["symbol"])}</span><span class="tf-lvl">{money(t["close"])}</span></div>'
                f'<div class="tf-small">W {e(t["states"]["W"])} · D {e(t["states"]["D"])}'
                + (f' · 1W {pct(t["rw"])} · 2W {pct(t["r2w"])} · 1M {pct(t["r1"])}'
                   if new else f' · 1M {pct(t["rs1"])} · 3M {pct(t["rs3"])}')
                + ' vs Nifty</div>'
                + (f'<div class="tf-small" style="color:{GREEN};font-weight:600">'
                   f'On the site: {e("; ".join(tags))}</div>' if tags else "")
                + '</div>')
        st.markdown(
            f'<div id="{sec_id(x["name"])}" class="tf-secblock" style="margin-top:22px;'
            f'border-top:2px solid {INK};padding-top:8px;display:flex;'
            f'justify-content:space-between;align-items:baseline">'
            f'<span style="font-family:Georgia,serif;font-size:19px;font-weight:700">'
            f'{e(x["name"])} <span style="font-size:14px;color:{STAGE_COLOR[x["stage"]]}">'
            f'{x["stage"]}</span></span>'
            f'<a class="tf-small" href="#sec-table">↑ Back to table</a></div>'
            f'<div class="tf-small">{e(label.split(" · ", 2)[2])} · {x["wk_bull"]}% of its '
            f'stocks bullish on Weekly, {x["d_bull"]}% on Daily.</div>'
            '<div class="tf-grid">' + "".join(rows_html[:5]) + '</div>',
            unsafe_allow_html=True)
        if len(rows_html) > 5:
            with st.expander(f"Show all {len(rows_html)} stocks in {x['name']}"):
                st.markdown('<div class="tf-grid">' + "".join(rows_html[5:]) + '</div>',
                            unsafe_allow_html=True)
        if x.get("trail"):
            with st.expander(f"{x['name']}: day by day, last {len(x['trail'])} sessions"):
                trs = "".join(
                    f"<tr><td>{e(dt.date.fromisoformat(t['date']).strftime('%a %d %b'))}</td>"
                    f"<td style='color:{STAGE_COLOR.get(t['stage'], MUTED)};font-weight:600'>"
                    f"{e(t['stage'] or 'n/a')}</td>"
                    f"<td class='n' style='color:{GREEN if (t['day'] or 0) > 0 else RED if (t['day'] or 0) < 0 else MUTED}'>"
                    f"{'n/a' if t['day'] is None else format(t['day'], '+.2f') + '%'}</td>"
                    f"<td class='n'>{'n/a' if t['d_bull'] is None else str(t['d_bull']) + '%'}</td></tr>"
                    for t in reversed(x["trail"]))
                st.markdown(
                    "<div style='overflow-x:auto'><table class='tf-sec'><tr>"
                    "<th style='text-align:left'>Day</th><th style='text-align:left'>Stage</th>"
                    "<th>Day vs Nifty</th><th>D bull</th></tr>"
                    f"{trs}</table></div>", unsafe_allow_html=True)
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


# ---------------------------------------------------------------- strategies
STRAT_RULES = {
    1: ("Sector Leader Swing", "F&O · 3-5 days",
        "Buy a short hourly dip in a strong stock inside a strong sector.",
        ["Market: Turning up with Daily breadth under 85% (full size), or No clear turn (half size)",
         "Swing Pullback, stage Ready, grade A, With trend",
         "Not Extended (under 4 ranges above the 50 EMA)",
         "Sector Leading or Improving, its index Daily Bullish, Wk bull 50% or more"],
        "Hourly close above the entry level. Book half at the 2R target, trail the rest under "
        "hourly swing lows, exit by day 5 if it has not worked."),
    2: ("Cooldown Buy", "F&O and Equity · 5-10 days",
        "After an overheated rally, buy the leaders on their first daily pullback.",
        ["Market: Daily breadth was 85% or more in the last 6 sessions and has cooled to 60-78%",
         "Nifty Daily not Bearish",
         "Trend Pullback or Daily Overspill, stage Ready, grade A",
         "Under 2 ranges above the 50 EMA (near value)",
         "Sector Leading or Weakening (was strong before the dip)"],
        "Daily close above the entry level. Half at 2R, trail the rest under the 20 EMA."),
    3: ("Squeeze Coil", "F&O and Equity · 3-10 days",
        "Catch a coiled stock as momentum turns up, before the breakout.",
        ["Market: No clear turn, or Turning up with Daily breadth under 70%",
         "Squeeze Break, stage Ready, grade A or B",
         "Sector Leading or Improving",
         "Bonus: old label Prime Setup; Unusual page shows Buying"],
        "Daily close above the 5-day high, ideally on above-normal volume. Half at 2R; exit "
        "when the Daily turns Topping."),
    4: ("Weekly Overspill Positional", "F&O and Equity · 2-4 weeks",
        "Buy a long uptrend when the weekly chart dips into its 20-50 EMA zone and the Daily "
        "turns up.",
        ["Market: anything except Still falling",
         "Weekly Overspill, stage Ready, grade A, With trend",
         "Sector not Lagging",
         "Bonus (Equity): delivery higher than before"],
        "Accordion: one third now, one third on a daily close above the entry level, the last "
        "third when Weekly momentum turns up. The stop is wide: size the first lot so it costs "
        "your normal risk. Trail under the weekly 20 EMA."),
    5: ("Unusual Activity Confluence", "F&O and Equity · 3-5 days",
        "Big money showing up in a stock that is already on a setup.",
        ["Market: anything except Still falling",
         "Unusual page: bias Buying, score 4 or more",
         "Also on a setup page, stage Watch or Ready, grade not Warning",
         "Weekly Bullish or Impulse; sector Leading or Improving"],
        "Enter when the setup is Ready, on its entry level. Watch-stage names are listed so "
        "you can set alerts."),
    6: ("Equity Base Breakout", "Equity · 8-10 days",
        "Buy a midcap or smallcap breaking out of a tight base on volume.",
        ["Market (Equity): Turning up with Daily breadth under 85%",
         "Breakout page: Ready (close to the level), grade A",
         "RS 80 or more, base 20% deep or less, volume in the base below usual",
         "Sector Leading or Improving, or not classified"],
        "Daily close above the breakout level on volume at least 1.5x usual; no volume, no "
        "trade. Stop about one daily range below the level. Exit if it closes back inside the "
        "base within 3 days."),
    7: ("Breakout + Trend Overspill", "F&O · 3-7 days",
        "After a range breakout, buy the first hourly dips into the 20-50 EMA zone of the "
        "new trend.",
        ["Market: anything except Still falling (half size when Overheated)",
         "A Range breakout in the last 15 sessions that still holds (price above the old ceiling)",
         "Weekly or Daily in Impulse",
         "Hourly Overspill, stage Ready, Dip quality A",
         "Not Extended (under 4 ranges above the Daily 50 EMA); sector not Lagging"],
        "Hourly close above the entry (the hourly 20 EMA). Stop under the spill low. Book half "
        "at 2R, trail the rest under the hourly 50 EMA. Best on the first or second dip after "
        "the breakout; later dips are weaker."),
}


def gate_of(m):
    """Market gate from a page's market block."""
    h, t, nf = m.get("daily_bull_hist"), m.get("turn"), (m.get("nifty") or {}).get("state")
    g = {"turn": t or "Unknown", "last": h[-1] if h else None, "hist": h,
         "overheated": bool(h and t == "Turning up" and h[-1] >= 85),
         "cooldown": bool(h and max(h[:-1]) >= 85 and 60 <= h[-1] <= 78
                          and h[-1] < max(h[:-1]) and nf != "Bearish"),
         "falling": t == "Falling"}
    if g["overheated"]:
        g["word"] = f"Overheated: {h[-1]}% of stocks already up on Daily"
    elif t == "Turning up":
        g["word"] = f"Turning up ({h[-1]}% up on Daily)"
    elif t == "Falling":
        g["word"] = "Still falling"
    elif t == "No clear turn":
        g["word"] = "No clear turn"
    else:
        g["word"] = "Market line not available yet"
    return g


def detail_num(r, pattern):
    import re as _re
    mt = _re.search(pattern, r.get("detail", ""))
    return float(mt.group(1)) if mt else None


def strategy_picks(data, sec_table):
    """Apply each strategy's rules. Returns {n: {"open": bool, "why_closed": str,
    "picks": [...], "blocked": [...]}} where blocked = names that pass every stock rule
    but not the market gate."""
    out = {}
    sec = lambda s: SECTORS.get(s, {})
    sec_ok = lambda s, ok: sec(s).get("stage") in ok
    ext_under = lambda r, x: r.get("ext") is None or r["ext"] < x

    def pick(n, r, market, size, checks, bonus=None, entry=None, stop=None):
        entry = r.get("trigger") if entry is None else entry
        stop = r.get("stop") if stop is None else stop
        tgt = (entry + 2 * (entry - stop)) if entry and stop and entry > stop else None
        return {"strategy": n, "symbol": r["symbol"], "market": market, "row": r,
                "entry": entry, "stop": stop, "target": tgt, "size": size,
                "checks": checks, "bonus": bonus or []}

    fo, eq, bo = data.get("F&O"), data.get("Equity"), data.get("Breakout")
    ufo, ueq = data.get("Unusual F&O", {}), data.get("Unusual Equity", {})
    gf = gate_of(fo["market"]) if fo else None
    ge = gate_of(eq["market"]) if eq else None

    def rows(res, keys=("rows", "overspill")):
        return [r for k in keys for r in (res or {}).get(k, [])]

    def sector_line(s):
        t = sec(s)
        return (f"Sector {t['sector']} {t['stage']}" if t.get("sector") and t.get("stage")
                else "Sector not ranked")

    # 1. Sector Leader Swing (F&O)
    res = {"picks": [], "blocked": []}
    if gf:
        open_ = not gf["falling"] and not gf["overheated"] and gf["turn"] in (
            "Turning up", "No clear turn")
        size = "Full size" if gf["turn"] == "Turning up" else "Half size"
        for r in rows(fo, ("rows",)):
            s = sec(r["symbol"])
            st_ = sec_table.get(s.get("sector"), {})
            if (r["setup"] == "Swing Pullback" and r["stage"] == "Ready" and r["grade"] == "A"
                    and r.get("trend") == "With trend" and ext_under(r, 4.0)
                    and s.get("stage") in ("Leading", "Improving")
                    and st_.get("idx_d") == "Bullish" and st_.get("wk_bull", 0) >= 50):
                p = pick(1, r, "F&O", size, [
                    "Swing Pullback Ready · grade A · With trend",
                    f"{r['ext']:.1f} ranges above the 50 EMA" if r.get("ext") is not None
                    else "Extension n/a",
                    f"{sector_line(r['symbol'])} · index Daily Bullish · Wk bull {st_['wk_bull']}%"],
                    [f"Old: {r['old']}"] if r.get("old") else [])
                (res["picks"] if open_ else res["blocked"]).append(p)
        res["open"] = open_
        res["gate"] = gf["word"]
    out[1] = res

    # 2. Cooldown Buy (both)
    res = {"picks": [], "blocked": [], "open": False, "gate": ""}
    for market, rs, g in (("F&O", fo, gf), ("Equity", eq, ge)):
        if not g:
            continue
        for r in rows(rs):
            if (r["setup"] in ("Trend Pullback", "Daily Overspill") and r["stage"] == "Ready"
                    and r["grade"] == "A" and r.get("ext") is not None and r["ext"] < 2
                    and sec_ok(r["symbol"], ("Leading", "Weakening"))):
                p = pick(2, r, market, "Full size", [
                    f"{r['setup']} Ready · grade A", f"{r['ext']:.1f} ranges above the 50 EMA",
                    sector_line(r["symbol"])])
                (res["picks"] if g["cooldown"] else res["blocked"]).append(p)
    res["open"] = bool((gf and gf["cooldown"]) or (ge and ge["cooldown"]))
    res["gate"] = ("Cooldown under way" if res["open"] else
                   "Waiting for a cooldown: Daily breadth has not yet cooled from 85%+ to 60-78%")
    out[2] = res

    # 3. Squeeze Coil (both)
    res = {"picks": [], "blocked": []}
    opens = []
    for market, rs, g, um in (("F&O", fo, gf, ufo), ("Equity", eq, ge, ueq)):
        if not g:
            continue
        open_ = (g["turn"] == "No clear turn" or
                 (g["turn"] == "Turning up" and (g["last"] or 100) < 70))
        opens.append(open_)
        for r in rows(rs, ("rows",)):
            if (r["setup"] == "Squeeze Break" and r["stage"] == "Ready" and r["grade"] in ("A", "B")
                    and sec_ok(r["symbol"], ("Leading", "Improving"))):
                bonus = []
                if r.get("old") == "🏆 Prime Setup":
                    bonus.append("Old: 🏆 Prime Setup")
                if um.get(r["symbol"], {}).get("bias") == "Buying":
                    bonus.append("Unusual: Buying")
                p = pick(3, r, market, "Full size" if r["grade"] == "A" else "Half size",
                         [f"Squeeze Break Ready · grade {r['grade']}", sector_line(r["symbol"])],
                         bonus)
                (res["picks"] if open_ else res["blocked"]).append(p)
    res["open"] = any(opens)
    res["gate"] = ("Open" if all(opens) else "Open for one market only" if res["open"] else
                   "Closed: works early in a turn, and Daily breadth is already 70% or more")
    out[3] = res

    # 4. Weekly Overspill Positional (both)
    res = {"picks": [], "blocked": []}
    for market, rs, g in (("F&O", fo, gf), ("Equity", eq, ge)):
        if not g:
            continue
        for r in rs.get("overspill", []):
            if (r["setup"] == "Weekly Overspill" and r["stage"] == "Ready" and r["grade"] == "A"
                    and r.get("trend") == "With trend"
                    and sec(r["symbol"]).get("stage") != "Lagging"):
                bonus = []
                d_now, d_before = (detail_num(r, r"Delivery (\d+)%"),
                                   detail_num(r, r"vs (\d+)% before"))
                if d_now and d_before and d_now > d_before:
                    bonus.append(f"Delivery {d_now:.0f}% vs {d_before:.0f}% before")
                p = pick(4, r, market, "Accordion: one third now", [
                    "Weekly Overspill Ready · grade A · With trend", sector_line(r["symbol"])],
                    bonus)
                (res["blocked"] if g["falling"] else res["picks"]).append(p)
    res["open"] = not (gf and gf["falling"])
    res["gate"] = "Open" if res["open"] else "Closed: market still falling"
    out[4] = res

    # 5. Unusual Activity Confluence (both)
    res = {"picks": [], "blocked": []}
    for market, rs, g, um in (("F&O", fo, gf, ufo), ("Equity", eq, ge, ueq)):
        if not g:
            continue
        by_sym = {}
        for r in rows(rs):
            if r["stage"] in ("Ready", "Watch") and r["grade"] != "Warning":
                by_sym.setdefault(r["symbol"], []).append(r)
        for sym, u in um.items():
            w = (u.get("states") or {}).get("W", "")
            if (u.get("bias") == "Buying" and u.get("score", 0) >= 4 and sym in by_sym
                    and w in ("Bullish", "Impulse")
                    and sec_ok(sym, ("Leading", "Improving"))):
                r = sorted(by_sym[sym], key=lambda x: (x["stage"] != "Ready", x["grade"]))[0]
                ev = u["events"][0]
                p = pick(5, r, market, "Full size" if r["stage"] == "Ready" else "Alert only", [
                    f"Unusual Buying · score {u['score']:.1f} · {ev['when']}: {ev['text']}",
                    f"{r['setup']} {r['stage']} · grade {r['grade']}",
                    f"Weekly {w} · {sector_line(sym)}"])
                (res["blocked"] if g["falling"] else res["picks"]).append(p)
    res["open"] = not (gf and gf["falling"])
    res["gate"] = "Open" if res["open"] else "Closed: market still falling"
    out[5] = res

    # 7. Breakout + Trend Overspill (F&O)
    res = {"picks": [], "blocked": []}
    if gf and fo:
        brk = fo.get("recent_breakouts", {})
        size = "Half size (market overheated)" if gf["overheated"] else "Full size"
        for r in fo.get("overspill", []):
            b = brk.get(r["symbol"])
            stt = r.get("states") or {}
            if (r["setup"] == "Hourly Overspill" and r["stage"] == "Ready" and r["grade"] == "A"
                    and b and ext_under(r, 4.0)
                    and ("Impulse" in (stt.get("W"), stt.get("D")))
                    and sec(r["symbol"]).get("stage") != "Lagging"):
                when = dt.date.fromisoformat(b["date"]).strftime("%a %d %b")
                p = pick(7, r, "F&O", size, [
                    f"Range breakout {when} ({b['days']} sessions ago): {b['weeks']}-week "
                    f"{b['kind']}, still holding above {money(b['level'])}",
                    "Hourly Overspill Ready · Dip quality A",
                    f"Weekly {stt.get('W', '?')} · Daily {stt.get('D', '?')} · {sector_line(r['symbol'])}"],
                    [f"VK: {r['vk']['final']}"] if r.get("vk") and
                    r["vk"]["final"] in ("ALMOST READY (Bullish)", "BUY NOW") else [])
                (res["blocked"] if gf["falling"] else res["picks"]).append(p)
        res["open"] = not gf["falling"]
        res["gate"] = ("Closed: market still falling" if gf["falling"] else
                       "Open (half size: overheated)" if gf["overheated"] else "Open")
        # the queue: breakout stocks dipping now, and breakout stocks not dipping yet
        dipping = [r for r in fo.get("overspill", []) if r["setup"] == "Hourly Overspill"
                   and r["symbol"] in brk and r["stage"] == "Watch" and r["grade"] != "Warning"]
        dsyms = {r["symbol"] for r in fo.get("overspill", []) if r["setup"] == "Hourly Overspill"}
        res["queue"] = {"dipping": sorted(dipping, key=lambda r: (r["grade"], r["symbol"])),
                        "waiting": sorted((s_, b) for s_, b in brk.items() if s_ not in dsyms)}
    out[7] = res

    # 6. Equity Base Breakout
    res = {"picks": [], "blocked": []}
    if bo:
        g = ge or gate_of(bo.get("market", {}))
        open_ = g["turn"] == "Turning up" and not g["overheated"]
        for r in bo.get("setting", []):
            rs_, base, vb = (detail_num(r, r"RS (\d+)"), detail_num(r, r"Base (\d+)% deep"),
                             detail_num(r, r"Volume in base ([\d.]+)x"))
            stg = sec(r["symbol"]).get("stage")
            if (r["stage"] == "Ready" and r["grade"] == "A" and (rs_ or 0) >= 80
                    and (base or 99) <= 20 and (vb or 9) < 1 and stg in (None, "", "Leading",
                                                                         "Improving")):
                p = pick(6, r, "Equity", "Full size", [
                    f"Ready · grade A · RS {rs_:.0f}", f"Base {base:.0f}% deep · volume {vb:.1f}x "
                    "usual in the base", sector_line(r["symbol"])])
                (res["picks"] if open_ else res["blocked"]).append(p)
        res["open"] = open_
        res["gate"] = ("Open" if open_ else f"Closed: Equity market is {g['word']}")
    out[6] = res
    for v in out.values():
        v.setdefault("open", False)
        v.setdefault("gate", "No data yet")
    return out


def pick_html(p):
    e = html.escape
    r = p["row"]
    lvl = (f'<span>Buy above {money(p["entry"])}</span>' if p.get("entry") else
           '<span>No entry level yet</span>')
    stop = (f'<span style="color:{RED}">Stop {money(p["stop"])}</span>' if p.get("stop")
            else f'<span style="color:{MUTED}">Stop set on breakout</span>')
    tgt = (f'<span style="color:{GREEN}">2R {money(p["target"])}</span>' if p.get("target")
           else "")
    checks = "".join(f'<div class="tf-small">✓ {e(c)}</div>' for c in p["checks"])
    bonus = "".join(f'<div class="tf-small" style="color:{GREEN};font-weight:600">+ {e(b)}</div>'
                    for b in p["bonus"])
    return (
        '<div class="tf-row"><div class="tf-top">'
        f'<span><a class="tf-sym" href="{chart_link(p["symbol"])}" target="_blank">{e(p["symbol"])}</a>'
        f'{wl_link(p["symbol"])}</span>'
        f'{grade_badge(r.get("grade", ""), r.get("stage"))}</div>'
        f'<div class="tf-set">{e(p["market"])} · {e(r.get("setup", ""))} · '
        f'<span style="color:{MUTED}">{e(p["size"])}</span>{quality_html(r)}</div>'
        f'{checks}{bonus}{deals_html(p["symbol"])}{stars_html(p["symbol"])}'
        f'<div class="tf-lvl">{lvl}{tgt}{stop}</div></div>')


def render_strategies():
    data = {}
    for key, path in (("F&O", PAGES["F&O"]["file"]), ("Equity", PAGES["Equity"]["file"]),
                      ("Breakout", PAGES["Breakout"]["file"]),
                      ("Sectors", PAGES["Sectors"]["file"])):
        if os.path.exists(path):
            data[key] = load(path, os.path.getmtime(path))
    data["Unusual F&O"] = load_unusual_map("F&O")
    data["Unusual Equity"] = load_unusual_map("Equity")
    sec_table = {s["name"]: s for s in data.get("Sectors", {}).get("sectors", [])}
    stamps = " · ".join(
        f"{k} " + dt.datetime.strptime(data[k]["generated_ist"], "%Y-%m-%d %H:%M").strftime(
            "%a %d %b %H:%M") for k in ("F&O", "Equity") if k in data)
    st.markdown(
        f'<div style="display:flex;justify-content:space-between;align-items:baseline;'
        f'border-bottom:2px solid {INK};padding-bottom:10px">'
        f'<span style="font-family:Georgia,serif;font-size:26px;font-weight:700;color:{INK}">'
        f'Strategies</span><span class="tf-small">Scanned: {html.escape(stamps)}</span></div>',
        unsafe_allow_html=True)
    if "F&O" not in data:
        st.info("No scan results yet.")
        return
    res = strategy_picks(data, sec_table)
    total = sum(len(v["picks"]) for v in res.values())
    headline = ("No trade passes every rule right now." if total == 0 else
                "1 trade passes every rule." if total == 1 else
                f"{total} trades pass every rule.")
    lines = []
    for k in ("F&O", "Equity"):
        if k in data:
            g = gate_of(data[k]["market"])
            lines.append(f"{k} market: {g['word']}.")
    st.markdown(
        f'<div style="font-family:Georgia,serif;font-size:22px;font-weight:600;'
        f'color:{INK};margin:6px 0 6px">{headline}</div>'
        f'<div style="font-size:14px;color:{INK};line-height:1.5">'
        f'{html.escape(" ".join(lines))}</div>', unsafe_allow_html=True)
    with st.expander("How this page works"):
        st.markdown("""
Each strategy from the playbook is applied automatically to the latest scans. A stock shows here only when it passes **every** rule of that strategy, including the market gate. Lines marked **+** are bonus confirmations, not required.

- **Buy above**: the entry level from the setup. Wait for a close above it (hourly for Strategy 1, daily for the rest).
- **Stop**: from the setup. **2R**: the price where the gain is twice the risk; book half there.
- **Size**: full, half, or accordion, from the market gate and the grade.
- **Waiting on the market gate**: stocks that pass every stock rule but the market does not allow the strategy today. Useful as a watchlist; do not trade them yet.

Rules are starting points, not tested results. Your own chart is the final check. Watchlist only, not investment advice.
""")
    for n, v in sorted(res.items()):
        name, hold, idea, rules, manage = STRAT_RULES[n]
        cnt = len(v["picks"])
        status_col = GREEN if v["open"] else MUTED
        st.markdown(
            f'<div style="margin-top:22px;border-top:2px solid {INK};padding-top:8px;'
            f'display:flex;justify-content:space-between;align-items:baseline;gap:10px">'
            f'<span style="font-family:Georgia,serif;font-size:19px;font-weight:700">'
            f'{n}. {html.escape(name)}</span>'
            f'<span class="tf-small">{html.escape(hold)}</span></div>'
            f'<div class="tf-small">{html.escape(idea)}</div>'
            f'<div class="tf-small" style="color:{status_col};font-weight:600;margin-top:2px">'
            f'{"Gate open" if v["open"] else "Gate closed"} · {html.escape(v["gate"])} · '
            f'{cnt} {"trade" if cnt == 1 else "trades"}</div>', unsafe_allow_html=True)
        if v["picks"]:
            st.markdown('<div class="tf-grid">' + "".join(pick_html(p) for p in v["picks"])
                        + '</div>', unsafe_allow_html=True)
        if v["blocked"]:
            with st.expander(f"Waiting on the market gate · {len(v['blocked'])}"):
                st.markdown('<div class="tf-grid">' + "".join(pick_html(p) for p in v["blocked"])
                            + '</div>', unsafe_allow_html=True)
        q = v.get("queue")
        if q and q["dipping"]:
            with st.expander(f"Dipping now, next to turn · {len(q['dipping'])}"):
                st.caption("Breakout stocks dipping into the hourly 20-50 EMA zone that have not "
                           "turned up yet. Set an alert at the hourly 20 EMA ('Above').")
                show_rows(q["dipping"])
        if q and q["waiting"]:
            with st.expander(f"Broke out, waiting for the first dip · {len(q['waiting'])}"):
                st.caption("Fresh range breakouts that are not dipping yet. Their first pullback "
                           "to the hourly 20-50 EMA zone is the next round of this strategy.")
                st.markdown("".join(
                    f'<div class="tf-small" style="margin:3px 0"><a href="{chart_link(s_)}" '
                    f'target="_blank"><b>{html.escape(s_)}</b></a>{wl_link(s_)} · broke a {b["weeks"]}-week '
                    f'{html.escape(b["kind"])} {b["days"]} sessions ago, ceiling {money(b["level"])}'
                    f'{" · " + html.escape(SECTORS[s_]["sector"] + " " + SECTORS[s_].get("stage", "")) if SECTORS.get(s_, {}).get("sector") else ""}'
                    '</div>' for s_, b in q["waiting"]), unsafe_allow_html=True)
        with st.expander("Rules"):
            st.markdown("\n".join(f"- {x}" for x in rules) + f"\n\n**Manage:** {manage}")
    with st.expander("Exits: signals on stocks you already hold"):
        st.markdown("""
| Site signal | Action |
| --- | --- |
| Stage Stretched, or the row shows Extended (red) | Book at least half; trail the rest tightly |
| Stage Fading, or Daily turns Topping | Tighten the stop to the last daily low |
| Old label from Confirmed Bullish to Extended (Weakening) | Book profits |
| Old label turns Avoid (Weekly below zero) | Exit swings; positional trades get one more week |
| Unusual page shows Selling | Exit or halve |
| Sector turns Weakening or Lagging | No adds; tighten the stop |
| Market line turns Still falling | Halve open swings; no new trades |
| Grade drops to Warning | Exit |

Risk the same amount on every trade; the stop sets the quantity. Never move a stop lower. A swing that has not moved in 5 days goes.
""")
    with st.expander("Daily routine"):
        st.markdown("""
1. **Evening, after 19:50** (all scans done, OI and delivery final): open this page first. Check the market lines and the trades listed.
2. Open each name's chart; mark Buy above and Stop; set TradingView alerts.
3. Check the Waiting lists and Watch stages for tomorrow's likely names.
4. Check open positions against the Exits table.
5. **Market hours:** F&O and Unusual refresh hourly from 10:20 to 15:20. Act only when an alert hits.
6. **After 16:40:** Sectors refresh; glance at stage changes.
""")
    st.caption("Strategies applied to the latest scans. Watchlist only, not investment advice.")


def spark(vals, w=88, h=22):
    """Tiny trend line of a stake across quarters (None = no filing that quarter)."""
    pts = [(i, v) for i, v in enumerate(vals) if v is not None]
    if not pts:
        return ""
    lo, hi = min(v for _, v in pts), max(v for _, v in pts)
    if lo >= 0 and hi > 0 and lo > 0.5 * hi:
        lo = 0.0                                   # stakes: keep zero as the floor
    span = (hi - lo) or 1.0
    n = max(len(vals) - 1, 1)
    xy = [(round(i * (w - 4) / n + 2, 1), round(h - 2 - ((v - lo) / span) * (h - 4), 1))
          for i, v in pts]
    col = GREEN if pts[-1][1] >= pts[0][1] else RED
    path = " ".join(f"{x},{y}" for x, y in xy)
    last = xy[-1]
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
            f'<polyline points="{path}" fill="none" stroke="{col}" stroke-width="1.6"/>'
            f'<circle cx="{last[0]}" cy="{last[1]}" r="2.2" fill="{col}"/></svg>')


def in_num(n):
    """Indian digit grouping: 12,69,832."""
    if n is None:
        return "—"
    s = str(int(n))
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    return ",".join([head] + parts + [tail]) if head else ",".join(parts + [tail])


def render_stars():
    e = html.escape
    if not os.path.exists(STARS_FILE):
        st.title("Star investors")
        st.info("No results yet. Run the 'Star investors' workflow once, or tap Scan all.")
        return
    res = load(STARS_FILE, os.path.getmtime(STARS_FILE))
    gen = dt.datetime.strptime(res["generated_ist"], "%Y-%m-%d %H:%M")
    st.markdown(
        f'<div style="display:flex;justify-content:space-between;align-items:baseline;'
        f'border-bottom:2px solid {INK};padding-bottom:10px">'
        f'<span style="font-family:Georgia,serif;font-size:26px;font-weight:700;color:{INK}">'
        f'Star investors</span><span class="tf-small">Updated {gen.strftime("%a %d %b")} · '
        f'{gen.strftime("%H:%M")}</span></div>', unsafe_allow_html=True)
    Q, P, cov = res.get("quarter"), res.get("previous"), res.get("coverage", {})
    if not Q:
        st.info("Reading company filings. Results appear after the first run finishes.")
        return
    qn = lambda q: dt.date.fromisoformat(q).strftime("%b %Y")
    rows = res["rows"]
    counts = {}
    for r in rows:
        counts[r["change"]] = counts.get(r["change"], 0) + 1
    nn, nx = counts.get('New ≥1%', 0), counts.get('Under 1% / exit', 0)
    head = (f"{nn} new {'stake' if nn == 1 else 'stakes'}, {counts.get('Increased', 0)} increased, "
            f"{counts.get('Reduced', 0)} reduced, {nx} {'exit' if nx == 1 else 'exits'}.")
    sub = (f"Stakes of 1% or more, quarter ending {qn(Q)}" + (f" vs {qn(P)}" if P else "")
           + f". From {cov.get(Q, 0):,} company filings for {qn(Q)}"
           + (f" and {cov.get(P, 0):,} for {qn(P)}" if P else "") + ".")
    st.markdown(
        f'<div style="font-family:Georgia,serif;font-size:22px;font-weight:600;color:{INK};'
        f'margin:6px 0 6px">{e(head)}</div>'
        f'<div style="font-size:14px;color:{INK};line-height:1.5">{e(sub)}</div>',
        unsafe_allow_html=True)
    comp, left = res.get("companies", 0), res.get("left", 0)
    if comp and res.get("latest_done", comp) < 0.9 * comp:
        st.caption(f"First fill in progress: {res.get('latest_done', 0):,} of {comp:,} companies "
                   f"read for the latest quarter. It finishes over about a day of background "
                   f"runs; the history for the trend lines follows over a few more days.")
    with st.expander("What the words mean"):
        st.markdown("""
Every listed company files its shareholding pattern each quarter, within 21 days of the quarter end. Anyone holding 1% or more is named, with their %. This page reads those filings for all NSE companies and picks out well-known investors.

- **New ≥1%**: at 1% or more this quarter, not named last quarter (either newly bought, or crossed 1%).
- **Increased / Reduced**: the % changed by more than 0.01 points (Δ pp = percentage points).
- **Under 1% / exit**: named last quarter, not named now: they sold out or fell below 1% (below 1% they are no longer named, so it may be a partial sale).
- **Trend**: their % in each quarter loaded. **Held since**: the first quarter of the current run at 1%+; "≤" means it was already held in the oldest quarter loaded.
- **Held as**: the exact names in the filing that were counted (a star's funds and family holdings are added together).
- **⚠**: a common name, so the match may be a different person with the same name.
- **Stars:** lines on stock cards elsewhere on the site come from this page.

Filings lag: a September quarter stake becomes public in October. Treat this as slow information about conviction, not a timing signal; your setups give the timing.
""")
    view = st.radio("Change", ["All changes", "New ≥1%", "Increased", "Reduced",
                               "Under 1% / exit", "Everything held"], horizontal=True,
                    label_visibility="collapsed", key="star_view")
    inv = res.get("investors", {})
    amb = {r["investor"] for r in rows if r.get("ambiguous")}
    pick = st.selectbox("Investor", ["All investors"] + list(inv),
                        format_func=lambda x: x if x == "All investors" else
                        f"{x}{' ⚠' if x in amb else ''} ({inv[x]})", key="star_inv")
    if view == "All changes":
        rs = [r for r in rows if r["change"] in ("New ≥1%", "Increased", "Reduced",
                                                  "Under 1% / exit")]
    elif view == "Everything held":
        rs = [r for r in rows if r["pct_now"] is not None]
    else:
        rs = [r for r in rows if r["change"] == view]
    if pick != "All investors":
        rs = [r for r in rs if r["investor"] == pick]
    on_site = {}
    for key in ("F&O", "Equity"):
        p_ = PAGES[key]["file"]
        if os.path.exists(p_):
            r_ = load(p_, os.path.getmtime(p_))
            for row in r_.get("rows", []) + r_.get("overspill", []):
                if row.get("stage") in ("Ready", "Triggered", "Watch"):
                    on_site.setdefault(row["symbol"], []).append(
                        f'{row["setup"]} {row["stage"]} ({row["grade"]})')
    badge = {"New ≥1%": GREEN, "Increased": GREEN, "Reduced": RED, "Under 1% / exit": RED,
             "Unchanged": MUTED, "Held": MUTED}
    if not rs:
        st.caption("None for this choice.")
        return
    body = "".join(
        f"<tr><td><b>{e(r['investor'])}</b>{' ⚠' if r.get('ambiguous') else ''}</td>"
        f"<td><a class='tf-jump' href='{chart_link(r['symbol'])}' target='_blank'>"
        f"{e(r['symbol'])}</a>{wl_link(r['symbol'])}"
        + (f"<div class='tf-small' style='color:{GREEN}'>{e('; '.join(on_site[r['symbol']][:2]))}</div>"
           if r["symbol"] in on_site else "")
        + f"</td><td style='color:{badge[r['change']]};font-weight:600;white-space:nowrap'>"
        f"{e(r['change'])}</td>"
        f"<td class='n'>{'—' if r['pct_now'] is None else format(r['pct_now'], '.2f') + '%'}</td>"
        f"<td class='n'>{'—' if r['pct_before'] is None else format(r['pct_before'], '.2f') + '%'}</td>"
        f"<td class='n' style='color:{GREEN if (r['delta'] or 0) > 0 else RED if (r['delta'] or 0) < 0 else MUTED}'>"
        f"{'—' if r['delta'] is None else format(r['delta'], '+.2f')}</td>"
        f"<td>{spark(r['trend'])}</td><td style='white-space:nowrap'>{e(r['since'] or '—')}</td>"
        f"<td class='n'>{in_num(r['shares'])}</td><td style='white-space:nowrap'>{e(r['filed'] or '')}</td>"
        f"<td class='tf-small'>{e(r['held_as'][:60])}</td></tr>"
        for r in rs[:400])
    st.markdown(
        f"<style>.tf-sec{{width:100%;border-collapse:collapse;font-size:13.5px;color:{INK}}}"
        f".tf-sec th{{font-size:11px;letter-spacing:.06em;text-transform:uppercase;"
        f"color:{MUTED};font-weight:600;text-align:left;padding:6px 4px;"
        f"border-bottom:2px solid {INK};white-space:nowrap}}"
        f".tf-sec td{{padding:7px 4px;border-bottom:1px solid {RULE};vertical-align:middle}}"
        f".tf-sec td.n{{text-align:right;font-family:{MONO};white-space:nowrap}}"
        f".tf-jump{{color:{INK};font-weight:600}}</style>"
        "<div style='overflow-x:auto;margin:6px 0 4px'><table class='tf-sec'>"
        "<tr><th>Investor</th><th>Symbol</th><th>Change</th><th style='text-align:right'>% now</th>"
        "<th style='text-align:right'>% before</th><th style='text-align:right'>Δ pp</th>"
        "<th>Trend</th><th>Held since</th><th style='text-align:right'>Shares now</th>"
        "<th>Filed</th><th>Held as</th></tr>"
        f"{body}</table></div>", unsafe_allow_html=True)
    if len(rs) > 400:
        st.caption(f"Showing 400 of {len(rs)}. Pick an investor to see the rest.")
    st.caption("From NSE shareholding filings. Watchlist only, not investment advice.")


@st.cache_data(ttl=600)
def company_names(stamp):
    """symbol -> company name, from the files the scans save."""
    names = {}
    for path in (os.path.join("data", "sector_map.csv"), os.path.join("data", "names.csv")):
        if os.path.exists(path):
            try:
                df = pd.read_csv(path)
                if "name" in df.columns:
                    names.update({s: n for s, n in zip(df["symbol"], df["name"])
                                  if isinstance(n, str) and n})
            except Exception:
                pass
    return names


def all_results():
    """Every result file the site has, loaded once."""
    files = {"F&O": PAGES["F&O"]["file"], "Equity": PAGES["Equity"]["file"],
             "Breakout": PAGES["Breakout"]["file"], "Sectors": PAGES["Sectors"]["file"],
             "Unusual F&O": PAGES["Unusual"]["F&O"]["file"],
             "Unusual Equity": PAGES["Unusual"]["Equity"]["file"],
             "Deals": DEALS_FILE, "Stars": STARS_FILE}
    out = {}
    for k, p in files.items():
        if os.path.exists(p):
            try:
                out[k] = load(p, os.path.getmtime(p))
            except Exception:
                pass
    return out


def known_symbols(R):
    syms = set()
    for k in ("F&O", "Equity"):
        r = R.get(k, {})
        for lab in r.get("old_labels", {}).values():
            syms |= set(lab)
        for key in ("rows", "overspill"):
            syms |= {x["symbol"] for x in r.get(key, [])}
        for v in r.get("others", {}).values():
            syms |= set(v)
    for k in ("broke", "setting"):
        syms |= {x["symbol"] for x in R.get("Breakout", {}).get(k, [])}
    for k in ("Unusual F&O", "Unusual Equity"):
        syms |= {x["symbol"] for x in R.get(k, {}).get("rows", [])}
    syms |= set(R.get("Deals", {}).get("stocks", {}))
    syms |= {x["symbol"] for x in R.get("Stars", {}).get("rows", [])}
    syms |= set(R.get("Sectors", {}).get("map", {}))
    return syms


def section(title, note=""):
    st.markdown(
        f'<div style="margin-top:20px;border-top:2px solid {INK};padding-top:8px;'
        f'display:flex;justify-content:space-between;align-items:baseline">'
        f'<span style="font-family:Georgia,serif;font-size:19px;font-weight:700">'
        f'{html.escape(title)}</span><span class="tf-small">{html.escape(note)}</span></div>',
        unsafe_allow_html=True)


def render_search():
    e = html.escape
    R = all_results()
    names = company_names(tuple(os.path.getmtime(p) for p in
                                (os.path.join("data", "sector_map.csv"),
                                 os.path.join("data", "names.csv")) if os.path.exists(p)))
    syms = sorted(known_symbols(R) | set(names))
    st.markdown(
        f'<div style="border-bottom:2px solid {INK};padding-bottom:10px">'
        f'<span style="font-family:Georgia,serif;font-size:26px;font-weight:700;color:{INK}">'
        f'Stock search</span></div>', unsafe_allow_html=True)
    label = {s: (f"{s} · {names[s]}" if s in names else s) for s in syms}
    q = st.query_params.get("stock")
    default = syms.index(q.upper()) if q and q.upper() in syms else None
    sym = st.selectbox("Stock", syms, index=default, format_func=lambda s: label[s],
                       placeholder="Type a symbol or company name, e.g. INFY or Infosys",
                       label_visibility="collapsed", key="search_pick")
    if not sym:
        st.caption("Everything the site knows about one stock, from every page: setups, old "
                   "labels, VK, strategies, sector, unusual activity, deals and star investors.")
        return
    st.query_params["stock"] = sym
    fo, eq = R.get("F&O", {}), R.get("Equity", {})
    in_fo = sym in {x for v in fo.get("old_labels", {}).values() for x in v}
    home = fo if in_fo else eq
    market = "F&O" if in_fo else "Equity" if sym in {x for v in eq.get("old_labels", {}).values()
                                                       for x in v} else "Not scanned"
    rows = [r for r in home.get("rows", []) + home.get("overspill", []) + home.get("ranges", [])
            if r["symbol"] == sym]
    bo = [r for k in ("broke", "setting") for r in R.get("Breakout", {}).get(k, [])
          if r["symbol"] == sym]
    vk = next((dict(x, final=lab) for lab, xs in fo.get("vk_labels", {}).items()
               for x in xs if x["symbol"] == sym), None)
    old = next((lab for lab, xs in home.get("old_labels", {}).items() if sym in xs), None)
    stage_other = next((st_ for st_, xs in home.get("others", {}).items() if sym in xs), None)
    close = next((r.get("close") for r in rows + bo if r.get("close")), None) or (
        vk or {}).get("close")
    states = next((r.get("states") for r in rows if r.get("states")), None) or (vk or {}).get("states")
    ext = next((r.get("ext") for r in rows if r.get("ext") is not None), None)

    # header
    st.markdown(
        f'<div style="margin-top:10px"><a class="tf-sym" style="font-size:26px" '
        f'href="{chart_link(sym)}" target="_blank">{e(sym)}</a>{wl_link(sym, big=True)}'
        f'<span style="font-size:16px;color:{MUTED}"> {e(names.get(sym, ""))}</span></div>'
        f'<div class="tf-set">{e(market)}' + (f" · {money(close)}" if close else "") + '</div>'
        + (f'<div class="tf-small">{" · ".join(f"{k} {e(v)}" for k, v in states.items())}</div>'
           if states else "")
        + (f'<div class="tf-small">{abs(ext):.1f} ranges {"above" if ext >= 0 else "below"} '
           f'the 50 EMA{" · Extended" if ext >= EXT_MAX else ""}</div>' if ext is not None else "")
        + sector_html(sym), unsafe_allow_html=True)

    # setups
    section("Setups", "new labels")
    if rows or bo:
        show_rows(rows + bo)
    else:
        st.caption("Not on any setup right now." + (f" Stage: {stage_other}." if stage_other else ""))

    # old label + VK
    section("Old TrendFire label")
    st.markdown(f'<div class="tf-set">{e(old) if old else "Not available"}</div>',
                unsafe_allow_html=True)
    if market == "F&O":
        section("VK System")
        if vk:
            st.markdown(
                f'<div class="tf-set" style="color:{VK_COLOR.get(vk["final"], INK)}">'
                f'{e(vk["final"])}</div>'
                f'<div class="tf-small">VK SIGNAL: {e(vk["signal"])} · {e(vk["ma"])}</div>'
                f'<div class="tf-small" style="color:{INK}">KYA KARNA HAI: {e(vk["advice"])}</div>'
                f'<div class="tf-small">Daily: {e(vk["daily"])} · Hourly: {e(vk["hourly"])} · '
                f'{e(vk["confirmed"])}</div>', unsafe_allow_html=True)
        else:
            st.caption("No VK reading (needs the latest F&O scan).")

    # strategies
    section("Strategies")
    try:
        sec_table = {x["name"]: x for x in R.get("Sectors", {}).get("sectors", [])}
        data = {"F&O": fo, "Equity": eq, "Breakout": R.get("Breakout"),
                "Unusual F&O": load_unusual_map("F&O"), "Unusual Equity": load_unusual_map("Equity")}
        res = strategy_picks({k: v for k, v in data.items() if v}, sec_table)
        hits = [(n, p, True) for n, v in res.items() for p in v["picks"] if p["symbol"] == sym]
        hits += [(n, p, False) for n, v in res.items() for p in v["blocked"] if p["symbol"] == sym]
    except Exception:
        hits = []
    if hits:
        for n, p, ok in hits:
            st.markdown(f'<div class="tf-set" style="margin-top:6px">{n}. {e(STRAT_RULES[n][0])} · '
                        + (f'<span style="color:{GREEN}">passes every rule</span>' if ok else
                           f'<span style="color:{AMBER}">waiting on the market gate</span>')
                        + '</div><div class="tf-grid">' + pick_html(p) + '</div>',
                        unsafe_allow_html=True)
    else:
        st.caption("Does not pass any strategy's rules right now.")

    # sector
    t = SECTORS.get(sym)
    if t and t.get("sector"):
        x = next((s_ for s_ in R.get("Sectors", {}).get("sectors", []) if s_["name"] == t["sector"]),
                 None)
        section("Sector", t["sector"])
        if x:
            pct = lambda v: "n/a" if v is None else f"{v:+.1f}%"
            st.markdown(
                f'<div class="tf-set"><span style="color:{STAGE_COLOR.get(x["stage"], MUTED)}">'
                f'{e(x["stage"])}</span> · vs Nifty 1W {pct(x.get("rw"))} · 2W {pct(x.get("r2w"))}'
                f' · 1M {pct(x.get("r1", x.get("rs1")))}</div>'
                f'<div class="tf-small">Index Daily {e(x.get("idx_d") or "n/a")} · '
                f'D bull {x.get("d_bull", "n/a")}% · Wk bull {x["wk_bull"]}%</div>',
                unsafe_allow_html=True)

    # unusual
    u = next((r for k in ("Unusual F&O", "Unusual Equity") for r in R.get(k, {}).get("rows", [])
              if r["symbol"] == sym), None)
    section("Unusual activity")
    if u:
        st.markdown('<div class="tf-grid">' + unusual_row(u) + '</div>', unsafe_allow_html=True)
    else:
        st.caption("Nothing unusual in volume, OI or delivery in the last few sessions.")

    # deals
    section("Bulk and block deals", "last 30 days")
    d = R.get("Deals", {})
    mine = [r for r in d.get("recent", []) if r["symbol"] == sym]
    if d.get("stocks", {}).get(sym):
        st.markdown(deals_html(sym), unsafe_allow_html=True)
    if mine:
        st.markdown("".join(
            f'<div class="tf-small">{e(r["date"])} · <span style="color:'
            f'{GREEN if r["side"] == "BUY" else RED}">{"Buy" if r["side"] == "BUY" else "Sell"}'
            f'</span> · {e(r["who"])} ({e(r["cat"])}, {e(r["kind"])}) · Rs {r["value_cr"]:,.1f} Cr'
            f' at {money(r["price"])}</div>' for r in mine), unsafe_allow_html=True)
    if not d.get("stocks", {}).get(sym) and not mine:
        st.caption("No fund, star or large deals recorded.")

    # stars
    section("Star investors", "stakes of 1% or more")
    sr = [r for r in R.get("Stars", {}).get("rows", []) if r["symbol"] == sym]
    if sr:
        st.markdown("".join(
            f'<div class="tf-small" style="margin:3px 0"><b>{e(r["investor"])}</b>'
            f'{" ⚠" if r.get("ambiguous") else ""} · {e(r["change"])} · '
            + ("—" if r["pct_now"] is None else f'{r["pct_now"]:.2f}%')
            + ("" if r["pct_before"] is None else f' (before {r["pct_before"]:.2f}%)')
            + f' {spark(r["trend"], 70, 18)} · held as {e(r["held_as"][:50])}</div>'
            for r in sr), unsafe_allow_html=True)
    else:
        st.caption("No star investor holds 1% or more (in the filings read so far).")
    st.caption("Watchlist only, not investment advice.")


def stock_brief(sym, R):
    """One stock in a few words, from every page: for the watchlist."""
    fo, eq = R.get("F&O", {}), R.get("Equity", {})
    in_fo = any(sym in xs for xs in fo.get("old_labels", {}).values())
    home = fo if in_fo else eq
    rows = [r for r in home.get("rows", []) + home.get("overspill", []) + home.get("ranges", [])
            if r["symbol"] == sym]
    vk = next((lab for lab, xs in fo.get("vk_labels", {}).items()
               for x in xs if x["symbol"] == sym), None)
    old = next((lab for lab, xs in home.get("old_labels", {}).items() if sym in xs), None)
    other = next((s_ for s_, xs in home.get("others", {}).items() if sym in xs), None)
    close = next((r.get("close") for r in rows if r.get("close")), None)
    rank = {"Ready": 0, "Triggered": 1, "Watch": 2}
    rows.sort(key=lambda r: (rank.get(r["stage"], 9), r["grade"]))
    return {"market": "F&O" if in_fo else ("Equity" if old else ""), "rows": rows, "vk": vk,
            "old": old, "other": other, "close": close,
            "best": rank.get(rows[0]["stage"], 9) if rows else 9}


def save_note(sym):
    txt = st.session_state.get(f"wl_note_{sym}", "")
    if txt.strip() == WATCH.get(sym, {}).get("note", ""):
        return
    err = watch_change(note={sym: txt})
    st.session_state["watch_msg"] = (err, sym, "note")


def render_watchlist():
    e = html.escape
    st.markdown(
        f'<div style="border-bottom:2px solid {INK};padding-bottom:10px">'
        f'<span style="font-family:Georgia,serif;font-size:26px;font-weight:700;color:{INK}">'
        f'Watchlist</span></div>', unsafe_allow_html=True)
    if not secret("GH_TOKEN"):
        st.info("The watchlist is saved in your GitHub repo, so it needs GH_TOKEN in the "
                "app's secrets.")
        return
    R = all_results()
    syms = sorted(known_symbols(R))
    with st.container(horizontal=True, vertical_alignment="bottom"):
        pick = st.selectbox("Add a stock", syms, index=None, placeholder="Type a symbol to add",
                            label_visibility="collapsed", key="wl_add_pick")
        if st.button("Add", disabled=not pick):
            err = watch_change(add=[pick])
            if err:
                st.error(err)
            else:
                WATCH[pick] = {"symbol": pick}
                st.rerun()
    if not WATCH:
        st.caption("Your watchlist is empty. Tap the ☆ next to any stock name on the site, or "
                   "add one above. It is saved in your GitHub repo, so it is the same on your "
                   "phone and laptop.")
        return
    briefs = {s: stock_brief(s, R) for s in WATCH}
    order = sorted(WATCH, key=lambda s: (briefs[s]["best"], s))
    n_ready = sum(1 for s in order if briefs[s]["best"] == 0)
    st.markdown(
        f'<div style="font-family:Georgia,serif;font-size:22px;font-weight:600;color:{INK};'
        f'margin:6px 0">{len(order)} {"stock" if len(order) == 1 else "stocks"} · '
        f'{n_ready} Ready now</div>', unsafe_allow_html=True)
    for s in order:
        b = briefs[s]
        bits = []
        for r in b["rows"][:3]:
            col = (GREEN if r["stage"] in LIVE_STAGES and r["grade"] == "A" else
                   RED if r["grade"] == "Warning" else INK)
            bits.append(f'<span style="color:{col}">{e(r["setup"])} {e(r["stage"])} '
                        f'({quality_word(r["setup"]).split()[0]} {e(r["grade"])})</span>')
        if not b["rows"] and b["other"]:
            bits.append(e(b["other"]))
        if b["vk"]:
            bits.append(f'<span style="color:{VK_COLOR.get(b["vk"], MUTED)}">VK {e(b["vk"])}</span>')
        if b["old"]:
            bits.append(f"Old {e(b['old'])}")
        added = WATCH[s].get("added", "")
        c1, c2 = st.columns([6, 1], vertical_alignment="center")
        with c1:
            st.markdown(
                f'<div style="border-top:1px solid {RULE};padding:8px 0">'
                f'<a class="tf-sym" href="{chart_link(s)}" target="_blank">{e(s)}</a>'
                f'<span class="tf-small"> {e(b["market"])}'
                + (f" · {money(b['close'])}" if b["close"] else "")
                + (f" · added {e(added)}" if added else "")
                + f' · <a href="?page=Search&stock={e(s)}" target="_self">full details</a></span>'
                f'<div class="tf-small" style="color:{INK}">'
                + (" · ".join(bits) if bits else "Nothing on the site for this stock right now")
                + f'</div>{sector_html(s)}</div>', unsafe_allow_html=True)
        st.text_area("Remarks", value=WATCH[s].get("note", ""), key=f"wl_note_{s}",
                     placeholder="Remarks: your notes on this stock", height=68,
                     max_chars=1000, label_visibility="collapsed",
                     on_change=save_note, args=(s,))
        with c2:
            if st.button("Remove", key=f"wl_rm_{s}"):
                err = watch_change(remove=[s])
                if err:
                    st.error(err)
                else:
                    WATCH.pop(s, None)
                    st.rerun()
    st.markdown(f'<div class="tf-small" style="letter-spacing:.08em;text-transform:uppercase;'
                f'font-weight:600;margin-top:22px">Export to TradingView</div>',
                unsafe_allow_html=True)
    tv = ",".join(f"NSE:{s}" for s in order)
    st.download_button("Download for TradingView", tv, file_name="tradeready_watchlist.txt",
                       mime="text/plain")
    st.caption("In TradingView (laptop): open the watchlist panel → the watchlist name menu → "
               "Import list → choose this file. It becomes a new watchlist. TradingView does "
               "not let other sites change your watchlists directly, so this is the closest "
               "to a sync. You can also copy the line below and paste it into a watchlist.")
    st.code(tv, language=None)


GUIDE = """
**What the site does.** It scans every F&O stock every hour, and midcaps/smallcaps every evening, with your MWDH TTM framework, and lists the setups, how good they are, and whether the market allows trading them. It is a filter. Your chart is the final check.

#### The pages
- **Strategies**: start here. Trades that pass every rule of 7 strategies, with buy level, stop and 2R target. Also the queue (stocks about to qualify).
- **Search**: everything about one stock: setups, old label, VK, strategies, sector, unusual activity, deals, star investors.
- **Watchlist**: your saved stocks, with what each one is doing now. Tap ☆ next to any stock name to add it.
- **A list**: every Ready, good-quality setup across F&O, Equity and Breakout.
- **F&O / Equity**: all setups by type (Squeeze, Swing, Trend, Overspill, Range), with Ready / Watch / Triggered tabs, plus Old labels and VK (F&O).
- **Unusual**: stocks with unusual volume, OI or delivery in the last few sessions.
- **Breakout**: equity bases close to breaking out.
- **Sectors**: which sectors lead, which way they are moving day by day, and their stocks.
- **Deals / Stars**: bulk and block deals, and star investors' 1%+ stakes.

#### Words to know
- **Watch** = still dipping or coiling, not yet. **Ready** = turned up, the entry stage. **Triggered** = already moved, you are late.
- **Dip / Coil / Base quality A, B, C**: how clean the setup is (volume, OI, delivery). Grey badge = not tradeable yet; green A = good and tradeable.
- **Extended**: far above the 50 EMA; chasing is risky.
- **Market line**: Turning up = trade; Overheated = small size, dips only; No clear turn = small, only A; Still falling = no new trades.

#### Evening routine (15 minutes, after 19:50)
1. **Market line** (top of F&O): decide size for tomorrow.
2. **Sectors**: pick 2-3 Leading or Improving sectors that are ▲ Rising.
3. **Strategies**: read the trades and the "Dipping now" lists.
4. **F&O → VK → ALMOST READY**: keep names that are also on a setup with quality A, in your sectors, MA signal ONLY BUY, not Extended.
5. **Search** each finalist: drop Unusual Selling, Warning grade, funds selling, or results due.
6. Tap **☆** on the 2-4 best. Open **Watchlist** → **Download for TradingView** → import, and set alerts at the "Buy above" levels.

#### During the day
- The **Telegram bot** pings when stocks turn BUY NOW / CONFIRMED, or a new Strategy 7 setup appears. A ping on a watchlist name is the best signal.
- **Enter on the alert at the trigger**, not after confirmation. Half size at the trigger, the rest at CONFIRMED.
- **15-min or hourly dip?** If the Hourly TTM is still rising, buy the 15-min dip. If it has started falling, wait for the hourly 20-50 EMA zone.

#### Managing trades
- **Stop** from the card. Never move it lower.
- **Book half at 2R**, trail the rest under the hourly 50 EMA.
- **Exit by day 5** if nothing happens.
- Exit or cut on: Stretched, Warning grade, Unusual Selling, sector turning Lagging, market turning Still falling (see Exits on the Strategies page).

#### What to skip
- Counter-trend tags, Lagging sectors, Extended stocks, grade C or Warning.
- Anything you only notice after it has already run: wait for the next dip.
- More than 3-4 open trades at once.

#### Housekeeping
- **Scan all** refreshes everything; tap once and watch the bars. Scans also run by themselves through the day.
- If a page looks old, tap **Refresh**; if still old, **Manage app → Reboot app**.
- Rules are starting values. After 3-4 weeks of noting which strategy each trade came from, we can see which ones work for you.

*Watchlist and research tool, not investment advice.*
"""


def render_guide():
    st.markdown(
        f'<div style="border-bottom:2px solid {INK};padding-bottom:10px">'
        f'<span style="font-family:Georgia,serif;font-size:26px;font-weight:700;color:{INK}">'
        f'How to use TradeReady</span></div>', unsafe_allow_html=True)
    st.markdown(GUIDE)


def render_deals():
    e = html.escape
    if not os.path.exists(DEALS_FILE):
        st.title("Deals")
        st.info("No deal results yet. Run the 'Deals scan' workflow once, or tap Scan all.")
        return
    res = load(DEALS_FILE, os.path.getmtime(DEALS_FILE))
    gen = dt.datetime.strptime(res["generated_ist"], "%Y-%m-%d %H:%M")
    st.markdown(
        f'<div style="display:flex;justify-content:space-between;align-items:baseline;'
        f'border-bottom:2px solid {INK};padding-bottom:10px">'
        f'<span style="font-family:Georgia,serif;font-size:26px;font-weight:700;color:{INK}">'
        f'Deals</span><span class="tf-small">Scanned {gen.strftime("%a %d %b")} · '
        f'{gen.strftime("%H:%M")} · deals to {e(res["asof"])}</span></div>',
        unsafe_allow_html=True)
    recent, stocks = res.get("recent", []), res.get("stocks", {})
    mf_buy = sorted(((s, d) for s, d in stocks.items() if (d.get("mf_net_cr") or 0) > 0),
                    key=lambda x: -x[1]["mf_net_cr"])
    st.markdown(
        f'<div style="font-family:Georgia,serif;font-size:22px;font-weight:600;color:{INK};'
        f'margin:6px 0 6px">{len(recent)} fund, star and large deals in the last 10 days.</div>'
        f'<div style="font-size:14px;color:{INK};line-height:1.5">Bulk and block deals '
        f'reported to NSE, tagged by who traded. {len(mf_buy)} stocks saw net mutual fund '
        f'buying in the last 30 days.</div>', unsafe_allow_html=True)
    with st.expander("What the words mean"):
        st.markdown("""
- **Bulk deal**: one client trading more than 0.5% of a company's shares in a day. **Block deal**: a single trade of Rs 10 crore or more in the special block window. NSE publishes both every evening.
- **Who**: **MF** = a mutual fund; **Star** = a well-known individual investor (Jhunjhunwala/Rare, Kacholia, Kedia, Dolly Khanna, Singhania, Mukul Agrawal, Damani and others); **Insurance** = LIC and insurers; **Other** = everyone else, listed only above Rs 25 crore.
- **Deals (30d)** on stock cards elsewhere on the site: the last 30 days of fund, star and insurance deals in that stock. Green = net buying, red = net selling.
- Bulk and block deals show only the big trades. Most fund buying happens quietly in the market and appears only in the monthly portfolios (planned next).
""")
    view = st.radio("Who", ["All", "Funds", "Stars", "Insurance", "Others"], horizontal=True,
                    label_visibility="collapsed", key="deal_view")
    cat = {"Funds": "MF", "Stars": "Star", "Insurance": "Insurance", "Others": "Other"}.get(view)
    rows = [r for r in recent if cat is None or r["cat"] == cat]
    on_site = {}
    for key in ("F&O", "Equity"):
        p_ = PAGES[key]["file"]
        if os.path.exists(p_):
            r_ = load(p_, os.path.getmtime(p_))
            for row in r_.get("rows", []) + r_.get("overspill", []):
                if row.get("stage") in ("Ready", "Triggered", "Watch"):
                    on_site.setdefault(row["symbol"], []).append(
                        f'{row["setup"]} {row["stage"]} ({row["grade"]})')
    if not rows:
        st.caption("No deals of this kind in the last 10 days.")
    else:
        body = "".join(
            f"<tr><td>{e(r['date'][5:])}</td>"
            f"<td><a class='tf-jump' href='{chart_link(r['symbol'])}' target='_blank'>"
            f"{e(r['symbol'])}</a>{wl_link(r['symbol'])}"
            + (f"<div class='tf-small' style='color:{GREEN}'>{e('; '.join(on_site[r['symbol']][:2]))}</div>"
               if r["symbol"] in on_site else "")
            + f"</td><td style='color:{GREEN if r['side'] == 'BUY' else RED};font-weight:600'>"
            f"{'Buy' if r['side'] == 'BUY' else 'Sell'}</td>"
            f"<td>{e(r['who'])}<div class='tf-small'>{e(r['cat'])} · {e(r['kind'])}</div></td>"
            f"<td class='n'>{r['value_cr']:,.1f}</td><td class='n'>{money(r['price'])}</td></tr>"
            for r in rows[:300])
        st.markdown(
            f"<style>.tf-sec{{width:100%;border-collapse:collapse;font-size:13.5px;color:{INK}}}"
            f".tf-sec th{{font-size:11px;letter-spacing:.06em;text-transform:uppercase;"
            f"color:{MUTED};font-weight:600;text-align:left;padding:6px 4px;"
            f"border-bottom:2px solid {INK}}}"
            f".tf-sec td{{padding:7px 4px;border-bottom:1px solid {RULE};vertical-align:top}}"
            f".tf-sec td.n{{text-align:right;font-family:{MONO}}}"
            f".tf-jump{{color:{INK};font-weight:600}}</style>"
            "<div style='overflow-x:auto;margin:6px 0 4px'><table class='tf-sec'>"
            "<tr><th>Date</th><th>Stock</th><th>Side</th><th>Who</th>"
            "<th style='text-align:right'>Rs Cr</th><th style='text-align:right'>Price</th></tr>"
            f"{body}</table></div>", unsafe_allow_html=True)
    if mf_buy:
        with st.expander(f"Most fund buying through deals, last 30 days · {len(mf_buy)}"):
            st.markdown("".join(
                f'<div class="tf-small" style="margin:4px 0"><a href="{chart_link(s)}" '
                f'target="_blank"><b>{e(s)}</b></a> · {e(d["text"])}</div>'
                for s, d in mf_buy[:40]), unsafe_allow_html=True)
    for note in res.get("notes", []):
        st.caption(f"Note: {note}")
    st.caption(f"{res.get('days', 0)} trading days of deals saved. Updated each evening "
               "after NSE publishes. Watchlist only, not investment advice.")


SCANS = {                      # what the button can start -> workflow file in .github/workflows
    "F&O setups": "scan.yml",
    "Equity, Breakout, Equity unusual": "equity.yml",
    "Unusual (F&O)": "unusual.yml",
    "Sectors": "sector.yml",
    "Deals": "deals.yml",
    "Star investors": "stars.yml",
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


TYPICAL_SECS = {"F&O setups": 420, "Equity, Breakout, Equity unusual": 600,
                "Unusual (F&O)": 360, "Sectors": 240, "Deals": 120, "Star investors": 2700}


def gh_time(s):
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


@st.cache_data(ttl=15)
def scan_runs():
    """Latest few runs of every scan workflow, from GitHub."""
    out = {}
    for name, wf in SCANS.items():
        code, data = github("GET", f"/actions/workflows/{wf}/runs?per_page=6")
        if code != 200:
            continue
        out[name] = [{k: r.get(k) for k in ("status", "conclusion", "run_started_at",
                                            "created_at", "updated_at", "html_url")}
                     for r in data.get("workflow_runs") or []]
    return out


@st.fragment(run_every="20s")
def scan_status():
    """A live bar for each scan running on GitHub, and how the last ones ended."""
    if not secret("GH_TOKEN"):
        return
    runs = scan_runs()
    if not runs:
        return
    now = dt.datetime.now(dt.timezone.utc)
    active, ended = [], []
    for name, rs in runs.items():
        if not rs:
            continue
        r = rs[0]
        if r["status"] != "completed":
            took = sorted((gh_time(x["updated_at"]) - gh_time(x["run_started_at"])).total_seconds()
                          for x in rs[1:] if x["status"] == "completed"
                          and x["conclusion"] == "success" and x.get("run_started_at"))
            typ = took[len(took) // 2] if took else TYPICAL_SECS.get(name, 600)
            start = r.get("run_started_at") or r["created_at"]
            el = max(0.0, (now - gh_time(start)).total_seconds())
            active.append((name, r["status"], el, typ))
        else:
            ended.append((name, r["conclusion"], gh_time(r["updated_at"]), r["html_url"]))
    for name, status, el, typ in active:
        if status in ("queued", "waiting", "pending", "requested"):
            st.caption(f"⏳ {name}: waiting for GitHub to start it")
            st.progress(0.0)
            continue
        pct = min(0.95, el / max(typ, 60))
        extra = ""
        if name == "Star investors" and os.path.exists(STARS_FILE):
            try:
                sr = load(STARS_FILE, os.path.getmtime(STARS_FILE))
                if sr.get("companies") and sr.get("latest_done", 0) < sr["companies"]:
                    extra = (f" · first fill: {sr['latest_done']:,} of {sr['companies']:,} "
                             "companies read before this run")
            except Exception:
                pass
        st.caption(f"🔄 {name}: running {int(el // 60)} min of about {round(typ / 60)}"
                   f" · {int(pct * 100)}%{extra}")
        st.progress(pct)
    if ended:
        bits = []
        for name, concl, t, url in sorted(ended, key=lambda x: x[2], reverse=True):
            when = t.astimezone(IST).strftime("%H:%M")
            short = name.split(",")[0].replace(" (F&O)", "")
            bits.append(f"{short} {when} ✓" if concl == "success" else
                        f"[{short} {when} ✗ {concl}]({url})")
        st.caption(("All scans idle. " if not active else "") + "Last finished: " + " · ".join(bits))


def scan_all():
    """Start every scan on GitHub at once. Returns (started, problems)."""
    started, problems = [], []
    scan_runs.clear()
    busy = {n for n, rs in scan_runs().items() if rs and rs[0]["status"] != "completed"}
    for name, wf in SCANS.items():
        if name in busy:                       # already running or waiting: no duplicate
            problems.append(f"{name}: already running, so not started again.")
            continue
        code, _ = github("POST", f"/actions/workflows/{wf}/dispatches", {"ref": "main"})
        if code == 204:
            started.append(name)
        else:
            problems.append(f"{name}: " + {
                401: "GitHub refused the key (expired or deleted).",
                403: "the key needs Actions set to Read and write.",
                404: f".github/workflows/{wf} not found in the repo.",
            }.get(code, f"could not reach GitHub (code {code})."))
    last_run.clear()
    scan_runs.clear()
    return started, problems


def file_stamp():
    return tuple(os.path.getmtime(p) if os.path.exists(p) else 0 for p in ALL_FILES)


@st.fragment(run_every="120s")
def watch_for_new_scans():
    """Every two minutes, reload the page if any scan has saved new results."""
    now, seen = file_stamp(), st.session_state.get("file_stamp")
    st.session_state["file_stamp"] = now
    if seen is not None and seen != now:
        st.rerun()


# ---------------------------------------------------------------- watchlist
WATCH_PATH = "data/watchlist.json"
WATCH = {}                    # symbol -> {"added": date}
CUR_PAGE = ""


@st.cache_data(ttl=30)
def watch_read(version):
    """The watchlist file from GitHub: (dict, sha). version busts the cache after a write."""
    code, data = github("GET", f"/contents/{WATCH_PATH}?ref=main")
    if code != 200:
        return {}, None
    try:
        import base64
        items = json.loads(base64.b64decode(data["content"]).decode())
        return {x["symbol"]: x for x in items}, data.get("sha")
    except Exception:
        return {}, data.get("sha")


def watch_write(items, sha, message):
    import base64
    body = {"message": message, "branch": "main",
            "content": base64.b64encode(json.dumps(sorted(items.values(), key=lambda x:
                                                          x["symbol"]), indent=1).encode()).decode()}
    if sha:
        body["sha"] = sha
    code, _ = github("PUT", f"/contents/{WATCH_PATH}", body)
    return code


def watch_change(add=None, remove=None, note=None):
    """Add or remove symbols. Returns an error message, or None."""
    if not secret("GH_TOKEN"):
        return "The watchlist needs GH_TOKEN in the app's secrets."
    for attempt in range(2):
        items, sha = watch_read(st.session_state.get("watch_v", 0))
        items = dict(items)
        today = dt.datetime.now(IST).strftime("%Y-%m-%d")
        for s in add or []:
            items.setdefault(s, {"symbol": s, "added": today})
        for s in remove or []:
            items.pop(s, None)
        for s, txt in (note or {}).items():
            items.setdefault(s, {"symbol": s, "added": today})["note"] = txt.strip()
        code = watch_write(items, sha, "Watchlist: " + ", ".join(
            [f"+{s}" for s in add or []] + [f"-{s}" for s in remove or []]
            + [f"note {s}" for s in note or {}]))
        st.session_state["watch_v"] = st.session_state.get("watch_v", 0) + 1
        watch_read.clear()
        if code in (200, 201):
            return None
        if code == 409:                       # changed meanwhile: read again and retry
            continue
        return {401: "GitHub refused the key.",
                403: "The GitHub key needs Contents set to Read and write (one-time change).",
                404: "The GitHub key needs Contents set to Read and write (one-time change)."
                }.get(code, f"Could not save the watchlist (code {code}).")
    return "Could not save the watchlist; try again."


def wl_link(sym, big=False):
    """A star next to a stock name: tap to add to (or remove from) the watchlist."""
    import urllib.parse as up
    on = sym in WATCH
    q = up.urlencode({"page": CUR_PAGE, ("unwatch" if on else "watch"): sym})
    size = "font-size:24px;" if big else ""
    return (f'<a class="tf-wl{" on" if on else ""}" style="{size}" href="?{q}" target="_self" '
            f'title="{"Remove from" if on else "Add to"} watchlist">{"★" if on else "☆"}</a>')


PAGE_LIST = (["Strategies", "Search", "Watchlist", "A list"] + list(PAGES)
             + ["Deals", "Stars", "Guide"])
qp = st.query_params
for key, kind in (("watch", "add"), ("unwatch", "remove")):
    if key in qp:
        sym_q = qp[key].upper().strip()
        err = watch_change(**{kind: [sym_q]})
        st.session_state["watch_msg"] = (err, sym_q, kind)
        del st.query_params[key]
start = qp.get("page")
page = st.radio("Market", PAGE_LIST, horizontal=True, label_visibility="collapsed",
                index=PAGE_LIST.index(start) if start in PAGE_LIST else 0)
CUR_PAGE = page
if qp.get("page") != page:
    st.query_params["page"] = page
if secret("GH_TOKEN"):
    WATCH.update(watch_read(st.session_state.get("watch_v", 0))[0])
wm = st.session_state.pop("watch_msg", None)
if wm:
    err, sym_q, kind = wm
    if err:
        st.error(f"Watchlist: {err}")
    else:
        st.toast(f"Remarks saved for {sym_q}" if kind == "note" else
                 f"{sym_q} {'added to' if kind == 'add' else 'removed from'} your watchlist")
with st.container(horizontal=True, vertical_alignment="center"):
    if st.button("Refresh"):
        st.cache_data.clear()
    scan_clicked = st.button("Scan all", disabled=not secret("GH_TOKEN"),
                             help=None if secret("GH_TOKEN") else
                             "Add GH_TOKEN to the app's secrets on Streamlit to use this.")
if scan_clicked:
    started, problems = scan_all()
    if started:
        import time as _t
        _t.sleep(3)                       # give GitHub a moment so the bars appear at once
    if started:
        st.success(f"Started {len(started)} scans: {', '.join(started)}. They take about "
                   "2 to 10 minutes, and the page reloads by itself as results arrive.")
    for p in problems:
        (st.info if "already running" in p else st.error)(p)
# faded diagonal name, fixed to the screen so it stays in view while scrolling;
# pointer-events:none lets every tap pass through to the page underneath
st.markdown(
    '<div aria-hidden="true" style="position:fixed;inset:0;display:flex;'
    'align-items:center;justify-content:center;pointer-events:none;z-index:999990;'
    'overflow:hidden"><div style="transform:rotate(-30deg);white-space:nowrap;'
    'font-family:Georgia,serif;font-weight:700;font-size:min(11vw,130px);'
    f'color:{INK};opacity:0.07;user-select:none">{html.escape(OWNER)}</div></div>',
    unsafe_allow_html=True)
scan_status()
watch_for_new_scans()
SECTORS.update(load_sector_tags())
DEALS.update(load_deals())
STAR_MAP.update(load_star_map())
if page == "Stars":
    render_stars()
    st.stop()
if page == "Search":
    render_search()
    st.stop()
if page == "Watchlist":
    render_watchlist()
    st.stop()
if page == "Guide":
    render_guide()
    st.stop()
if page == "Deals":
    render_deals()
    st.stop()
if page == "Sectors":
    render_sectors(PAGES[page])
    st.stop()
if page == "A list":
    render_alist()
    st.stop()
if page == "Strategies":
    render_strategies()
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
                  format_func=lambda x: x if x == "Old labels" else x.split()[0],
                  key=f"setup_{page}")
OLD_ORDER = ["🏆 Prime Setup", "✅ Confirmed Bullish", "🎯 Consolidating",
             "⚠️ Extended (Gaining)", "⏳ Building Momentum", "⚠️ Extended",
             "⚠️ Extended (Weakening)", "❌ Avoid", "— Insufficient Data"]
OLD_DEF = """**Old TrendFire labels (same rule as before: Weekly, Daily and Hourly momentum)**
- **🏆 Prime Setup**: Weekly above zero. Daily below zero but rising, and Daily is in a squeeze.
- **✅ Confirmed Bullish**: Weekly above zero. Daily above zero and rising.
- **🎯 Consolidating**: Weekly and Daily above zero, Daily flattening. Hourly below zero but rising.
- **⚠️ Extended (Gaining)**: Weekly and Daily above zero, Daily flattening. Hourly above zero and rising.
- **⚠️ Extended**: Weekly and Daily above zero, Daily flattening. Hourly above zero and falling.
- **⚠️ Extended (Weakening)**: Weekly and Daily above zero, Daily flattening. Hourly below zero and falling.
- **⏳ Building Momentum**: Weekly above zero. Daily below zero but rising, with no squeeze.
- **❌ Avoid**: Weekly below zero, or Daily below zero and falling.

**Rough match with the new words**
- Prime Setup is close to **Squeeze** Watch or Ready.
- Building Momentum is close to **Trend** Ready (Daily dip turning up).
- Consolidating is close to **Swing** Ready (Hourly dip turning up).
- Confirmed Bullish is close to **Running**.
- Extended is close to **Stretched** or **Fading**.

The old labels can differ a little from the old site, because Hourly here uses only
completed bars."""
if EQUITY:
    OLD_ORDER = ["🏆 Prime Setup", "✅ Confirmed Bullish", "⏳ Building Momentum",
                 "⚠️ Extended", "❌ Avoid"]
    OLD_DEF = """**Old TrendFire labels for Equity (Weekly and Daily momentum)**
- **🏆 Prime Setup**: Weekly above zero. Daily below zero but rising, and Daily is in a squeeze.
- **✅ Confirmed Bullish**: Weekly above zero. Daily above zero and rising.
- **⏳ Building Momentum**: Weekly above zero. Daily below zero but rising, with no squeeze.
- **⚠️ Extended**: Weekly and Daily above zero, Daily flattening or falling.
- **❌ Avoid**: Weekly below zero, or Daily below zero and falling.

Same rule as F&O, but Equity has no Hourly, so Consolidating and the three Extended
variants are all shown as Extended.

**Rough match with the new words**
- Prime Setup is close to **Squeeze** Watch or Ready.
- Building Momentum is close to **Trend** Ready (Daily dip turning up).
- Confirmed Bullish is close to **Running**.
- Extended is close to **Stretched** or **Fading**."""
if choice == "VK":
    groups = res.get("vk_labels")
    if not groups:
        st.caption("VK labels need the updated F&O scan file. They will appear after the "
                   "next F&O scan.")
    else:
        labs = [l for l in VK_ORDER if l != "NO SIGNAL"] + ["NO SIGNAL"]
        pick = st.radio("VK label", labs, horizontal=True, label_visibility="collapsed",
                        format_func=lambda l: f"{l} · {len(groups.get(l, []))}",
                        key="vk_label_pick")
        on_list = {}
        for r in res["rows"] + res.get("overspill", []):
            on_list.setdefault(r["symbol"], []).append(
                f'{r["setup"]} {r["stage"]} ({r["grade"]})')
        items = groups.get(pick, [])
        if not items:
            st.caption("No stocks with this VK reading right now.")
        else:
            e = html.escape
            cards = "".join(
                '<div class="tf-row"><div class="tf-top">'
                f'<span><a class="tf-sym" href="{chart_link(x["symbol"])}" target="_blank">'
                f'{e(x["symbol"])}</a>{wl_link(x["symbol"])}</span><span class="tf-lvl">{money(x["close"])}</span></div>'
                f'<div class="tf-set">{e(x["signal"])} · <span style="color:{MUTED}">'
                f'{e(x["ma"])}</span></div>'
                f'<div class="tf-small">{" · ".join(f"{k} {e(v)}" for k, v in x["states"].items())}</div>'
                f'<div class="tf-small" style="color:{INK}">{e(x["advice"])}</div>'
                f'<div class="tf-small">Daily: {e(x["daily"])} · Hourly: {e(x["hourly"])} · '
                f'{e(x["confirmed"])}</div>'
                + (f'<div class="tf-small" style="color:{GREEN};font-weight:600">On Setups: '
                   f'{e("; ".join(on_list[x["symbol"]]))}</div>' if x["symbol"] in on_list else "")
                + sector_html(x["symbol"]) + '</div>'
                for x in items)
            st.markdown(f'<div class="tf-set" style="margin:6px 0">{len(items)} '
                        f'{"stock" if len(items) == 1 else "stocks"}: {e(pick)}</div>'
                        f'<div class="tf-grid">{cards}</div>', unsafe_allow_html=True)
    with st.expander("What the VK words mean"):
        st.markdown(VK_DEF)
    st.caption("Watchlist only, not investment advice.")
    st.stop()
if choice == "Old labels":
    groups = res.get("old_labels")
    if not groups:
        st.caption("Old labels need the updated F&O scan file. They will appear after the "
                   "next scan.")
    else:
        labs = [l for l in OLD_ORDER if not l.startswith("—")]
        pick = st.radio("Old label", labs, horizontal=True, label_visibility="collapsed",
                        format_func=lambda l: f"{l} · {len(groups.get(l, []))}",
                        key="old_label_pick")
        names = groups.get(pick, [])
        on_list = {}
        for r in res["rows"] + res.get("overspill", []):
            on_list.setdefault(r["symbol"], []).append(
                f'{r["setup"]} {r["stage"]} ({r["grade"]})')
        if not names:
            st.caption("No stocks with this label right now.")
        else:
            e = html.escape
            cards = "".join(
                '<div class="tf-row"><div class="tf-top">'
                f'<span><a class="tf-sym" href="{chart_link(n)}" target="_blank">{e(n)}</a>'
                f'{wl_link(n)}</span></div>'
                + (f'<div class="tf-small">On Setups: {e("; ".join(on_list[n]))}</div>'
                   if n in on_list else "")
                + sector_html(n) + '</div>'
                for n in names)
            st.markdown(f'<div class="tf-set" style="margin:6px 0">{len(names)} '
                        f'{"stock" if len(names) == 1 else "stocks"}: {e(pick)}</div>'
                        f'<div class="tf-grid">{cards}</div>', unsafe_allow_html=True)
    with st.expander("What the old labels mean"):
        st.markdown(OLD_DEF)
    st.caption("Watchlist only, not investment advice.")
    st.stop()
SPILL = choice == "Overspill"
if choice == "Range":
    rows = res.get("ranges", [])
    if "ranges" not in res:
        st.caption("Range breakouts need the updated scan file. They will appear after the "
                   "next scan with that file.")
elif SPILL:
    rows = res.get("overspill", [])
    if "overspill" not in res:
        st.caption("Overspill needs the updated F&O scan file. It will appear after the "
                   "next scan with that file.")
else:
    rows = [r for r in res["rows"] if choice == "All" or r["setup"] == choice]
count = {g: sum(r["stage"] == g for r in rows) for g in ("Ready", "Watch", "Triggered")}
view = st.radio("Stage", list(count), horizontal=True, label_visibility="collapsed",
                format_func=lambda g: f"{g} · {count[g]}", key=f"stage_{page}")
ready = [r for r in rows if r["stage"] == "Ready"]
held = []
if cautious(m):
    held = [r for r in ready if is_extended(r)
            or (m["regime"] == "Weak" and r["grade"] != "A")]
    ready = [r for r in ready if r not in held]

n = len(ready)
headline = ("No stocks are Ready right now." if n == 0 else
            "1 stock is Ready." if n == 1 else f"{n} stocks are Ready.")
if n == 0 and held:
    headline = (f"Nothing clean is Ready. {len(held)} hidden "
                f"{'one is' if len(held) == 1 else 'ones are'} listed below.")
shown = [r for r in rows if r["stage"] == view]
if view != "Ready":
    k = len(shown)
    headline = (f"No stocks are {'on Watch' if view == 'Watch' else 'Triggered'} right now."
                if k == 0 else
                f"{k} {'stock' if k == 1 else 'stocks'} "
                + ("on Watch." if view == "Watch" else "Triggered."))
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
    + turn_html(m) +
    f'<div style="height:6px;border-radius:3px;background:#dcdcd4;margin:8px 0 4px">'
    f'<div style="height:6px;border-radius:3px;width:{bar}%;background:{GREEN}"></div></div>',
    unsafe_allow_html=True)

SPILL_DEF = (
    "- **Overspill** (its own list): in an uptrend, with the 20 EMA over the 50 EMA over "
    "the 200 SMA, price comes back into the 20-50 EMA zone, sometimes spilling under the "
    "50 EMA, then reclaims the 20 EMA. **Weekly Overspill** is the pattern on the weekly "
    "chart and needs Monthly bullish; it is Ready when the Daily turns up inside the "
    "weekly zone, and its stop is wide because it sits under the weekly low. "
    "**Daily Overspill** needs Weekly bullish. There is no Hourly Overspill for equity. "
    "Watch means price is in the zone, Ready means it has started to turn, and Triggered "
    "means it closed back above the 20 EMA. 'Above' is the 20 EMA level, and the stop is "
    "under the low of the spill. The grade uses volume drying up in the dip and "
    "delivery.\n") if EQUITY else (
    "- **Overspill** (its own list): in an uptrend, with the 20 EMA over the 50 EMA over "
    "the 200 SMA, price comes back into the 20-50 EMA zone, sometimes spilling under the "
    "50 EMA, then reclaims the 20 EMA. **Weekly Overspill** is the pattern on the weekly "
    "chart and needs Monthly bullish; it is Ready when the Daily turns up inside the "
    "weekly zone, and its stop is wide because it sits under the weekly low. "
    "**Daily Overspill** needs Weekly bullish. "
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
    GRADE_DEF = """**Quality grade (volume and delivery, as of the last close)**

The grade says how clean the setup is, not whether to buy: **Dip quality** for pullbacks and Overspill, **Coil quality** for Squeeze. The badge stays grey while the stock is on Watch and turns colour at Ready, when it becomes tradeable.

- **A**: volume and delivery both support the setup.
- **B**: one of the two supports it.
- **C**: neither does.
- **Warning**: volume in the dip is heavier than in the up-move. That points to selling pressure."""
    THIRD_DEF = ("- **Delivery 55% vs 40% before**: delivery share in the last 10 days "
                 "against the 30 days before. Higher is better.")
    TF_DEF = "**M, W, D (Monthly, Weekly, Daily momentum)**"
else:
    GRADE_DEF = """**Quality grade (volume and OI, as of the last close)**

The grade says how clean the setup is, not whether to buy: **Dip quality** for pullbacks and Overspill, **Coil quality** for Squeeze. The badge stays grey while the stock is on Watch and turns colour at Ready, when it becomes tradeable.

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
- **Range** (its own list): weeks of sideways trading under a ceiling, either a **falling trendline** through at least 3 swing highs or a **flat top**, while the floor holds (no new lows, so not a downtrend). **Daily Range** looks back about 8 months, **Hourly Range** about 2 months. Watch = inside the range; Ready = within one normal range of the ceiling; Triggered = closed above the ceiling recently (dropped once it has run more than 2.5 ranges away). "Above" is the ceiling today; the stop is under the range floor or 1.5 ranges below the ceiling, whichever is closer; the target adds the range depth to the breakout level. Quality: volume on the breakout (1.5x or more), or volume going quiet while still inside; for F&O also OI.

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
- **2.1 ranges above the 50 EMA**: how far price is above the Daily 50 EMA, in normal daily ranges (ATR). Near 0 is buying near value. **Extended** (red) means 4 or more: the stock has run a lot, and it falls hard if the market dips. While the market is weak or falling, extended Ready stocks are moved into the hidden list.
- **Market line** (under the headline): the share of stocks with Daily momentum above zero over the last 6 sessions, plus Nifty's Daily state. **Turning up** = that share rose 5 points or more in 3 sessions and Nifty is not falling; the best time for fresh setups. **Still falling** = it dropped 5 points or more, or Nifty Daily is Bearish; stay light. **No clear turn** = neither.
- **Old**: the label the old TrendFire site would give this stock. See the Old labels button for what each one means.
""")

if view == "Ready":
    if ready:
        show_rows(ready)
    if held:
        why = ("not grade A, or extended" if m["regime"] == "Weak" else "extended")
        with st.expander(f"{len(held)} more Ready, hidden ({why}) while the market is weak"):
            show_rows(held)
else:
    st.caption("Still dipping or coiling. These move to Ready when momentum turns up."
               if view == "Watch" else
               "Already closed above the trigger. The turn is confirmed, so you are a "
               "little late.")
    if shown:
        show_rows(sorted(shown, key=lambda r: {"A": 0, "B": 1, "C": 2}.get(r["grade"], 3)))

if choice == "All":
    st.markdown(f'<div class="tf-small" style="letter-spacing:.08em;text-transform:uppercase;'
                f'font-weight:600;margin-top:18px">Other stages</div>', unsafe_allow_html=True)
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
