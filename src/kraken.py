"""Kraken futures paper trading CLI wrapper.

All subprocess calls to the `kraken` binary live here.
Uses `kraken futures paper` commands for perpetual futures trading.
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
        timeout=15,
    )
    stdout = (result.stdout or b"").decode("utf-8", errors="replace")
    stderr = (result.stderr or b"").decode("utf-8", errors="replace")
    if result.returncode != 0:
        err = (stderr or stdout).strip()
        raise RuntimeError(f"kraken exited {result.returncode}: {err}")
    return json.loads(stdout)


def get_portfolio_status() -> dict:
    """Fetch current portfolio status from `kraken futures paper status`.

    Returns total_value, unrealized_pnl, pnl_percent, total_trades.
    Falls back gracefully on any CLI error.
    """
    try:
        raw = run_kraken_command(["futures", "paper", "status"])
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
    """Combine `kraken futures paper balance` and `kraken futures paper positions`.

    Returns {"collateral": float, "positions": {symbol: {"side": str, "size": float, "entry_price": float, "unrealized_pnl": float}}}.
    Falls back gracefully if the CLI is unavailable.
    """
    try:
        bal_raw = run_kraken_command(["futures", "paper", "balance"])
        # available_margin = free capital after position margin is locked
        # collateral = total account value (ignores margin used by open positions)
        collateral = float(
            bal_raw.get("available_margin")
            or bal_raw.get("availableMargin")
            or bal_raw.get("collateral")
            or bal_raw.get("equity")
            or bal_raw.get("USD", 0.0)
        )

        pos_raw = run_kraken_command(["futures", "paper", "positions"])
        positions: dict[str, dict] = {}
        for p in pos_raw.get("positions", pos_raw.get("openPositions", [])):
            symbol_raw = p.get("symbol") or p.get("instrument") or ""
            side = (p.get("side") or p.get("direction") or "").lower()
            size = abs(float(p.get("size") or p.get("quantity") or p.get("volume") or 0))
            entry_price = float(p.get("entry_price") or p.get("avgEntryPrice") or p.get("price") or 0)
            upnl = float(p.get("unrealized_pnl") or p.get("unrealizedPnl") or p.get("pnl") or 0)
            if size > 0:
                positions[symbol_raw] = {
                    "side": side,
                    "size": size,
                    "entry_price": entry_price,
                    "unrealized_pnl": upnl,
                }

        return {"collateral": collateral, "positions": positions}

    except FileNotFoundError:
        return {"error": "kraken CLI not found"}
    except subprocess.TimeoutExpired:
        return {"error": "kraken CLI timed out"}
    except (json.JSONDecodeError, KeyError, RuntimeError) as exc:
        return {"error": f"get_kraken_balance failed: {exc}"}


def execute_futures_trade(action: str, futures_symbol: str, size: float) -> dict:
    """Execute a Kraken futures paper trade via `kraken futures paper buy/sell`.

    action: "buy" or "sell"
    futures_symbol: e.g. "PI_XBTUSD"
    Returns a dict with keys: success, raw_output, order_id, fill_price, fill_size, fee.
    """
    if size <= 0:
        return {"success": False, "raw_output": f"Invalid trade size: {size}"}

    cmd = ["kraken", "futures", "paper", action.lower(), futures_symbol, f"{size:.8f}", "--type", "market"]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=15)
        out = (result.stdout or b"").decode("utf-8", errors="replace")
        err = (result.stderr or b"").decode("utf-8", errors="replace")
        raw = (out + err).strip() or f"[kraken exited {result.returncode}]"

        parsed = {
            "success": result.returncode == 0,
            "raw_output": raw,
            "order_id": None,
            "fill_price": None,
            "fill_size": None,
            "fee": None,
        }

        # Try to extract structured data from the CLI table output
        for line in raw.splitlines():
            lower = line.lower()
            if "order id" in lower or "order_id" in lower:
                parsed["order_id"] = line.split("│")[-1].strip() if "│" in line else line.split()[-1]
            elif "status" in lower and "filled" in lower:
                parsed["success"] = True
            elif "fill" in lower and "@" in line:
                # e.g. "│ Fill     ┆ 0.02077958 @ 72179.50 (fee: 0.7499) │"
                fill_part = line.split("│")[-1].strip() if "│" in line else line
                fill_part = fill_part.replace("┆", "").strip()
                try:
                    parts = fill_part.split("@")
                    if len(parts) == 2:
                        parsed["fill_size"] = float(parts[0].strip().split()[-1])
                        price_fee = parts[1].strip()
                        if "(fee:" in price_fee:
                            parsed["fill_price"] = float(price_fee.split("(fee:")[0].strip())
                            parsed["fee"] = float(price_fee.split("(fee:")[1].replace(")", "").strip())
                        else:
                            parsed["fill_price"] = float(price_fee.strip())
                except (ValueError, IndexError):
                    pass  # best-effort parsing

        if result.returncode != 0:
            parsed["success"] = False
            logger.warning("Trade CLI returned exit code %d: %s", result.returncode, raw[:200])

        return parsed

    except FileNotFoundError:
        return {"success": False, "raw_output": f"[MOCK] kraken CLI not found — simulated {action} {size:.8f} {futures_symbol}"}
    except subprocess.TimeoutExpired:
        return {"success": False, "raw_output": "[ERROR] kraken CLI timed out"}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _status_error(msg: str) -> dict:
    return {
        "error": msg,
        "total_value": 0.0,
        "unrealized_pnl": 0.0,
        "pnl_percent": 0.0,
        "total_trades": 0,
    }
