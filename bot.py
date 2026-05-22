import os
from datetime import datetime

from dotenv import load_dotenv
from anthropic import Anthropic

# =========================
# MODEL + RISK PARAMETERS (Micron-style)
# =========================
MODEL = "claude-sonnet-4-5-20250929"

STARTER_SHARES = 5
STOP_PCT_DEFAULT = 0.06   # 6%
STOP_PCT_MAX = 0.08       # 8% hard cap

MAX_TRADES_PER_DAY = 3
MAX_DAILY_LOSS_R = 2
MAX_OPEN_TRADES = 1

# =========================
# TIMEFRAME DEFINITIONS
# =========================
TIMEFRAME_MINUTES = {
    "1m": 1,
    "3m": 3,
    "5m": 5,
    "10m": 10,
    "15m": 15,
    "30m": 30,
    "45m": 45,
    "1H": 60,
    "2H": 120,
    "4H": 240,
    "1D": 1440,
}

# Require next-candle hold confirmation by timeframe
# (You can keep 1H True for swing confirmation.)
REQUIRE_NEXT_CANDLE_HOLD = {
    "1m": True,
    "3m": True,
    "5m": True,
    "10m": True,
    "15m": True,
    "30m": True,
    "45m": True,
    "1H": True,
    "2H": True,
    "4H": True,
    "1D": False,
}


# =========================
# SETUP
# =========================
load_dotenv()

API_KEY = os.getenv("ANTHROPIC_API_KEY")
if not API_KEY:
    raise RuntimeError(
        "Missing ANTHROPIC_API_KEY. Create a .env file in this folder with:\n"
        "ANTHROPIC_API_KEY=your_key_here"
    )

client = Anthropic(api_key=API_KEY)


def _is_non_day_trade(timeframe: str) -> bool:
    """Treat >= 1H as non-day trade intent."""
    return TIMEFRAME_MINUTES.get(timeframe, 5) >= 60


def analyze_alert(alert_text: str, timeframe: str = "1H") -> str:
    """
    Produces an ACCEPT/REJECT/WAIT decision constrained by Micron-style risk rules.

    IMPORTANT CHANGE:
    - VWAP numeric value may be UNKNOWN. Do NOT reject solely because VWAP is missing.
    - Treat TradingView VWAP alerts as event-based truth: reclaim/hold/reject already implies
      the price-vs-VWAP relationship.
    """
    non_day = _is_non_day_trade(timeframe)
    hold_required = REQUIRE_NEXT_CANDLE_HOLD.get(timeframe, True)

    prompt = f"""
You are a disciplined VWAP trading assistant. Follow rules exactly. Do not predict.

RISK RULES (NON-NEGOTIABLE):
- Shares are fixed: STARTER_SHARES = {STARTER_SHARES}. Never recommend more.
- Default stop distance is {int(STOP_PCT_DEFAULT*100)}% of entry.
- Hard maximum stop distance is {int(STOP_PCT_MAX*100)}% of entry.
- If the setup requires a stop wider than {int(STOP_PCT_MAX*100)}%, output DECISION: REJECT and explain why.
- Targets must be R-multiples based on stop_pct:
  - T1 = +1R (trim 50%)
  - T2 = +2R (trim 25%)
  - Runner = 25% trail (use 1H structure; avoid intraday micromanagement for swing)
- Constraints:
  - Max open trades: {MAX_OPEN_TRADES}
  - Max trades/day: {MAX_TRADES_PER_DAY}
  - Stop for the day at -{MAX_DAILY_LOSS_R}R

SIGNAL RULES:
- VWAP numeric level may be UNKNOWN in the alert payload. This is normal. Do NOT reject because VWAP is missing.
- Treat the alert's VWAP SIGNAL as the truth about price relative to VWAP:
  - If signal indicates REJECT/BELOW VWAP or FAILED HOLD => DECISION must be REJECT.
  - If signal indicates RECLAIM PENDING and next-candle hold is required ({hold_required}) => DECISION must be WAIT.
  - If signal indicates RECLAIM CONFIRMED / HOLD CONFIRMED => you may ACCEPT if trade plan fits risk rules.
- If non-day trade intent (>= 1H): Provide swing/position guidance (less intraday management).

FORMAT RULE (STRICT):
- Output must be plain text only (no markdown, no **bold**, no bullet formatting that changes labels).
- The first line must be exactly: DECISION: ACCEPT or DECISION: REJECT or DECISION: WAIT
- The labels must appear exactly as written: DECISION:, REASON:, ENTRY:, STOP (include stop_pct):, SHARES:, TARGETS:, NOTES:

ENTRY/STOP/TARGETS:
- If entry price is present, use it. If missing, set ENTRY to "use current price at alert time" and use formulas.
- Choose stop_pct = 0.06 preferred; 0.08 only if needed; never exceed 0.08.
- Because VWAP value may be unknown, stops must be expressed as:
  - Percent-based stop below entry (stop_pct), AND
  - A structure note (e.g., below reclaim/hold bar low) if available (but do not require it).
- Formulas:
  - StopPrice = Entry * (1 - stop_pct)
  - T1 = Entry * (1 + stop_pct)
  - T2 = Entry * (1 + 2*stop_pct)

OUTPUT FORMAT (exact labels, in order):
DECISION: ACCEPT or REJECT or WAIT
REASON:
ENTRY:
STOP (include stop_pct):
SHARES:
TARGETS:
NOTES:

ALERT (timeframe {timeframe}):
{alert_text}
""".strip()

    resp = client.messages.create(
        model=MODEL,
        max_tokens=650,
        messages=[{"role": "user", "content": prompt}],
    )

    return resp.content[0].text


def log_result(alert_text: str, output_text: str) -> None:
    os.makedirs("logs", exist_ok=True)
    with open(r"logs\trades.log", "a", encoding="utf-8") as f:
        f.write(f"\n[{datetime.now().isoformat(timespec='seconds')}]\n")
        f.write("=== ALERT ===\n")
        f.write(alert_text.strip() + "\n")
        f.write("=== OUTPUT ===\n")
        f.write(output_text.strip() + "\n")
