"""ARIA Web Dashboard — reads logs/trades.json + logs/status.json and serves
a lightweight HTML page on PORT (default 8080).

Run standalone:  python -m src.dashboard
Or imported by aria.py which starts it in a daemon thread.
"""
import json
import functools
import os

from flask import Flask, jsonify, render_template_string, request, abort

from .config import LOG_PATH as TRADES_PATH, STATUS_PATH, DASHBOARD_PORT as PORT, DASHBOARD_TOKEN

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TEMPLATE_PATH = os.path.join(_ROOT, "templates", "dashboard.html")

app = Flask(__name__)


def _require_auth(f):
    """Decorator: reject requests unless ARIA_DASHBOARD_TOKEN matches (if set)."""
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if DASHBOARD_TOKEN:
            token = request.args.get("token") or request.headers.get("X-Aria-Token", "")
            if token != DASHBOARD_TOKEN:
                abort(401)
        return f(*args, **kwargs)
    return wrapper


def _read_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _load_template() -> str:
    """Load the dashboard HTML template from disk (allows hot-editing)."""
    with open(_TEMPLATE_PATH, encoding="utf-8") as f:
        return f.read()


@app.route("/healthz")
def healthz():
    """Unauthenticated health check for Railway / load balancers."""
    return jsonify({"status": "ok"}), 200


@app.route("/")
@_require_auth
def index():
    trades = _read_json(TRADES_PATH, [])
    raw = _read_json(STATUS_PATH, {})

    class _S:
        total_value = raw.get("total_value", 0.0)
        unrealized_pnl = raw.get("unrealized_pnl", 0.0)
        pnl_percent = raw.get("pnl_percent", 0.0)
        total_trades = raw.get("total_trades", len(trades))
        timestamp = raw.get("timestamp", "—")

    fg = raw.get("fear_greed") or {}
    ld = raw.get("latest_decisions") or {}
    decisions = sorted(ld.values(), key=lambda d: d.get("symbol", "")) if ld else []

    return render_template_string(
        _load_template(),
        trades=trades,
        status=_S(),
        fg_score=fg.get("score"),
        fg_label=fg.get("label", ""),
        decisions=decisions,
    )


@app.route("/api/trades")
@_require_auth
def api_trades():
    return jsonify(_read_json(TRADES_PATH, []))


@app.route("/api/status")
@_require_auth
def api_status():
    return jsonify(_read_json(STATUS_PATH, {}))


def run_dashboard():
    """Start the Flask dashboard. Called from a daemon thread in aria.py."""
    app.run(host="0.0.0.0", port=PORT, use_reloader=False, threaded=True)


if __name__ == "__main__":
    run_dashboard()
