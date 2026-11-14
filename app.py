"""TrendFire Setups - one light page. Reads data/setups.json; does no scanning itself."""
import datetime as dt
import html
import json
import os

import streamlit as st

DATA = os.path.join("data", "setups.json")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
INK, MUTED, RULE = "#1a1a18", "#5c5c57", "#b9b9b0"
GREEN, RED, BLUE = "#0f6b4f", "#b42318", "#1d5fa8"
TREND_COLOR = {"With trend": GREEN, "Early turn": BLUE, "Counter-trend": RED}
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
.tf-names a {{font-family: {MONO}; font-size: 13px; color: {INK} !important;}}
</style>""", unsafe_allow_html=True)


@st.cache_data(ttl=120)
def load(mtime):
    with open(DATA) as f:
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
        detail += (f'<br><span style="color:{RED};font-weight:600">'
                   "OI rose while price fell: short build-up</span>")
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


if not os.path.exists(DATA):
    st.title("Setups")
    st.info("No scan results yet. Run the 'Setups scan' workflow once from the "
            "GitHub Actions tab.")
    st.stop()

res = load(os.path.getmtime(DATA))
m = res["market"]
gen = dt.datetime.strptime(res["generated_ist"], "%Y-%m-%d %H:%M")
age_days = (dt.datetime.now(IST).replace(tzinfo=None) - gen).days

st.markdown(
    f'<div style="display:flex;justify-content:space-between;align-items:baseline;'
    f'border-bottom:2px solid {INK};padding-bottom:10px">'
    f'<span style="font-family:Georgia,serif;font-size:26px;font-weight:700;color:{INK}">'
    f'Setups</span><span class="tf-small">{gen.strftime("%a %d %b")} · updated '
    f'{gen.strftime("%H:%M")}</span></div>', unsafe_allow_html=True)
if age_days >= 3:
    st.warning(f"These results are {age_days} days old. Check the Actions tab on "
               "GitHub for a failed run.")

choice = st.radio("Setup", ["All", "Squeeze Break", "Swing Pullback", "Trend Pullback"],
                  horizontal=True, label_visibility="collapsed",
                  format_func=lambda x: x.split()[0])
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
    market = (f"{m['regime']} market: {m['weekly_bull_pct']}% of F&O stocks are "
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
st.caption(
    f"{res['stocks']} F&O stocks scanned. Grade uses volume and OI as of the F&O file "
    f"for {res['oi_asof']}. Daily, Weekly and Monthly use completed days; Hourly uses "
    "completed bars and refreshes about once an hour. Ready sightings are saved for "
    "the scorecard. Watchlist only, not investment advice.")
