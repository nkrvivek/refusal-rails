"""Alpaca REST client — the order path for the Refusal Rails book.

One of two Alpaca transports this agent uses. REST owns orders and account
state; the MCP server (see ``mcp_alpaca.py``) serves options market data. The
split is not decorative: MCP option orders are market and limit only, confirmed
by Alpaca's Danny in the 2026-08-28 hackathon Q&A, and this book's entry logic
needs the wider order-type surface, so orders go out over REST while contract
discovery is served over MCP.

Pinned to the paper endpoint. ``_require_paper`` runs on construction and
refuses any host that is not ``paper-api.alpaca.markets``, so a mistyped
environment variable fails loudly at startup rather than quietly sending a live
order. This book is a hackathon entry against a dedicated $100,000 paper
account and has no live mandate of any kind.
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import requests

PAPER_HOST = "paper-api.alpaca.markets"
DATA_HOST = "https://data.alpaca.markets"
DEFAULT_TIMEOUT_S = 20.0

# The literal values shipped in .env.example. They are non-empty, so the
# missing-credential check below waves them through and Alpaca answers 401 with
# nothing naming the cause. Refuse them by name instead.
ENV_EXAMPLE_PLACEHOLDERS = frozenset({"your_paper_key_id", "your_paper_secret"})


class AlpacaRestError(RuntimeError):
    """Any non-2xx answer from Alpaca, with the body kept for the audit trail."""


class AlpacaRest:
    def __init__(
        self,
        key_id: str | None = None,
        secret_key: str | None = None,
        base_url: str | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self.key_id = key_id or os.environ.get("ALPACA_HACKATHON_API_KEY_ID", "")
        self.secret_key = secret_key or os.environ.get("ALPACA_HACKATHON_API_SECRET", "")
        raw = base_url or os.environ.get(
            "ALPACA_HACKATHON_TRADING_ENDPOINT", f"https://{PAPER_HOST}",
        )
        self.base_url = raw.rstrip("/").removesuffix("/v2")
        self.timeout_s = timeout_s
        if not self.key_id or not self.secret_key:
            raise AlpacaRestError(
                "Set ALPACA_HACKATHON_API_KEY_ID and ALPACA_HACKATHON_API_SECRET "
                "(see .env.example).",
            )
        self._require_real_credentials()
        self._require_paper()

    def _require_real_credentials(self) -> None:
        """Refuse the .env.example placeholders before they earn a bare 401."""
        if ENV_EXAMPLE_PLACEHOLDERS & {self.key_id, self.secret_key}:
            raise AlpacaRestError(
                "The .env.example placeholder is still in the environment. Put "
                "your real paper keys in .env, then source it in this shell: "
                "set -a && . ./.env && set +a",
            )

    def _require_paper(self) -> None:
        host = urlparse(self.base_url).hostname or ""
        if host != PAPER_HOST:
            raise AlpacaRestError(
                f"Refusing to start against {host!r}. This book is paper-only and "
                f"pins {PAPER_HOST}.",
            )

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.key_id,
            "APCA-API-SECRET-KEY": self.secret_key,
            "accept": "application/json",
        }

    def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        resp = requests.get(
            url, headers=self._headers, params=params, timeout=self.timeout_s,
        )
        if resp.status_code >= 400:
            raise AlpacaRestError(f"GET {url} -> {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    # ---- account ---------------------------------------------------------

    def get_account(self) -> dict[str, Any]:
        return self._get(f"{self.base_url}/v2/account")

    def get_positions(self) -> list[dict[str, Any]]:
        out = self._get(f"{self.base_url}/v2/positions")
        return out if isinstance(out, list) else []

    def get_clock(self) -> dict[str, Any]:
        """Market clock. The pre-order guard below reads this and nothing else."""
        return self._get(f"{self.base_url}/v2/clock")

    # ---- options market data --------------------------------------------

    def _underlying_snapshots(
        self, underlying: str, expiration: str | None = None,
    ) -> dict[str, Any]:
        """Paginate the option snapshot feed for one underlying.

        The free indicative feed is used deliberately: it is what the hackathon
        account has, and the entry logic only needs mid and delta, both of which
        it carries.
        """
        params: dict[str, Any] = {"feed": "indicative", "limit": 1000}
        if expiration:
            params["expiration_date"] = expiration
        snapshots: dict[str, Any] = {}
        page: str | None = None
        for _ in range(10):  # bounded: ~10k contracts is far past any real chain
            if page:
                params["page_token"] = page
            payload = self._get(
                f"{DATA_HOST}/v1beta1/options/snapshots/{underlying}", params,
            )
            snapshots.update(payload.get("snapshots") or {})
            page = payload.get("next_page_token")
            if not page:
                break
        return snapshots

    def get_option_expirations(self, underlying: str) -> list[str]:
        expirations: set[str] = set()
        for symbol in self._underlying_snapshots(underlying):
            parsed = parse_osi(symbol.replace(" ", ""))
            if parsed:
                expirations.add(parsed["expiry"])
        return sorted(expirations)

    def get_option_chain(self, underlying: str, expiration: str) -> dict[str, Any]:
        calls: list[dict[str, Any]] = []
        puts: list[dict[str, Any]] = []
        for symbol, snap in self._underlying_snapshots(underlying, expiration).items():
            compact = symbol.replace(" ", "")
            parsed = parse_osi(compact)
            if not parsed or parsed["expiry"] != expiration:
                continue
            quote = snap.get("latestQuote") or {}
            greeks = snap.get("greeks") or {}
            bid, ask = quote.get("bp"), quote.get("ap")
            mid = (bid + ask) / 2 if isinstance(bid, (int, float)) and isinstance(ask, (int, float)) and bid and ask else None
            row = {
                "symbol": compact,
                "strike": parsed["strike"],
                "expiry": parsed["expiry"],
                "bid": bid,
                "ask": ask,
                "mid_price": mid,
                "delta": greeks.get("delta"),
                "iv": snap.get("impliedVolatility"),
            }
            (calls if parsed["right"] == "C" else puts).append(row)
        calls.sort(key=lambda r: r["strike"])
        puts.sort(key=lambda r: r["strike"])
        return {"underlying": underlying, "expiration": expiration, "calls": calls, "puts": puts}

    def get_options_chain(
        self, underlying: str, *, dte_min: int = 1, dte_max: int = 3,
    ) -> dict[str, Any]:
        """Chain envelope for the first expiration inside the DTE band.

        Dual-transport: REST builds the envelope and is the source of truth, then
        the MCP server is asked for the same chain and its answer is recorded
        under ``mcp``. MCP is additive on purpose — it proves the transport ran
        and can add coverage, but it cannot remove or contradict a contract REST
        already validated, so a broken MCP path cannot change what this book
        trades. See ``mcp_alpaca`` for why that constraint is absolute.
        """
        today = datetime.now(timezone.utc).date()
        target = None
        for expiration in self.get_option_expirations(underlying):
            try:
                dte = (datetime.fromisoformat(expiration).date() - today).days
            except ValueError:
                continue
            if dte_min <= dte <= dte_max:
                target = expiration
                break
        if target is None:
            return {"underlying": underlying, "chain": None, "mcp": {"ok": False, "status": "no_expiration"},
                    "note": f"no expiration in DTE [{dte_min},{dte_max}]"}
        envelope = {
            "underlying": underlying,
            "expiration": target,
            "chain": self.get_option_chain(underlying, target),
        }
        envelope["mcp"] = self._mcp_chain_probe(underlying, target)
        return envelope

    @staticmethod
    def _mcp_chain_probe(underlying: str, expiration: str) -> dict[str, Any]:
        """Ask MCP for the same chain. Never raises, always returns a block."""
        try:
            import mcp_alpaca
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "status": f"import_error:{type(exc).__name__}"}
        result = mcp_alpaca.get_option_chain(underlying, expiration)
        contracts = 0
        paged = False
        if isinstance(result, dict):
            # The server wraps its payload in a "data" envelope alongside its own
            # security block, so the counted keys live one level down. Scanning
            # only the top level reported 0 contracts on a healthy ok response
            # (measured 2026-09-03), which reads on screen as a dead MCP path.
            payload = result.get("data") if isinstance(result.get("data"), dict) else result
            for key in ("snapshots", "contracts", "chain", "options"):
                value = payload.get(key)
                if isinstance(value, (dict, list)):
                    contracts = len(value)
                    break
            paged = bool(payload.get("next_page_token"))
        return {
            "ok": result is not None,
            "status": mcp_alpaca.LAST_STATUS,
            "contracts": contracts,
            "paged": paged,
        }

    # ---- orders ----------------------------------------------------------

    def place_option_order(
        self, occ_symbol: str, qty: int, side: str = "buy",
        order_type: str = "limit", limit_price: float | None = None,
        time_in_force: str = "day",
    ) -> dict[str, Any]:
        """Submit exactly once. Order POSTs are never automatically retried.

        A retry on an ambiguous response is how one intended contract becomes
        two filled ones. On a timeout the caller reconciles against
        ``/v2/orders`` using the client_order_id rather than resubmitting.
        """
        if not self.assert_market_open():
            raise AlpacaRestError("market closed — refusing to submit")
        body: dict[str, Any] = {
            "symbol": occ_symbol,
            "qty": str(int(qty)),
            "side": side,
            "type": order_type,
            "time_in_force": time_in_force,
            "client_order_id": f"refusal-rails-{uuid.uuid4().hex[:16]}",
        }
        if order_type == "limit":
            if limit_price is None:
                raise AlpacaRestError("limit order requires limit_price")
            body["limit_price"] = f"{float(limit_price):.2f}"
        resp = requests.post(
            f"{self.base_url}/v2/orders", headers=self._headers, json=body,
            timeout=self.timeout_s,
        )
        if resp.status_code >= 400:
            raise AlpacaRestError(f"order rejected {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    def assert_market_open(self) -> bool:
        """Hard pre-order guard, checked against Alpaca's own clock.

        This exists because a validation dispatch on a sibling book once placed
        two real day orders while the author believed it was a dry run. The
        guard is a live API call rather than a local time calculation so it
        cannot be wrong about holidays or half days.
        """
        try:
            return bool(self.get_clock().get("is_open"))
        except AlpacaRestError:
            return False  # unknown clock is treated as closed


def parse_osi(symbol: str) -> dict[str, Any] | None:
    """Parse an OSI option symbol, e.g. ``AAPL260904C00200000``.

    Layout: root, then YYMMDD, then C/P, then strike in thousandths padded to 8.
    """
    compact = symbol.replace(" ", "").upper()
    if len(compact) < 15:
        return None
    tail = compact[-15:]
    root = compact[: -15]
    if not root:
        return None
    try:
        yy, mm, dd = int(tail[0:2]), int(tail[2:4]), int(tail[4:6])
        right = tail[6]
        strike = int(tail[7:]) / 1000.0
    except (ValueError, IndexError):
        return None
    if right not in ("C", "P"):
        return None
    return {
        "root": root,
        "expiry": f"20{yy:02d}-{mm:02d}-{dd:02d}",
        "right": right,
        "strike": strike,
    }


__all__ = ["AlpacaRest", "AlpacaRestError", "parse_osi", "PAPER_HOST"]
