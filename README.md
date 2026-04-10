# ARIA — Autonomous Reasoning & Intelligence Agent

A crypto futures paper-trading agent powered by Claude claude-opus-4-5, built for the **AI Trading Agents** hackathon (Kraken + ERC-8004, $55k prize pool, deadline April 12 2026).

ARIA runs autonomous 3-minute trading cycles: fetches real-time market data from PRISM API, feeds it to Claude's 5-step regime detection framework, validates every decision through a 6-rule risk engine, and executes BTC/ETH perpetual futures on Kraken's paper trading platform.

---

## Architecture

```
aria.py                  # Thin orchestrator — wires everything together
├── src/
│   ├── config.py        # Centralised env vars, paths, logging
│   ├── market.py        # PRISM API wrapper with TTL cache
│   ├── agent.py         # Claude decision engine (regime framework)
│   ├── risk.py          # 6-rule risk management + ATR-based stops
│   ├── kraken.py        # Kraken CLI subprocess wrapper
│   ├── memory.py        # Trade lifecycle memory (entry → exit)
│   ├── store.py         # Atomic JSON persistence
│   └── dashboard.py     # Flask web dashboard + REST API
├── templates/
│   └── dashboard.html   # Jinja2 dashboard template
├── tests/               # Pytest test suite
├── Dockerfile           # Production container (Python 3.12 + Rust/kraken-cli)
└── railway.toml         # Railway deployment config
```

## How It Works

Each 3-minute cycle:

1. **Portfolio Sync** — Fetches collateral, open positions, and P&L from Kraken CLI
2. **Market Data** — Pulls price, technicals (RSI, MACD, ADX, Bollinger), support/resistance, ATR, momentum, and on-chain flow from PRISM API (with 5-minute TTL cache)
3. **Fear & Greed** — Fetches market sentiment index
4. **Claude Decision** — Sends all data to Claude claude-opus-4-5 with a 5-step regime framework:
   - Regime detection (TRENDING_UP, RANGING, VOLATILE, TRENDING_DOWN)
   - Confluence counting (requires ≥2 aligned signals)
   - Structure context (support/resistance awareness)
   - Regime-action mapping (different rules per regime)
   - Final output: action, confidence, risk level, reasoning
5. **Risk Check** — 6 ordered rules + ATR-based pre-rules:
   - Pre: Force-close at 2×ATR loss or 3×ATR profit
   - R1: Confidence ≥ 45% minimum
   - R2: No HIGH-risk entries
   - R3: No same-direction duplicates
   - R4: ≤ 10 trades/day
   - R5: Position sizing 10-15% of collateral, capped 20% of starting balance
   - R6: Total exposure ≤ 30% of starting balance
6. **Execution** — Sends market orders via Kraken CLI, validates fill data
7. **Memory** — Records trade entry/exit with outcome tracking for Claude to learn from
8. **Dashboard** — Updates status JSON for the live web dashboard

## Setup

### Prerequisites

- Python 3.12+
- [kraken-cli](https://crates.io/crates/kraken-cli) (Rust binary, v0.3.0)
- PRISM API key ([prismapi.ai](https://prismapi.ai))
- Anthropic API key

### Install

```bash
# Clone
git clone https://github.com/your-user/aria.git
cd aria

# Virtual environment
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # Linux/macOS

# Dependencies
pip install -r requirements.txt

# Kraken CLI (requires Rust toolchain)
cargo install kraken-cli

# Init paper trading account
kraken futures paper init
```

### Configure

Copy `.env.example` to `.env` and fill in your keys:

```bash
cp .env.example .env
```

| Variable | Required | Default | Description |
|---|---|---|---|
| `PRISM_API_KEY` | Yes | — | PRISM API key for market data |
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key for Claude |
| `ARIA_DASHBOARD_TOKEN` | No | — | Bearer token for dashboard auth |
| `ARIA_ASSETS` | No | `BTC,ETH` | Comma-separated asset list |
| `ARIA_LOOP_INTERVAL` | No | `180` | Seconds between trading cycles |
| `PORT` | No | `8080` | Dashboard HTTP port |

### Run

```bash
python aria.py
```

The agent starts immediately with a preflight check (API keys, kraken CLI, paper account, 1× leverage), then enters the trading loop.

## Dashboard

ARIA serves a live web dashboard at `http://localhost:8080`:

- **Summary bar** — Portfolio value, unrealized P&L, total trades
- **Fear & Greed gauge** — Real-time market sentiment
- **Asset cards** — Per-asset regime, action, confidence, reasoning
- **Trade history** — Last 50 trades with full details

### API Endpoints

| Endpoint | Auth | Description |
|---|---|---|
| `GET /healthz` | No | Health check (`{"status": "ok"}`) |
| `GET /` | Yes | HTML dashboard |
| `GET /api/trades` | Yes | Full trade log (JSON) |
| `GET /api/status` | Yes | Current portfolio status (JSON) |

Auth: query param `?token=<TOKEN>` or header `X-Aria-Token: <TOKEN>`.

## Testing

```bash
pip install pytest
pytest tests/ -v
```

## Deploy (Railway)

The included `Dockerfile` and `railway.toml` are configured for Railway:

```bash
# Push to deploy
git push
```

Railway will build the Docker image (installs Rust + kraken-cli), run health checks on `/healthz`, and auto-restart on failure.

Set these environment variables in your Railway service:
- `PRISM_API_KEY`
- `ANTHROPIC_API_KEY`
- `ARIA_DASHBOARD_TOKEN` (recommended for production)

## License

MIT
