# NSE Screener — Codebase Map

Quick reference so any edit targets the smallest possible file.
**Do NOT add logic to app.py** — it is wiring-only.

## File → Responsibility

| File | What lives here | Typical edit reason |
|------|----------------|---------------------|
| `app.py` | Flask init, blueprint registration, before_request | Add a new routes file |
| `core/config.py` | Constants, TTLs, shared in-memory stores | Change cache TTL, add global state |
| `core/data.py` | `fetch_yahoo()`, `get_live_price()`, `parse_ohlcv()` | Change data source, bar format |
| `core/indicators.py` | EMA, RSI, ATR, VWAP, `compute_indicators()` | Add indicator, tune math |
| `core/ai.py` | `gemini()` wrapper | Switch AI provider, change temp/tokens |
| `core/patterns.py` | Candle patterns, FVGs, Order Blocks, S/R zones | Add pattern, tune thresholds |
| `core/alerts.py` | WhatsApp sending, market-hours check, alert poller | Change alert format, market hours |
| `routes/charts.py` | `/api/chart`, `/api/chart-extended`, `/api/chart-5m` | Add chart endpoint |
| `routes/analyze.py` | `/api/analyze` — strategy, backtest, Pine Script | Change strategy prompt, Pine template |
| `routes/signals.py` | `/api/signals`, `/api/market-strategy`, `/api/sr-zones` | Add signal type, tune confidence |
| `routes/nifty5min.py`| `/api/nifty-5min-signal`, `/api/nifty-5min-backtest` | Change 5-min rules |
| `routes/misc.py` | WhatsApp routes, TV webhook, FnO OI, health, visitors | Add utility route |

## API Surface

```
GET  /                          → nse_screener_live.html
GET  /api/health
GET  /api/visitors
GET  /api/chart?symbol=
GET  /api/chart-extended?symbol=&range=
GET  /api/chart-5m?symbol=&range=
GET  /api/fno-oi?symbol=
GET  /api/nifty-5min-signal?symbol=&range=
GET  /api/nifty-5min-backtest?symbol=&range=
GET  /api/tv-signals?limit=&symbol=
POST /api/analyze           {symbol, timeframe}
POST /api/signals           {symbol, timeframe}
POST /api/market-strategy   {symbol, timeframe, type}
POST /api/sr-zones          {symbol, timeframe, with_ai}
POST /api/sr-zones/batch    {symbols[], timeframe}
POST /api/tv-signal         {symbol, action, price, timeframe}
POST /api/tv-signals/clear
POST /api/wa/test           {phone, key}
POST /api/wa/alerts         {phone, key, alerts[]}
GET  /api/wa/triggered?phone=
```

## Shared state (core/config.py)

- `_stock_cache`  — Yahoo OHLCV cache `{cache_key: (ts, bytes)}`
- `_tv_signals`   — Last 200 TradingView webhook signals (protected by `_tv_lock`)
- `_wa_store`     — WhatsApp alert registry `{phone: {key, alerts[]}}` (protected by `_wa_lock`)
- `_visitors`     — Visitor tracking dict (protected by `_visitor_lock`)

## Adding a new feature (token-efficient workflow)

1. Identify the right file from the table above — read only that file
2. Make the change
3. If it's a new route file, register its blueprint in `app.py` (4-line change)
4. Never touch `nse_screener_live.html` for backend-only changes
