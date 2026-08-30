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

**MCP is additive and cannot change what this book trades.** That is a deliberate
design constraint. The REST envelope is complete before MCP is consulted, the MCP
client uses deferred imports and a hard timeout, and every helper returns `None`
rather than raising. A degraded MCP path costs the agent an audit-trail line, not
a trading session — which is the right tradeoff for any secondary data source on
a live decision path.

One failure is deliberately *not* swallowed. `KeyboardInterrupt` and `SystemExit`
mean the container is shutting down, and an agent that absorbs those keeps making
trading decisions through its own shutdown. `tests/test_mcp_alpaca.py` pins that
distinction: every ordinary exception degrades to `None`, shutdown signals
propagate.

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
  days. An unreadable clock is treated as closed — the agent never infers a
  tradable market from a failed lookup.
- **Orders are never automatically retried.** A retry on an ambiguous response
  is how one intended contract becomes two filled ones. Timeouts reconcile
  against `/v2/orders` by `client_order_id`.
- **Hackathon-scoped credentials only.** The agent reads `ALPACA_HACKATHON_*`
  and never the generic `ALPACA_API_KEY_ID` pair, so it cannot authenticate as
  another account even when both are present in the environment. Paper mode is
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
