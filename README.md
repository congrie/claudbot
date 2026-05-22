# Claudbot TradingView Webhook

Small Flask app that receives TradingView JSON alerts at `/tv`, checks a shared secret, logs the event, and optionally sends Pushover notifications for configured VWAP events.

## Structure

- `webhook_server.py` - Flask app, `/health` and `/tv` routes, event gating, Pushover notifications.
- `bot.py` - Anthropic-based alert analysis helper. It is not imported by the Flask webhook path.
- `requirements.txt` - Python dependencies.
- `.env.example` - environment variable template.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python webhook_server.py
```

The app binds to `127.0.0.1:5000`. Use ngrok or another HTTPS tunnel to expose it to TradingView.

## Environment

Required:

- `TV_WEBHOOK_SECRET` - shared secret expected in the TradingView payload as `secret`.

Optional:

- `PUSHOVER_USER_KEY` - Pushover user key.
- `PUSHOVER_APP_TOKEN` - Pushover app token.
- `PUSH_ON_ACCEPT_ONLY` - defaults to `true`.
- `PUSH_WATCH_EVENTS` - defaults to `false`.
- `STOP_PCT_DEFAULT` - defaults to `0.06`.
- `ANTHROPIC_API_KEY` - required only when running `bot.py` directly.

## TradingView Payload

Keep the existing JSON shape:

```json
{
  "secret": "tv_secret_XXXX",
  "ticker": "MU",
  "timeframe": "1H",
  "event": "VWAP_RECLAIM_ACCEPTED",
  "price": "371.33",
  "vwap": "370.10",
  "atr": "4.20",
  "atr_ma": "5.00"
}
```

Current webhook behavior:

- `VWAP_RECLAIM_ACCEPTED` - action event; calculates entry, stop, targets, then sends Pushover if configured.
- `VWAP_HOLD_COMPRESSION` - watch event; logs only unless `PUSH_WATCH_EVENTS=true`.
- Other event values - accepted and logged as ignored.

The route returns JSON responses and preserves compatibility with TradingView alerts that send numeric fields as strings.

## Notes

- Logs are written to `logs/trades.log`.
- Do not commit `.env`, backup codes, or live webhook secrets.
- The committed `ngrok.exe` is not required to run the Flask app if ngrok is installed separately.
