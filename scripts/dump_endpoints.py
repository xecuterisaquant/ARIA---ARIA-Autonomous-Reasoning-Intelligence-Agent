"""Dump raw JSON from each PRISM endpoint to see actual key names."""
import sys, pathlib, json, time, requests
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from src.config import API_KEY, BASE_URL

H = {"X-API-Key": API_KEY}
T = 15

endpoints = [
    "/technicals/BTC",
    "/technicals/BTC/trend",
    "/technicals/BTC/support-resistance",
    "/market/fear-greed",
    "/macro/market",
    "/macro/summary",
]

for ep in endpoints:
    time.sleep(7)  # respect rate limit
    print(f"\n{'='*60}")
    print(f"GET {ep}")
    print('='*60)
    try:
        r = requests.get(f"{BASE_URL}{ep}", headers=H, timeout=T)
        print(f"Status: {r.status_code}")
        if r.status_code == 200:
            d = r.json()
            print(json.dumps(d, indent=2)[:2000])
        else:
            print(r.text[:500])
    except Exception as e:
        print(f"ERROR: {e}")
