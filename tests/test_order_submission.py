"""What the order POST is allowed to contain, and when it may go out at all.

Every rule here was paid for by a rejected or duplicated order somewhere in the
engine this book was cut from.

The limit-price rule was measured on 2026-08-31, day one of the book. A sibling
sleeve in the private engine passed a raw float and Alpaca answered
``422 {"code":42210000,"message":"limit price must be limited to 2 decimal
places"}`` on all four contracts it had selected. The sleeve opened nothing that
session. The fix is a string format rather than ``round()``, because a rounded
float still serialises as ``1.2300000000000002`` and gets rejected again.

The market-closed and no-retry rules are older, and the docstrings on the
methods themselves say what they cost.
"""

from __future__ import annotations

import pytest

import alpaca_rest
from alpaca_rest import AlpacaRest, AlpacaRestError


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ALPACA_HACKATHON_API_KEY_ID", "test-key")
    monkeypatch.setenv("ALPACA_HACKATHON_API_SECRET", "test-secret")
    monkeypatch.delenv("ALPACA_HACKATHON_TRADING_ENDPOINT", raising=False)
    return AlpacaRest()


@pytest.fixture
def posted(monkeypatch, client):
    """Captures the order body instead of sending it. Market reads as open."""
    calls: list[dict] = []

    class Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"id": "stub", "status": "accepted"}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "body": json})
        return Resp()

    monkeypatch.setattr(client, "assert_market_open", lambda: True)
    monkeypatch.setattr(alpaca_rest.requests, "post", fake_post)
    return calls


# --- the limit price ------------------------------------------------------

@pytest.mark.parametrize("given, expected", [
    (1.2300000000000002, "1.23"),
    (0.555, "0.56"),
    (1.0, "1.00"),
    (12, "12.00"),
])
def test_a_limit_price_reaches_alpaca_at_two_decimal_places(
        client, posted, given, expected):
    client.place_option_order("BAC261002P00060000", 1, limit_price=given)
    assert posted[0]["body"]["limit_price"] == expected


def test_the_limit_price_is_a_string_not_a_float(client, posted):
    # round() is not enough. 1.2300000000000002 rounds to a float that still
    # serialises with the tail attached; only formatting removes it.
    client.place_option_order("BAC261002P00060000", 1, limit_price=1.23)
    assert isinstance(posted[0]["body"]["limit_price"], str)


def test_a_limit_order_with_no_price_is_refused_before_it_is_sent(client, posted):
    with pytest.raises(AlpacaRestError):
        client.place_option_order("BAC261002P00060000", 1, limit_price=None)
    assert posted == [], "nothing may reach the broker"


def test_a_market_order_carries_no_limit_price(client, posted):
    client.place_option_order("BAC261002P00060000", 1, order_type="market")
    assert "limit_price" not in posted[0]["body"]


# --- the pre-order guard --------------------------------------------------

def test_a_closed_market_stops_the_order(client, monkeypatch):
    sent: list = []
    monkeypatch.setattr(client, "assert_market_open", lambda: False)
    monkeypatch.setattr(alpaca_rest.requests, "post",
                        lambda *a, **k: sent.append(1))
    with pytest.raises(AlpacaRestError):
        client.place_option_order("BAC261002P00060000", 1, limit_price=1.23)
    assert sent == []


def test_an_unreadable_clock_reads_as_closed(client, monkeypatch):
    # Absent is never permissive. A failed lookup must not become a tradable
    # market.
    def boom(*a, **k):
        raise AlpacaRestError("simulated: clock unreachable")

    monkeypatch.setattr(client, "get_clock", boom)
    assert client.assert_market_open() is False


# --- exactly once ---------------------------------------------------------

def test_the_order_post_happens_exactly_once(client, posted):
    client.place_option_order("BAC261002P00060000", 1, limit_price=1.23)
    assert len(posted) == 1


def test_every_order_carries_its_own_client_order_id(client, posted):
    client.place_option_order("BAC261002P00060000", 1, limit_price=1.23)
    client.place_option_order("MO261009P00065000", 1, limit_price=0.77)
    ids = [c["body"]["client_order_id"] for c in posted]
    assert ids[0] != ids[1]
    assert all(i.startswith("refusal-rails-") for i in ids)


def test_a_rejection_is_raised_with_the_brokers_own_words(client, monkeypatch):
    """The reject body is the audit trail. Swallowing it loses the reason."""
    class Resp:
        status_code = 422
        text = '{"code":42210000,"message":"limit price must be limited to 2 decimal places"}'

    monkeypatch.setattr(client, "assert_market_open", lambda: True)
    monkeypatch.setattr(alpaca_rest.requests, "post", lambda *a, **k: Resp())
    with pytest.raises(AlpacaRestError) as e:
        client.place_option_order("BAC261002P00060000", 1, limit_price=1.23)
    assert "42210000" in str(e.value)


# --- the paper pin --------------------------------------------------------

def test_a_live_endpoint_is_refused_at_construction(monkeypatch):
    monkeypatch.setenv("ALPACA_HACKATHON_API_KEY_ID", "test-key")
    monkeypatch.setenv("ALPACA_HACKATHON_API_SECRET", "test-secret")
    with pytest.raises(AlpacaRestError):
        AlpacaRest(base_url="https://api.alpaca.markets")


def test_the_env_example_placeholder_is_refused_at_construction(monkeypatch):
    """A filled .env that was never sourced looks exactly like an unset key.

    Measured 2026-09-03. ``cp .env.example .env`` leaves the literal
    ``your_paper_key_id`` in the environment. It is a non-empty string, so the
    missing-credential check passes it through and the first GET comes back
    ``401 {"message": "unauthorized."}`` with nothing naming the cause. The
    placeholder is a known value; refuse it by name instead.
    """
    monkeypatch.setenv("ALPACA_HACKATHON_API_KEY_ID", "your_paper_key_id")
    monkeypatch.setenv("ALPACA_HACKATHON_API_SECRET", "your_paper_secret")
    with pytest.raises(AlpacaRestError) as exc:
        AlpacaRest()
    assert ".env" in str(exc.value)


def test_a_placeholder_in_either_half_is_enough_to_refuse(monkeypatch):
    monkeypatch.setenv("ALPACA_HACKATHON_API_KEY_ID", "PKREALLOOKINGKEY")
    monkeypatch.setenv("ALPACA_HACKATHON_API_SECRET", "your_paper_secret")
    with pytest.raises(AlpacaRestError):
        AlpacaRest()


def test_a_real_looking_pair_still_constructs(monkeypatch):
    monkeypatch.setenv("ALPACA_HACKATHON_API_KEY_ID", "PKREALLOOKINGKEY")
    monkeypatch.setenv("ALPACA_HACKATHON_API_SECRET", "arealsecretvalue")
    assert AlpacaRest().key_id == "PKREALLOOKINGKEY"
