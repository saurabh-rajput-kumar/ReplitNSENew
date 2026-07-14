"""
Signal engine routes.
/api/signals        POST — AI Analyze tab signals for any NSE stock
/api/market-strategy POST — Full market strategy for Nifty/Crypto tabs
/api/sr-zones       POST — S/R zone analysis
/api/sr-zones/batch POST — Batch S/R analysis for screener universe

Edit this file to add new signal types or change signal confidence thresholds.
"""
import re, json
from flask import Blueprint, request as freq, jsonify
from ..core.data import fetch_yahoo, parse_ohlcv
from ..core.indicators import compute_indicators, compute_emas, vwap as compute_vwap
from ..core.patterns import detect_candle_patterns, detect_fvg, detect_order_blocks, find_sr_zones
from ..core.ai import gemini
from ..core.config import GEMINI_API_KEY

bp = Blueprint("signals", __name__)


# ── Signal generation helpers ──────────────────────────────────────────────────

def _generate_market_signals(bars, indicators, patterns, fvgs, obs, symbol, timeframe, asset_type):
    signals = []
    n     = len(bars)
    if n < 10:
        return signals
    price     = bars[-1]["c"]
    atr_v     = indicators.get("atr14", price * 0.01)
    emas      = indicators.get("emas",  {})
    rsi_v     = indicators.get("rsi14", 50)
    vwap_v    = indicators.get("vwap")

    def make_signal(direction, sig_type, entry, sl, pattern_name, confidence, reason):
        risk = abs(entry - sl)
        if risk <= 0: return None
        t1 = round(entry + risk * 2 if direction == "LONG" else entry - risk * 2, 2)
        t2 = round(entry + risk * 3 if direction == "LONG" else entry - risk * 3, 2)
        t3 = round(entry + risk * 5 if direction == "LONG" else entry - risk * 5, 2)
        return {
            "direction": direction, "signal_type": sig_type, "pattern": pattern_name,
            "entry": round(entry, 2), "sl": round(sl, 2),
            "target1": t1, "target2": t2, "target3": t3,
            "rr": round(abs(t1 - entry) / risk, 1),
            "trail_method": f"Move SL to entry after T1. Trail 1.5x ATR (₹{round(atr_v*1.5,1)}) below each new high.",
            "confidence": confidence, "reason": reason,
        }

    # 1. EMA Crossover
    e9 = emas.get("ema9"); e21 = emas.get("ema21"); e50 = emas.get("ema50")
    if e9 and e21:
        prev_closes = [b["c"] for b in bars[-6:-1]]
        if len(prev_closes) >= 5:
            def _ema(arr, p):
                k = 2/(p+1); e = arr[0]
                for x in arr[1:]: e = x*k + e*(1-k)
                return e
            pe9  = _ema(prev_closes[-10:] if len(prev_closes)>=10 else prev_closes, min(9, len(prev_closes)))
            pe21 = _ema(prev_closes[-20:] if len(prev_closes)>=20 else prev_closes, min(21, len(prev_closes)))
            if e9 > e21 and pe9 <= pe21 and rsi_v > 50 and (e50 is None or price > e50):
                sl_lv = round(min(b["l"] for b in bars[-5:]) - atr_v*0.3, 2)
                sig = make_signal("LONG", "EMA Crossover", price, sl_lv,
                                  "EMA9 x EMA21 (Golden Cross)", 72,
                                  f"EMA9 ({e9}) crossed above EMA21 ({e21}). RSI {rsi_v:.0f} confirms.")
                if sig: signals.append(sig)
            elif e9 < e21 and pe9 >= pe21 and rsi_v < 50 and (e50 is None or price < e50):
                sl_lv = round(max(b["h"] for b in bars[-5:]) + atr_v*0.3, 2)
                sig = make_signal("SHORT", "EMA Crossover", price, sl_lv,
                                  "EMA9 x EMA21 (Death Cross)", 68,
                                  f"EMA9 ({e9}) crossed below EMA21 ({e21}). RSI {rsi_v:.0f} bearish.")
                if sig: signals.append(sig)

    # 2. FVG Retest
    for fvg in fvgs[:2]:
        if fvg["near"]:
            if fvg["type"] == "bullish" and price <= fvg["hi"]*1.01 and price >= fvg["lo"]*0.99:
                sl_lv = round(fvg["lo"] - atr_v*0.5, 2)
                sig = make_signal("LONG", "FVG Retest", price, sl_lv,
                                  "Bullish Fair Value Gap", 75,
                                  f"Price retesting bullish FVG ₹{fvg['lo']}–₹{fvg['hi']}.")
                if sig: signals.append(sig)
            elif fvg["type"] == "bearish" and price >= fvg["lo"]*0.99 and price <= fvg["hi"]*1.01:
                sl_lv = round(fvg["hi"] + atr_v*0.5, 2)
                sig = make_signal("SHORT", "FVG Retest", price, sl_lv,
                                  "Bearish Fair Value Gap", 73,
                                  f"Price retesting bearish FVG ₹{fvg['lo']}–₹{fvg['hi']}.")
                if sig: signals.append(sig)

    # 3. Order Block Reaction
    for ob in obs:
        if ob["near"]:
            if ob["type"] == "bullish_ob":
                sl_lv = round(ob["lo"] - atr_v*0.3, 2)
                sig = make_signal("LONG", "Order Block", price, sl_lv,
                                  "Bullish Order Block", 70,
                                  f"Price at bullish OB ₹{ob['lo']}–₹{ob['hi']}.")
                if sig: signals.append(sig)
            elif ob["type"] == "bearish_ob":
                sl_lv = round(ob["hi"] + atr_v*0.3, 2)
                sig = make_signal("SHORT", "Order Block", price, sl_lv,
                                  "Bearish Order Block", 68,
                                  f"Price at bearish OB ₹{ob['lo']}–₹{ob['hi']}.")
                if sig: signals.append(sig)

    # 4. VWAP Deviation
    if vwap_v:
        dev = (price - vwap_v) / vwap_v * 100
        if dev < -1.5 and rsi_v < 45:
            sl_lv = round(price - atr_v*1.5, 2)
            sig = make_signal("LONG", "VWAP Reversion", price, sl_lv,
                              "Below VWAP Reversion", 65,
                              f"Price {abs(dev):.1f}% below VWAP ({vwap_v}). RSI {rsi_v:.0f} oversold.")
            if sig: signals.append(sig)
        elif dev > 1.5 and rsi_v > 65:
            sl_lv = round(price + atr_v*1.5, 2)
            sig = make_signal("SHORT", "VWAP Reversion", price, sl_lv,
                              "Above VWAP Reversion", 62,
                              f"Price {dev:.1f}% above VWAP ({vwap_v}). RSI {rsi_v:.0f} overbought.")
            if sig: signals.append(sig)

    # 5. Candlestick pattern at key level
    recent_patterns = [p for p in patterns if p.get("bullish") is not None]
    for pat in recent_patterns[:1]:
        if pat["bullish"]:
            sl_lv = round(bars[-1]["l"] - atr_v*0.3, 2)
            sig = make_signal("LONG", "Pattern Signal", price, sl_lv, pat["name"], 60,
                              f'{pat["name"]} at ₹{pat["level"]}. Wait for confirmation.')
            if sig: signals.append(sig)
        else:
            sl_lv = round(bars[-1]["h"] + atr_v*0.3, 2)
            sig = make_signal("SHORT", "Pattern Signal", price, sl_lv, pat["name"], 58,
                              f'{pat["name"]} at ₹{pat["level"]}. Bearish reversal — wait for confirmation.')
            if sig: signals.append(sig)

    signals.sort(key=lambda x: -x["confidence"])
    return signals[:3]


def _aggregate_4hr(bars):
    agg = []
    for i in range(0, len(bars) - 3, 4):
        chunk = bars[i:i+4]
        agg.append({"d": chunk[0]["d"], "t": chunk[0]["t"],
                    "o": chunk[0]["o"], "c": chunk[-1]["c"],
                    "h": max(b["h"] for b in chunk),
                    "l": min(b["l"] for b in chunk),
                    "v": sum(b["v"] for b in chunk)})
    return agg


def _gemini_market_analysis(symbol, timeframe, bars, indicators, patterns, fvgs, obs):
    if not GEMINI_API_KEY:
        return ""
    price = bars[-1]["c"]; emas = indicators.get("emas", {}); rsi_v = indicators.get("rsi14", 50)
    vwap_v = indicators.get("vwap")
    pat_str = ", ".join(p["name"] for p in patterns[:4]) or "none"
    fvg_str = "; ".join(f"{'Bull' if f['type']=='bullish' else 'Bear'} FVG {f['lo']}-{f['hi']}" for f in fvgs[:3]) or "none"
    ob_str  = "; ".join(f"{'Bull' if 'bullish' in ob['type'] else 'Bear'} OB {ob['lo']}-{ob['hi']}" for ob in obs[:2]) or "none"
    bars_txt = " | ".join(f"{b['d'][5:]}: O{b['o']:.0f} H{b['h']:.0f} L{b['l']:.0f} C{b['c']:.0f}" for b in bars[-10:])
    prompt = (
        f"Analyze {symbol} {timeframe} market structure. Be concise — 3 sentences max.\n\n"
        f"Price: {price} | RSI: {rsi_v:.0f} | VWAP: {vwap_v or 'N/A'}\n"
        f"EMA9: {emas.get('ema9','?')} | EMA21: {emas.get('ema21','?')} | EMA50: {emas.get('ema50','?')}\n"
        f"Patterns: {pat_str}\nFVGs: {fvg_str}\nOrder Blocks: {ob_str}\nRecent bars: {bars_txt}\n\n"
        "Describe: (1) market structure, (2) most significant level, (3) what to watch next."
    )
    return gemini("", prompt, max_tokens=300) or ""


def _sr_gemini_commentary(symbol, zones, current_price, timeframe):
    if not GEMINI_API_KEY:
        return {}
    lines = [f"{symbol} price ₹{current_price} | ATR ₹{zones.get('atr','?')} | TF: {timeframe}"]
    lines.append("RESISTANCE ZONES:")
    for z in zones.get("resistances", [])[:3]:
        lines.append(f"  ₹{z['zone_lo']}–{z['zone_hi']} | {z['touches']} touches | strength {z['strength']}%")
    lines.append("SUPPORT ZONES:")
    for z in zones.get("supports", [])[:3]:
        lines.append(f"  ₹{z['zone_lo']}–{z['zone_hi']} | {z['touches']} touches | strength {z['strength']}%")
    prompt = (
        "For each S/R zone below, write ONE sentence (max 12 words) about its significance.\n\n"
        + "\n".join(lines)
        + '\n\nReturn JSON only: {"center_price": "sentence", ...}'
    )
    raw = gemini("", prompt, max_tokens=400)
    if not raw: return {}
    raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    try:
        return json.loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try: return json.loads(m.group())
            except Exception: pass
    return {}


# ── Routes ─────────────────────────────────────────────────────────────────────

@bp.route("/api/signals", methods=["POST"])
def get_signals():
    body      = freq.get_json(silent=True) or {}
    symbol    = str(body.get("symbol",    "")).upper().strip()
    timeframe = str(body.get("timeframe", "daily")).lower()
    if not symbol:
        return jsonify({"error": "symbol required"}), 400
    sym_yf = symbol + ".NS" if not symbol.endswith(".NS") and not symbol.startswith("^") else symbol
    if timeframe == "15min":
        raw = fetch_yahoo(sym_yf, interval="15m", range_="60d")
    elif timeframe in ("1hr", "4hr"):
        raw = fetch_yahoo(sym_yf, interval="60m", range_="1y")
    else:
        raw = fetch_yahoo(sym_yf, interval="1d",  range_="1y")
    if not raw:
        return jsonify({"signals": []})
    bars = parse_ohlcv(raw, symbol)
    if len(bars) < 20:
        return jsonify({"signals": []})
    if timeframe == "4hr":
        bars = _aggregate_4hr(bars)
    closes     = [b["c"] for b in bars]
    indicators = compute_indicators(bars)
    indicators["emas"] = compute_emas(closes)
    indicators["vwap"] = compute_vwap(bars[-50:])
    patterns = detect_candle_patterns(bars)
    fvgs     = detect_fvg(bars, closes[-1])
    obs      = detect_order_blocks(bars, closes[-1])
    signals  = _generate_market_signals(bars, indicators, patterns, fvgs, obs, symbol, timeframe, "stock")
    return jsonify({"signals": signals, "patterns": patterns[:4]})


@bp.route("/api/market-strategy", methods=["POST"])
def market_strategy():
    body      = freq.get_json(silent=True) or {}
    symbol    = str(body.get("symbol",    "")).strip()
    timeframe = str(body.get("timeframe", "15min")).lower()
    asset_type= str(body.get("type",      "nifty")).lower()
    if not symbol:
        return jsonify({"error": "symbol required"}), 400
    tf_map = {"5min": ("5m","60d"), "15min": ("15m","60d"),
              "1hr":  ("60m","730d"), "4hr": ("60m","730d"), "daily": ("1d","1825d")}
    interval, range_ = tf_map.get(timeframe, ("15m", "60d"))
    raw  = fetch_yahoo(symbol, interval=interval, range_=range_)
    if not raw:
        return jsonify({"error": f"Could not fetch data for {symbol}"}), 404
    bars = parse_ohlcv(raw, symbol)
    if len(bars) < 30:
        return jsonify({"error": f"Insufficient data ({len(bars)} bars)."}), 400
    if timeframe == "4hr":
        bars = _aggregate_4hr(bars)
    closes     = [b["c"] for b in bars]
    price      = closes[-1]
    indicators = compute_indicators(bars)
    indicators["emas"] = compute_emas(closes)
    indicators["vwap"] = compute_vwap(bars[-100:])
    patterns = detect_candle_patterns(bars)
    fvgs     = detect_fvg(bars, price)
    obs      = detect_order_blocks(bars, price)
    signals  = _generate_market_signals(bars, indicators, patterns, fvgs, obs, symbol, timeframe, asset_type)
    sr_data  = find_sr_zones(bars, price)
    levels   = {"supports":    [round(z["center"],2) for z in sr_data.get("supports",   [])[:3]],
                "resistances": [round(z["center"],2) for z in sr_data.get("resistances",[])[:3]]}
    ai_analysis = _gemini_market_analysis(symbol, timeframe, bars, indicators, patterns, fvgs, obs)
    names = {"^NSEI": "Nifty 50", "^NSEBANK": "Nifty Bank",
             "BTC-USD": "Bitcoin (BTC/USD)", "ETH-USD": "Ethereum (ETH/USD)", "GC=F": "Gold (XAU/USD)"}
    return jsonify({
        "symbol": symbol, "name": names.get(symbol, symbol), "timeframe": timeframe,
        "current_price": price, "bars_analyzed": len(bars), "signals": signals,
        "patterns": patterns, "fvgs": fvgs[:4], "order_blocks": obs[:3], "levels": levels,
        "ai_analysis": ai_analysis,
        "indicators": {k: indicators.get("emas", {}).get(k) if "ema" in k else indicators.get(k)
                       for k in ("ema9","ema21","ema50","rsi14","vwap","atr14")},
    })


@bp.route("/api/sr-zones", methods=["POST"])
def sr_zones():
    body      = freq.get_json(silent=True) or {}
    symbol    = str(body.get("symbol",    "")).upper().strip()
    timeframe = str(body.get("timeframe", "15min")).lower()
    with_ai   = bool(body.get("with_ai",  False))
    if not symbol:
        return jsonify({"error": "symbol required"}), 400
    sym_yf = symbol + ".NS" if not symbol.endswith(".NS") else symbol
    raw = fetch_yahoo(sym_yf, interval="15m" if timeframe=="15min" else "1d",
                      range_="60d" if timeframe=="15min" else "2y")
    if not raw:
        return jsonify({"error": f"Could not fetch data for {symbol}"}), 404
    bars = parse_ohlcv(raw, symbol)
    if len(bars) < 30:
        return jsonify({"error": f"Insufficient data ({len(bars)} bars)"}), 400
    price = bars[-1]["c"]
    zones = find_sr_zones(bars, price)
    commentary = _sr_gemini_commentary(symbol, zones, price, timeframe) if with_ai and GEMINI_API_KEY else {}
    return jsonify({"symbol": symbol, "timeframe": timeframe, "current_price": price,
                    "bars_analyzed": len(bars), "zones": zones, "commentary": commentary,
                    "ai_used": bool(commentary)})


@bp.route("/api/sr-zones/batch", methods=["POST"])
def sr_zones_batch():
    body      = freq.get_json(silent=True) or {}
    symbols   = body.get("symbols", [])[:30]
    timeframe = str(body.get("timeframe", "15min")).lower()
    if not symbols:
        return jsonify({"error": "symbols list required"}), 400
    results = []
    for sym in symbols:
        sym_yf = sym + ".NS" if not sym.endswith(".NS") else sym
        try:
            raw = fetch_yahoo(sym_yf,
                              interval="15m" if timeframe=="15min" else "1d",
                              range_="60d"   if timeframe=="15min" else "2y")
            if not raw: continue
            bars = parse_ohlcv(raw, sym)
            if len(bars) < 30: continue
            price = bars[-1]["c"]
            zones = find_sr_zones(bars, price)
            close_zones = [z for z in zones.get("supports",[]) + zones.get("resistances",[])
                           if abs(z["dist_pct"]) <= 3.0 and z["strength"] >= 40]
            if close_zones:
                results.append({
                    "symbol": sym, "price": price,
                    "zones": {"supports": zones["supports"][:3], "resistances": zones["resistances"][:3]},
                    "closest_zone": sorted(close_zones, key=lambda x: abs(x["dist_pct"]))[0],
                    "atr": zones.get("atr", 0),
                })
        except Exception as e:
            print(f"[SR batch] {sym}: {e}"); continue
    results.sort(key=lambda x: abs(x["closest_zone"]["dist_pct"]))
    return jsonify({"results": results, "timeframe": timeframe, "total": len(results)})
