"""
Gold (XAU/USD) Telegram bot - GitHub Actions edition.

Each workflow run lives ~5 minutes: it long-polls Telegram for your messages,
checks the gold price every minute, sends scheduled updates and fires alerts.
The next scheduled run takes over, so the bot is "always on".

What you can send the bot:
  /start                 - turn on updates (every 30 min) + help
  /price                 - current price
  /subscribe 15          - updates every 15 min
  /unsubscribe           - stop updates
  2750                   - alert when gold hits 2750 (direction picked for you)
  /alert above 2750      - alert with explicit direction
  /alerts, /clearalerts  - list / remove alerts
"""

import json
import os
import re
import time
from datetime import datetime, timezone

import requests

TOKEN = os.environ["BOT_TOKEN"]
API = f"https://api.telegram.org/bot{TOKEN}"
PRICE_URL = "https://api.gold-api.com/price/XAU"
STATE_FILE = "state.json"
DEFAULT_INTERVAL = 30          # minutes between automatic updates
CHECK_EVERY = 60               # seconds between price checks
RUN_SECONDS = int(os.environ.get("RUN_SECONDS", "285"))

MAX_ALERTS_PER_USER = 10

HELP = (
    "🥇 Gold Price Bot (XAU/USD)\n\n"
    "• Send a price like 2750 and I'll alert you when gold hits it\n"
    "• /price – current price\n"
    "• /subscribe 15 – updates every 15 min\n"
    "• /unsubscribe – stop updates\n"
    "• /alert above|below 2750\n"
    "• /alerts – list alerts\n"
    "• /clearalerts – remove all alerts"
)

NUM = re.compile(r"\d[\d,]*\.?\d*")

# Buttons shown under the Telegram chat box, so everything works by tapping
KEYBOARD = {
    "keyboard": [
        [{"text": "💰 Price"}, {"text": "🔔 My Alerts"}],
        [{"text": "⏸ Stop Updates"}, {"text": "🗑 Clear Alerts"}],
    ],
    "resize_keyboard": True,
}
BUTTONS = {
    "💰 price": "/price",
    "🔔 my alerts": "/alerts",
    "⏸ stop updates": "/unsubscribe",
    "🗑 clear alerts": "/clearalerts",
}
COMMAND_MENU = [
    {"command": "price", "description": "Current gold price"},
    {"command": "subscribe", "description": "Auto updates, e.g. /subscribe 15"},
    {"command": "unsubscribe", "description": "Stop auto updates"},
    {"command": "alerts", "description": "List my price alerts"},
    {"command": "clearalerts", "description": "Remove all alerts"},
    {"command": "help", "description": "How to use the bot"},
]


# ---------- state ----------
def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"offset": 0, "last_price": None, "chats": {}}


def save_state():
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


state = load_state()


# ---------- telegram / price ----------
def tg(method, **params):
    r = requests.post(f"{API}/{method}", json=params, timeout=40)
    r.raise_for_status()
    return r.json()


def send(chat_id, text):
    try:
        tg("sendMessage", chat_id=chat_id, text=text, reply_markup=KEYBOARD)
    except requests.HTTPError as e:
        # 403 = the person blocked the bot or deleted the chat: forget them
        if e.response is not None and e.response.status_code == 403:
            state["chats"].pop(str(chat_id), None)
        print("send failed:", e)
    except Exception as e:
        print("send failed:", e)


def get_price():
    r = requests.get(PRICE_URL, timeout=15)
    r.raise_for_status()
    return float(r.json()["price"])


def price_text(price):
    prev = state.get("last_price")
    change = ""
    if prev:
        diff = price - prev
        arrow = "🟢▲" if diff >= 0 else "🔴▼"
        change = f"\n{arrow} {diff:+.2f} since last update"
    state["last_price"] = price
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"🥇 Gold (XAU/USD)\n💵 ${price:,.2f} per oz{change}\n🕒 {now}"


# ---------- message handling ----------
def new_chat():
    return {"interval": DEFAULT_INTERVAL, "last_sent": 0, "alerts": []}


def handle_text(chat_id, text):
    chat = state["chats"].setdefault(str(chat_id), new_chat())
    low = text.strip().lower()
    if low in BUTTONS:  # a keyboard button was tapped
        low = BUTTONS[low]
        text = low
    parts = low.split()
    cmd = parts[0].split("@")[0] if low.startswith("/") and parts else ""
    args = parts[1:]

    if cmd in ("/start", "/help"):
        chat["interval"] = chat["interval"] or DEFAULT_INTERVAL
        send(chat_id, HELP + f"\n\n✅ Updates every {chat['interval']} min.")
        chat["last_sent"] = 0  # send a price on the next check
        return

    if cmd == "/price":
        try:
            send(chat_id, price_text(get_price()))
        except Exception as e:
            send(chat_id, f"⚠️ Couldn't fetch price: {e}")
        return

    if cmd == "/subscribe":
        try:
            chat["interval"] = max(1, int(args[0])) if args else DEFAULT_INTERVAL
        except ValueError:
            send(chat_id, "Usage: /subscribe 15")
            return
        chat["last_sent"] = 0
        send(chat_id, f"✅ Updates every {chat['interval']} min.")
        return

    if cmd == "/unsubscribe":
        chat["interval"] = 0
        send(chat_id, "🛑 Auto updates stopped. (Alerts still work.)")
        return

    if cmd == "/alerts":
        if not chat["alerts"]:
            send(chat_id, "No active alerts.")
        else:
            lines = [f"{i+1}. {a['dir']} ${a['level']:,.2f}" for i, a in enumerate(chat["alerts"])]
            send(chat_id, "🔔 Your alerts:\n" + "\n".join(lines))
        return

    if cmd == "/clearalerts":
        chat["alerts"] = []
        send(chat_id, "🗑 All alerts cleared.")
        return

    # Anything else: look for a number -> price alert
    m = NUM.search(text)
    if not m:
        send(chat_id, "Send me a price like 2750 and I'll alert you when gold hits it.")
        return
    level = float(m.group().replace(",", ""))
    if level <= 0:
        return
    try:
        current = get_price()
    except Exception as e:
        send(chat_id, f"⚠️ Couldn't fetch price: {e}")
        return

    if "above" in args:
        direction = "above"
    elif "below" in args:
        direction = "below"
    elif abs(level - current) < 0.01:
        send(chat_id, f"🎯 Gold is already at ${current:,.2f}!")
        return
    else:
        direction = "above" if level > current else "below"

    if len(chat["alerts"]) >= MAX_ALERTS_PER_USER:
        send(chat_id, f"⚠️ You already have {MAX_ALERTS_PER_USER} alerts. Clear some first.")
        return
    chat["alerts"].append({"dir": direction, "level": level})
    gap = abs(level - current)
    send(
        chat_id,
        f"🔔 Got it. I'll alert you when gold is {direction} ${level:,.2f}.\n"
        f"Now: ${current:,.2f} ({gap:,.2f} away)",
    )


def poll(timeout):
    try:
        data = tg(
            "getUpdates",
            offset=state["offset"],
            timeout=timeout,
            allowed_updates=["message"],
        )
    except Exception as e:
        print("getUpdates failed:", e)
        time.sleep(3)
        return
    for u in data.get("result", []):
        state["offset"] = u["update_id"] + 1
        msg = u.get("message") or {}
        if msg.get("text"):
            handle_text(msg["chat"]["id"], msg["text"])
    save_state()


# ---------- periodic work ----------
def periodic_check():
    chats = state["chats"]
    if not chats:
        return
    try:
        price = get_price()
    except Exception as e:
        print("price fetch failed:", e)
        return

    now = time.time()
    sent_text = None
    for chat_id, chat in list(chats.items()):
        # fire alerts
        remaining = []
        for a in chat["alerts"]:
            hit = (a["dir"] == "above" and price >= a["level"]) or (
                a["dir"] == "below" and price <= a["level"]
            )
            if hit:
                send(chat_id, f"🚨 Gold is {a['dir']} ${a['level']:,.2f}!\nNow: ${price:,.2f}")
            else:
                remaining.append(a)
        chat["alerts"] = remaining

        # scheduled update
        if chat["interval"] and now - chat["last_sent"] >= chat["interval"] * 60 - 30:
            if sent_text is None:
                sent_text = price_text(price)
            send(chat_id, sent_text)
            chat["last_sent"] = now
    save_state()


def main():
    try:
        tg("setMyCommands", commands=COMMAND_MENU)
    except Exception as e:
        print("setMyCommands failed:", e)
    end = time.time() + RUN_SECONDS
    last_check = 0
    while True:
        remaining = end - time.time()
        if remaining <= 2:
            break
        poll(min(25, int(remaining)))
        if time.time() - last_check >= CHECK_EVERY:
            periodic_check()
            last_check = time.time()
    save_state()
    print("run finished")


if __name__ == "__main__":
    main()
