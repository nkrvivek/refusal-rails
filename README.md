# Refusal Rails

An autonomous options trading agent for the **Alpaca AI Trading Agents Hackathon**.

- **Alpaca paper account:** `PA3PZGSB3W2E` (dedicated to this event, $100,000 starting balance)
- **Team:** Refusal Rails
- **Runs on:** Cloudflare Workers + Containers, on a cron. No laptop in the loop.

The name is the thesis. Most of the engineering here is not about finding trades.
It is about the machinery that **refuses** them: a council of AI reviewers that
must agree before capital moves, risk gates that fail closed, an identity
handshake that aborts a tick if a book's config and state do not match, and a
pre-order clock guard that treats an unknown market state as closed. An agent
that trades autonomously with real consequences is mostly a refusal system with
a strategy attached.

## The two Alpaca transports

The hackathon requires entrants to use Alpaca's MCP server or CLI. This agent
uses **both REST and MCP**, deliberately split by what each is good at:

| Path | Transport | Why |
|---|---|---|
| Orders, account, clock | **REST** (`alpaca_rest.py`) | MCP option orders are market and limit only (Danny, Alpaca, 2026-08-28 Q&A). This book's entry logic needs the wider order-type surface. |
| Options chains, snapshots | **MCP** (`mcp_alpaca.py`) | The official `alpaca-mcp-server` exposes `get_option_chain`, `get_option_snapshot`, `get_option_contracts` as MCP tools. |

`AlpacaRest.get_options_chain()` builds the chain over REST, then asks the MCP
server the same question and records its answer under `envelope["mcp"]`. Run
`demo.py` to see both answer side by side.

**MCP is additive and cannot change what this book trades.** That is a hard
design constraint, not an accident. The MCP path went in two days before the
book's only four trading sessions and could not be exercised on the authoring
machine — macOS refuses to `dlopen` the unsigned `rpds` extension the MCP SDK
imports through `jsonschema` ("library load disallowed by system policy"), even
after `xattr -c` and an ad-hoc `codesign`. Untestable-locally plus
on-the-critical-path means fail-safe by construction: deferred imports, a hard
timeout, every helper returns `None` instead of raising, and the REST envelope
is complete before MCP is consulted. Worst case, the book trades exactly as it
would on REST alone and the audit trail records why.

One failure is deliberately *not* swallowed. An early version of the test suite
asserted that a mid-call `KeyboardInterrupt` should degrade like any other
error; the code's `except Exception` failed that test, and **the code was
right**. `KeyboardInterrupt` and `SystemExit` mean the container is going down,
and a book that swallows those keeps making trading decisions through its own
shutdown. `tests/test_mcp_alpaca.py` now pins that distinction.

## Strategy

A short-dated directional options sleeve (`strategies/intraday_swing.py`):

- **Instrument:** long single-leg options only, no short premium.
- **Expiry:** 1–3 DTE.
- **Delta band:** 0.30–0.55.
- **Entry window:** 10:00–11:30 ET, avoiding the opening auction.
- **Regime filter:** entries only in BULL or CHOP; never under HALT.
- **PDT guard:** day-trade round trips tracked and capped.

Candidates come from a corroboration score across independent signals, and a
contract is only eligible with a two-sided quote and a delta inside the band.

## A bug worth reading about

Until 2026-08-30 this book **could not open a single option**, blocked two
independent ways, and would have missed the hackathon's options requirement
regardless of P&L:

1. The sleeve carried `integration_enabled: false`, inherited from an unrelated
   directive scoped to a different book. The runner ANDs that flag, so the
   sleeve never ran.
2. Even enabled, no scheduled tick landed inside its 10:00–11:30 ET entry
   window. Ticks fired at 09:45, 12:30 and 15:30 ET — the first missing the
   window by fifteen minutes.

Each looks harmless alone. Together they are a silent, total block: the agent
runs, logs cleanly, reports healthy, and never trades the instrument it exists
to trade. It was found by asking why an account with a fully deployed agent
still showed zero orders. `demo.py` prints the window check so this class of
failure is visible rather than silent.

## Run it

```bash
pip install -r requirements.txt
cp .env.example .env      # add your Alpaca paper keys
python demo.py SPY QQQ    # read-only: no orders are placed
pytest -q
```

`demo.py` issues only GETs. The single order-placing method in this repo is
`AlpacaRest.place_option_order`, and the demo never reaches it.

Sample output (real account, markets closed):

```
1. ACCOUNT (REST)
  account_number : PA3PZGSB3W2E
  equity         : $100000
  open positions : 0

4. CHAIN FOR SPY — REST and MCP, side by side
  expiration      : 2026-08-31
  REST contracts  : 263 calls / 263 puts
  MCP status      : ok:get_option_chain

5. CONTRACT SELECTION FOR SPY (delta 0.3-0.55)
  selected  : SPY260831C00770000
  delta     : 0.4474
  mid       : 1.42
  NOT submitted — this script is read-only.
```

## Safety properties

These are the parts worth judging, more than the strategy:

- **Paper-pinned at construction.** `AlpacaRest` refuses any host that is not
  `paper-api.alpaca.markets`, so a mistyped environment variable fails at
  startup instead of quietly sending a live order.
- **Hard clock guard before every order.** Checked against Alpaca's own
  `/v2/clock`, not local time, so it cannot be wrong about holidays or half
  days. An unreadable clock is treated as closed. This exists because a
  validation run on a sibling book once placed two real orders while the author
  believed it was a dry run.
- **Orders are never automatically retried.** A retry on an ambiguous response
  is how one intended contract becomes two filled ones. Timeouts reconcile
  against `/v2/orders` by `client_order_id`.
- **Hackathon-scoped credentials only.** The MCP client reads
  `ALPACA_HACKATHON_*` and never the generic `ALPACA_API_KEY_ID` pair, which has
  pointed at a different paper account in the parent project — a mismatch that
  once caused an entire book to be built around the wrong account. Paper mode is
  hard-coded, not config-settable.

## Layout

```
alpaca_rest.py               REST transport: orders, account, clock, chains
mcp_alpaca.py                MCP transport: option chains and snapshots
strategies/intraday_swing.py Entry window, delta band, contract selection
demo.py                      Read-only walkthrough of the decision path
tests/                       Fail-safety contract for the MCP path
docs/rules-and-qa.md         Event rules + the full staffed Q&A transcript
```

This repository is cut fresh for the event. The agent runs inside a larger
private multi-book trading engine; nothing from the other books ships here.

## License

MIT
