"""
Shared config, constants, and in-memory stores.
Edit this file to change TTLs, limits, or add new global state.
"""
import os, threading

# ── Cache / TTL ────────────────────────────────────────────────────────────────
CACHE_TTL   = 300   # seconds — daily bars
FII_TTL     = 1800  # seconds — FII data

# ── AI ─────────────────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# ── In-memory stores ───────────────────────────────────────────────────────────
_stock_cache: dict = {}
_fii_cache         = {"ts": 0, "data": None}

# TradingView signals (last 200)
_tv_signals: list  = []
_tv_lock           = threading.Lock()
MAX_TV_SIGNALS     = 200

# WhatsApp alert store  {phone: {key, alerts[]}}
_wa_store: dict    = {}
_wa_lock           = threading.Lock()

# Visitor tracking
_visitor_lock      = threading.Lock()
_visitors          = {"today_date": "", "today_ips": set(), "active": {}}
_ACTIVE_TIMEOUT    = 300  # seconds

# ── HTTP User-Agent ─────────────────────────────────────────────────────────────
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)
