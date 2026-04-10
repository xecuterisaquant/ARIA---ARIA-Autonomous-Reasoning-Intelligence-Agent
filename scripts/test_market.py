"""Quick smoke test for src.market endpoints."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.market import get_market_data, get_fear_greed

print("=== get_market_data('BTC') ===")
d = get_market_data("BTC")
for k, v in d.items():
    print(f"  {k}: {v}")

print("\n=== get_market_data('ETH') ===")
d2 = get_market_data("ETH")
for k, v in d2.items():
    print(f"  {k}: {v}")

print("\n=== get_fear_greed() ===")
fg = get_fear_greed()
for k, v in fg.items():
    print(f"  {k}: {v}")
