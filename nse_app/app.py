"""
NSE Screener Proxy — entry point.
Only wiring lives here: Flask setup, Blueprint registration, before_request hook.

To add a new feature:
  1. Create/edit the relevant file in core/ or routes/
  2. Register the blueprint below if it's a new routes file
  3. This file stays tiny — do NOT add logic here
"""
import os
from flask import Flask, request as freq
from flask_cors import CORS

from .routes.charts   import bp as charts_bp
from .routes.analyze  import bp as analyze_bp
from .routes.signals  import bp as signals_bp
from .routes.nifty5min import bp as nifty5min_bp
from .routes.misc     import bp as misc_bp, record_visit
from .core.alerts     import start_alert_checker

app = Flask(__name__, template_folder="..")
CORS(app)

app.register_blueprint(charts_bp)
app.register_blueprint(analyze_bp)
app.register_blueprint(signals_bp)
app.register_blueprint(nifty5min_bp)
app.register_blueprint(misc_bp)


@app.before_request
def track_visitor():
    if freq.path in ("/api/health", "/api/visitors") or freq.path.startswith("/static"):
        return
    record_visit(freq)


# Start background WhatsApp alert checker
start_alert_checker()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
