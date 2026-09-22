#!/usr/bin/env python3
"""
Halal Coin Tracker — Binance live price tracker

Fetches live entry price at start, then tracks price movement against that
entry (live "exit" price = current price at each refresh), computing % and
$ gain/loss on a hypothetical $1,000 position.

USAGE:
    python halal_coin_tracker.py --once          # single snapshot, then exit
    python halal_coin_tracker.py --loop           # refresh every 15 min until Ctrl+C
    python halal_coin_tracker.py                  # prompts you to pick a mode

SETUP:
    1. pip install requests
    2. Fill in BINANCE_API_KEY / BINANCE_API_SECRET below (or set as
       environment variables — see the os.environ lines).
       NOTE: Binance's public market-data endpoints (price, klines) do NOT
       require an API key at all. The key is only wired in here so this
       script is ready if you later add authenticated endpoints (account
       balance, trading, etc). For pure price tracking, it will work even
       with the key fields left blank.
"""

import os
import sys
import time
import argparse
from datetime import datetime

import requests

# ── CONFIG ────────────────────────────────────────────────────────────────

# Optional — only needed if you later add authenticated Binance endpoints.
# Public price data works without these.
BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.environ.get("BINANCE_API_SECRET", "")

BINANCE_BASE_URL = "https://api.binance.com"

# Hypothetical position size used for the gain/loss-in-dollars column
POSITION_SIZE_USD = 1000

# Refresh interval for --loop mode, in seconds (15 minutes)
REFRESH_SECONDS = 15 * 60

# Coins to track: Binance symbol -> (display name, halal/haram/unknown, business model note)
# Sourced from Saima's "Safe Trading Universe" (project file, liquidity tier 6+,
# GRAM excluded for gambling-linked ecosystem activity). All 13 carry a
# "Halal-leaning" label per that screening; business_model notes are drawn
# from the "Why" reasoning in that same sheet.
COINS = {
    "BTCUSDT": {
        "name": "Bitcoin (BTC)",
        "label": "Halal-leaning",
        "business_model": "Currency / store of value — decentralized digital currency, no interest mechanism.",
    },
    "ETHUSDT": {
        "name": "Ethereum (ETH)",
        "label": "Halal-leaning",
        "business_model": "Smart contract infrastructure — base-layer platform, neutral technology infrastructure.",
    },
    "XRPUSDT": {
        "name": "XRP",
        "label": "Halal-leaning",
        "business_model": "Cross-border payments network — settlement infrastructure, no interest mechanism in the token.",
    },
    "SOLUSDT": {
        "name": "Solana (SOL)",
        "label": "Halal-leaning",
        "business_model": "Smart contract infrastructure — high-throughput base layer, same reasoning as Ethereum.",
    },
    "ZECUSDT": {
        "name": "Zcash (ZEC)",
        "label": "Halal-leaning",
        "business_model": "Privacy coin — currency-like asset; privacy itself isn't prohibited (real-world illicit-market association is a separate reputational caution).",
    },
    "DOGEUSDT": {
        "name": "Dogecoin (DOGE)",
        "label": "Halal-leaning",
        "business_model": "Meme coin — no inherent prohibited element, purely speculative currency-like token.",
    },
    "LINKUSDT": {
        "name": "Chainlink (LINK)",
        "label": "Halal-leaning",
        "business_model": "Oracle / data services — connects blockchains to real-world data, legitimate data-services business.",
    },
    "ADAUSDT": {
        "name": "Cardano (ADA)",
        "label": "Halal-leaning",
        "business_model": "Smart contract infrastructure — base-layer platform, same reasoning as Ethereum.",
    },
    "XLMUSDT": {
        "name": "Stellar (XLM)",
        "label": "Halal-leaning",
        "business_model": "Cross-border payments network — payments infrastructure, same reasoning as XRP.",
    },
    "BCHUSDT": {
        "name": "Bitcoin Cash (BCH)",
        "label": "Halal-leaning",
        "business_model": "Currency (payments) — peer-to-peer digital cash, same reasoning as Bitcoin.",
    },
    "LTCUSDT": {
        "name": "Litecoin (LTC)",
        "label": "Halal-leaning",
        "business_model": "Currency (payments) — digital cash, same reasoning as Bitcoin.",
    },
    "HBARUSDT": {
        "name": "Hedera (HBAR)",
        "label": "Halal-leaning",
        "business_model": "Enterprise DLT infrastructure — general-purpose enterprise ledger, no inherent prohibited mechanism.",
    },
    "AVAXUSDT": {
        "name": "Avalanche (AVAX)",
        "label": "Halal-leaning",
        "business_model": "Smart contract infrastructure — base-layer platform, same reasoning as Ethereum.",
    },
}

# ── CORE LOGIC ───────────────────────────────────────────────────────────

def fetch_price(symbol: str) -> float:
    """Fetch the current live price for a symbol from Binance's public ticker endpoint."""
    url = f"{BINANCE_BASE_URL}/api/v3/ticker/price"
    resp = requests.get(url, params={"symbol": symbol}, timeout=10)
    resp.raise_for_status()
    return float(resp.json()["price"])


def fetch_entry_prices() -> dict:
    """Fetch the live entry price for every tracked coin, right now.

    If a symbol isn't tradable on Binance (delisted, unsupported, typo),
    it's skipped with a warning rather than crashing the whole run — the
    rest of the list keeps going.
    """
    entries = {}
    invalid_symbols = []
    for symbol in list(COINS.keys()):
        try:
            entries[symbol] = fetch_price(symbol)
        except requests.exceptions.HTTPError:
            invalid_symbols.append(symbol)
            print(f"  WARNING: '{symbol}' ({COINS[symbol]['name']}) not found on Binance — skipping.")
            del COINS[symbol]
    if invalid_symbols:
        print(
            f"  {len(invalid_symbols)} symbol(s) skipped — verify they're spelled/listed "
            f"correctly on Binance if this is unexpected.\n"
        )
    return entries


def compute_row(symbol: str, entry_price: float, exit_price: float) -> dict:
    info = COINS[symbol]
    pct_change = ((exit_price - entry_price) / entry_price) * 100
    gain_pct = pct_change if pct_change > 0 else None
    loss_pct = abs(pct_change) if pct_change < 0 else None
    dollar_change = POSITION_SIZE_USD * (pct_change / 100)

    return {
        "coin": info["name"],
        "label": info["label"],
        "business_model": info["business_model"],
        "entry_price": entry_price,
        "exit_price": exit_price,
        "gain_pct": gain_pct,
        "loss_pct": loss_pct,
        "dollar_change": dollar_change,
    }


def print_table(rows: list):
    header = (
        f"{'Coin':<14} {'Label':<9} {'Entry $':>12} {'Exit $':>12} "
        f"{'Gain %':>9} {'Loss %':>9} {'$ on $1000':>12}"
    )
    print(header)
    print("-" * len(header))
    for r in rows:
        gain_str = f"{r['gain_pct']:.2f}%" if r["gain_pct"] is not None else "-"
        loss_str = f"{r['loss_pct']:.2f}%" if r["loss_pct"] is not None else "-"
        dollar_str = f"{r['dollar_change']:+.2f}"
        print(
            f"{r['coin']:<14} {r['label']:<9} {r['entry_price']:>12.4f} "
            f"{r['exit_price']:>12.4f} {gain_str:>9} {loss_str:>9} {dollar_str:>12}"
        )
    print()
    print("Business model notes:")
    for r in rows:
        print(f"  - {r['coin']} ({r['label']}): {r['business_model']}")


def run_once(entry_prices: dict):
    print(f"\nSnapshot at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    rows = []
    for symbol, entry_price in entry_prices.items():
        try:
            exit_price = fetch_price(symbol)
        except requests.exceptions.HTTPError:
            print(f"  WARNING: couldn't refresh '{symbol}' this cycle — skipping.")
            continue
        rows.append(compute_row(symbol, entry_price, exit_price))
    print_table(rows)


def run_loop(entry_prices: dict):
    print(f"Entry prices locked at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}:")
    for symbol, price in entry_prices.items():
        print(f"  {COINS[symbol]['name']}: {price:.4f}")
    print(f"\nRefreshing every {REFRESH_SECONDS // 60} minutes. Press Ctrl+C to stop.\n")

    try:
        while True:
            run_once(entry_prices)
            time.sleep(REFRESH_SECONDS)
    except KeyboardInterrupt:
        print("\nStopped by user. Goodbye.")
        sys.exit(0)


# ── ENTRY POINT ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Halal Coin Tracker")
    parser.add_argument("--once", action="store_true", help="Run a single snapshot and exit")
    parser.add_argument("--loop", action="store_true", help="Refresh every 15 minutes until stopped")
    args = parser.parse_args()

    mode = None
    if args.once:
        mode = "once"
    elif args.loop:
        mode = "loop"
    else:
        choice = input("Run mode — (1) snapshot once, (2) keep refreshing every 15 min: ").strip()
        mode = "once" if choice == "1" else "loop"

    print("Fetching live entry prices from Binance...")
    entry_prices = fetch_entry_prices()

    if mode == "once":
        run_once(entry_prices)
    else:
        run_loop(entry_prices)


if __name__ == "__main__":
    main()
