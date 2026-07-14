"""
WhatsApp alerts via CallMeBot + market-hours helper + background checker.
Edit this file to change alert message format or market hours.
"""
import time, threading
from datetime import datetime, timezone
import requests
from .config import _wa_store, _wa_lock
from .data import get_live_price


# ── Market hours ───────────────────────────────────────────────────────────────

def is_market_open() -> bool:
    """True if NSE is currently open (Mon–Fri, 09:15–15:35 IST)."""
    now_utc    = datetime.now(timezone.utc)
    ist_hour   = (now_utc.hour + 5) % 24
    ist_minute = (now_utc.minute + 30) % 60
    if (now_utc.minute + 30) >= 60:
        ist_hour = (ist_hour + 1) % 24
    ist_total  = ist_hour * 60 + ist_minute
    if now_utc.weekday() > 4:
        return False
    return (9 * 60 + 15) <= ist_total <= (15 * 60 + 35)


# ── WhatsApp via CallMeBot ─────────────────────────────────────────────────────

def send_whatsapp(phone: str, key: str, message: str) -> tuple[bool, str]:
    try:
        r = requests.get(
            "https://api.callmebot.com/whatsapp.php",
            params={"phone": phone, "text": message, "apikey": key},
            timeout=15,
        )
        body = r.text.lower()
        if r.ok and ("message queued" in body or "message sent" in body
                     or "queued" in body
                     or (r.status_code == 200 and "error" not in body)):
            return True, ""
        return False, r.text[:120]
    except Exception as e:
        return False, str(e)[:120]


# ── Background alert checker ───────────────────────────────────────────────────

def _check_all_alerts():
    with _wa_lock:
        snapshot = {p: dict(v) for p, v in _wa_store.items()}
    for phone, info in snapshot.items():
        key    = info.get("key", "")
        alerts = [a for a in info.get("alerts", []) if not a.get("triggered")]
        for alert in alerts:
            sym    = alert.get("sym", "")
            cond   = alert.get("cond", "above")
            target = float(alert.get("price", 0))
            aid    = alert.get("id")
            price  = get_live_price(sym)
            if price is None:
                continue
            triggered = (cond == "above" and price >= target) or \
                        (cond == "below" and price <= target)
            if not triggered:
                continue
            arrow  = "📈" if cond == "above" else "📉"
            dirstr = "crossed above" if cond == "above" else "crossed below"
            now_ist = datetime.now().strftime("%d %b %H:%M")
            msg = (f"{arrow} *NSE Alert* — *{sym}*\n"
                   f"Price ₹{price:.1f} {dirstr} your target ₹{target}\n"
                   f"Time: {now_ist} IST")
            ok, _ = send_whatsapp(phone, key, msg)
            with _wa_lock:
                if phone in _wa_store:
                    for a in _wa_store[phone]["alerts"]:
                        if a.get("id") == aid:
                            a["triggered"] = True
                            a["at"]        = now_ist
                            a["sentOk"]    = ok
                            break


def start_alert_checker():
    """Start the background thread that polls prices every 5 min."""
    def loop():
        print("[WA] Alert checker started")
        while True:
            time.sleep(300)
            try:
                if is_market_open():
                    _check_all_alerts()
            except Exception as e:
                print(f"[WA] Checker error: {e}")
    t = threading.Thread(target=loop, daemon=True)
    t.start()
