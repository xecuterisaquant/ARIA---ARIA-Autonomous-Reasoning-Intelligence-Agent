"""Kraken paper trading CLI wrapper.

All subprocess calls to the `kraken` binary live here.
"""
import json
import subprocess

from .config import logger


def run_kraken_command(args: list) -> dict:
    """Run `kraken <args> -o json` and return the parsed JSON result.

    Raises RuntimeError if the process exits non-zero.
    """
    result = subprocess.run(
        ["kraken"] + args + ["-o", "json"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"kraken exited {result.returncode}: {err}")
    return json.loads(result.stdout)


def get_portfolio_status() -> dict:
    """Fetch current portfolio status from `kraken paper status`.

    Returns total_value, unrealized_pnl, pnl_percent, total_trades.
    Falls back gracefully on any CLI error.
    """
    try:
        raw = run_kraken_command(["paper", "status"])
        total_value = (
            raw.get("total_value") or raw.get("portfolio_value") or raw.get("equity") or 0.0
        )
        unrealized_pnl = raw.get("unrealized_pnl") or raw.get("open_pnl") or 0.0
        pnl_percent = (
            raw.get("pnl_percent")
            or raw.get("return_pct")
            or (unrealized_pnl / total_value * 100 if total_value else 0.0)
        )
        total_trades = raw.get("total_trades") or raw.get("trade_count") or 0
        return {
            "total_value": float(total_value),
            "unrealized_pnl": float(unrealized_pnl),
            "pnl_percent": float(pnl_percent),
            "total_trades": int(total_trades),
            "error": None,
        }
    except FileNotFoundError:
        return _status_error("kraken CLI not found")
    except subprocess.TimeoutExpired:
        return _status_error("kraken CLI timed out")
    except (json.JSONDecodeError, KeyError, RuntimeError) as exc:
        return _status_error(f"get_portfolio_status failed: {exc}")


def get_kraken_balance() -> dict:
    """Combine `kraken paper balance` and `kraken paper history`.

    Returns {"USD": float, "positions": {symbol: {"volume": float, "avg_price": float}}}.
    Reconstructs average cost basis by replaying fills chronologically.
    Falls back gracefully if the CLI is unavailable.
    """
    try:
        bal_raw = run_kraken_command(["paper", "balance"])
        balances = {
            asset: info["total"]
            for asset, info in bal_raw.get("balances", {}).items()
            if isinstance(info, dict) and "total" in info
        }

        hist_raw = run_kraken_command(["paper", "history"])
        running: dict[str, dict] = {}
        for t in sorted(hist_raw.get("trades", []), key=lambda x: x.get("time", "")):
            if t.get("status") != "filled":
                continue
            pair: str = t.get("pair", "")
            base = pair[:-3] if len(pair) > 3 else pair
            vol = float(t.get("volume", 0))
            price = float(t.get("price", 0))
            side = t.get("side", "").lower()
            entry = running.setdefault(base, {"volume": 0.0, "cost_basis": 0.0})
            if side == "buy":
                new_vol = entry["volume"] + vol
                entry["cost_basis"] = (
                    (entry["cost_basis"] * entry["volume"] + price * vol) / new_vol
                    if new_vol > 0
                    else 0.0
                )
                entry["volume"] = new_vol
            elif side == "sell":
                entry["volume"] = max(0.0, entry["volume"] - vol)
                if entry["volume"] == 0.0:
                    entry["cost_basis"] = 0.0

        positions = {
            asset: {
                "volume": total,
                "avg_price": round(running.get(asset, {}).get("cost_basis", 0.0), 8),
            }
            for asset, total in balances.items()
            if asset != "USD" and total > 0
        }
        return {"USD": balances.get("USD", 0.0), "positions": positions}

    except FileNotFoundError:
        return {"error": "kraken CLI not found"}
    except subprocess.TimeoutExpired:
        return {"error": "kraken CLI timed out"}
    except (json.JSONDecodeError, KeyError, RuntimeError) as exc:
        return {"error": f"get_kraken_balance failed: {exc}"}


def execute_paper_trade(action: str, symbol: str, volume: float) -> str:
    """Execute a Kraken paper trade via `kraken paper buy/sell`.

    Falls back to a mock response string if the CLI is unavailable.
    """
    pair = f"{symbol}/USD"
    cmd = ["kraken", "paper", action.lower(), pair, f"{volume:.8f}"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return (result.stdout + result.stderr).strip() or f"[kraken exited {result.returncode}]"
    except FileNotFoundError:
        return f"[MOCK] kraken CLI not found — simulated {action} {volume:.8f} {pair}"
    except subprocess.TimeoutExpired:
        return "[ERROR] kraken CLI timed out"


# ── Internal helpers ──────────────────────────────────────────────────────────

def _status_error(msg: str) -> dict:
    return {
        "error": msg,
        "total_value": 0.0,
        "unrealized_pnl": 0.0,
        "pnl_percent": 0.0,
        "total_trades": 0,
    }
