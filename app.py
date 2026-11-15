"""TrendFire Setups - F&O and Equity views. Reads the scan results; does no scanning itself."""
import datetime as dt
import html
import json
import os

import streamlit as st

PAGES = {
    "F&O": {"file": os.path.join("data", "setups.json"), "workflow": "Setups scan",
            "setups": ["All", "Squeeze Break", "Swing Pullback", "Trend Pullback"],
            "who": "F&O stocks"},
    "Equity": {"file": os.path.join("data", "equity.json"), "workflow": "Equity scan",
               "setups": ["All", "Squeeze Break", "Trend Pullback"],
               "who": "these midcaps and smallcaps"},
    "Unusual": {"file": os.path.join("data", "unusual.json"), "workflow": "Unusual scan"},
}
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
INK, MUTED, RULE = "#1a1a18", "#5c5c57", "#b9b9b0"
GREEN, RED, BLUE = "#0f6b4f", "#b42318", "#1d5fa8"
TREND_COLOR = {"With trend": GREEN, "Early turn": BLUE, "Counter-trend": RED}
BIAS_COLOR = {"Buying": GREEN, "Selling": RED, "Mixed": MUTED}
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"

st.set_page_config(page_title="Setups", layout="centered")
st.markdown(f"""<style>
.block-container {{max-width: 480px; padding: 1.2rem 1rem 3rem;}}
#MainMenu, footer, header {{visibility: hidden;}}
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
    return (
        '<div class="tf-row"><div class="tf-top">'
        f'<a class="tf-sym" href="{chart_link(r["symbol"])}" target="_blank">'
        f'{e(r["symbol"])}</a>{grade_badge(r["grade"])}</div>'
        f'<div class="tf-set">{e(r["setup"])} · '
        f'<span style="color:{tcol}">{e(trend)}</span></div>'
        f'<div class="tf-small">{states}</div>'
        f'<div class="tf-small">{detail}</div>'
        f'<div class="tf-lvl"><span>{left}</span>'
        f'<span style="color:{RED}">Stop {money(r["stop"])}</span></div></div>')


def show_rows(rows):
    st.markdown("".join(row_html(r) for r in rows)
                + f'<div style="border-top:1px solid {RULE}"></div>',
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
    if r.get("vs_high"):
        ctx.append(e(r["vs_high"]))
    if r.get("setup"):
        ctx.append("On Setups: " + e(r["setup"]))
    return (
        '<div class="tf-row"><div class="tf-top">'
        f'<a class="tf-sym" href="{chart_link(r["symbol"])}" target="_blank">'
        f'{e(r["symbol"])}</a><span class="tf-grade" style="border:2px solid {INK};'
        f'color:{INK};font-family:{MONO}">{r["score"]:.1f}</span></div>'
        f'<div class="tf-set"><span style="color:{bcol}">{e(r["bias"])}</span>'
        + (f' · {money(r["close"])}' if r.get("close") else "") + '</div>'
        f'{events}<div class="tf-small" style="margin-top:4px">{"<br>".join(ctx)}</div></div>')


def render_unusual(P):
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
        f'Unusual activity</span><span class="tf-small">{gen.strftime("%a %d %b")} · '
        f'updated {gen.strftime("%H:%M")}</span></div>', unsafe_allow_html=True)
    if age_days >= 3:
        st.warning(f"These results are {age_days} days old. Check the Actions tab on "
                   "GitHub for a failed run.")
    lean = st.radio("Lean", ["All", "Buying", "Selling", "Mixed"], horizontal=True,
                    label_visibility="collapsed", key="unusual_lean")
    rows = [r for r in res["rows"] if lean == "All" or r["bias"] == lean]
    total = len(res["rows"])
    headline = ("Nothing unusual right now." if total == 0 else
                f"{total} of {res['stocks']} stocks show something unusual.")
    st.markdown(
        f'<div style="font-family:Georgia,serif;font-size:22px;font-weight:600;'
        f'color:{INK};margin:6px 0 6px">{headline}</div>'
        f'<div style="font-size:14px;color:{INK};line-height:1.5">Covers the last few '
        f'hours and the last two to three sessions. Strongest first.</div>',
        unsafe_allow_html=True)
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

**The number on the right** is the score. Different kinds of evidence add up. Today counts fully, yesterday 0.7, two days ago 0.5. Two or more means at least one strong event.

**Buying, Selling, Mixed**: which way the events lean. A green bar beside an event leans to buying, red to selling, grey is neutral.

**W and D** are Weekly and Daily momentum: Bullish, Topping, Bearish, or Bottoming.

This page says something is happening. It does not say the move will continue. Check the chart.
""")
    if rows:
        st.markdown("".join(unusual_row(r) for r in rows[:15])
                    + f'<div style="border-top:1px solid {RULE}"></div>',
                    unsafe_allow_html=True)
        if len(rows) > 15:
            with st.expander(f"{len(rows) - 15} more, weaker"):
                st.markdown("".join(unusual_row(r) for r in rows[15:]), unsafe_allow_html=True)
    elif total:
        st.caption("None with this lean.")
    for note in res.get("notes", []):
        st.caption(f"Note: {note}")
    st.caption(
        f"{res['stocks']} F&O stocks scanned. OI as of the F&O file for {res['oi_asof']}. "
        "Refreshes about once an hour in market hours. Thresholds are starting values, "
        "not tested ones. Watchlist only, not investment advice.")


page = st.radio("Market", list(PAGES), horizontal=True, label_visibility="collapsed")
if page == "Unusual":
    render_unusual(PAGES[page])
    st.stop()
P = PAGES[page]
DATA, EQUITY = P["file"], page == "Equity"
if not os.path.exists(DATA):
    st.title(f"{page} Setups")
    st.info(f"No scan results yet. Run the '{P['workflow']}' workflow once from the "
            "GitHub Actions tab.")
    st.stop()

res = load(DATA, os.path.getmtime(DATA))
m = res["market"]
gen = dt.datetime.strptime(res["generated_ist"], "%Y-%m-%d %H:%M")
age_days = (dt.datetime.now(IST).replace(tzinfo=None) - gen).days

st.markdown(
    f'<div style="display:flex;justify-content:space-between;align-items:baseline;'
    f'border-bottom:2px solid {INK};padding-bottom:10px">'
    f'<span style="font-family:Georgia,serif;font-size:26px;font-weight:700;color:{INK}">'
    f'{html.escape(page)} Setups</span><span class="tf-small">{gen.strftime("%a %d %b")} · updated '
    f'{gen.strftime("%H:%M")}</span></div>', unsafe_allow_html=True)
if age_days >= 3:
    st.warning(f"These results are {age_days} days old. Check the Actions tab on "
               "GitHub for a failed run.")

choice = st.radio("Setup", P["setups"], horizontal=True, label_visibility="collapsed",
                  format_func=lambda x: x.split()[0], key=f"setup_{page}")
rows = [r for r in res["rows"] if choice == "All" or r["setup"] == choice]
ready = [r for r in rows if r["stage"] == "Ready"]
held = []
if m["regime"] == "Weak":
    held = [r for r in ready if r["grade"] != "A"]
    ready = [r for r in ready if r["grade"] == "A"]

n = len(ready)
headline = ("No stocks are Ready right now." if n == 0 else
            "1 stock is Ready." if n == 1 else f"{n} stocks are Ready.")
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

SWING_DEF = ("- **Swing** (Swing Pullback): Daily is in an impulse and Hourly has dipped. "
             "The aim is to join a moving stock on a short dip. Suits 3 to 5 day holds.\n")
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
{"" if EQUITY else SWING_DEF}- **Trend** (Trend Pullback): Monthly and Weekly are in an impulse and Daily has dipped. Suits holds of one to three weeks.

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
