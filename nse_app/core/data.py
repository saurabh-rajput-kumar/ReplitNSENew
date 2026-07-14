"""
Yahoo Finance fetching + OHLCV parsing.
Edit this file to change data sources, cache TTLs, or bar format.
"""
import json, time
from datetime import datetime
import requests
from .config import _stock_cache, CACHE_TTL, UA


def fetch_yahoo(symbol: str, interval: str = "1d", range_: str = "2y"):
    """Fetch OHLCV bytes from Yahoo Finance.
    interval: 1d / 60m / 15m / 5m
    range_:   1d / 5d / 30d / 60d / 1mo / 6mo / 1y / 2y / 730d / 1825d / max
    """
    cache_key = f"{symbol}_{interval}_{range_}"
    now = time.time()
    if cache_key in _stock_cache:
        ts, data = _stock_cache[cache_key]
        ttl = 60 if interval in ("5m", "15m", "60m") else CACHE_TTL
        if now - ts < ttl:
            return data
    for base in ["https://query1.finance.yahoo.com",
                 "https://query2.finance.yahoo.com"]:
        try:
            r = requests.get(
                f"{base}/v8/finance/chart/{symbol}?interval={interval}&range={range_}",
                headers={"User-Agent": UA, "Accept": "application/json"},
                timeout=20,
            )
            if r.ok:
                _stock_cache[cache_key] = (now, r.content)
                return r.content
        except Exception:
            continue
    return None


def get_live_price(symbol: str) -> float | None:
    """Fetch the latest market price for a symbol (e.g. 'RELIANCE.NS')."""
    for base in ["https://query1.finance.yahoo.com",
                 "https://query2.finance.yahoo.com"]:
        try:
            r = requests.get(
                f"{base}/v8/finance/chart/{symbol}.NS?interval=1m&range=1d",
                headers={"User-Agent": UA, "Accept": "application/json"},
                timeout=10,
            )
            if r.ok:
                price = (r.json().get("chart", {})
                          .get("result", [{}])[0]
                          .get("meta", {})
                          .get("regularMarketPrice"))
                if price:
                    return float(price)
        except Exception:
            continue
    return None


def parse_ohlcv(raw: bytes, symbol: str) -> list:
    """Parse raw Yahoo Finance bytes → list of bar dicts {t,d,o,h,l,c,v}."""
    try:
        d   = json.loads(raw)
        res = d["chart"]["result"][0]
        q   = res["indicators"]["quote"][0]
        ts  = res.get("timestamp", [])
        closes  = q.get("close",  [])
        opens   = q.get("open",   [])
        highs   = q.get("high",   [])
        lows    = q.get("low",    [])
        volumes = q.get("volume", [])
        bars = []
        for i, t in enumerate(ts):
            c = closes[i]
            if c is None or c <= 0:
                continue
            bars.append({
                "t": t,
                "d": datetime.utcfromtimestamp(t).strftime("%Y-%m-%d"),
                "o": round(opens[i]  or c, 2),
                "h": round(highs[i]  or c, 2),
                "l": round(lows[i]   or c, 2),
                "c": round(c,             2),
                "v": int(volumes[i]  or 0),
            })
        return bars
    except Exception as e:
        print(f"[parse_ohlcv:{symbol}] {e}")
        return []
