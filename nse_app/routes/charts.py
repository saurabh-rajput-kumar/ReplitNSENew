"""
Chart proxy routes — stream Yahoo Finance data to the frontend.
/api/chart          daily bars
/api/chart-extended up to 5y daily bars
/api/chart-5m       5-min bars
"""
import os
from flask import Blueprint, request as freq, jsonify, Response, send_file
from ..core.data import fetch_yahoo

bp = Blueprint("charts", __name__)

# Absolute path to HTML — works regardless of working directory
HTML_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "nse_screener_live.html")
HTML_PATH = os.path.abspath(HTML_PATH)


@bp.route("/")
def index():
    return send_file(HTML_PATH)


@bp.route("/api/chart")
def chart():
    sym = "".join(c for c in freq.args.get("symbol", "").upper()
                  if c.isalnum() or c in "-_.%")
    if not sym:
        return jsonify({"error": "missing symbol"}), 400
    data = fetch_yahoo(sym + ".NS" if not sym.endswith(".NS") else sym)
    if not data:
        return jsonify({"error": "upstream failed"}), 502
    return Response(data, mimetype="application/json")


@bp.route("/api/chart-extended")
def chart_extended():
    sym   = "".join(c for c in freq.args.get("symbol", "").upper()
                    if c.isalnum() or c in "-_.%")
    range_ = freq.args.get("range", "5y")
    if range_ not in ("2y", "3y", "5y", "10y", "max"):
        range_ = "5y"
    if not sym:
        return jsonify({"error": "missing symbol"}), 400
    sym_ns = sym + ".NS" if not sym.endswith(".NS") else sym
    data   = fetch_yahoo(sym_ns, interval="1d", range_=range_)
    if not data:
        return jsonify({"error": "upstream failed"}), 502
    return Response(data, mimetype="application/json")


@bp.route("/api/chart-5m")
def chart_5m():
    sym   = "".join(c for c in freq.args.get("symbol", "^NSEI").upper()
                    if c.isalnum() or c in "-_.%^")
    range_ = freq.args.get("range", "30d")
    if range_ not in ("5d", "30d", "60d"):
        range_ = "30d"
    if not sym.startswith("^") and not sym.endswith(".NS"):
        sym = sym + ".NS"
    data = fetch_yahoo(sym, interval="5m", range_=range_)
    if not data:
        return jsonify({"error": f"Could not fetch 5-min data for {sym}"}), 502
    return Response(data, mimetype="application/json")
