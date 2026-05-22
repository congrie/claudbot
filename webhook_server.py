import os
import re
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request

# -------------------------
# ENV + CONFIG
# -------------------------
load_dotenv()

TV_WEBHOOK_SECRET = os.getenv("TV_WEBHOOK_SECRET", "").strip()
if not TV_WEBHOOK_SECRET:
    raise RuntimeError(
        "Missing TV_WEBHOOK_SECRET in .env. Example:\n"
        "TV_WEBHOOK_SECRET=tv_secret_XXXX\n"
    )

PUSHOVER_USER_KEY = os.getenv("PUSHOVER_USER_KEY", "").strip()
PUSHOVER_APP_TOKEN = os.getenv("PUSHOVER_APP_TOKEN", "").strip()

# Push behavior
PUSH_ON_ACCEPT_ONLY = os.getenv("PUSH_ON_ACCEPT_ONLY", "true").lower() == "true"
PUSH_WATCH_EVENTS = os.getenv("PUSH_WATCH_EVENTS", "false").lower() == "true"

# Risk defaults (matches your bot’s philosophy)
STOP_PCT_DEFAULT = float(os.getenv("STOP_PCT_DEFAULT", "0.06"))  # 6%

# Event gating
ACTION_EVENTS = {"VWAP_RECLAIM_ACCEPTED"}     # actionable: run calc + push
WATCH_EVENTS = {"VWAP_HOLD_COMPRESSION"}      # watchlist only (log; optional push)

# -------------------------
# APP
# -------------------------
app = Flask(__name__)


# -------------------------
# HELPERS
# -------------------------
def now_ts() -> str:
    return datetime.now().isoformat(timespec="seconds")


def safe_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def log_line(text: str) -> None:
    os.makedirs("logs", exist_ok=True)
    with open(r"logs\trades.log", "a", encoding="utf-8") as f:
        f.write(f"\n[{now_ts()}] {text}\n")


def send_pushover(title: str, message: str) -> Tuple[bool, str]:
    """
    Sends a Pushover notification. Returns (ok, error_message).
    """
    if not (PUSHOVER_USER_KEY and PUSHOVER_APP_TOKEN):
        return False, "Pushover not configured (missing PUSHOVER_USER_KEY or PUSHOVER_APP_TOKEN)."

    try:
        resp = requests.post(
            "https://api.pushover.net/1/messages.json",
            timeout=10,
            data={
                "token": PUSHOVER_APP_TOKEN,
                "user": PUSHOVER_USER_KEY,
                "title": title[:250],
                "message": message[:1024],
                # optional: "priority": 0,
            },
        )
        if resp.status_code >= 200 and resp.status_code < 300:
            return True, ""
        return False, f"Pushover error {resp.status_code}: {resp.text}"
    except Exception as e:
        return False, f"Pushover exception: {e}"


def calc_entry_and_stop(price: float, stop_pct: float) -> Tuple[str, str]:
    """
    For your workflow: entry is a LIMIT at current/close price;
    stop is percent-based.
    """
    entry = price
    stop = price * (1.0 - stop_pct)

    # format to 2 decimals for equities
    return f"{entry:.2f}", f"{stop:.2f}"


def calc_targets(price: float, stop_pct: float) -> Tuple[str, str]:
    """
    R-multiple targets:
      T1 = +1R = +stop_pct
      T2 = +2R = +2*stop_pct
    """
    t1 = price * (1.0 + stop_pct)
    t2 = price * (1.0 + 2.0 * stop_pct)
    return f"{t1:.2f}", f"{t2:.2f}"


# -------------------------
# ROUTES
# -------------------------
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "status": "webhook alive"})


@app.route("/tv", methods=["POST"])
def tv_webhook():
    # 1) Parse JSON
    data: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
    if not data:
        return jsonify({"ok": False, "error": "missing_json"}), 400

    # 2) Auth
    secret = str(data.get("secret", "")).strip()
    if secret != TV_WEBHOOK_SECRET:
        log_line(f"AUTH_FAIL payload={data}")
        return jsonify({"ok": False, "error": "bad_secret"}), 403

    # 3) Pull fields (Pine script provides these)
    ticker = str(data.get("ticker", "UNKNOWN")).strip()
    tf = str(data.get("timeframe", "UNKNOWN")).strip()
    event = str(data.get("event", "")).strip()

    price = safe_float(data.get("price"))
    vwap = safe_float(data.get("vwap"))
    atr = safe_float(data.get("atr"))
    atr_ma = safe_float(data.get("atr_ma"))

    # 4) Log raw event
    log_line(f"IN event={event} ticker={ticker} tf={tf} price={price} vwap={vwap} atr={atr} atr_ma={atr_ma}")

    # 5) Event gating: WATCH vs ACTION vs ignore
    if event in WATCH_EVENTS:
        # Watch events are informational: log them. Optional push.
        if PUSH_WATCH_EVENTS:
            msg = (
                f"{ticker} {tf}\n"
                f"EVENT: {event}\n"
                f"Close: {price if price is not None else 'UNKNOWN'}\n"
                f"VWAP: {vwap if vwap is not None else 'UNKNOWN'}\n"
                f"ATR: {atr if atr is not None else 'UNKNOWN'} (MA {atr_ma if atr_ma is not None else 'UNKNOWN'})\n"
                f"Note: VWAP holding + volatility compressing (breakout watch)"
            )
            ok, err = send_pushover(title=f"{ticker} Watch", message=msg)
            if not ok:
                log_line(f"PUSH_FAIL watch err={err}")

        return jsonify({"ok": True, "handled": "watch_logged", "ticker": ticker, "timeframe": tf})

    if event not in ACTION_EVENTS:
        return jsonify({"ok": True, "handled": "ignored", "ticker": ticker, "timeframe": tf})

    # 6) ACTION event: calculate entry/stop/targets and push
    if price is None:
        log_line("ACTION_FAIL missing price")
        return jsonify({"ok": False, "handled": "missing_price"}), 400

    stop_pct = STOP_PCT_DEFAULT
    entry_str, stop_str = calc_entry_and_stop(price, stop_pct)
    t1_str, t2_str = calc_targets(price, stop_pct)

    # If you only want pushes on action events, that’s already enforced by gating.
    # PUSH_ON_ACCEPT_ONLY is effectively always true here, but we respect it anyway.
    if (not PUSH_ON_ACCEPT_ONLY) or True:
        msg = (
            f"{ticker} {tf}\n"
            f"DECISION: ACCEPT\n"
            f"EVENT: {event}\n\n"
            f"Close/Ref Price: {price:.2f}\n"
            f"Entry (LIMIT): {entry_str}\n"
            f"Stop ({stop_pct*100:.0f}%): {stop_str}\n"
            f"T1 (1R): {t1_str}\n"
            f"T2 (2R): {t2_str}\n\n"
            f"VWAP: {vwap if vwap is not None else 'n/a'}\n"
            f"Shares: 5 (starter)\n"
            f"Note: Do not chase; if not filled, cancel per candle rule."
        )
        ok, err = send_pushover(title=f"{ticker} Swing Alert", message=msg)
        if not ok:
            log_line(f"PUSH_FAIL action err={err}")

    return jsonify({"ok": True, "handled": "action_pushed", "ticker": ticker, "timeframe": tf})


if __name__ == "__main__":
    # Bind to localhost:5000
    app.run(host="127.0.0.1", port=5000, debug=False)
