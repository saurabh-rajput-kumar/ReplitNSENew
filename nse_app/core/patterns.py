"""
Market structure detection: S/R zones, candlestick patterns, FVGs, Order Blocks.
Edit this file to tune detection thresholds or add new pattern types.
"""


# ── Candlestick patterns ───────────────────────────────────────────────────────

def detect_candle_patterns(bars: list) -> list:
    """Detect candlestick patterns in last 5 bars. Returns up to 6 patterns."""
    patterns = []
    n = len(bars)
    if n < 3:
        return patterns

    def body(b):        return abs(b["c"] - b["o"])
    def rng(b):         return b["h"] - b["l"]
    def upper_wick(b):  return b["h"] - max(b["c"], b["o"])
    def lower_wick(b):  return min(b["c"], b["o"]) - b["l"]

    for i in range(max(1, n - 5), n):
        b   = bars[i]; b1 = bars[i - 1]
        bd  = body(b);  rg = rng(b)
        uw  = upper_wick(b); lw = lower_wick(b)
        bull = b["c"] > b["o"]

        if lw >= bd * 2.5 and uw <= bd * 0.3 and rg > 0 and b1["c"] < b1["o"]:
            patterns.append({"name": "Hammer / Pin Bar",   "bullish": True,
                             "level": round(b["l"], 2), "bar": i})
        if uw >= bd * 2.5 and lw <= bd * 0.3 and rg > 0 and b1["c"] > b1["o"]:
            patterns.append({"name": "Shooting Star",      "bullish": False,
                             "level": round(b["h"], 2), "bar": i})
        if (bull and not (b1["c"] > b1["o"])
                and b["o"] <= b1["c"] and b["c"] >= b1["o"] and bd > body(b1) * 1.2):
            patterns.append({"name": "Bullish Engulfing",  "bullish": True,
                             "level": round(b["l"], 2), "bar": i})
        if (not bull and b1["c"] > b1["o"]
                and b["o"] >= b1["c"] and b["c"] <= b1["o"] and bd > body(b1) * 1.2):
            patterns.append({"name": "Bearish Engulfing",  "bullish": False,
                             "level": round(b["h"], 2), "bar": i})
        if bd <= rg * 0.1 and rg > 0:
            patterns.append({"name": "Doji",               "bullish": None,
                             "level": round(b["c"], 2), "bar": i})

    return patterns[-6:]


# ── Fair Value Gaps ────────────────────────────────────────────────────────────

def detect_fvg(bars: list, current_price: float) -> list:
    """Detect unfilled Fair Value Gaps in last 50 bars, sorted by proximity."""
    fvgs = []
    n = len(bars)
    for i in range(2, min(n, 50)):
        if bars[i]["l"] > bars[i - 2]["h"]:
            lo, hi = bars[i - 2]["h"], bars[i]["l"]
            filled = any(bars[j]["l"] <= hi and bars[j]["h"] >= lo
                        for j in range(i + 1, n))
            fvgs.append({"type": "bullish", "lo": round(lo, 2), "hi": round(hi, 2),
                         "mid": round((lo + hi) / 2, 2), "filled": filled,
                         "date": bars[i]["d"],
                         "near": abs(current_price - (lo + hi) / 2) / current_price < 0.02})
        if bars[i]["h"] < bars[i - 2]["l"]:
            lo, hi = bars[i]["h"], bars[i - 2]["l"]
            filled = any(bars[j]["l"] <= hi and bars[j]["h"] >= lo
                        for j in range(i + 1, n))
            fvgs.append({"type": "bearish", "lo": round(lo, 2), "hi": round(hi, 2),
                         "mid": round((lo + hi) / 2, 2), "filled": filled,
                         "date": bars[i]["d"],
                         "near": abs(current_price - (lo + hi) / 2) / current_price < 0.02})

    fvgs = [f for f in fvgs if not f["filled"]]
    fvgs.sort(key=lambda x: abs(current_price - x["mid"]))
    return fvgs[:6]


# ── Order Blocks ───────────────────────────────────────────────────────────────

def detect_order_blocks(bars: list, current_price: float) -> list:
    """Detect SMC order blocks in last 60 bars, near-price first."""
    obs = []
    n   = len(bars)
    if n < 5:
        return []
    for i in range(3, min(n - 3, 60)):
        b0, b1 = bars[i], bars[i + 1]
        if b0["c"] > b0["o"] and b1["c"] < b1["o"] and b1["c"] < b0["l"]:
            obs.append({"type": "bearish_ob",
                        "lo": round(b0["o"], 2), "hi": round(b0["h"], 2),
                        "date": b0["d"],
                        "near": current_price <= b0["h"] * 1.005 and current_price >= b0["o"] * 0.995})
        if b0["c"] < b0["o"] and b1["c"] > b1["o"] and b1["c"] > b0["h"]:
            obs.append({"type": "bullish_ob",
                        "lo": round(b0["l"], 2), "hi": round(b0["o"], 2),
                        "date": b0["d"],
                        "near": current_price >= b0["l"] * 0.995 and current_price <= b0["o"] * 1.005})
    obs.sort(key=lambda x: (not x["near"], abs(current_price - (x["lo"] + x["hi"]) / 2)))
    return obs[:4]


# ── S/R Zone Detection ─────────────────────────────────────────────────────────

def find_sr_zones(bars: list, current_price: float) -> dict:
    """
    Detect support/resistance zones via pivot clustering.
    Returns {"supports": [...], "resistances": [...], "atr": float}
    Each zone includes a trade plan (entry, SL, target, R:R).
    """
    if len(bars) < 20:
        return {"supports": [], "resistances": []}

    highs  = [b["h"] for b in bars]
    lows   = [b["l"] for b in bars]
    n      = len(bars)
    atr_v  = (sum(highs[i] - lows[i] for i in range(max(0, n - 20), n))
              / min(20, n) or current_price * 0.01)

    # 1. Find pivot highs/lows (5-bar window each side)
    pivot_highs, pivot_lows = [], []
    window = 5
    for i in range(window, n - window):
        if highs[i] == max(highs[i - window: i + window + 1]):
            pivot_highs.append({"price": highs[i], "bar": i, "date": bars[i]["d"]})
        if lows[i]  == min(lows[i - window: i + window + 1]):
            pivot_lows.append( {"price": lows[i],  "bar": i, "date": bars[i]["d"]})

    # 2. Cluster pivots within 0.8%
    def cluster(pivots, tol=0.008):
        if not pivots:
            return []
        pivots = sorted(pivots, key=lambda x: x["price"])
        zones  = []
        grp    = [pivots[0]]
        for p in pivots[1:]:
            if abs(p["price"] - grp[0]["price"]) / grp[0]["price"] <= tol:
                grp.append(p)
            else:
                zones.append(grp); grp = [p]
        zones.append(grp)
        return zones

    # 3. Build zone objects
    def build_zone(pts, zone_type):
        prices  = [p["price"] for p in pts]
        idxs    = [p["bar"]   for p in pts]
        center  = sum(prices) / len(prices)
        spread  = max(prices) - min(prices)
        touches = len(pts)
        recent  = max(idxs) / n
        strength_pct = min(100, int(touches * (0.5 + 0.5 * recent) * 20))
        half    = max(spread / 2, atr_v * 0.4)
        z_lo    = round(center - half, 2)
        z_hi    = round(center + half, 2)
        dist    = round((center - current_price) / current_price * 100, 2)
        risk    = round(atr_v * 1.5, 2)
        if zone_type == "resistance":
            entry   = z_lo
            sl      = round(z_hi + atr_v * 0.5, 2)
            target  = round(entry - risk * 2.5, 2)
            action  = "SELL / SHORT"
            trigger = "bearish candle at zone (upper wick, engulfing, shooting star)"
        else:
            entry   = z_hi
            sl      = round(z_lo - atr_v * 0.5, 2)
            target  = round(entry + risk * 2.5, 2)
            action  = "BUY / LONG"
            trigger = "bullish candle at zone (hammer, bullish engulfing, pin bar)"
        actual_risk   = abs(entry - sl)
        actual_reward = abs(target - entry)
        rr = round(actual_reward / actual_risk, 1) if actual_risk > 0 else 0
        return {
            "type":     zone_type,
            "center":   round(center, 2),
            "zone_lo":  z_lo, "zone_hi": z_hi,
            "touches":  touches,
            "strength": strength_pct,
            "strength_label": "Strong" if strength_pct >= 60 else "Medium" if strength_pct >= 35 else "Weak",
            "recency":  round(recent * 100),
            "last_date": bars[max(idxs)]["d"],
            "dist_pct": dist,
            "trade":    {"action": action, "entry": entry, "sl": sl,
                         "target": target, "rr": rr, "trigger": trigger},
        }

    # 4. Build, filter, sort
    high_z = cluster(pivot_highs)
    low_z  = cluster(pivot_lows)
    supports    = []
    resistances = []
    for z in low_z:
        c = sum(p["price"] for p in z) / len(z)
        if c < current_price * 0.995 and len(z) >= 2:
            supports.append(build_zone(z, "support"))
    for z in high_z:
        c = sum(p["price"] for p in z) / len(z)
        if c > current_price * 1.005 and len(z) >= 2:
            resistances.append(build_zone(z, "resistance"))
    supports    = sorted(
        sorted(supports,    key=lambda x:  x["dist_pct"], reverse=True)[-6:],
        key=lambda x: -x["strength"])
    resistances = sorted(
        sorted(resistances, key=lambda x:  x["dist_pct"])[:6],
        key=lambda x: -x["strength"])
    return {"supports": supports, "resistances": resistances, "atr": round(atr_v, 2)}
