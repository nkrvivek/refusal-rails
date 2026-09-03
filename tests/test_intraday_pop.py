"""The three rules the intraday-pop sleeve holds, pinned as tests.

The sleeve buys a name already up hard on the day. That is the setup where a
sizing bug is most expensive, so the rules below are the ones written down
before the code, and each is a refusal rather than an entry:

* Sizing reads the two config dollar caps and nothing else. A live
  buying-power field moves inside a tick, so it is never an input.
* Absent is never zero. A snapshot missing any price field is dropped, an
  unreadable VIX blocks the gate, and missing flow is not a pass.
* A stop at or above the entry means the pop already failed, so no legs go out.
"""

from __future__ import annotations

from strategies.intraday_pop import (
    GATE_INSTRUMENT,
    IntradayPopConfig,
    confirm_flow,
    open_gate,
    pick_atm_call,
    rank_candidates,
    read_snapshot,
    size_legs,
)

CFG = IntradayPopConfig.from_cfg({
    "intraday_pop": {
        "enabled": True,
        "equity_cap_usd": 25_000,
        "options_cap_usd": 20_000,
        "entry_window_et": "10:00-11:30",
        "close_out_et": "15:45",
        "min_day_move_pct": 5.0,
        "min_call_put_ratio": 2.0,
        "min_calls": 20,
        "stop_pct": 3.0,
        "spy_band_pct": 0.6,
        "vix_max": 22.0,
        "blocked_regimes": ["HALT", "BEAR"],
    }
})


def snap(last, prev, opened, bid, ask):
    return {
        "latestTrade": {"p": last},
        "prevDailyBar": {"c": prev},
        "dailyBar": {"o": opened},
        "latestQuote": {"bp": bid, "ap": ask},
    }


# --- sizing comes from the caps, never from the account -------------------

def test_size_reads_only_the_config_caps():
    legs = size_legs(entry_ask=100.0, day_open=90.0, option_ask=2.50, cfg=CFG)
    assert legs.eq_qty == 250          # 25,000 / 100
    assert legs.opt_qty == 80          # 20,000 / (2.50 * 100)
    assert legs.opt_limit == 2.50


def test_size_is_unchanged_by_anything_the_broker_reports():
    # There is no buying-power parameter to pass. The signature is the rule.
    first = size_legs(entry_ask=100.0, day_open=90.0, option_ask=2.50, cfg=CFG)
    second = size_legs(entry_ask=100.0, day_open=90.0, option_ask=2.50, cfg=CFG)
    assert first == second


def test_limit_price_is_rounded_to_two_places():
    legs = size_legs(entry_ask=100.0, day_open=90.0, option_ask=2.4567, cfg=CFG)
    assert legs.opt_limit == 2.46
    assert f"{legs.opt_limit:.2f}" == "2.46"


# --- a failed pop opens nothing -------------------------------------------

def test_stop_at_or_above_entry_places_no_legs():
    # Price has fallen back under the day's open, so the stop would sit above
    # the fill. The pop already failed.
    legs = size_legs(entry_ask=89.0, day_open=90.0, option_ask=2.50, cfg=CFG)
    assert legs.eq_qty == 0
    assert legs.opt_qty == 0
    assert "pop already failed" in legs.reason


def test_stop_is_the_higher_of_day_open_and_the_percentage_stop():
    legs = size_legs(entry_ask=100.0, day_open=99.0, option_ask=2.50, cfg=CFG)
    assert legs.stop_px == 99.0        # day open beats 100 * 0.97


# --- absent is never zero -------------------------------------------------

def test_snapshot_missing_a_price_field_is_dropped():
    assert read_snapshot("AAA", snap(110.0, 100.0, 101.0, 109.9, 110.1)) is not None
    assert read_snapshot("AAA", snap(110.0, None, 101.0, 109.9, 110.1)) is None
    assert read_snapshot("AAA", snap(110.0, 100.0, 101.0, 109.9, 0)) is None
    assert read_snapshot("AAA", None) is None


def test_unreadable_price_never_sizes_a_position():
    legs = size_legs(entry_ask=None, day_open=90.0, option_ask=2.50, cfg=CFG)
    assert legs.eq_qty == 0 and legs.opt_qty == 0


def test_unreadable_option_ask_drops_the_option_leg_only():
    legs = size_legs(entry_ask=100.0, day_open=90.0, option_ask=None, cfg=CFG)
    assert legs.eq_qty == 250
    assert legs.opt_qty == 0


def test_gate_blocks_on_every_input_it_cannot_read():
    ok, why = open_gate(spy_last=600.0, spy_prev_close=598.0, vix=15.0,
                        regime_label="BULL", factor_stress=False, cfg=CFG)
    assert ok, why
    assert not open_gate(spy_last=600.0, spy_prev_close=598.0, vix=None,
                         regime_label="BULL", factor_stress=False, cfg=CFG)[0]
    assert not open_gate(spy_last=None, spy_prev_close=598.0, vix=15.0,
                         regime_label="BULL", factor_stress=False, cfg=CFG)[0]
    assert not open_gate(spy_last=600.0, spy_prev_close=598.0, vix=15.0,
                         regime_label=None, factor_stress=False, cfg=CFG)[0]
    assert not open_gate(spy_last=600.0, spy_prev_close=598.0, vix=15.0,
                         regime_label="HALT", factor_stress=False, cfg=CFG)[0]


def test_missing_flow_is_not_a_pass():
    ok, _ = confirm_flow({"call_put_ratio": 3.0, "n_calls": 40},
                         {"net_above_market": 1_000_000}, CFG)
    assert ok
    assert not confirm_flow(None, {"net_above_market": 1_000_000}, CFG)[0]
    assert not confirm_flow({"call_put_ratio": 3.0, "n_calls": 40}, None, CFG)[0]
    assert not confirm_flow({"n_calls": 40}, {"net_above_market": 1_000_000}, CFG)[0]


def test_distributing_darkpool_blocks_the_entry():
    ok, why = confirm_flow({"call_put_ratio": 3.0, "n_calls": 40},
                           {"net_above_market": -500_000}, CFG)
    assert not ok
    assert "distributing" in why


# --- candidate selection --------------------------------------------------

def test_only_names_above_the_move_floor_rank_and_the_gate_never_does():
    rows = rank_candidates({
        "AAA": snap(110.0, 100.0, 101.0, 109.9, 110.1),   # +10%
        "BBB": snap(106.0, 100.0, 101.0, 105.9, 106.1),   # +6%
        "CCC": snap(102.0, 100.0, 101.0, 101.9, 102.1),   # +2%, under the floor
        GATE_INSTRUMENT: snap(660.0, 600.0, 601.0, 659.9, 660.1),
    }, CFG)
    assert [r.symbol for r in rows] == ["AAA", "BBB"]


def test_atm_call_takes_the_nearest_expiry_then_the_strike_at_or_below_spot():
    rows = [
        {"type": "call", "strike": 100.0, "dte": 3, "sym": "far"},
        {"type": "call", "strike": 105.0, "dte": 1, "sym": "above"},
        {"type": "call", "strike": 100.0, "dte": 1, "sym": "at"},
        {"type": "put", "strike": 100.0, "dte": 1, "sym": "put"},
    ]
    assert pick_atm_call(rows, spot=102.0)["sym"] == "at"
    assert pick_atm_call([], spot=102.0) is None
