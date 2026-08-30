"""Intraday-swing sleeve — 1-3 DTE long-options swings (10% / $500).

User directive 2026-05-26: "explore PDT scenarios where we enter a stock
in the morning and exit before the end of day and scalp it for some quick
bucks". Choice: 3/wk options swings (NOT futures).

Design:
- Universe restricted to (active theses tickers) ∪ (active banger picks)
  — never blue-sky names. Anchored to current convictions.
- Long calls / long puts only; NO short premium (PDT-stress = bad for
  defined-risk-undefined-reward).
- 1-3 DTE weeklies. 0.30-0.55 delta target.
- Entry window 10:00-11:30 ET (post-open vol drift settled).
- Hold up to 3 sessions; exit at EOD only if (target/stop hit) AND
  (PDT room available). Otherwise carry overnight to avoid PDT slot burn.
- PDT counter (R2 `state/pdt_counter.json`) gates same-day exits via
  R-PDT-1 (risk_gates.pdt_check).

This module produces a list of intended actions. Runner risk-gates +
executes via Public.com broker.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class IntradaySwingActionKind(str, Enum):
    OPEN_LONG_CALL = "open_long_call"
    OPEN_LONG_PUT = "open_long_put"
    CLOSE_LONG_OPTION = "close_long_option"
    SKIP = "skip"


@dataclass
class IntradaySwingAction:
    kind: str
    symbol: str                                  # underlying ticker
    occ_symbol: str | None = None                # specific contract for open/close
    qty: int = 0
    side: str = ""                               # "buy" | "sell"
    reason: str = ""
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class IntradaySwingPosition:
    pos_id: str
    underlying: str
    occ_symbol: str
    direction: str                               # "call" | "put"
    qty: int
    open_date: date
    open_price: float
    open_order_id: str
    notes: list[str] = field(default_factory=list)


@dataclass
class IntradaySwingContext:
    today: date
    now_et_minutes: int                          # minutes since midnight ET
    candidate_universe: list[dict]               # [{ticker, signal_score, theme, flow_dir, ...}]
    open_positions: list[IntradaySwingPosition]
    pdt_room_available: bool                     # from pdt_counter.pdt_block_check inverted
    regime_label: str                            # "BULL" | "CHOP" | "BEAR" | "HALT"
    underlying_quotes: dict[str, float]          # ticker → spot
    option_chains: dict[str, list[dict]]         # ticker → list of OCC strikes


def _window_open(cfg_entry: dict, ctx: IntradaySwingContext) -> bool:
    """Inside entry window 10:00-11:30 ET (default)."""
    window = cfg_entry.get("open_window_et", "10:00-11:30")
    try:
        start_s, end_s = window.split("-")
        start = int(start_s.split(":")[0]) * 60 + int(start_s.split(":")[1])
        end = int(end_s.split(":")[0]) * 60 + int(end_s.split(":")[1])
    except (ValueError, IndexError):
        return False
    return start <= ctx.now_et_minutes <= end


def _passes_regime(cfg_entry: dict, label: str) -> bool:
    allowed = set((cfg_entry.get("pre_filters") or []))
    if "regime_in_BULL_or_CHOP" in allowed:
        return label.upper() in {"BULL", "CHOP"}
    return label.upper() != "HALT"


def _passes_signal_floor(cand: dict, cfg_entry: dict) -> bool:
    """Require ≥3 flow-confluence signals (or whatever filter says)."""
    flags = (cand.get("signal_flags") or {})
    n_true = sum(1 for v in flags.values() if v)
    return n_true >= 3


def _passes_iv_rank(cand: dict, cfg_entry: dict) -> bool:
    ivr = cand.get("iv_rank")
    if ivr is None:
        return True  # fail-open when missing
    pre = (cfg_entry.get("pre_filters") or [])
    cap = 85
    for f in pre:
        if f.startswith("underlying_iv_rank_lt_"):
            try:
                cap = int(f.rsplit("_", 1)[-1])
            except ValueError:
                cap = 85
            break
    return float(ivr) < cap


def _select_strike_dte(cand: dict, ctx: IntradaySwingContext,
                       cfg_entry: dict) -> dict | None:
    """Pick a long-option leg matching delta + DTE targets.

    Minimal selector: nearest delta to mid of (delta_target_min, delta_target_max),
    DTE within (expiry_dte_min, expiry_dte_max). Returns dict {occ, strike, dte,
    delta, mid_price} or None if no chain.
    """
    tkr = (cand.get("ticker") or "").upper()
    chain = ctx.option_chains.get(tkr) or []
    if not chain:
        return None
    d_min = float(cfg_entry.get("delta_target_min", 0.30))
    d_max = float(cfg_entry.get("delta_target_max", 0.55))
    d_mid = (d_min + d_max) / 2.0
    dte_min = int(cfg_entry.get("expiry_dte_min", 1))
    dte_max = int(cfg_entry.get("expiry_dte_max", 3))
    direction = (cand.get("direction") or "call").lower()
    eligible = [c for c in chain
                if (c.get("type") or "").lower() == direction
                and dte_min <= int(c.get("dte") or 0) <= dte_max
                and d_min <= abs(float(c.get("delta") or 0)) <= d_max]
    if not eligible:
        return None
    eligible.sort(key=lambda c: abs(abs(float(c.get("delta") or 0)) - d_mid))
    pick = eligible[0]
    return {
        "occ": pick.get("occ_symbol"),
        "strike": pick.get("strike"),
        "dte": pick.get("dte"),
        "delta": pick.get("delta"),
        "mid_price": pick.get("mid_price"),
        "type": direction,
    }


class IntradaySwingStrategy:
    """Mechanical state machine for intraday options swing sleeve."""

    def __init__(self, cfg: dict, audit_writer: Any | None = None):
        self.cfg = cfg["intraday_swing"]
        self.audit = audit_writer

    # ---------- ENTRY POINT ----------

    def tick(self, ctx: IntradaySwingContext,
             sleeve_capital_usd: float) -> list[IntradaySwingAction]:
        actions: list[IntradaySwingAction] = []
        actions.extend(self._exits(ctx))
        actions.extend(self._entries(ctx, sleeve_capital_usd))
        return actions

    # ---------- EXITS ----------

    def _exits(self, ctx: IntradaySwingContext) -> list[IntradaySwingAction]:
        out: list[IntradaySwingAction] = []
        exit_cfg = self.cfg.get("exit", {})
        max_hold = int(exit_cfg.get("max_hold_sessions", 3))
        force_dte = int(exit_cfg.get("force_close_dte", 0))
        for pos in ctx.open_positions:
            sessions_held = (ctx.today - pos.open_date).days
            # Hard ceiling: max_hold sessions
            if sessions_held >= max_hold:
                out.append(IntradaySwingAction(
                    kind=IntradaySwingActionKind.CLOSE_LONG_OPTION,
                    symbol=pos.underlying, occ_symbol=pos.occ_symbol,
                    qty=pos.qty, side="sell",
                    reason=f"max_hold_sessions={max_hold}",
                ))
                continue
            # Same-day exit blocked unless PDT room AND target/stop hit
            if sessions_held == 0 and not ctx.pdt_room_available:
                # PDT slots exhausted → carry overnight, no scalp exit
                continue
            # Real target/stop computation requires fresh option mark — runner
            # passes it via params. Skeleton emits SKIP if no signal here.
        return out

    # ---------- ENTRIES ----------

    def _entries(self, ctx: IntradaySwingContext,
                 sleeve_capital_usd: float) -> list[IntradaySwingAction]:
        out: list[IntradaySwingAction] = []
        entry_cfg = self.cfg.get("entry", {})
        if not _window_open(entry_cfg, ctx):
            return [IntradaySwingAction(
                kind=IntradaySwingActionKind.SKIP,
                symbol="ALL",
                reason="outside entry window",
            )]
        if not _passes_regime(entry_cfg, ctx.regime_label):
            return [IntradaySwingAction(
                kind=IntradaySwingActionKind.SKIP,
                symbol="ALL",
                reason=f"regime {ctx.regime_label} blocks new opens",
            )]
        max_concurrent = int(self.cfg.get("max_concurrent_picks", 2))
        if len(ctx.open_positions) >= max_concurrent:
            return [IntradaySwingAction(
                kind=IntradaySwingActionKind.SKIP,
                symbol="ALL",
                reason=f"max_concurrent_picks={max_concurrent} reached",
            )]
        slots_remaining = max_concurrent - len(ctx.open_positions)
        per_pick_usd = float(self.cfg.get("per_pick_size_usd", 250))

        for cand in ctx.candidate_universe[: slots_remaining * 3]:  # over-fetch
            if slots_remaining <= 0:
                break
            tkr = (cand.get("ticker") or "").upper()
            if any(p.underlying == tkr for p in ctx.open_positions):
                continue
            if not _passes_signal_floor(cand, entry_cfg):
                continue
            if not _passes_iv_rank(cand, entry_cfg):
                continue
            pick = _select_strike_dte(cand, ctx, entry_cfg)
            if not pick or not pick.get("occ"):
                continue
            mid = float(pick.get("mid_price") or 0)
            if mid <= 0:
                continue
            qty = max(1, int(per_pick_usd // (mid * 100)))  # contracts = $/(mid*100)
            if qty * mid * 100 > sleeve_capital_usd * 0.5:
                continue  # don't blow >50% of remaining sleeve in one leg
            kind = (IntradaySwingActionKind.OPEN_LONG_CALL
                    if pick["type"] == "call"
                    else IntradaySwingActionKind.OPEN_LONG_PUT)
            out.append(IntradaySwingAction(
                kind=kind, symbol=tkr, occ_symbol=pick["occ"],
                qty=qty, side="buy",
                reason=f"signals={cand.get('signal_flags')} direction={pick['type']} "
                       f"delta={pick.get('delta')} dte={pick.get('dte')}",
                params={"limit_price": mid, "strike": pick.get("strike"),
                        "dte": pick.get("dte"), "iv_rank": cand.get("iv_rank")},
            ))
            slots_remaining -= 1
        return out
