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

Two sleeves, both short-dated and both flat by the close.

### Swing (`strategies/intraday_swing.py`)

A short-dated directional options sleeve:

- **Instrument:** long single-leg options only, no short premium.
- **Expiry:** 1–3 DTE.
- **Delta band:** 0.30–0.55.
- **Entry window:** 10:00–11:30 ET, avoiding the opening auction.
- **Regime filter:** entries only in BULL or CHOP; never under HALT.
- **PDT guard:** day-trade round trips tracked and capped.

Candidates come from a corroboration score across independent signals, and a
contract is only eligible with a two-sided quote and a delta inside the band.

### Pop (`strategies/intraday_pop.py`)

A name already up hard on the day, taken as shares with a stop attached at
entry plus the nearest-expiry at-the-money call:

- **Trigger:** at least 5% on the day, measured against the previous close.
- **Confirmation:** call/put ratio at or above 2.0 on at least 20 calls, and a
  dark pool that is not distributing.
- **Entry window:** 10:00-11:30 ET, one entry a day, never while a position is
  already open.
- **Stop:** the higher of the day's open and entry less 3%, attached to the
  equity order as an OTO child rather than watched by the runner.
- **Market gate:** SPY inside a 0.6% band, VIX at or below 22, regime not
  blocked. Every input it cannot read blocks the open.
- **Close-out:** a dedicated 15:45 ET tick flattens both legs.

This is the sleeve where a sizing bug is most expensive, because the entry is
into a move that has already happened. So sizing reads two config dollar caps
and nothing from the account: a live buying-power field moves inside a tick and
would size the position off whatever the broker happened to report at that
instant. A stop that lands at or above the entry is read as a pop that already
failed, and no legs go out. `tests/test_intraday_pop.py` pins both.

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

## Day one, 2026-08-31

Seven ticks, 06:46Z to 19:57Z. NAV $100,000.00 to $99,946.90.

The swing sleeve reached its 10:15 ET entry window, had greeks, and selected
four contracts inside its delta band: `XLF260903C00058000`,
`TQQQ260902C00072000`, `AMD260902C00475000`, `TSLA260902C00365000`. Alpaca
rejected all four:

```
422 {"code":42210000,"message":"limit price must be limited to 2 decimal places"}
```

The sleeve opened nothing on day one. That is the honest headline.

Two defects were found and fixed the same day, both on the order path, and both
in the private engine rather than in this transport:

- **Snapshot requests were unbounded.** Alpaca's multi-symbol snapshot endpoint
  answers `400 {"message":"symbol limit is 100"}` above 100 symbols, and the
  greeks backfill was sending up to 1,138. Every batch failed, so the sleeve had
  no delta data, would have found nothing selectable, and would have opened
  nothing while logging a clean tick. Healthy-looking and completely inert is
  the failure mode this whole repository is named after. Found by running a
  dry-run tick against the deployed worker with the market closed, rather than
  letting Monday's open be the first execution of the code.
- **Limit prices carried float tails.** Fixed by formatting to a string, not by
  `round()`: a rounded float still serialises as `1.2300000000000002`.

`alpaca_rest.py` in this repository was already correct on both counts. It
paginates one underlying at a time rather than batching symbols, and it has
always sent `f"{float(limit_price):.2f}"`. `tests/test_order_submission.py` now
pins that so it stays true.

Two more defects were in the record rather than the order path, and they are the
worse pair. The tick summary wrote `halts_opens: true` on the freshness gate for
five of the seven ticks, when a freshness failure that halts returns before any
summary is built, so the flag can never be true where it was written. And the
placed-order counter read 0 on every tick of a day the book sent four legs to
the broker, because the sleeve that opened them runs after the loop that
increments the counter. An agent that misreports its own day is worse than one
that has a bad day, because every downstream reader takes the record for the
truth.

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
strategies/intraday_pop.py   Day-move trigger, flow confirmation, cap-only sizing
demo.py                      Read-only walkthrough of the decision path
tests/                       Fail-safety contract for the MCP and order paths
docs/rules-and-qa.md         Event rules + the full staffed Q&A transcript
```

This repository is cut fresh for the event. The agent runs inside a larger
private multi-book trading engine; nothing from the other books ships here.

## License

MIT
