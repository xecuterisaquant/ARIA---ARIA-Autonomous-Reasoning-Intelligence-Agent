"""
ARIA Web Dashboard — reads logs/trades.json + logs/status.json and serves
a lightweight HTML page on PORT (default 8080).

Run standalone:  python dashboard.py
Or imported by aria.py which starts it in a daemon thread.
"""
import json

from flask import Flask, jsonify, render_template_string

from config import LOG_PATH as TRADES_PATH, STATUS_PATH, DASHBOARD_PORT as PORT

app = Flask(__name__)


def _read_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta http-equiv="refresh" content="30" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>ARIA — Trading Dashboard</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: 'Segoe UI', system-ui, sans-serif; background: #0d1117; color: #c9d1d9; min-height: 100vh; padding: 24px; }
    h1 { font-size: 1.6rem; font-weight: 700; color: #58a6ff; margin-bottom: 4px; }
    .subtitle { font-size: 0.8rem; color: #8b949e; margin-bottom: 28px; }
    .cards { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 32px; }
    .card { background: #161b22; border: 1px solid #30363d; border-radius: 10px; padding: 18px 24px; min-width: 180px; flex: 1; }
    .card .label { font-size: 0.72rem; text-transform: uppercase; letter-spacing: .07em; color: #8b949e; margin-bottom: 6px; }
    .card .value { font-size: 1.5rem; font-weight: 700; color: #e6edf3; }
    .card .value.pos { color: #3fb950; }
    .card .value.neg { color: #f85149; }
    .section-title { font-size: 1rem; font-weight: 600; color: #e6edf3; margin-bottom: 12px; border-bottom: 1px solid #30363d; padding-bottom: 8px; }
    .last-decisions { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 32px; }
    .decision-card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 14px 18px; min-width: 200px; flex: 1; }
    .decision-card .asset { font-size: 0.8rem; color: #8b949e; margin-bottom: 4px; }
    .decision-card .action { font-size: 1.1rem; font-weight: 700; margin-bottom: 4px; }
    .decision-card .meta { font-size: 0.75rem; color: #8b949e; margin-bottom: 6px; }
    .decision-card .reasoning { font-size: 0.78rem; color: #c9d1d9; line-height: 1.4; }
    .BUY  { color: #3fb950; }
    .SELL { color: #f85149; }
    .HOLD { color: #e3b341; }
    .table-wrap { overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; font-size: 0.8rem; }
    th { background: #161b22; color: #8b949e; text-align: left; padding: 8px 12px; border-bottom: 1px solid #30363d; white-space: nowrap; }
    td { padding: 8px 12px; border-bottom: 1px solid #21262d; vertical-align: top; }
    tr:hover td { background: #161b22; }
    .reasoning-cell { max-width: 340px; color: #8b949e; line-height: 1.4; }
    .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.7rem; font-weight: 600; }
    .badge.BUY  { background: #1a3a1e; color: #3fb950; }
    .badge.SELL { background: #3a1a1a; color: #f85149; }
    .badge.HOLD { background: #2d2a1a; color: #e3b341; }
    .badge.LOW    { background: #1a2a3a; color: #58a6ff; }
    .badge.MEDIUM { background: #2d2a1a; color: #e3b341; }
    .badge.HIGH   { background: #3a1a1a; color: #f85149; }
    .refresh-note { font-size: 0.72rem; color: #8b949e; text-align: right; margin-top: 20px; }
    .empty { color: #8b949e; font-size: 0.85rem; padding: 20px 0; }
  </style>
</head>
<body>
  <h1>⚡ ARIA</h1>
  <p class="subtitle">Autonomous Reasoning &amp; Intelligence Agent &mdash; Live Dashboard</p>

  <!-- Portfolio cards -->
  <div class="cards">
    <div class="card">
      <div class="label">Portfolio Value</div>
      <div class="value">${{ "{:,.2f}".format(status.total_value) }}</div>
    </div>
    <div class="card">
      <div class="label">Unrealized PnL</div>
      <div class="value {{ 'pos' if status.unrealized_pnl >= 0 else 'neg' }}">
        {{ ('+' if status.unrealized_pnl >= 0 else '') }}${{ "{:,.2f}".format(status.unrealized_pnl|abs) }}
        <span style="font-size:0.85rem">({{ ('+' if status.pnl_percent >= 0 else '') }}{{ "{:.3f}".format(status.pnl_percent) }}%)</span>
      </div>
    </div>
    <div class="card">
      <div class="label">Lifetime Trades</div>
      <div class="value">{{ status.total_trades }}</div>
    </div>
    <div class="card">
      <div class="label">Last Updated</div>
      <div class="value" style="font-size:0.95rem">{{ status.timestamp or "—" }}</div>
    </div>
  </div>

  <!-- Last decision per asset -->
  {% if last_decisions %}
  <div class="section-title">Last Decision Per Asset</div>
  <div class="last-decisions" style="margin-bottom:32px">
    {% for d in last_decisions %}
    <div class="decision-card">
      <div class="asset">{{ d.symbol }}</div>
      <div class="action {{ d.action }}">{{ d.action }}</div>
      <div class="meta">
        Confidence: {{ d.confidence }} &nbsp;|&nbsp;
        Risk: <span class="badge {{ d.risk_level }}">{{ d.risk_level }}</span>
        &nbsp;|&nbsp; {{ d.timestamp }}
      </div>
      <div class="reasoning">{{ d.reasoning or "—" }}</div>
    </div>
    {% endfor %}
  </div>
  {% endif %}

  <!-- Trade history table -->
  <div class="section-title">Trade History ({{ trades|length }} executed trades)</div>
  {% if trades %}
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Time</th>
          <th>Cycle</th>
          <th>Asset</th>
          <th>Action</th>
          <th>Price</th>
          <th>Volume</th>
          <th>USD Value</th>
          <th>Confidence</th>
          <th>Risk</th>
          <th>Balance After</th>
          <th>Reasoning</th>
        </tr>
      </thead>
      <tbody>
        {% for t in trades|reverse %}
        <tr>
          <td style="white-space:nowrap">{{ t.timestamp }}</td>
          <td>{{ t.cycle }}</td>
          <td><strong>{{ t.symbol }}</strong></td>
          <td><span class="badge {{ t.action }}">{{ t.action }}</span></td>
          <td>${{ "{:,.2f}".format(t.price) }}</td>
          <td>{{ "{:.6f}".format(t.volume) }}</td>
          <td>${{ "{:,.2f}".format(t.position_usd) }}</td>
          <td>{{ t.confidence }}</td>
          <td><span class="badge {{ t.risk_level }}">{{ t.risk_level }}</span></td>
          <td>${{ "{:,.2f}".format(t.balance_after) }}</td>
          <td class="reasoning-cell">{{ t.reasoning or "—" }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% else %}
  <p class="empty">No executed trades yet. ARIA is analysing the market...</p>
  {% endif %}

  <p class="refresh-note">Auto-refreshes every 30 seconds</p>
</body>
</html>"""


@app.route("/")
def index():
    trades = _read_json(TRADES_PATH, [])
    raw_status = _read_json(STATUS_PATH, {})

    # Build a status object with safe defaults
    class _S:
        total_value = raw_status.get("total_value", 0.0)
        unrealized_pnl = raw_status.get("unrealized_pnl", 0.0)
        pnl_percent = raw_status.get("pnl_percent", 0.0)
        total_trades = raw_status.get("total_trades", len(trades))
        timestamp = raw_status.get("timestamp", "—")

    # Latest decision per asset (last entry per symbol)
    seen: set = set()
    last_decisions = []
    for t in reversed(trades):
        sym = t.get("symbol", "")
        if sym and sym not in seen:
            seen.add(sym)
            last_decisions.append(t)

    return render_template_string(_HTML, trades=trades, status=_S(), last_decisions=last_decisions)


@app.route("/api/trades")
def api_trades():
    return jsonify(_read_json(TRADES_PATH, []))


@app.route("/api/status")
def api_status():
    return jsonify(_read_json(STATUS_PATH, {}))


def run_dashboard():
    """Start the Flask dashboard. Called from a daemon thread in aria.py."""
    app.run(host="0.0.0.0", port=PORT, use_reloader=False, threaded=True)


if __name__ == "__main__":
    run_dashboard()
