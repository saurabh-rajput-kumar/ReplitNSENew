"""
AI Analyze routes — Gemini strategy generation, backtesting, Pine Script export.
/api/analyze    POST {symbol, timeframe}

Edit this file to change the analysis pipeline or Pine Script template.
"""
import re, json
from flask import Blueprint, request as freq, jsonify
from ..core.config import GEMINI_API_KEY
from ..core.data import fetch_yahoo, parse_ohlcv
from ..core.indicators import compute_indicators
from ..core.ai import gemini

bp = Blueprint("analyze", __name__)


# ── Strategy generation ────────────────────────────────────────────────────────

def _generate_strategy(symbol: str, bars: list, indicators: dict, timeframe: str) -> dict:
    ind    = indicators
    price  = ind.get("price", 0)
    atr    = ind.get("atr14",  round(price * 0.02, 2))
    rsi_v  = ind.get("rsi14",  50)
    e20    = ind.get("ema20",  price)
    e50    = ind.get("ema50",  price)
    e200   = ind.get("ema200", price)
    vr     = ind.get("vol_ratio", 1.0)
    hi52   = ind.get("hi52",   price)

    sample      = bars[-50:]
    bars_compact = ";".join(
        f"{b['d'][5:]}:{b['c']:.0f}/{b['h']:.0f}/{b['l']:.0f}/{b['v']//1000}k"
        for b in sample
    )
    trend      = ("uptrend"   if (e20 and e50 and price > e20 > e50)
                  else "downtrend" if (e20 and price < e20) else "sideways")
    near_high  = price >= hi52 * 0.95 if hi52 else False
    oversold   = rsi_v < 40
    overbought = rsi_v > 72

    prompt = (
        f"You are a quantitative trading expert for NSE Indian stocks.\n"
        f"Design the BEST mechanical trading strategy for {symbol} ({timeframe}).\n\n"
        f"CURRENT STATE:\n"
        f"Price=₹{price} | ATR={atr} | RSI={rsi_v} | Trend={trend}\n"
        f"EMA20={e20} | EMA50={e50} | EMA200={e200}\n"
        f"VolRatio={vr}x | Near52WHigh={near_high} | Oversold={oversold}\n\n"
        f"LAST 50 BARS (MM-DD:close/high/low/volume):\n{bars_compact}\n\n"
        "REQUIREMENTS:\n"
        "- Min win rate 55%, min R:R 1:2 on every trade\n"
        "- Rule-based only (EMA, RSI, Volume, ATR)\n"
        "- Entry needs 2+ confirming conditions\n"
        "- ALWAYS return viable:true\n"
        f"- Choose: trend_following/breakout/momentum/mean_reversion\n\n"
        'Return ONLY this JSON:\n'
        '{"viable":true,"name":"...","type":"trend_following","timeframe":"'
        + timeframe +
        '","entry_conditions":[{"indicator":"EMA9","operator":"crosses_above","value":"EMA21","description":"..."}],'
        '"exit_conditions":[{"trigger":"stop_loss","method":"atr_multiple","multiplier":1.5,"description":"1.5x ATR"},'
        '{"trigger":"target","method":"risk_multiple","multiplier":2.5,"description":"2.5x risk"}],'
        '"filters":["Only trade when EMA50 > EMA200"],'
        '"hold_period_bars":20,"expected_win_rate":58,"expected_rr":2.5,"rationale":"..."}'
    )

    def _parse(text):
        if not text:
            return None
        text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
        try:
            return json.loads(text)
        except Exception:
            pass
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except Exception:
                pass
        return None

    result = _parse(gemini("", prompt, max_tokens=800))

    if not result:
        fb = (
            f"For NSE stock {symbol}, RSI={rsi_v}, trend={trend}, ATR={atr}:\n"
            f'Return a simple EMA crossover strategy as JSON only:\n'
            f'{{"viable":true,"name":"{symbol} EMA Strategy","type":"trend_following","timeframe":"{timeframe}",'
            f'"entry_conditions":[{{"indicator":"EMA9","operator":"crosses_above","value":"EMA21","description":"EMA9 crosses above EMA21"}},'
            f'{{"indicator":"RSI14","operator":"greater_than","value":50,"description":"RSI above 50"}}],'
            f'"exit_conditions":[{{"trigger":"stop_loss","method":"atr_multiple","multiplier":1.5,"description":"1.5x ATR"}},'
            f'{{"trigger":"target","method":"risk_multiple","multiplier":2.5,"description":"2.5x risk"}}],'
            f'"filters":["Price above EMA50"],"hold_period_bars":20,"expected_win_rate":57,"expected_rr":2.5,'
            f'"rationale":"EMA crossover with volume confirmation."}}'
        )
        result = _parse(gemini("", fb, max_tokens=600))

    if not result:
        strat_type = "breakout" if near_high else "mean_reversion" if oversold else "trend_following"
        entry_rsi  = 55 if not oversold else 35
        result = {
            "viable": True,
            "name":   f"{symbol} {strat_type.replace('_',' ').title()}",
            "type":   strat_type, "timeframe": timeframe,
            "entry_conditions": [
                {"indicator": "EMA9",   "operator": "crosses_above", "value": "EMA21",    "description": "Short-term EMA crosses above medium-term"},
                {"indicator": "RSI14",  "operator": "greater_than",  "value": entry_rsi,  "description": f"RSI above {entry_rsi}"},
                {"indicator": "volume", "operator": "greater_than",  "value": "1.3x_avg", "description": "Volume surge confirms interest"},
            ],
            "exit_conditions": [
                {"trigger": "stop_loss", "method": "atr_multiple", "multiplier": 1.5, "description": f"1.5x ATR (₹{round(atr*1.5,1)}) below entry"},
                {"trigger": "target",   "method": "risk_multiple", "multiplier": 2.5, "description": "2.5x risk above entry"},
                {"trigger": "trailing", "method": "ema_cross",     "value": "EMA9_below_EMA21", "description": "Trail: exit when EMA9 crosses below EMA21"},
            ],
            "filters": [
                f"Only trade when price above EMA50 (₹{e50})" if e50 else "Only trade in uptrend",
                "Skip if RSI > 75 at entry",
                "Minimum volume 1.3x 20-day average",
            ],
            "hold_period_bars": 20, "expected_win_rate": 56, "expected_rr": 2.5,
            "rationale": f"Rule-based {strat_type} strategy calibrated to {symbol}'s ATR={atr}.",
            "_note": "Generated from indicators (Gemini unavailable)",
        }
        print(f"[Strategy] Used indicator fallback for {symbol}")

    result["viable"] = True
    return result


# ── Backtest ───────────────────────────────────────────────────────────────────

def _run_backtest(bars: list, strategy: dict, indicators: dict) -> dict:
    from ..core.indicators import ema_arr, rsi_arr, atr_at

    if not strategy.get("viable"):
        return {"trades": [], "stats": {}}
    closes  = [b["c"] for b in bars]
    highs   = [b["h"] for b in bars]
    lows    = [b["l"] for b in bars]
    volumes = [b["v"] for b in bars]
    n = len(bars)
    if n < 60:
        return {"trades": [], "stats": {"error": "Not enough bars"}}

    e9   = ema_arr(closes,  9)
    e21  = ema_arr(closes, 21)
    e20  = ema_arr(closes, 20)
    e50  = ema_arr(closes, 50) if n >= 55 else [closes[0]] * n
    e200 = ema_arr(closes, 200) if n >= 205 else [closes[0]] * n
    rsi  = rsi_arr(closes, 14)
    avg_vol_arr = [sum(volumes[max(0, i-20):i]) / max(len(volumes[max(0, i-20):i]), 1)
                   for i in range(n)]

    stype    = strategy.get("type", "trend_following")
    hold_max = int(strategy.get("hold_period_bars", 20))
    atr_mult = 1.5; rr_target = 2.5
    for ex in strategy.get("exit_conditions", []):
        if ex.get("trigger") == "stop_loss" and "multiplier" in ex:
            atr_mult  = float(ex["multiplier"])
        if ex.get("trigger") == "target" and "multiplier" in ex:
            rr_target = float(ex["multiplier"])

    def check_entry(i):
        if i < 21: return False
        c = closes[i]
        if stype in ("trend_following", "breakout", "momentum"):
            if c < e50[i] * 0.98: return False
        conds = strategy.get("entry_conditions", [])
        if not conds:
            return e9[i] > e21[i] and e9[i-1] <= e21[i-1] and 50 <= rsi[i] <= 72 and volumes[i] >= avg_vol_arr[i] * 1.2
        score = 0; required = max(2, len(conds) - 1)
        for cond in conds:
            ind_ = cond.get("indicator", "").lower()
            op   = cond.get("operator", "")
            val  = cond.get("value", "")
            iv   = (e9[i] if "ema9" in ind_ else e21[i] if "ema21" in ind_ else
                    e20[i] if "ema20" in ind_ else e50[i] if "ema50" in ind_ else
                    e200[i] if "ema200" in ind_ else rsi[i] if "rsi" in ind_ else
                    volumes[i] if "volume" in ind_ else c if "price" in ind_ else None)
            if iv is None: score += 1; continue
            try:
                ref = None
                if isinstance(val, (int, float)):
                    ref = float(val)
                elif "ema9"  in str(val).lower(): ref = e9[i]
                elif "ema21" in str(val).lower(): ref = e21[i]
                elif "ema20" in str(val).lower(): ref = e20[i]
                elif "ema50" in str(val).lower(): ref = e50[i]
                elif "ema200"in str(val).lower(): ref = e200[i]
                elif "avg"   in str(val).lower():
                    import re as re_
                    nums = re_.findall(r"[\d.]+", str(val))
                    ref  = avg_vol_arr[i] * float(nums[0]) if nums else avg_vol_arr[i] * 1.5
                elif "atr"   in str(val).lower():
                    ref  = atr_at(highs, lows, closes, i)
                else:
                    ref  = float(val) if str(val).replace(".", "").isdigit() else None
                if ref is None: score += 1; continue
                if op in ("crosses_above", "cross_above"):
                    if iv > ref and (e9[i-1] <= e21[i-1] or closes[i-1] <= ref): score += 1
                elif op in ("crosses_below", "cross_below"):
                    if iv < ref and closes[i-1] >= ref: score += 1
                elif op in ("greater_than", "above", ">"): score += (1 if iv > ref else 0)
                elif op in ("less_than", "below", "<"):    score += (1 if iv < ref else 0)
            except Exception:
                score += 1
        return score >= required

    trades = []
    in_trade = False
    entry_price = sl = target = 0.0
    entry_bar = entry_date = ""
    trail_sl  = None

    for i in range(22, n):
        if not in_trade:
            if check_entry(i):
                entry_price = closes[i]
                a           = atr_at(highs, lows, closes, i)
                sl          = round(entry_price - atr_mult * a, 2)
                target      = round(entry_price + rr_target * atr_mult * a, 2)
                trail_sl    = sl
                entry_bar   = i
                entry_date  = bars[i]["d"]
                in_trade    = True
        else:
            bar_hi, bar_lo = highs[i], lows[i]
            new_trail = round(closes[i] - atr_mult * atr_at(highs, lows, closes, i), 2)
            if new_trail > trail_sl:
                trail_sl = new_trail
            exit_price = None; exit_reason = "time"
            if bar_hi >= target:             exit_price = target;   exit_reason = "target"
            elif bar_lo <= trail_sl:         exit_price = trail_sl; exit_reason = "sl"
            elif (i - entry_bar) >= hold_max: exit_price = closes[i]; exit_reason = "time"
            if exit_price is not None:
                ret_pct = round((exit_price - entry_price) / entry_price * 100, 2)
                risk_   = entry_price - sl
                ach_rr  = round((exit_price - entry_price) / risk_, 2) if risk_ > 0 else 0
                trades.append({
                    "entry_date": entry_date, "exit_date": bars[i]["d"],
                    "entry": round(entry_price, 2), "exit": round(exit_price, 2),
                    "sl": round(sl, 2), "target": round(target, 2),
                    "trail_sl": round(trail_sl, 2), "ret_pct": ret_pct,
                    "won": ret_pct > 0, "exit_reason": exit_reason, "rr_achieved": ach_rr,
                })
                in_trade = False

    if not trades:
        return {"trades": [], "stats": {"total": 0, "win_rate": 0}}
    wins   = [t for t in trades if t["won"]]
    losses = [t for t in trades if not t["won"]]
    avg_win  = round(sum(t["ret_pct"] for t in wins)   / len(wins),   2) if wins   else 0
    avg_loss = round(sum(t["ret_pct"] for t in losses) / len(losses), 2) if losses else 0
    pf       = round(abs(len(wins) * avg_win / (len(losses) * avg_loss)), 2) if losses and avg_loss != 0 else 99.0
    return {
        "trades": trades[-50:],
        "stats": {
            "total": len(trades), "wins": len(wins), "losses": len(losses),
            "win_rate": round(len(wins) / len(trades) * 100, 1),
            "avg_win": avg_win, "avg_loss": avg_loss,
            "avg_ret": round(sum(t["ret_pct"] for t in trades) / len(trades), 2),
            "profit_factor": pf,
            "target_hits": sum(1 for t in trades if t["exit_reason"] == "target"),
            "sl_hits":     sum(1 for t in trades if t["exit_reason"] == "sl"),
            "time_exits":  sum(1 for t in trades if t["exit_reason"] == "time"),
        },
    }


# ── Pine Script generation ─────────────────────────────────────────────────────

def _gen_pine_script(symbol: str, strategy: dict, timeframe: str) -> str:
    """Generate Pine Script v6. Tries Gemini first, falls back to hand-validated template."""
    s       = strategy
    name    = s.get("name", f"{symbol} Strategy")
    stype   = s.get("type", "trend_following")
    hold    = s.get("hold_period_bars", 20)
    entries = "; ".join(f"{e.get('indicator','')} {e.get('operator','')} {e.get('value','')}"
                        for e in s.get("entry_conditions", []))
    sl_mult  = next((e.get("multiplier", 1.5) for e in s.get("exit_conditions", [])
                     if e.get("trigger") == "stop_loss"), 1.5)
    tgt_mult = next((e.get("multiplier", 2.5) for e in s.get("exit_conditions", [])
                     if e.get("trigger") == "target"), 2.5)
    sl_r  = round(float(sl_mult),  1)
    tgt_r = round(float(tgt_mult), 1)
    filters = "; ".join(s.get("filters", []))

    prompt = (
        "Write Pine Script VERSION 6 for NSE strategy. Return ONLY code, no markdown.\n\n"
        f"Strategy: {name} | Type: {stype} | Symbol: {symbol} | TF: {timeframe}\n"
        f"Entry: {entries}\nSL: {sl_r}x ATR | Target: {tgt_r}x risk | Hold max: {hold} bars\n"
        f"Filters: {filters}\n\n"
        "CRITICAL v6 rules:\n"
        "1. //@version=6 first line\n"
        "2. SINGLE QUOTES for all string literals\n"
        "3. var float slLevel = na and var float tgtLevel = na OUTSIDE if-blocks\n"
        "4. strategy.exit() OUTSIDE if-blocks\n"
        "5. NO Unicode chars in comments\n"
        "6. 4-space indentation\n"
        "7. No semicolons at line ends\n"
    )
    code = gemini("", prompt, max_tokens=2000)

    if code and len(code.strip()) >= 100:
        import re
        code = re.sub(r"```(?:pine|pinescript|pine.?script)?", "", code, flags=re.IGNORECASE)
        code = code.strip().strip("`").strip()
        code = code.replace("//@version=5", "//@version=6")
        code = code.replace("alert.freq_once_per_bar_close", "alert.freq_once_per_bar")
        clean = []
        for line in code.split("\n"):
            if line.lstrip().startswith("//"):
                line = line.encode("ascii", "ignore").decode("ascii")
            clean.append(line)
        code = "\n".join(clean)
        if "//@version" in code and "strategy(" in code:
            return code

    # ── Fallback template (hand-validated v6) ─────────────────────────────────
    wb = '{"symbol":"' + symbol + '","action":"BUY","timeframe":"' + timeframe + '"}'
    ws = '{"symbol":"' + symbol + '","action":"SELL","timeframe":"' + timeframe + '"}'
    lines = [
        "//@version=6",
        f"strategy('{name}', overlay=true, default_qty_type=strategy.percent_of_equity, default_qty_value=10, commission_type=strategy.commission.percent, commission_value=0.1)",
        "", "// -- Inputs",
        f"slMulti  = input.float({sl_r},  title='SL ATR Multiple',  minval=0.5, maxval=5.0,  step=0.1)",
        f"tgtMulti = input.float({tgt_r}, title='Tgt ATR Multiple', minval=1.0, maxval=10.0, step=0.1)",
        "emaFast   = input.int(9,  title='EMA Fast')",
        "emaSlow   = input.int(21, title='EMA Slow')",
        "emaFilter = input.int(50, title='EMA Trend Filter')",
        "", "// -- Indicators",
        "e_fast   = ta.ema(close, emaFast)",
        "e_slow   = ta.ema(close, emaSlow)",
        "e_filter = ta.ema(close, emaFilter)",
        "rsi14    = ta.rsi(close, 14)",
        "atr14    = ta.atr(14)",
        "avgVol   = ta.sma(volume, 20)",
        "", "// -- Entry conditions",
        "crossUp   = ta.crossover(e_fast, e_slow)",
        "trendOk   = close > e_filter",
        "rsiOk     = rsi14 > 50 and rsi14 < 75",
        "volOk     = volume > avgVol * 1.3",
        "entryLong = crossUp and trendOk and rsiOk and volOk",
        "", "var float slLevel  = na",
        "var float tgtLevel = na",
        "", "if entryLong and strategy.position_size == 0",
        "    slLevel  := close - slMulti * atr14",
        "    tgtLevel := close + tgtMulti * (close - slLevel)",
        "    strategy.entry('Long', strategy.long)",
        "", "if strategy.position_size == 0 and not entryLong",
        "    slLevel  := na",
        "    tgtLevel := na",
        "", "strategy.exit('Exit Long', from_entry='Long', stop=slLevel, limit=tgtLevel)",
        "",
        f"alertcondition(entryLong and strategy.position_size[1] == 0, title='BUY Signal', message='{wb}')",
        f"alertcondition(strategy.position_size[1] > 0 and strategy.position_size == 0, title='SELL Signal', message='{ws}')",
        "", "plot(e_fast,   'EMA Fast',   color=color.blue,   linewidth=1)",
        "plot(e_slow,   'EMA Slow',   color=color.orange, linewidth=1)",
        "plot(e_filter, 'EMA Filter', color=color.new(color.green, 20), linewidth=2)",
        "plot(strategy.position_size > 0 ? slLevel  : na, 'Stop Loss', color.red,   1, plot.style_linebr)",
        "plot(strategy.position_size > 0 ? tgtLevel : na, 'Target',    color.green, 1, plot.style_linebr)",
        "plotshape(entryLong, 'Buy Signal', shape.triangleup, location.belowbar, color.new(color.lime, 0), size=size.small)",
        "bgcolor(strategy.position_size > 0 ? color.new(color.lime, 93) : na)",
        "", "var table infoTbl = table.new(position.top_right, 2, 4, bgcolor=color.new(color.black, 70), border_width=1)",
        "if barstate.islast",
        "    table.cell(infoTbl, 0, 0, 'Strategy',    text_color=color.gray,   text_size=size.small)",
        f"    table.cell(infoTbl, 1, 0, '{name}',     text_color=color.white,  text_size=size.small)",
        "    table.cell(infoTbl, 0, 1, 'Symbol',      text_color=color.gray,   text_size=size.small)",
        f"    table.cell(infoTbl, 1, 1, '{symbol}',   text_color=color.yellow, text_size=size.small)",
        "    table.cell(infoTbl, 0, 2, 'Timeframe',   text_color=color.gray,   text_size=size.small)",
        f"    table.cell(infoTbl, 1, 2, '{timeframe}',text_color=color.aqua,   text_size=size.small)",
        "    table.cell(infoTbl, 0, 3, 'Risk:Reward', text_color=color.gray,   text_size=size.small)",
        f"    table.cell(infoTbl, 1, 3, '1:{tgt_r}',  text_color=color.lime,   text_size=size.small)",
    ]
    return "\n".join(line.encode("ascii", "ignore").decode("ascii") for line in lines)


# ── Route ──────────────────────────────────────────────────────────────────────

@bp.route("/api/analyze", methods=["POST"])
def analyze():
    body      = freq.get_json(silent=True) or {}
    symbol    = str(body.get("symbol",    "")).upper().strip()
    timeframe = str(body.get("timeframe", "daily")).lower()

    if not symbol:
        return jsonify({"error": "symbol required"}), 400
    if not GEMINI_API_KEY:
        return jsonify({"error": "GEMINI_API_KEY not configured — get free key at aistudio.google.com"}), 503

    sym_yf = symbol + ".NS" if not symbol.endswith(".NS") else symbol
    raw    = fetch_yahoo(sym_yf, interval="15m" if timeframe == "15min" else "1d",
                         range_="60d" if timeframe == "15min" else "2y")
    if not raw:
        return jsonify({"error": f"Could not fetch data for {symbol}"}), 404

    bars = parse_ohlcv(raw, symbol)
    if len(bars) < 50:
        return jsonify({"error": f"Insufficient data ({len(bars)} bars). Need 50+."}), 400

    indicators = compute_indicators(bars)
    strategy   = _generate_strategy(symbol, bars, indicators, timeframe)
    if not strategy.get("viable"):
        return jsonify({"symbol": symbol, "timeframe": timeframe, "viable": False,
                        "message": strategy.get("error", "No viable strategy found."),
                        "indicators": indicators})
    bt   = _run_backtest(bars, strategy, indicators)
    pine = _gen_pine_script(symbol, strategy, timeframe)
    return jsonify({
        "symbol": symbol, "timeframe": timeframe, "viable": True,
        "bars_analyzed": len(bars), "indicators": indicators,
        "strategy": strategy, "backtest": bt, "pine_script": pine,
    })
