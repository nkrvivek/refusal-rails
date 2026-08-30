#!/usr/bin/env python3
"""Refusal Rails — read-only demonstration of the agent's decision path.

Runs the real pipeline against the real hackathon paper account and prints what
the agent sees, WITHOUT placing an order. Every call here is a GET; the only
order-placing method in this repo is ``AlpacaRest.place_option_order`` and this
script never reaches it.

    python demo.py            # default underlyings
    python demo.py SPY QQQ    # explicit underlyings

What it shows, in the order the agent does it:

  1. Account state over REST.
  2. Alpaca's market clock — the hard pre-order guard.
  3. An options chain over REST, and the SAME chain over MCP, side by side.
     This is the dual-transport requirement made visible: two independent
     Alpaca integrations answering the same question.
  4. The contract the intraday_swing sleeve would select, with the delta band
     and entry window that governed the choice.
  5. Whether the sleeve's entry window is open right now — the time gate that
     governs when the agent is permitted to open at all.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from alpaca_rest import AlpacaRest, AlpacaRestError

DEFAULT_UNDERLYINGS = ["SPY", "QQQ"]
DELTA_MIN, DELTA_MAX = 0.30, 0.55
WINDOW_ET = (10 * 60, 11 * 60 + 30)  # 10:00-11:30 ET, from config.hackathon.yaml


def rule(title: str) -> None:
    print(f"\n{'=' * 66}\n{title}\n{'=' * 66}")


def main(argv: list[str]) -> int:
    underlyings = argv[1:] or DEFAULT_UNDERLYINGS

    rule("1. ACCOUNT (REST)")
    try:
        broker = AlpacaRest()
    except AlpacaRestError as exc:
        print(f"  {exc}")
        return 1
    account = broker.get_account()
    print(f"  account_number : {account['account_number']}")
    print(f"  equity         : ${account['equity']}")
    print(f"  cash           : ${account['cash']}")
    print(f"  options_level  : {account.get('options_trading_level')}")
    positions = broker.get_positions()
    print(f"  open positions : {len(positions)}")

    rule("2. MARKET CLOCK (REST) — the hard pre-order guard")
    clock = broker.get_clock()
    print(f"  is_open   : {clock.get('is_open')}")
    print(f"  next_open : {clock.get('next_open')}")
    print("  place_option_order() refuses to submit unless is_open is True.")

    rule("3. ENTRY WINDOW — when the agent is permitted to open")
    now_et = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York"))
    minutes = now_et.hour * 60 + now_et.minute
    inside = WINDOW_ET[0] <= minutes <= WINDOW_ET[1]
    print(f"  now (ET)      : {now_et:%Y-%m-%d %H:%M} ({now_et:%A})")
    print(f"  open window   : 10:00-11:30 ET")
    print(f"  inside window : {inside}")
    print("  The opening tick runs 14:15 UTC = 10:15 ET, inside this window, so")
    print("  the agent is scheduled to evaluate entries when it is allowed to take")
    print("  them. A tick outside the window can only manage and exit.")

    for underlying in underlyings:
        rule(f"4. CHAIN FOR {underlying} — REST and MCP, side by side")
        try:
            envelope = broker.get_options_chain(underlying, dte_min=1, dte_max=3)
        except AlpacaRestError as exc:
            print(f"  REST error: {exc}")
            continue
        chain = envelope.get("chain")
        if not chain:
            print(f"  {envelope.get('note')}")
            continue
        calls = chain["calls"]
        print(f"  expiration      : {envelope['expiration']}")
        print(f"  REST contracts  : {len(calls)} calls / {len(chain['puts'])} puts")
        mcp = envelope.get("mcp") or {}
        print(f"  MCP status      : {mcp.get('status')}")
        print(f"  MCP contracts   : {mcp.get('contracts')}")
        if not mcp.get("ok"):
            print("  (MCP unavailable here → REST result stands. The agent is")
            print("   designed so a dead MCP path cannot change what it trades.)")

        rule(f"5. CONTRACT SELECTION FOR {underlying} (delta {DELTA_MIN}-{DELTA_MAX})")
        eligible = [
            c for c in calls
            if isinstance(c.get("delta"), (int, float))
            and DELTA_MIN <= abs(c["delta"]) <= DELTA_MAX
            and c.get("mid_price")
        ]
        if not eligible:
            print("  No contract in the delta band with a two-sided quote.")
            print("  (Expected outside market hours — the indicative feed is thin.)")
            continue
        pick = min(eligible, key=lambda c: abs(abs(c["delta"]) - 0.42))
        print(f"  selected  : {pick['symbol']}")
        print(f"  strike    : {pick['strike']}")
        print(f"  delta     : {pick['delta']}")
        print(f"  mid       : {pick['mid_price']}")
        print("  NOT submitted — this script is read-only.")

    rule("DONE")
    print("  No orders were placed. Only GET requests were issued.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
