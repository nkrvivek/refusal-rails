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
a trading session, which is the right tradeoff for any secondary data source
on a live decision path.

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

## Day two, 2026-09-01

NAV $99,949.79 to $96,737.40.

The swing sleeve reached its window with working greeks and opened four long
calls at 14:20Z: `MU260904C00947500` x2, `SPCX260904C00145000` x14,
`AMZN260902C00255000` x23, `AXON260904C00545000` x3. The put-credit sleeve
added three spreads on VZ, QQQ and SPY.

One of the four was closed the same session:

```
16:33:53 intraday_swing_exit_signal  ret -0.5824  reason "stop_loss -58.2%<=-25%"
```

The stop is a threshold checked on a tick, not an order resting at the broker.
Ticks run at 14:20Z and 16:30Z, so a position that crosses -25% at 14:35Z is
not closed until the next tick sees it, at whatever it is worth by then. AXON
was worth -58.2% when the check ran, and the sleeve took $1,845 out of a $2,665
position on a rule written to cap the loss at a quarter.

That is the honest reading of a polled stop, and it is worth stating plainly
because the number that gets published is "-25% stop loss" while the number
that gets paid is whatever the gap between two ticks allows. Nothing about the
threshold was wrong. The refusal machinery in this repo fails closed on data it
cannot read; a stop that only exists between ticks fails open on time, which is
a different axis and one this book did not have covered.

## Day three, 2026-09-02

NAV $95,806.64 to $94,492.72, and the day the swing sleeve was switched off.

Three of the four day-two calls closed: SPCX -$2,072, AMZN -$897, MU +$1,220.
Realized on the sleeve was then far past what the rest of the book earns in a
week, so entries were stopped rather than tuned. The knob was
`intraday_swing.max_concurrent_picks: 4 -> 0`, not `integration_enabled:
false`. The disabled flag skips the whole dispatch including exits, which would
have left two open positions with nothing watching them until expiry. A kill
switch that also stops the exits is not a kill switch.

Three defects were found in the sleeve on the way out, and none were hot-fixed
mid-run:

- **The entry TTL was never enforced.** `VFC260904C00013500` was submitted at
  14:20:39Z on a 0.14 limit with `entry_ttl_minutes: 25`, and filled at
  16:24:54Z. It should have been cancelled at 14:45Z. It lived two hours and
  four minutes, and filled into a setup that no longer existed.
- **The same-day stop is suppressed by the PDT guard.** The runner skips a
  same-day stop when there is no day-trade room. On a paper account the guard
  is protecting nothing at all, and it costs the drawdown it was written to
  prevent.
- **Alpaca paper will not accept a stop on a long option.** A `stop` order on
  `MU260904C00947500` answered
  `403 {"code":40310000,"message":"account not eligible to trade uncovered option contracts"}`.
  So the polled tick stop above is not one stop among several. It is the only
  stop any options sleeve on this venue has. Anyone pointing an options agent
  at Alpaca paper should know that before they design around a resting stop.

## Day four, 2026-09-03: the close, the override, and the sleeve's live record

Thursday was the book's last trading day. The ticks run `2-5` on Cloudflare's
day numbering, which is Monday to Thursday, so nothing in this agent fires
again after 19:45Z on the 3rd.

### The morning close

NAV opened at $94,228.80. The exit was a price, not a bell: flatten when the mid
mark-to-market reached $112,000. It printed $112,664.97 and the flatten ran on
its own, cancelling both stop children first, then buying back every short
option leg, then selling the longs, then the equities. Shorts lead because the
account answers 403 to any order that would leave an option uncovered even for
an instant. The book was flat at **$111,303.62**, +11.30% on a $100,000 base.

**That result is not the sleeves.** The account also carried positions an
operator entered directly, and they account for the gain and more. Read by
sleeve over the four days, the code in this repository was down: the swing
sleeve realized **-$6,443** across MU, AXON, SPCX, AMZN and VFC, and the
put-credit sleeve made **+$640** across BAC, MO, VZ, QQQ and SPY. Publishing
the book's +11.3% as agent performance would be the same class of error as day
one's `n_placed: 0`, which is the failure this repository is named after.

### The pop sleeve's entire live record is two refusals

`strategies/intraday_pop.py` was written, reviewed and committed this day. It
reached the running container late, and its whole live history is two audit
rows, both from the last hour of the last session:

```
19:34:42Z intraday_pop_ran  opened 0  skips ["outside entry window"]
19:49:11Z intraday_pop_ran  opened 0  skips ["outside entry window"]
```

It never opened a live position, and under the Monday-to-Thursday cron it never
will. The only tick of the day that fell inside the sleeve's 10:00-11:30 ET
entry window started at 14:16Z, and the sleeve was committed at 14:44Z, twenty
eight minutes too late.

There is a second gap worth naming rather than smoothing over. The 16:30Z tick
reported commit `2f2c6d3`, which does contain the sleeve and its config block,
and it logged no pop row at all. The likeliest reading is the failure this repo
already hit four times today: a deploy uploads the worker version, stamps the
commit, and then dies at the container-apply step.

```
The requested Worker version could not be found, please check the ID being
passed and try again. [code: 100146]
```

The worker's reported commit and the image actually serving the tick can
disagree, and the reported one is the optimistic half. Stated as an inference,
not a measurement: the image digest for that tick was not captured, which is
itself the defect. Later runs of the same script went green with no change to
the repository, so `100146` is a platform-side transient at the versions API.

### The override

The book was flat at $111,303.62 by lunchtime with two hours of session left,
and the operator asked to redeploy it.

**The sleeve was run against its own code first, and it refused.** Two of its
three market gates were closed:

| Gate | Reading | Verdict |
|---|---|---|
| `in_entry_window` | 13:47 ET against a 10:00-11:30 window | refuse |
| `open_gate` (SPY band) | SPY +1.05% against a 0.6% band | refuse |
| VIX cap | 14.49 against a 22.0 cap | pass |

The scan found 14 names over the 5% day-move floor. Its own top candidate,
SNOW at +20.30%, was killed by the sleeve's own stop rule: the day's open of
380.35 sat above the ask of 369.28, which the code reads as a pop that already
failed. The recommendation was to stand aside, and the reason was named:
buying a name up 16% at 13:47 ET into a tape up 1% is the chase both closed
gates exist to prevent.

The operator directed the trade anyway. That is their call, and it is recorded
here as an **override, not a sleeve result**. Three departures from the sleeve's
own rules, each of them deliberate and each sized down rather than at cap:

- It opens exactly one name a day. This took three.
- RIOT and PLTR went in at half and 60% of the equity cap.
- `confirm_flow`, the call/put and dark pool confirmation, never ran at all,
  because the entry window was already shut before it would have been reached.

The one rule carried over intact was the exit. Long option legs cannot carry a
resting stop on this venue, so the only exit is time, and the sleeve's own
`close_out_et` of 15:45 ET flattened everything.

| Leg | In | Out | P&L |
|---|---|---|---|
| HOOD 200 sh | 124.47 | 123.5194 | -$190.12 |
| `HOOD260904C00124000` x79 | 2.48 | 1.95 | **-$4,187.00** |
| RIOT 601 sh | 20.77 | 21.06 | +$174.29 |
| `RIOT260904C00020500` x105 | 0.60 | 0.68 | +$840.00 |
| PLTR 81 sh | 183.00 | 182.95 | -$4.05 |
| `PLTR260904C00182500` x29 | 2.67 | 2.63 | -$116.00 |
| `SPY260904C00772000` x36 | 2.70 | 2.88 | +$648.00 |
| **Total** | | | **-$2,834.88** |

Equity went $111,303.62 to **$108,456.28**, a loss of $2,847.34 or 2.56%. The
$12 difference between the leg total and the equity change is fees and the one
leg that did not close.

The HOOD call was the entire loss and then some: the other three names netted
+$1,542.24 between them. HOOD was the one name where the trade took both the
shares and the calls at full size, and it was the one name the sleeve would
have picked first, so the override is not the only thing being scored here. A
profitable override would not have made the gates wrong, and a losing one does
not make them right on its own. What it does show is the cost of the specific
departure: size concentrated in the leverage leg of the name that faded.

### One leg did not close

`MO261009P00060000`, the long wing of an October put spread, has a 0 bid and
answered

```
403 {"code":40310000,"message":"order has been rejected due to no available
quote for symbol. please reenter with a limit"}
```

on a market order. The limit fallback found no bid either. It is marked at $0,
carries no obligation and no assignment exposure, and a resting 0.01 limit is
the only exit it has. A book is not flat because the flatten script returned 0.
It is flat when the position list is empty, and this one is not. The script
printed `NOT FLAT` and named the leg rather than reporting a clean exit.

### The operational finding

The 15:45 ET flatten was owned by a watcher running inside an agent session,
and that session's task was killed twice, once with 34 minutes still to run.
Had it stayed there, the flatten would simply never have fired and eight legs
would have carried into a Friday with no engine tick. Relaunching it detached,
so that it was re-parented to the init process, survived a second kill that
took the reporting task with it.

**A session-scoped watcher is not a rail.** Everything else in this repository
fails closed on data it cannot read. An exit that depends on a process nobody
guarantees is alive fails open on infrastructure, which is a third axis after
day two's fail-open-on-time. Any unattended exit that matters belongs in cron
or in the container, not in a session.

## Results

| Day | NAV close | What moved it |
|---|---|---|
| 2026-08-31 | $99,946.90 | two put credit spreads opened; swing rejected on limit precision |
| 2026-09-01 | $96,737.40 | four swing calls opened, one stopped at -58.2% |
| 2026-09-02 | $94,492.72 | three swing calls closed at a loss; sleeve switched off |
| 2026-09-03 | $108,456.28 | flat at $111,303.62 at the target, then a manual override gave back $2,847.34 |

Final equity **$108,456.28** against a $100,000 base, +8.46%.

Attribution, which is the only number worth publishing:

| Source | Realized |
|---|---|
| swing sleeve | **-$6,443** |
| put credit spread sleeve | **+$640** |
| pop sleeve | **never opened a position** |
| operator trades and the override | the remainder |

The agent's sleeves lost money over four sessions. The account made money. Those
are two different sentences and this README will not merge them.

## Safety properties

These are the parts worth judging, more than the strategy:

- **Paper-pinned at construction.** `AlpacaRest` refuses any host that is not
  `paper-api.alpaca.markets`, so a mistyped environment variable fails at
  startup instead of quietly sending a live order.
- **Hard clock guard before every order.** Checked against Alpaca's own
  `/v2/clock`, not local time, so it cannot be wrong about holidays or half
  days. An unreadable clock is treated as closed, so the agent never infers a
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
docs/demo-video.md           Demo script and shot list, under five minutes
```

This repository is cut fresh for the event. The agent runs inside a larger
private multi-book trading engine; nothing from the other books ships here.

## License

MIT
