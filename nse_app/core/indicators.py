"""
Technical indicator calculations: EMA, RSI, ATR, VWAP, Bollinger.
Edit this file to tweak indicator math or add new indicators.
All functions are pure (no I/O) — easy to unit-test.
"""


# ── Single-value indicators ────────────────────────────────────────────────────

def ema(arr: list, period: int) -> float:
    """Exponential Moving Average of the last items in arr."""
    k = 2 / (period + 1)
    e = arr[0]
    for x in arr[1:]:
        e = x * k + e * (1 - k)
    return round(e, 2)


def rsi(arr: list, period: int = 14) -> float:
    """Wilder RSI. Returns 50.0 if not enough data."""
    if len(arr) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, period + 1):
        d = arr[i] - arr[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains) / period
    al = sum(losses) / period
    for i in range(period + 1, len(arr)):
        d  = arr[i] - arr[i - 1]
        ag = (ag * (period - 1) + max(d, 0))  / period
        al = (al * (period - 1) + max(-d, 0)) / period
    return round(100 - 100 / (1 + ag / al) if al else 100, 1)


def atr(highs: list, lows: list, closes: list, period: int = 14) -> float:
    """Average True Range over last `period` bars."""
    n = len(closes)
    trs = []
    for i in range(1, min(period + 1, n)):
        trs.append(max(
            highs[-i] - lows[-i],
            abs(highs[-i] - closes[-i - 1]),
            abs(lows[-i]  - closes[-i - 1]),
        ))
    return round(sum(trs) / len(trs), 2) if trs else 0.0


def vwap(bars: list) -> float | None:
    """VWAP for the given bars."""
    total_pv = sum((b["h"] + b["l"] + b["c"]) / 3 * b["v"] for b in bars)
    total_v  = sum(b["v"] for b in bars)
    return round(total_pv / total_v, 2) if total_v > 0 else None


# ── Array-form indicators (for backtesting) ────────────────────────────────────

def ema_arr(arr: list, period: int) -> list:
    """EMA array same length as arr."""
    k = 2 / (period + 1)
    e = arr[0]
    result = [e]
    for x in arr[1:]:
        e = x * k + e * (1 - k)
        result.append(round(e, 3))
    return result


def rsi_arr(arr: list, period: int = 14) -> list:
    """RSI array same length as arr (first `period` values = 50.0)."""
    result = [50.0] * period
    gains  = [0.0]
    losses = [0.0]
    for i in range(1, period + 1):
        d = arr[i] - arr[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains[1:]) / period
    al = sum(losses[1:]) / period
    result.append(round(100 - 100 / (1 + ag / al), 1) if al else 100.0)
    for i in range(period + 1, len(arr)):
        d  = arr[i] - arr[i - 1]
        ag = (ag * (period - 1) + max(d, 0))  / period
        al = (al * (period - 1) + max(-d, 0)) / period
        result.append(round(100 - 100 / (1 + ag / al), 1) if al else 100.0)
    return result


def atr_at(highs: list, lows: list, closes: list, i: int, period: int = 14) -> float:
    """ATR at bar index i (for backtesting loop)."""
    trs = []
    for j in range(max(1, i - period + 1), i + 1):
        trs.append(max(
            highs[j]  - lows[j],
            abs(highs[j]  - closes[j - 1]),
            abs(lows[j]   - closes[j - 1]),
        ))
    return sum(trs) / len(trs) if trs else closes[i] * 0.02


# ── Multi-indicator snapshot for a bar series ─────────────────────────────────

def compute_indicators(bars: list) -> dict:
    """
    Compute indicator snapshot from OHLCV bars.
    Returns EMA9/20/50/200, RSI14, ATR14, avg_vol_20, vol_ratio,
    52W hi/lo, and computed trade levels (entry, SL, target, R:R).
    """
    if len(bars) < 20:
        return {}
    closes  = [b["c"] for b in bars]
    highs   = [b["h"] for b in bars]
    lows    = [b["l"] for b in bars]
    volumes = [b["v"] for b in bars]
    n       = len(closes)

    atr_val  = atr(highs, lows, closes)
    avg_vol  = (sum(volumes[-21:-1]) / 20 if n >= 21
                else sum(volumes) / max(len(volumes), 1))
    price    = closes[-1]

    # ── Trade levels ──────────────────────────────────────────────────────────
    entry     = round(price, 2)
    swing_lo  = min(lows[-10:]) if len(lows) >= 10 else price - atr_val * 1.5
    sl_swing  = round(swing_lo - atr_val * 0.3, 2)
    sl_atr    = round(price - atr_val * 1.5, 2)
    sl_dist   = (price - sl_swing) / price * 100
    stop      = sl_swing if 1.0 <= sl_dist <= 7.0 else sl_atr
    stop      = round(stop, 2)
    risk      = price - stop
    min_tgt   = price + risk * 2.0

    # Pivot-based target (nearest pivot high that gives ≥1:2)
    pivot_highs = []
    scan_start  = max(0, n - 252)
    for i in range(scan_start, n - 1):
        ph  = highs[i]
        lo_ = max(0, i - 5)
        hi_ = min(n - 1, i + 6)
        if ph == max(highs[lo_:hi_]) and ph >= min_tgt:
            pivot_highs.append(ph)
    pivot_highs = sorted(set(pivot_highs))
    target_pivot = pivot_highs[0] if pivot_highs else None

    hi52       = max(closes[-252:]) if n >= 252 else max(closes)
    target_52w = round(hi52, 2) if hi52 >= min_tgt else None
    target_2_5 = round(price + risk * 2.5, 2)

    if target_pivot:
        target = round(target_pivot, 2)
    elif target_52w and target_52w <= price * 1.15:
        target = target_52w
    else:
        target = target_2_5
    rr_val = round((target - price) / risk, 2) if risk > 0 else 0
    if rr_val < 2.0:
        target = target_2_5
        rr_val = round((target - price) / risk, 2) if risk > 0 else 0

    return {
        "ema9":   ema(closes[-15:],   9),
        "ema20":  ema(closes[-25:],  20),
        "ema50":  ema(closes[-60:],  50) if n >= 55  else None,
        "ema200": ema(closes[-210:], 200) if n >= 205 else None,
        "rsi14":  rsi(closes[-20:],  14),
        "atr14":  atr_val,
        "avg_vol_20":  int(avg_vol),
        "vol_ratio":   round(volumes[-1] / avg_vol, 2) if avg_vol else 1.0,
        "hi52":        max(closes[-252:]) if n >= 252 else max(closes),
        "lo52":        min(closes[-252:]) if n >= 252 else min(closes),
        "price":       price,
        "trade_entry": entry,
        "trade_sl":    stop,
        "trade_target":target,
        "trade_rr":    rr_val,
        "trade_sl_dist_pct": round((price - stop) / price * 100, 2),
    }


def compute_emas(closes: list) -> dict:
    """Return EMA9/21/50/200 for signal engine."""
    n = len(closes)
    return {
        "ema9":  ema(closes[-15:],   9),
        "ema21": ema(closes[-30:],  21),
        "ema50": ema(closes[-65:],  50)  if n >= 55  else None,
        "ema200":ema(closes[-210:], 200) if n >= 200 else None,
    }
