"""
5-min body expansion breakout signal + full backtest.
/api/nifty-5min-signal    GET — latest signal
/api/nifty-5min-backtest  GET — 30/60-day backtest

Edit this file to change signal rules (lookback, EOD cutoff, R:R targets).
"""
from datetime import datetime
from flask import Blueprint, request as freq, jsonify
from ..core.data import fetch_yahoo, parse_ohlcv

bp = Blueprint("nifty5min", __name__)

IST_OFF  = int(5.5 * 3600)
EOD_MINS = 15 * 60 + 25   # 15:25 IST

def _ist_mins(ts): dt = datetime.utcfromtimestamp(ts + IST_OFF); return dt.hour*60 + dt.minute
def _ist_date(ts): return datetime.utcfromtimestamp(ts + IST_OFF).strftime("%Y-%m-%d")
def _ist_str(ts):  return datetime.utcfromtimestamp(ts + IST_OFF).strftime("%Y-%m-%d %H:%M")

def to_ist_str(ts):
    return datetime.utcfromtimestamp(ts + IST_OFF).strftime("%Y-%m-%d %H:%M") if ts else ""


def _build_context(bars, bodies):
    return [{"time": to_ist_str(b["t"]) if "t" in b else b["d"],
             "o": round(b["o"],2), "h": round(b["h"],2),
             "l": round(b["l"],2), "c": round(b["c"],2),
             "body": round(bodies[i],2), "bull": b["c"] >= b["o"]}
            for i, b in enumerate(bars)]


def _max_drawdown_r(trades):
    if not trades: return 0
    peak = cum = dd = 0.0
    for t in trades:
        cum  += t["pnl_r"] or 0
        peak  = max(peak, cum)
        dd    = max(dd, peak - cum)
    return round(dd, 2)


# ── Signal route ───────────────────────────────────────────────────────────────

@bp.route("/api/nifty-5min-signal")
def nifty_5min_signal():
    """Body Expansion Breakout on 5-min candles. Returns latest signal + context."""
    sym   = "".join(c for c in freq.args.get("symbol","^NSEI").upper()
                    if c.isalnum() or c in "-_.%^")
    range_ = freq.args.get("range", "5d")
    if range_ not in ("1d", "2d", "5d"):
        range_ = "5d"
    if not sym.startswith("^") and not sym.endswith(".NS"):
        sym = sym + ".NS"
    raw = fetch_yahoo(sym, interval="5m", range_=range_)
    if not raw:
        return jsonify({"error": f"Could not fetch 5-min data for {sym}"}), 404
    bars = parse_ohlcv(raw, sym)
    if not bars:
        return jsonify({"error": "No bars returned"}), 400

    # Drop incomplete last candle
    bars = bars[:-1]
    n = len(bars)

    opens   = [b["o"] for b in bars]; closes = [b["c"] for b in bars]
    highs   = [b["h"] for b in bars]; lows   = [b["l"] for b in bars]
    unix_t  = [b["t"] for b in bars]
    display_times = [to_ist_str(t) for t in unix_t]
    bodies  = [abs(closes[i] - opens[i]) for i in range(n)]

    atr_vals = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
                for i in range(1, min(15, n))]
    atr = round(sum(atr_vals)/len(atr_vals), 2) if atr_vals else 1.0

    LOOKBACK = 5
    signals = []

    for i in range(LOOKBACK, n):
        t_mins = _ist_mins(unix_t[i])
        if t_mins < 9*60+30 or t_mins > EOD_MINS - 15:
            continue
        body_i   = bodies[i]
        prev_max = max(bodies[i - LOOKBACK: i])
        if body_i <= prev_max:
            continue
        direction = "BUY" if closes[i] > opens[i] else "SELL"
        entry     = closes[i]
        sl        = lows[i]  + atr*0.2 if direction == "BUY" else highs[i] - atr*0.2
        sl        = lows[i]  - atr*0.2 if direction == "BUY" else highs[i] + atr*0.2
        risk      = abs(entry - sl)
        if risk <= 0:
            continue
        expansion = round(body_i / prev_max, 2) if prev_max > 0 else 0
        conf = min(95, 60 + int(expansion * 15) + (10 if direction == "BUY" and t_mins < 12*60 else 0))
        t1 = round(entry + risk*2 if direction=="BUY" else entry - risk*2, 2)
        t2 = round(entry + risk*3 if direction=="BUY" else entry - risk*3, 2)
        t3 = round(entry + risk*5 if direction=="BUY" else entry - risk*5, 2)
        signals.append({
            "bar_idx": i, "time": display_times[i], "direction": direction,
            "entry": round(entry,2), "sl": round(sl,2), "t1": t1, "t2": t2, "t3": t3,
            "risk": round(risk,2), "rr1": 2.0, "rr2": 3.0, "rr3": 5.0,
            "body": round(body_i,2), "prev_max_body": round(prev_max,2),
            "expansion": expansion, "confidence": conf, "atr": round(atr,2),
            "trail_method": f"Move SL to entry after T1. Then trail {round(atr,1)} pts below each new high.",
            "candle": {"o": round(opens[i],2), "h": round(highs[i],2),
                       "l": round(lows[i],2),  "c": round(closes[i],2)},
        })

    if not signals:
        latest = {
            "bar_idx": n-1, "time": display_times[-1], "direction": "NONE",
            "entry": round(closes[-1],2), "body": round(bodies[-1],2),
            "prev_max_body": round(max(bodies[-6:-1]),2) if n>=6 else 0,
            "expansion": round(bodies[-1]/max(bodies[-6:-1]),2) if n>=6 and max(bodies[-6:-1])>0 else 0,
            "atr": round(atr,2), "confidence": 0,
        }
        return jsonify({"symbol": sym, "bars_fetched": n, "atr": round(atr,2),
                        "current_price": round(closes[-1],2), "signal": None,
                        "latest_candle": latest, "recent_context": _build_context(bars[-8:], bodies[-8:])})

    return jsonify({"symbol": sym, "bars_fetched": n, "atr": round(atr,2),
                    "current_price": round(closes[-1],2), "signal": signals[-1],
                    "recent_context": _build_context(bars[-8:], bodies[-8:]),
                    "signal_count_today": len([s for s in signals
                                               if s["time"][:10] == display_times[-1][:10]])})


# ── Backtest route ─────────────────────────────────────────────────────────────

@bp.route("/api/nifty-5min-backtest")
def nifty_5min_backtest():
    """Backtest 5-min body expansion strategy on 30/60 days."""
    sym   = freq.args.get("symbol", "^NSEI").strip()
    range_ = freq.args.get("range", "30d")
    if range_ not in ("30d", "60d"):
        range_ = "30d"
    raw = fetch_yahoo(sym, interval="5m", range_=range_)
    if not raw:
        return jsonify({"error": f"Could not fetch 5-min data for {sym}"}), 404
    bars = parse_ohlcv(raw, sym)
    if len(bars) < 20:
        return jsonify({"error": f"Insufficient bars ({len(bars)})"}), 400

    bars = bars[:-1]; n = len(bars)
    closes = [b["c"] for b in bars]; opens  = [b["o"] for b in bars]
    highs  = [b["h"] for b in bars]; lows   = [b["l"] for b in bars]
    unix_t = [b["t"] for b in bars]
    bodies = [abs(closes[i] - opens[i]) for i in range(n)]
    times  = [_ist_str(t) for t in unix_t]

    LOOKBACK = 5
    trades   = []
    in_trade = False
    trade    = {}

    for i in range(LOOKBACK, n):
        t_mins = _ist_mins(unix_t[i])
        t_date = _ist_date(unix_t[i])

        # EOD forced exit
        if in_trade and (t_date != trade["entry_date"] or t_mins >= EOD_MINS):
            trade.update(exit_price=closes[i], exit_time=times[i], exit_reason="EOD",
                         pnl=round((closes[i]-trade["entry"]) if trade["direction"]=="BUY"
                                   else (trade["entry"]-closes[i]), 2))
            trade["pnl_r"] = round(trade["pnl"]/trade["risk"],2) if trade["risk"] else 0
            trade["outcome"] = "WIN" if trade["pnl"] > 0 else "LOSS"
            trades.append(trade); in_trade = False; trade = {}

        if t_mins >= EOD_MINS - 30:
            continue

        if in_trade:
            d  = trade["direction"]; entry = trade["entry"]; sl = trade["sl"]; t1 = trade["t1"]
            sl_hit    = lows[i] <= sl    if d == "BUY" else highs[i] >= sl
            t1_hit_now= highs[i] >= t1   if d == "BUY" else lows[i]  <= t1
            if sl_hit and not trade["t1_hit"]:
                trade.update(exit_price=sl, exit_time=_ist_str(unix_t[i]), exit_reason="SL",
                             pnl=round((sl-entry) if d=="BUY" else (entry-sl), 2))
                trade["pnl_r"] = round(trade["pnl"]/trade["risk"],2) if trade["risk"] else 0
                trade["outcome"] = "LOSS"
                trades.append(trade); in_trade = False; trade = {}; continue
            if t1_hit_now and not trade["t1_hit"]:
                trade["t1_hit"] = True; trade["sl"] = entry
            if trade["t1_hit"]:
                if d == "BUY":
                    if lows[i] > trade["sl"]: trade["sl"] = lows[i]
                else:
                    if highs[i] < trade["sl"]: trade["sl"] = highs[i]
                if (lows[i] <= trade["sl"] if d=="BUY" else highs[i] >= trade["sl"]):
                    trade.update(exit_price=trade["sl"], exit_time=_ist_str(unix_t[i]), exit_reason="TRAIL",
                                 pnl=round((trade["sl"]-entry) if d=="BUY" else (entry-trade["sl"]),2))
                    trade["pnl_r"] = round(trade["pnl"]/trade["risk"],2) if trade["risk"] else 0
                    trade["outcome"] = "WIN"
                    trades.append(trade); in_trade = False; trade = {}
            continue

        body_i   = bodies[i]
        prev_max = max(bodies[i - LOOKBACK: i])
        if body_i <= prev_max:
            continue
        d    = "BUY" if closes[i] > opens[i] else "SELL"
        entry= closes[i]
        sl   = lows[i]  if d == "BUY" else highs[i]
        risk = abs(entry - sl)
        if risk < 0.5: continue
        t1   = round(entry + 2*risk if d=="BUY" else entry - 2*risk, 2)
        in_trade = True
        trade = {"entry_bar": i, "entry_time": _ist_str(unix_t[i]), "entry_date": _ist_date(unix_t[i]),
                 "direction": d, "entry": round(entry,2), "sl": round(sl,2), "t1": t1,
                 "risk": round(risk,2), "expansion": round(body_i/prev_max,2) if prev_max else 0,
                 "body": round(body_i,2), "prev_max": round(prev_max,2), "t1_hit": False,
                 "exit_price": None, "exit_time": None, "exit_reason": None, "pnl": None,
                 "pnl_r": None, "outcome": None}

    if in_trade:
        trade.update(exit_price=closes[-1], exit_time=_ist_str(unix_t[-1]), exit_reason="DATA_END",
                     pnl=round((closes[-1]-trade["entry"]) if trade["direction"]=="BUY"
                               else (trade["entry"]-closes[-1]),2))
        trade["pnl_r"] = round(trade["pnl"]/trade["risk"],2) if trade["risk"] else 0
        trade["outcome"] = "WIN" if trade["pnl"] > 0 else "LOSS"
        trades.append(trade)

    total     = len(trades)
    wins      = [t for t in trades if t["outcome"]=="WIN"]
    losses    = [t for t in trades if t["outcome"]=="LOSS"]
    win_rate  = round(len(wins)/total*100,1) if total else 0
    avg_win_r = round(sum(t["pnl_r"] for t in wins)   /len(wins),  2) if wins   else 0
    avg_los_r = round(sum(t["pnl_r"] for t in losses) /len(losses),2) if losses else 0
    total_r   = round(sum(t["pnl_r"] for t in trades), 2)
    buys  = [t for t in trades if t["direction"]=="BUY"]
    sells = [t for t in trades if t["direction"]=="SELL"]
    daily_pnl = {}
    for t in trades:
        d_ = t.get("entry_date","")[:10]
        daily_pnl[d_] = round(daily_pnl.get(d_,0) + (t["pnl_r"] or 0), 2)
    cum = 0.0; equity_curve = []
    for d_ in sorted(daily_pnl):
        cum = round(cum + daily_pnl[d_], 2)
        equity_curve.append({"date": d_, "daily_r": daily_pnl[d_], "cumulative_r": cum})

    return jsonify({
        "symbol": sym, "range": range_, "bars_tested": n,
        "date_from": _ist_date(unix_t[0]), "date_to": _ist_date(unix_t[-1]),
        "stats": {
            "total_trades": total, "wins": len(wins), "losses": len(losses), "win_rate": win_rate,
            "total_r": total_r, "avg_win_r": avg_win_r, "avg_loss_r": avg_los_r,
            "expectancy_r": round(win_rate/100*avg_win_r + (1-win_rate/100)*avg_los_r, 3),
            "max_drawdown_r": _max_drawdown_r(trades),
            "sl_exits":    len([t for t in trades if t["exit_reason"]=="SL"]),
            "trail_exits": len([t for t in trades if t["exit_reason"]=="TRAIL"]),
            "eod_exits":   len([t for t in trades if t["exit_reason"]=="EOD"]),
            "buy_trades": len(buys), "sell_trades": len(sells),
            "buy_win_rate":  round(len([t for t in buys  if t["outcome"]=="WIN"])/len(buys) *100,1) if buys  else 0,
            "sell_win_rate": round(len([t for t in sells if t["outcome"]=="WIN"])/len(sells)*100,1) if sells else 0,
        },
        "trades": trades[-60:], "equity_curve": equity_curve,
    })
