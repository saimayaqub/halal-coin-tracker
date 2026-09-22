#!/usr/bin/env python3
"""
Halal Coin Tracker — Binance live price tracker

Two ways to run this:

1. LOCAL / INTERACTIVE (on your own machine):
    python halal_coin_tracker.py --once      # single snapshot, then exit
    python halal_coin_tracker.py --loop      # refresh every 15 min until Ctrl+C
    python halal_coin_tracker.py             # prompts you to pick a mode

2. CI / SCHEDULED (e.g. GitHub Actions, cron):
   Each scheduled run is a fresh process — it can't just "stay open" for 15
   minutes like --loop does locally. So --ci mode instead PERSISTS the entry
   price to a small JSON file (entry_state.json by default). The first run
   fetches and locks in entry prices; every run after that reads the same
   entry prices back and compares against the live "exit" price, so the
   gain/loss stays anchored to that original entry across runs.

    python halal_coin_tracker.py --ci                 # normal scheduled run
    python halal_coin_tracker.py --ci --reset-entry    # start a fresh entry point

   --ci also appends every run's results to a CSV log (results_log.csv by
   default) so you get a running history, not just the latest snapshot.

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
import csv
import json
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

# Default file paths for --ci (scheduled/GitHub Actions) mode
STATE_FILE_DEFAULT = "entry_state.json"
LOG_FILE_DEFAULT = "results_log.csv"

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


# ── CI / SCHEDULED MODE (e.g. GitHub Actions) ───────────────────────────

def load_entry_state(state_file: str):
    """Return the saved entry-price dict, or None if no state file exists yet."""
    if not os.path.exists(state_file):
        return None
    with open(state_file, "r") as f:
        data = json.load(f)
    # Only trust symbols we still track; drop anything stale/removed
    return {sym: price for sym, price in data.items() if sym in COINS}


def save_entry_state(state_file: str, entry_prices: dict):
    with open(state_file, "w") as f:
        json.dump(entry_prices, f, indent=2)


def append_csv_log(log_file: str, rows: list, timestamp: str):
    file_exists = os.path.exists(log_file)
    with open(log_file, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(
                ["timestamp", "coin", "label", "entry_price", "exit_price",
                 "gain_pct", "loss_pct", "dollar_change_on_1000"]
            )
        for r in rows:
            writer.writerow([
                timestamp,
                r["coin"],
                r["label"],
                f"{r['entry_price']:.6f}",
                f"{r['exit_price']:.6f}",
                f"{r['gain_pct']:.2f}" if r["gain_pct"] is not None else "",
                f"{r['loss_pct']:.2f}" if r["loss_pct"] is not None else "",
                f"{r['dollar_change']:.2f}",
            ])


def run_ci(state_file: str, log_file: str, reset_entry: bool):
    """One scheduled run: load or create entry prices, fetch live exit prices,
    print the table, and append the results to the CSV log."""
    entry_prices = None if reset_entry else load_entry_state(state_file)

    if entry_prices is None:
        print("No existing entry state found (or --reset-entry used) — "
              "fetching fresh entry prices and locking them in.")
        entry_prices = fetch_entry_prices()
        save_entry_state(state_file, entry_prices)
    else:
        print(f"Loaded entry prices from {state_file} (set previously).")
        # Drop any tracked coin whose symbol turned out invalid this run
        for symbol in list(COINS.keys()):
            if symbol not in entry_prices:
                pass  # covered by fetch_entry_prices' own skip-logic on future resets

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\nSnapshot at {timestamp}")
    rows = []
    for symbol, entry_price in entry_prices.items():
        if symbol not in COINS:
            continue
        try:
            exit_price = fetch_price(symbol)
        except requests.exceptions.HTTPError:
            print(f"  WARNING: couldn't fetch '{symbol}' this run — skipping.")
            continue
        rows.append(compute_row(symbol, entry_price, exit_price))

    print_table(rows)
    append_csv_log(log_file, rows, timestamp)
    print(f"\nAppended {len(rows)} row(s) to {log_file}")


# ── ENTRY POINT ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Halal Coin Tracker")
    parser.add_argument("--once", action="store_true", help="Run a single snapshot and exit")
    parser.add_argument("--loop", action="store_true", help="Refresh every 15 minutes until stopped (local use)")
    parser.add_argument("--ci", action="store_true", help="Scheduled/CI mode — persists entry price to a state file (GitHub Actions, cron)")
    parser.add_argument("--reset-entry", action="store_true", help="With --ci: discard saved entry prices and lock in fresh ones now")
    parser.add_argument("--state-file", default=STATE_FILE_DEFAULT, help="With --ci: path to the entry-price state JSON file")
    parser.add_argument("--log-file", default=LOG_FILE_DEFAULT, help="With --ci: path to the CSV results log")
    args = parser.parse_args()

    if args.ci:
        run_ci(args.state_file, args.log_file, args.reset_entry)
        return

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
