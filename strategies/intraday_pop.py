"""Intraday-pop sleeve, pure logic (PAPER-ONLY, hackathon).

A name up at least 5% on the day whose options tape agrees (call-heavy flow,
dark pool not distributing), bought as shares with a stop attached at entry,
plus the nearest-expiry at-the-money call. Everything is flat by 15:45 ET, so nothing
carries overnight and the day's move is the whole trade.

Rules this module holds, written down before the code so they cannot drift:

* Sizing reads the config dollar caps and nothing else. A live buying-power
  field is never an input: it moves inside a tick and sizes the position off
  whatever the broker happened to report at that instant.
* Absent is never zero. A snapshot without a previous close is unreadable
  and is dropped; a missing VIX blocks the gate; missing flow is not a pass.
* The stop sits at the higher of the day's open and entry less `stop_pct`.
  A stop at or above the entry means the pop already failed: no trade.

No broker, no network, no clock in here. The runner owns all of those.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

GATE_INSTRUMENT = "SPY"


def _hhmm_to_minutes(text: str) -> int:
    hours, minutes = str(text).strip().split(":")
    return int(hours) * 60 + int(minutes)


def _float_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None  # NaN never reads as a number


@dataclass(frozen=True)
class MarketWideConfig:
    """Screener as a NAME source for the pop scan.

    A screener's `close`/`prev_close` can be a session stale, so the day-move
    filter never runs on them; the broker's live snapshots re-price every name
    and `rank_candidates` applies the move floor. This block only decides who
    gets a snapshot."""
    enabled: bool = False
    min_marketcap_usd: float = 2_000_000_000.0
    min_price: float = 5.0
    scan_limit: int = 500
    max_names: int = 400
    issue_types: tuple[str, ...] = ("COMMON STOCK", "ADR")

    @classmethod
    def from_raw(cls, raw: Mapping[str, Any] | None) -> "MarketWideConfig":
        r = dict(raw or {})
        return cls(
            enabled=bool(r.get("enabled", False)),
            min_marketcap_usd=float(r.get("min_marketcap_usd", cls.min_marketcap_usd)),
            min_price=float(r.get("min_price", cls.min_price)),
            scan_limit=int(r.get("scan_limit", cls.scan_limit)),
            max_names=int(r.get("max_names", cls.max_names)),
            issue_types=tuple(str(t).upper() for t in
                              (r.get("issue_types") or cls.issue_types)),
        )


@dataclass(frozen=True)
class IntradayPopConfig:
    enabled: bool
    equity_cap_usd: float
    options_cap_usd: float
    entry_window: tuple[int, int]
    close_out_minutes: int
    close_out_weekdays: frozenset[int]
    min_day_move_pct: float
    max_flow_lookups: int
    min_call_put_ratio: float
    min_calls: int
    stop_pct: float
    spy_band_pct: float
    vix_max: float
    blocked_regimes: tuple[str, ...]
    dte_min: int
    dte_max: int
    scan_universe: tuple[str, ...]
    market_wide: MarketWideConfig = MarketWideConfig()

    @classmethod
    def from_cfg(cls, cfg: Mapping[str, Any] | None) -> "IntradayPopConfig":
        raw = dict((cfg or {}).get("intraday_pop") or {})
        start, end = str(raw.get("entry_window_et", "10:00-11:30")).split("-")
        return cls(
            enabled=bool(raw.get("enabled", False)),
            equity_cap_usd=float(raw.get("equity_cap_usd", 0) or 0),
            options_cap_usd=float(raw.get("options_cap_usd", 0) or 0),
            entry_window=(_hhmm_to_minutes(start), _hhmm_to_minutes(end)),
            close_out_minutes=_hhmm_to_minutes(raw.get("close_out_et", "15:45")),
            close_out_weekdays=frozenset(
                int(d) for d in raw.get("close_out_weekdays", (1, 2, 3, 4, 5))),
            min_day_move_pct=float(raw.get("min_day_move_pct", 5.0)),
            max_flow_lookups=int(raw.get("max_flow_lookups", 5)),
            min_call_put_ratio=float(raw.get("min_call_put_ratio", 2.0)),
            min_calls=int(raw.get("min_calls", 20)),
            stop_pct=float(raw.get("stop_pct", 3.0)),
            spy_band_pct=float(raw.get("spy_band_pct", 0.6)),
            vix_max=float(raw.get("vix_max", 22.0)),
            blocked_regimes=tuple(str(r).upper() for r in
                                  (raw.get("blocked_regimes") or ["HALT", "CHOP", "BEAR"])),
            dte_min=int(raw.get("dte_min", 1)),
            dte_max=int(raw.get("dte_max", 2)),
            scan_universe=tuple(str(s).upper() for s in (raw.get("scan_universe") or [])),
            market_wide=MarketWideConfig.from_raw(raw.get("market_wide")),
        )


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def market_universe(rows: Sequence[Mapping[str, Any]], cfg: IntradayPopConfig) -> list[str]:
    """Filter screener rows to the names worth a live snapshot.

    Keeps Common Stock / ADR rows with a readable market cap at or above the
    floor and a readable close at or above the price floor, ordered by market
    cap descending, capped at `max_names`. A row with an unreadable cap or
    price is dropped: absent is never a pass, and this list only decides who
    gets re-priced, so a dropped name costs nothing but a look."""
    mw = cfg.market_wide
    kept: list[tuple[float, str]] = []
    seen: set[str] = set()
    for r in rows or []:
        if not isinstance(r, Mapping):
            continue
        sym = str(r.get("ticker") or "").upper().strip()
        if not sym or sym in seen:
            continue
        if str(r.get("issue_type") or "").upper() not in mw.issue_types:
            continue
        mcap = _num(r.get("marketcap"))
        px = _num(r.get("close"))
        if mcap is None or px is None:
            continue
        if mcap < mw.min_marketcap_usd or px < mw.min_price:
            continue
        seen.add(sym)
        kept.append((mcap, sym))
    kept.sort(key=lambda t: (-t[0], t[1]))
    return [sym for _, sym in kept[: max(0, mw.max_names)]]


@dataclass(frozen=True)
class PopCandidate:
    symbol: str
    last: float
    prev_close: float
    day_open: float
    bid: float
    ask: float
    day_move_pct: float


def read_snapshot(symbol: str, snapshot: Mapping[str, Any] | None) -> PopCandidate | None:
    """Build a candidate from one Alpaca stock snapshot. Any missing field
    makes the whole row unreadable: a name we cannot price is not a candidate."""
    if not isinstance(snapshot, Mapping):
        return None
    last = _float_or_none((snapshot.get("latestTrade") or {}).get("p"))
    prev_close = _float_or_none((snapshot.get("prevDailyBar") or {}).get("c"))
    day_open = _float_or_none((snapshot.get("dailyBar") or {}).get("o"))
    quote = snapshot.get("latestQuote") or {}
    bid = _float_or_none(quote.get("bp"))
    ask = _float_or_none(quote.get("ap"))
    fields = (last, prev_close, day_open, bid, ask)
    if any(v is None or v <= 0 for v in fields):
        return None
    move = (last / prev_close - 1.0) * 100.0
    return PopCandidate(symbol=symbol, last=last, prev_close=prev_close,
                        day_open=day_open, bid=bid, ask=ask, day_move_pct=move)


def rank_candidates(snapshots: Mapping[str, Any], cfg: IntradayPopConfig) -> list[PopCandidate]:
    """Names up at least `min_day_move_pct` on the day, biggest move first.
    The gate instrument is never a candidate."""
    rows = [read_snapshot(sym, snap) for sym, snap in snapshots.items()
            if str(sym).upper() != GATE_INSTRUMENT]
    keep = [r for r in rows if r is not None and r.day_move_pct >= cfg.min_day_move_pct]
    return sorted(keep, key=lambda r: r.day_move_pct, reverse=True)


def open_gate(*, spy_last: float | None, spy_prev_close: float | None,
              vix: float | None, regime_label: str | None, factor_stress: bool,
              cfg: IntradayPopConfig) -> tuple[bool, str]:
    """May the sleeve open anything today? Every input it cannot read blocks."""
    label = str(regime_label or "").upper()
    if not label or label in cfg.blocked_regimes:
        return False, f"regime {label or 'unknown'} blocks opens"
    if factor_stress:
        return False, "factor stress"
    vix_val = _float_or_none(vix)
    if vix_val is None:
        return False, "vix unreadable"
    if vix_val > cfg.vix_max:
        return False, f"vix {vix_val:.1f} > {cfg.vix_max:.1f}"
    last = _float_or_none(spy_last)
    prev = _float_or_none(spy_prev_close)
    if last is None or prev is None or prev <= 0:
        return False, "spy unreadable"
    move = (last / prev - 1.0) * 100.0
    if abs(move) > cfg.spy_band_pct:
        return False, f"spy {move:+.2f}% outside ±{cfg.spy_band_pct:.1f}% band"
    return True, f"regime {label}, vix {vix_val:.1f}, spy {move:+.2f}%"


def confirm_flow(flow: Mapping[str, Any] | None, darkpool: Mapping[str, Any] | None,
                 cfg: IntradayPopConfig) -> tuple[bool, str]:
    """Does the options tape agree with the pop? Missing data is a no."""
    if not isinstance(flow, Mapping):
        return False, "flow unreadable"
    if not isinstance(darkpool, Mapping):
        return False, "darkpool unreadable"
    ratio = _float_or_none(flow.get("call_put_ratio"))
    n_calls = _float_or_none(flow.get("n_calls"))
    net_above = _float_or_none(darkpool.get("net_above_market"))
    if ratio is None or n_calls is None:
        return False, "flow fields unreadable"
    if net_above is None:
        return False, "darkpool net_above_market unreadable"
    if ratio < cfg.min_call_put_ratio:
        return False, f"call/put {ratio:.1f} < {cfg.min_call_put_ratio:.1f}"
    if n_calls < cfg.min_calls:
        return False, f"{int(n_calls)} calls < {cfg.min_calls}"
    if net_above < 0:
        return False, f"darkpool distributing ({net_above:,.0f} net below market)"
    return True, f"call/put {ratio:.1f} on {int(n_calls)} calls, darkpool net above {net_above:,.0f}"


@dataclass(frozen=True)
class PopLegs:
    eq_qty: int
    stop_px: float
    opt_qty: int
    opt_limit: float
    reason: str = ""


def size_legs(*, entry_ask: float | None, day_open: float | None,
              option_ask: float | None, cfg: IntradayPopConfig) -> PopLegs:
    """Whole shares and contracts that the config dollar caps buy at the ask.
    No account field is consulted, by design."""
    ask = _float_or_none(entry_ask)
    opened = _float_or_none(day_open)
    if ask is None or ask <= 0 or opened is None or opened <= 0:
        return PopLegs(0, 0.0, 0, 0.0, "entry price or day open unreadable")
    stop_px = round(max(opened, ask * (1.0 - cfg.stop_pct / 100.0)), 2)
    if stop_px >= ask:
        return PopLegs(0, stop_px, 0, 0.0,
                       f"stop {stop_px:.2f} at or above entry {ask:.2f}: pop already failed")
    eq_qty = int(cfg.equity_cap_usd // ask)
    opt_ask = _float_or_none(option_ask)
    if opt_ask is None or opt_ask <= 0:
        return PopLegs(eq_qty, stop_px, 0, 0.0, "option ask unreadable")
    opt_limit = round(opt_ask, 2)
    opt_qty = int(cfg.options_cap_usd // (opt_limit * 100.0))
    return PopLegs(eq_qty, stop_px, opt_qty, opt_limit)


def in_entry_window(now_et_minutes: int, cfg: IntradayPopConfig) -> bool:
    start, end = cfg.entry_window
    return start <= now_et_minutes <= end


def close_out_due(now_et_minutes: int, cfg: IntradayPopConfig) -> bool:
    return now_et_minutes >= cfg.close_out_minutes


def close_out_reachable(iso_weekday: int, cfg: IntradayPopConfig) -> bool:
    """Does a close-out tick run on this weekday (1=Mon .. 7=Sun)?

    The long call cannot carry a stop, since Alpaca rejects one outright, so
    close_out_et tick is the ONLY exit it has. On a day the worker has no such
    tick, opening a position means opening one nothing will close. The
    hackathon book runs Mon-Thu by design, so Friday is genuinely empty rather
    than misconfigured, and the sleeve has to stand aside rather than trust a
    tick that is not coming.
    """
    return iso_weekday in cfg.close_out_weekdays


def pick_atm_call(rows: Sequence[Mapping[str, Any]], spot: float) -> Mapping[str, Any] | None:
    """Nearest expiry first, then the strike closest to spot at or below it.
    With no strike at or below spot, the closest one above is taken."""
    calls = [r for r in rows if str(r.get("type", "")).lower() == "call"
             and _float_or_none(r.get("strike")) is not None
             and _float_or_none(r.get("dte")) is not None]
    if not calls:
        return None
    nearest_dte = min(float(r["dte"]) for r in calls)
    same_expiry = [r for r in calls if float(r["dte"]) == nearest_dte]
    at_or_below = [r for r in same_expiry if float(r["strike"]) <= spot]
    pool = at_or_below or same_expiry
    return min(pool, key=lambda r: abs(spot - float(r["strike"])))
