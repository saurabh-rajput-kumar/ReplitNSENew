"""
Miscellaneous routes:
  WhatsApp alerts (/api/wa/*)
  TradingView webhook (/api/tv-signal, /api/tv-signals)
  F&O OI proxy (/api/fno-oi)
  Health check (/api/health)
  Visitor tracking (/api/visitors)

Edit this file to change alert message templates or add new utility routes.
"""
import time, hashlib, json
from datetime import datetime, timezone
from flask import Blueprint, request as freq, jsonify
from ..core.config import (_wa_store, _wa_lock, _tv_signals, _tv_lock,
                           MAX_TV_SIGNALS, _visitor_lock, _visitors,
                           _ACTIVE_TIMEOUT, _stock_cache, GEMINI_API_KEY)
from ..core.alerts import send_whatsapp, is_market_open
from ..core.data import fetch_yahoo, parse_ohlcv

bp = Blueprint("misc", __name__)


# ── WhatsApp routes ────────────────────────────────────────────────────────────

@bp.route("/api/wa/test", methods=["POST"])
def wa_test():
    body  = freq.get_json(silent=True) or {}
    phone = str(body.get("phone", "")).strip()
    key   = str(body.get("key",   "")).strip()
    if not phone or not key:
        return jsonify({"ok": False, "error": "phone and key required"}), 400
    msg = ("✅ *NSE Screener Pro* connected!\n"
           "You will now receive WhatsApp alerts when your price targets are hit.")
    ok, err = send_whatsapp(phone, key, msg)
    if ok:
        with _wa_lock:
            if phone not in _wa_store:
                _wa_store[phone] = {"key": key, "alerts": []}
            else:
                _wa_store[phone]["key"] = key
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": err})


@bp.route("/api/wa/alerts", methods=["POST"])
def wa_set_alerts():
    body   = freq.get_json(silent=True) or {}
    phone  = str(body.get("phone",  "")).strip()
    key    = str(body.get("key",    "")).strip()
    alerts = body.get("alerts", [])
    if not phone or not key:
        return jsonify({"ok": False, "error": "phone and key required"}), 400
    with _wa_lock:
        existing   = _wa_store.get(phone, {})
        old_alerts = {a["id"]: a for a in existing.get("alerts", [])}
        merged = []
        for a in alerts:
            aid = a.get("id")
            merged.append(old_alerts[aid] if aid in old_alerts and old_alerts[aid].get("triggered")
                          else a)
        _wa_store[phone] = {"key": key, "alerts": merged, "updated": time.time()}
    return jsonify({"ok": True, "count": len(alerts)})


@bp.route("/api/wa/triggered")
def wa_triggered():
    phone = freq.args.get("phone", "").strip()
    if not phone:
        return jsonify({"triggered": []})
    with _wa_lock:
        info = _wa_store.get(phone, {})
    triggered = [{"id": a["id"], "sym": a["sym"], "at": a.get("at", "")}
                 for a in info.get("alerts", []) if a.get("triggered") and a.get("sentOk", True)]
    return jsonify({"triggered": triggered})


# ── TradingView webhook ────────────────────────────────────────────────────────

@bp.route("/api/tv-signal", methods=["POST"])
def tv_signal_receive():
    try:
        body = freq.get_json(silent=True)
        if body is None:
            raw_text = freq.get_data(as_text=True)
            try:    body = json.loads(raw_text)
            except: body = {"raw": raw_text}
    except Exception:
        body = {}
    signal = {
        "id":        int(time.time() * 1000),
        "ts":        time.time(),
        "dt":        datetime.now().strftime("%d %b %H:%M"),
        "symbol":    str(body.get("symbol",    "UNKNOWN")).upper(),
        "action":    str(body.get("action",    "signal")).upper(),
        "price":     body.get("price", ""),
        "timeframe": str(body.get("timeframe", "?")),
        "strategy":  str(body.get("strategy",  "")),
        "raw":       body,
    }
    with _tv_lock:
        _tv_signals.insert(0, signal)
        if len(_tv_signals) > MAX_TV_SIGNALS:
            _tv_signals.pop()
    print(f"[TV] {signal['symbol']} {signal['action']} @ {signal['price']}")
    return jsonify({"ok": True, "received": signal["dt"]})


@bp.route("/api/tv-signals")
def tv_signals_get():
    limit  = min(int(freq.args.get("limit", 50)), 200)
    symbol = freq.args.get("symbol", "").upper()
    with _tv_lock:
        sigs = list(_tv_signals[:limit])
    if symbol:
        sigs = [s for s in sigs if s["symbol"] == symbol]
    return jsonify({"signals": sigs, "total": len(_tv_signals)})


@bp.route("/api/tv-signals/clear", methods=["POST"])
def tv_signals_clear():
    with _tv_lock:
        _tv_signals.clear()
    return jsonify({"ok": True})


# ── F&O OI (volume proxy) ──────────────────────────────────────────────────────

@bp.route("/api/fno-oi")
def fno_oi():
    sym = "".join(c for c in freq.args.get("symbol", "").upper() if c.isalnum() or c in "-_.")
    if not sym:
        return jsonify({"error": "symbol required"}), 400
    try:
        raw_data = fetch_yahoo(sym + ".NS", interval="1d", range_="1mo")
        if not raw_data:
            return jsonify({"error": f"No data for {sym}"}), 404
        bars = parse_ohlcv(raw_data, sym)
        if len(bars) < 5:
            return jsonify({"error": "insufficient data"}), 400

        closes  = [b["c"] for b in bars]
        volumes = [b["v"] for b in bars]
        n       = len(bars)
        price     = closes[-1]
        prev_close= closes[-2] if n >= 2 else closes[-1]
        price_chg = round((price - prev_close) / prev_close * 100, 2) if prev_close else 0

        avg_vol_5  = sum(volumes[-6:-1]) / 5 if n >= 6 else sum(volumes[:-1]) / max(len(volumes)-1, 1)
        vol_today  = volumes[-1]
        vol_ratio  = round(vol_today / avg_vol_5, 2) if avg_vol_5 else 1.0
        oi_chg     = vol_today - int(avg_vol_5)
        oi_chg_pct = round(oi_chg / avg_vol_5 * 100, 2) if avg_vol_5 else 0.0
        price_3d   = round((closes[-1]-closes[-4])/closes[-4]*100,2) if n>=4 else price_chg

        signal = ("long_build"  if vol_ratio >= 1.0 and price_chg >= 0 else
                  "short_build" if vol_ratio >= 1.0 and price_chg <  0 else
                  "long_unwind" if vol_ratio <  1.0 and price_chg <  0 else "short_cover")
        strength = min(100, 50 + min(30,int(abs(oi_chg_pct)*0.5)) + min(20,int(abs(price_chg)*3)))

        try:
            meta = json.loads(raw_data).get("chart",{}).get("result",[{}])[0].get("meta",{})
            name = meta.get("longName") or meta.get("shortName") or sym
        except Exception:
            name = sym

        return jsonify({"symbol": sym, "name": name, "price": round(price,2), "priceChg": price_chg,
                        "price3dChg": price_3d, "oi": vol_today, "oiChg": oi_chg, "oiChgPct": oi_chg_pct,
                        "volRatio": vol_ratio, "rsi": None, "signal": signal, "strength": strength})
    except Exception as e:
        print(f"[FnO OI] {sym}: {e}")
        return jsonify({"error": str(e)}), 500


# ── Health check ───────────────────────────────────────────────────────────────

@bp.route("/api/health")
def health():
    with _wa_lock:
        wa_phones = len(_wa_store)
        wa_alerts = sum(len(v.get("alerts", [])) for v in _wa_store.values())
    with _tv_lock:
        tv_count = len(_tv_signals)
    return jsonify({
        "status":      "ok",
        "stock_cache": len(_stock_cache),
        "wa_phones":   wa_phones,
        "wa_alerts":   wa_alerts,
        "tv_signals":  tv_count,
        "market_open": is_market_open(),
        "ai_ready":    bool(GEMINI_API_KEY),
        "ai_provider": "Gemini 1.5 Flash (free)" if GEMINI_API_KEY else "not configured",
    })


# ── Visitor tracking ───────────────────────────────────────────────────────────

def _visitor_ip(req) -> str:
    raw = req.headers.get("X-Forwarded-For", req.remote_addr or "unknown")
    return hashlib.sha256(raw.split(",")[0].strip().encode()).hexdigest()[:16]


def record_visit(req):
    now     = time.time()
    today   = datetime.utcnow().strftime("%Y-%m-%d")
    ip_hash = _visitor_ip(req)
    with _visitor_lock:
        if _visitors["today_date"] != today:
            _visitors["today_date"] = today
            _visitors["today_ips"]  = set()
        _visitors["today_ips"].add(ip_hash)
        _visitors["active"][ip_hash] = now
        cutoff = now - _ACTIVE_TIMEOUT
        _visitors["active"] = {k: v for k, v in _visitors["active"].items() if v >= cutoff}


@bp.route("/api/visitors")
def visitors():
    now = time.time()
    with _visitor_lock:
        cutoff = now - _ACTIVE_TIMEOUT
        live  = sum(1 for v in _visitors["active"].values() if v >= cutoff)
        today = len(_visitors["today_ips"])
    return jsonify({"live": live, "today": today})
