#!/usr/bin/env python3
"""
TradeReady Telegram alerts. Runs after every F&O scan (hourly in market hours).

1. Checks every F&O stock's VK System reading against the last scan and sends one message
   listing the stocks that just changed to:
     BUY NOW (Hourly turned positive)      CONFIRMED, Buy ON (Hourly positive 2 bars)
     SELL NOW                              CONFIRMED, Sell ON
2. Reads your messages to the bot: status, sells off/on, pause/resume, help.

Needs the GitHub secret TELEGRAM_BOT_TOKEN. The first person to message the bot becomes
its owner; messages from anyone else are ignored.
State lives in data/telegram.json (owner, settings, last readings of every stock).

  python tg_alerts.py             # normal run
  python tg_alerts.py --selftest  # offline check
"""
import html
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

DATA = "data"
STATE = os.path.join(DATA, "telegram.json")
SITE = "https://tradeready.streamlit.app"
API = "https://api.telegram.org/bot{token}/{method}"

HELP = ("<b>TradeReady bot</b>\n"
        "After every hourly F&amp;O scan I check all F&amp;O stocks and message you the "
        "ones whose VK reading just changed to:\n"
        "🟢 can buy now (Hourly turned positive, wait for confirmation)\n"
        "✅ CONFIRMED, Buy ON (Hourly positive 2 bars)\n"
        "🔴 can sell now · ⛔ CONFIRMED, Sell ON\n\n"
        "Commands:\n"
        "status  - every stock that is BUY NOW or CONFIRMED right now\n"
        "sells off / sells on  - hide or show the sell side\n"
        "pause / resume  - stop or restart alerts\n\n"
        "Scans run 10:20 to 15:20 every hour, plus 16:15 and 19:30, so replies can take up "
        "to an hour.")

GROUPS = [   # (key, heading, test on (before, now))
    ("buy", "🟢 Can buy now (wait for confirmation)",
     lambda b, n: n["final"] == "BUY NOW" and b["final"] != "BUY NOW"),
    ("buy_ok", "✅ CONFIRMED, Buy ON",
     lambda b, n: n["confirmed"] == "CONFIRMED — Buy ON" and b["confirmed"] != n["confirmed"]),
    ("sell", "🔴 Can sell now (wait for confirmation)",
     lambda b, n: n["final"] == "SELL NOW" and b["final"] != "SELL NOW"),
    ("sell_ok", "⛔ CONFIRMED, Sell ON",
     lambda b, n: n["confirmed"] == "CONFIRMED — Sell ON" and b["confirmed"] != n["confirmed"]),
]
MAX_CHARS = 3800

def tg(token, method, **params):
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(API.format(token=token, method=method), data=data,
                                timeout=20) as r:
        return json.loads(r.read().decode())


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def vk_map(setups):
    """symbol -> VK reading, from the F&O scan."""
    out = {}
    for label, items in (setups or {}).get("vk_labels", {}).items():
        for x in items:
            out[x["symbol"]] = dict(x, final=label)
    return out


def on_setups(setups, sym):
    out = []
    for r in (setups or {}).get("rows", []) + (setups or {}).get("overspill", []):
        if r["symbol"] == sym and r["stage"] in ("Ready", "Triggered", "Watch"):
            lvl = (f", above {r['trigger']:,.2f}" if r.get("trigger") else "") + (
                f", stop {r['stop']:,.2f}" if r.get("stop") else "")
            out.append(f"{r['setup']} {r['stage']} ({r['grade']}{lvl})")
    return out


def line(sym, v, setups, sectors):
    """One compact line per stock."""
    e = html.escape
    url = f"https://www.tradingview.com/chart/?symbol=NSE:{urllib.parse.quote(sym)}"
    bits = [f'• <a href="{url}"><b>{e(sym)}</b></a> {v.get("close", 0):,.1f}']
    s = on_setups(setups, sym)
    if s:
        bits.append(e(s[0]))
    sec = sectors.get(sym) or {}
    if sec.get("sector"):
        bits.append(e(f"{sec['sector']} {sec.get('stage', '')}".strip()))
    bits.append(e(v["ma"]))
    return " · ".join(bits)


def digest(found, setups, sectors, when, show_sells):
    """Messages (split to fit Telegram) listing the new changes."""
    parts = []
    for key, head, _ in GROUPS:
        if key.startswith("sell") and not show_sells:
            continue
        names = found.get(key, [])
        if names:
            parts.append(f"<b>{head}: {len(names)}</b>\n"
                         + "\n".join(line(s, v, setups, sectors) for s, v in names))
    if not parts:
        return []
    msgs, cur = [], f"🔔 <b>TradeReady</b> · {html.escape(when)} scan"
    for p in parts:
        for chunk in p.split("\n"):
            if len(cur) + len(chunk) + 2 > MAX_CHARS:
                msgs.append(cur)
                cur = ""
            cur += ("\n\n" if chunk.startswith("<b>") else "\n") + chunk
    msgs.append(cur.strip())
    return msgs


def strategy7(setups, sectors):
    """Breakout + Trend Overspill: a recent range breakout that still holds, Weekly or Daily
    in Impulse, Hourly Overspill Ready with Dip quality A, not extended, sector not Lagging.
    (The same rule as Strategy 7 on the site.)"""
    m = (setups or {}).get("market", {})
    if m.get("turn") == "Falling":
        return {}
    brk = (setups or {}).get("recent_breakouts", {})
    out = {}
    for r in (setups or {}).get("overspill", []):
        b, stt = brk.get(r["symbol"]), r.get("states") or {}
        if (r["setup"] == "Hourly Overspill" and r["stage"] == "Ready" and r["grade"] == "A"
                and b and (r.get("ext") is None or r["ext"] < 4.0)
                and "Impulse" in (stt.get("W"), stt.get("D"))
                and (sectors.get(r["symbol"]) or {}).get("stage") != "Lagging"):
            out[r["symbol"]] = (r, b)
    return out


def s7_message(new, setups, sectors, when):
    e = html.escape
    hot = ((setups or {}).get("market", {}).get("daily_bull_hist") or [0])[-1] >= 85
    lines = [f"⭐ <b>Strategy 7 · Breakout + Trend Overspill: {len(new)} new</b> · "
             f"{e(when)} scan" + (" · market overheated, half size" if hot else "")]
    for sym, (r, b) in new:
        url = f"https://www.tradingview.com/chart/?symbol=NSE:{urllib.parse.quote(sym)}"
        sec = sectors.get(sym) or {}
        lines.append(
            f'• <a href="{url}"><b>{e(sym)}</b></a> · buy above {r["trigger"]:,.2f} · '
            f'stop {r["stop"]:,.2f} · broke a {b["weeks"]}-week {e(b["kind"])} '
            f'{b["days"]} sessions ago' + (f' · {e(sec["sector"])} {e(sec.get("stage", ""))}'
                                           if sec.get("sector") else ""))
    return "\n".join(lines)


def handle_command(text, state):
    words = text.split()
    if not words:
        return None
    cmd = " ".join(words).lower().lstrip("/")
    if cmd in ("start", "help"):
        return HELP
    if cmd in ("sells off", "sell off"):
        state["sells"] = False
        return "OK, buy side only."
    if cmd in ("sells on", "sell on"):
        state["sells"] = True
        return "OK, sell side included."
    if cmd in ("pause", "stop"):
        state["paused"] = True
        return "⏸ Alerts paused. Send resume to restart."
    if cmd in ("resume", "start alerts"):
        state["paused"] = False
        return "▶️ Alerts back on."
    if cmd == "status":
        return "STATUS"
    return "Didn't get that.\n\n" + HELP


def find_changes(state, vks):
    """Every stock whose VK reading changed into one of the GROUPS since the last run."""
    last = state.get("last", {})
    found = {}
    for sym, v in sorted(vks.items()):
        now = {"final": v["final"], "confirmed": v["confirmed"]}
        before = last.get(sym)
        if before:
            for key, _, test in GROUPS:
                if test(before, now):
                    found.setdefault(key, []).append((sym, v))
    state["last"] = {s: {"final": v["final"], "confirmed": v["confirmed"]}
                     for s, v in vks.items()}
    return found


def status_text(vks, setups, sectors, show_sells):
    found = {"buy": [], "buy_ok": [], "sell": [], "sell_ok": []}
    for s, v in sorted(vks.items()):
        if v["confirmed"] == "CONFIRMED — Buy ON":
            found["buy_ok"].append((s, v))
        elif v["final"] == "BUY NOW":
            found["buy"].append((s, v))
        if v["confirmed"] == "CONFIRMED — Sell ON":
            found["sell_ok"].append((s, v))
        elif v["final"] == "SELL NOW":
            found["sell"].append((s, v))
    return digest(found, setups, sectors, "Right now", show_sells) or [
        "Nothing is BUY NOW or CONFIRMED right now."]


def run(send, get_updates, state, setups, sectors, when):
    vks = vk_map(setups)
    replies = []
    for u in get_updates(state.get("offset", 0)):
        state["offset"] = u["update_id"] + 1
        msg = u.get("message") or {}
        chat = (msg.get("chat") or {}).get("id")
        if chat is None:
            continue
        if state.get("owner") is None:
            state["owner"] = chat
        if chat != state["owner"]:
            continue
        r = handle_command(msg.get("text", ""), state)
        if r == "STATUS":
            replies += status_text(vks, setups, sectors, state.get("sells", True))
        elif r:
            replies.append(r)
    found = find_changes(state, vks) if vks else {}
    s7 = strategy7(setups, sectors)
    new7 = [(k, v) for k, v in sorted(s7.items()) if k not in set(state.get("s7", []))]
    state["s7"] = sorted(s7)
    alerts = []
    if state.get("owner") is not None:
        if not state.get("welcomed") and vks:
            state["welcomed"] = True
            n_buy = sum(v["final"] == "BUY NOW" for v in vks.values())
            n_ok = sum(v["confirmed"] == "CONFIRMED — Buy ON" for v in vks.values())
            alerts = [f"✅ <b>TradeReady alerts are on.</b> Watching all {len(vks)} F&amp;O "
                      f"stocks. Right now {n_buy} are BUY NOW and {n_ok} are CONFIRMED (send "
                      f"status to see them). From the next scan I will message you only the "
                      f"new ones."]
        elif not state.get("paused"):
            alerts = digest(found, setups, sectors, when, state.get("sells", True))
            if new7:
                alerts.append(s7_message(new7, setups, sectors, when))
        for t in replies + alerts:
            send(state["owner"], t)
    return replies, alerts


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("[error] TELEGRAM_BOT_TOKEN secret not set (or empty): no Telegram alerts.")
        return
    state = load(STATE, {})
    setups = load(os.path.join(DATA, "setups.json"), {})

    def send(chat, text):
        try:
            tg(token, "sendMessage", chat_id=chat, text=text, parse_mode="HTML",
               disable_web_page_preview="true")
        except Exception as e:
            print(f"[warn] send failed: {e}")

    def get_updates(offset):
        try:
            res = tg(token, "getUpdates", offset=offset, timeout=0)
        except urllib.error.HTTPError as e:
            hint = {401: "Telegram rejected the token: copy it again from BotFather into "
                         "the TELEGRAM_BOT_TOKEN secret",
                    404: "the token looks malformed: it should look like 123456789:AAH..."
                    }.get(e.code, "Telegram refused the request")
            print(f"[error] HTTP {e.code}: {hint}")
            raise SystemExit(0)
        except Exception as e:
            print(f"[error] could not reach Telegram: {e}")
            raise SystemExit(0)
        print(f"Telegram OK: {len(res.get('result', []))} new messages")
        return res.get("result", [])

    sectors = load(os.path.join(DATA, "sectors.json"), {}).get("map", {})
    when = setups.get("generated_ist", "")[-5:]
    replies, alerts = run(send, get_updates, state, setups, sectors, when)
    os.makedirs(DATA, exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)
    print(f"{len(replies)} replies, {len(alerts)} alert messages")


def selftest():
    def vk(sym, final, conf="Not confirmed"):
        return {"symbol": sym, "close": 4789.0, "states": {}, "signal": "x", "ma": "ONLY BUY",
                "advice": "x", "daily": "x", "hourly": "x", "confirmed": conf}

    def setups_of(**finals):
        g = {}
        for sym, (final, conf) in finals.items():
            g.setdefault(final, []).append(vk(sym, final, conf))
        return {"generated_ist": "2026-11-17 11:20", "vk_labels": g, "overspill": [],
                "rows": [{"symbol": "HAL", "setup": "Daily Overspill", "stage": "Ready",
                          "grade": "B", "trigger": 4881.86, "stop": 4727.13}]}
    nc = "Not confirmed"
    sectors = {"HAL": {"sector": "Capital Goods", "stage": "Improving"}}
    sent, inbox = [], []
    send = lambda chat, t: sent.append((chat, t))
    upd = lambda off: [u for u in inbox if u["update_id"] >= off]
    msg = lambda i, chat, t: {"update_id": i, "message": {"chat": {"id": chat}, "text": t}}
    state = {}

    s1 = setups_of(HAL=("ALMOST READY (Bullish)", nc), SBIN=("BUY NOW", nc), TCS=("NO SIGNAL", nc))
    inbox[:] = [msg(1, 111, "/start"), msg(2, 999, "status")]
    run(send, upd, state, s1, sectors, "11:20")
    assert state["owner"] == 111 and len(sent) == 2 and "alerts are on" in sent[1][1], sent
    assert "1 are BUY NOW" in sent[1][1]
    sent.clear()

    run(send, upd, state, s1, sectors, "12:20")       # nothing changed
    assert sent == [], sent

    s2 = setups_of(HAL=("BUY NOW", nc), SBIN=("BUY NOW", "CONFIRMED — Buy ON"),
                   TCS=("SELL NOW", nc))
    run(send, upd, state, s2, sectors, "13:20")
    t = sent[0][1]
    assert len(sent) == 1 and "Can buy now" in t and "HAL" in t and "CONFIRMED, Buy ON: 1" in t, t
    assert "Daily Overspill Ready" in t and "Capital Goods Improving" in t and "Can sell now" in t
    assert "SBIN" in t.split("CONFIRMED, Buy ON")[1]
    sent.clear()

    inbox.append(msg(3, 111, "sells off"))
    s3 = setups_of(HAL=("BUY NOW", nc), SBIN=("BUY NOW", "CONFIRMED — Buy ON"),
                   TCS=("NO SIGNAL", nc), INFY=("NO SIGNAL", nc))
    run(send, upd, state, s3, sectors, "14:20")
    assert sent == [(111, "OK, buy side only.")], sent
    sent.clear()
    s4 = setups_of(HAL=("BUY NOW", nc), SBIN=("NO SIGNAL", nc), TCS=("SELL NOW", nc),
                   INFY=("NO SIGNAL", nc))
    run(send, upd, state, s4, sectors, "15:20")
    assert sent == [], sent                           # only a sell change, sells are off

    inbox.append(msg(4, 111, "status"))
    run(send, upd, state, s4, sectors, "16:15")
    assert len(sent) == 1 and "HAL" in sent[0][1] and "Right now" in sent[0][1], sent
    sent.clear()

    inbox.append(msg(5, 111, "pause"))
    s5 = setups_of(HAL=("NO SIGNAL", nc), SBIN=("BUY NOW", nc), TCS=("NO SIGNAL", nc),
                   INFY=("NO SIGNAL", nc))
    run(send, upd, state, s5, sectors, "19:30")
    assert sent == [(111, "⏸ Alerts paused. Send resume to restart.")], sent
    assert state["offset"] == 6

    big = {"vk_labels": {"NO SIGNAL": [vk(f"S{i:03d}", "NO SIGNAL") for i in range(300)]}}
    st2 = {"owner": 1}
    run(send, upd, st2, big, {}, "x")
    big2 = {"vk_labels": {"BUY NOW": [vk(f"S{i:03d}", "BUY NOW") for i in range(300)]}}
    sent.clear()
    run(send, lambda off: [], st2, big2, {}, "y")
    assert len(sent) >= 2 and all(len(t) <= 4096 for _, t in sent), [len(t) for _, t in sent]
    # Strategy 7 pings once per new stock
    st7 = {"owner": 1, "welcomed": True}
    base = {"generated_ist": "2026-11-17 11:20", "market": {"turn": "Turning up",
            "daily_bull_hist": [60, 70]}, "vk_labels": {},
            "recent_breakouts": {"PRESTIGE": {"days": 3, "date": "2026-11-12", "level": 1654.9,
                                              "kind": "flat top", "weeks": 13}},
            "overspill": [{"symbol": "PRESTIGE", "setup": "Hourly Overspill", "stage": "Ready",
                           "grade": "A", "ext": 2.8, "states": {"W": "Bottoming", "D": "Impulse"},
                           "trigger": 1676.2, "stop": 1640.0}]}
    out7 = []
    run(lambda c, t: out7.append(t), lambda off: [], st7, base,
        {"PRESTIGE": {"sector": "Realty", "stage": "Leading"}}, "11:20")
    assert len(out7) == 1 and "Strategy 7" in out7[0] and "PRESTIGE" in out7[0], out7
    assert "buy above 1,676.20" in out7[0] and "13-week flat top" in out7[0]
    out7.clear()
    run(lambda c, t: out7.append(t), lambda off: [], st7, base, {}, "12:20")
    assert out7 == [], out7                                 # same stock: no repeat
    base["overspill"][0]["grade"] = "B"
    run(lambda c, t: out7.append(t), lambda off: [], st7, base, {}, "13:20")
    assert out7 == [] and st7["s7"] == []
    print("SELFTEST PASSED")


if __name__ == "__main__":
    selftest() if "--selftest" in sys.argv else main()
