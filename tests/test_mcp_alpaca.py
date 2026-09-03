"""mcp_alpaca must never be able to stop a trade.

This book's MCP path was written two days before its only four trading
sessions and could not be exercised on the authoring machine — macOS refuses to
dlopen the unsigned `rpds` extension the MCP SDK pulls in through jsonschema
("library load disallowed by system policy"), even after xattr -c and an ad-hoc
codesign. So the contract these tests pin is not "MCP works", which only the
Linux container can demonstrate; it is "every way MCP can fail is survivable".

That is the property that actually matters here. A broken MCP path costs the
book an audit-trail line. A raising MCP path costs it a trading session, and it
only gets four.
"""

from __future__ import annotations

import builtins

import pytest

import mcp_alpaca


def test_every_public_helper_swallows_a_broken_sdk(monkeypatch):
    """The real local failure mode: the SDK import blows up inside the call."""
    real_import = builtins.__import__

    def exploding_import(name, *args, **kwargs):
        if name == "mcp" or name.startswith("mcp."):
            raise ModuleNotFoundError("simulated: no MCP SDK")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", exploding_import)

    assert mcp_alpaca.call_tool("get_account_info") is None
    assert mcp_alpaca.get_option_chain("AAPL", "2026-09-04") is None
    assert mcp_alpaca.get_option_snapshot(["AAPL260904C00200000"]) is None
    assert mcp_alpaca.probe()["ok"] is False


@pytest.mark.parametrize(
    "boom",
    [
        RuntimeError("server died"),
        ValueError("garbage payload"),
        OSError("no such binary"),
        TypeError("protocol changed shape"),
    ],
)
def test_call_tool_absorbs_every_ordinary_failure(monkeypatch, boom):
    """Every Exception subclass degrades to None with a recorded status."""
    async def explode(*_a, **_k):
        raise boom

    monkeypatch.setattr(mcp_alpaca, "_call_tool_async", explode)
    assert mcp_alpaca.call_tool("get_option_chain", {"underlying_symbol": "AAPL"}) is None
    assert mcp_alpaca.LAST_STATUS.startswith(("error:", "timeout:", "empty:"))


@pytest.mark.parametrize("boom", [KeyboardInterrupt, SystemExit])
def test_shutdown_signals_deliberately_propagate(monkeypatch, boom):
    """Shutdown is the one thing MCP must NOT swallow.

    An earlier draft of this test asserted the opposite — that a SIGINT mid-call
    should degrade to 'no MCP data' like any other failure — and the code
    (`except Exception`) failed it. The code was right and the test was wrong.
    KeyboardInterrupt and SystemExit mean the container is going down; a book
    that swallows them keeps making trading decisions through its own shutdown.
    Absorbing a dead MCP server is fail-safe, absorbing a shutdown is not.
    """
    async def explode(*_a, **_k):
        raise boom()

    monkeypatch.setattr(mcp_alpaca, "_call_tool_async", explode)
    with pytest.raises(boom):
        mcp_alpaca.call_tool("get_account_info")


def test_missing_credentials_do_not_raise(monkeypatch):
    monkeypatch.delenv("ALPACA_HACKATHON_API_KEY_ID", raising=False)
    monkeypatch.delenv("ALPACA_HACKATHON_API_SECRET", raising=False)
    assert mcp_alpaca.call_tool("get_account_info") is None


def test_credentials_are_hackathon_scoped_and_paper_only(monkeypatch):
    """Guards the mistake that cost a whole book earlier this month.

    The generic ALPACA_API_KEY_ID pair has pointed at a DIFFERENT paper account
    (PA3GFFYS3PYL) in this repo. If _credentials ever falls back to it, an MCP
    session would authenticate as the wrong book, so pin that it reads only the
    hackathon names — and that paper mode is not config-settable.
    """
    monkeypatch.setenv("ALPACA_API_KEY_ID", "WRONG-ACCOUNT-KEY")
    monkeypatch.setenv("ALPACA_API_SECRET", "WRONG-ACCOUNT-SECRET")
    monkeypatch.delenv("ALPACA_HACKATHON_API_KEY_ID", raising=False)
    monkeypatch.delenv("ALPACA_HACKATHON_API_SECRET", raising=False)
    assert mcp_alpaca._credentials() is None, "fell back to a non-hackathon account"

    monkeypatch.setenv("ALPACA_HACKATHON_API_KEY_ID", "RIGHT-KEY")
    monkeypatch.setenv("ALPACA_HACKATHON_API_SECRET", "RIGHT-SECRET")
    creds = mcp_alpaca._credentials()
    assert creds["ALPACA_API_KEY"] == "RIGHT-KEY"
    assert creds["ALPACA_PAPER_TRADE"] == "true"


def test_non_dict_tool_results_are_rejected(monkeypatch):
    """A tool that answers with a bare string must not reach the sleeve."""
    monkeypatch.setattr(mcp_alpaca, "call_tool", lambda *_a, **_k: "unexpected text")
    assert mcp_alpaca.get_option_chain("AAPL") is None
    assert mcp_alpaca.get_option_snapshot(["AAPL260904C00200000"]) is None


def test_empty_inputs_short_circuit_before_spawning(monkeypatch):
    """No subprocess for an empty request — the sleeve calls this per candidate."""
    def fail(*_a, **_k):
        raise AssertionError("spawned a server for an empty request")

    monkeypatch.setattr(mcp_alpaca, "call_tool", fail)
    assert mcp_alpaca.get_option_snapshot([]) is None
    assert mcp_alpaca.get_option_chain("") is None


def test_the_child_server_is_told_to_stay_quiet(monkeypatch):
    """The server's own banner lands in the middle of the demo output.

    FastMCP prints a boxed banner and an update-available notice to stderr on
    every spawn, plus an INFO line naming its transport. The child inherits this
    process's stderr, so all three appear between demo sections and read as
    output from this book. Measured 2026-09-03. Turn them off in the child's
    environment rather than swallowing its stderr, so a real server error still
    reaches the terminal.
    """
    monkeypatch.setenv("ALPACA_HACKATHON_API_KEY_ID", "PKTEST")
    monkeypatch.setenv("ALPACA_HACKATHON_API_SECRET", "sectest")
    env = mcp_alpaca._server_env()
    assert env["FASTMCP_SHOW_SERVER_BANNER"] == "false"
    # 'off', not "false": this setting is a three-way literal and a boolean
    # string fails the server's settings validation at import, taking the whole
    # MCP path down behind a quiet banner.
    assert env["FASTMCP_CHECK_FOR_UPDATES"] == "off"
    assert env["FASTMCP_LOG_LEVEL"] == "ERROR"
    assert env["ALPACA_API_KEY"] == "PKTEST"


def test_no_credentials_means_no_child_environment(monkeypatch):
    monkeypatch.delenv("ALPACA_HACKATHON_API_KEY_ID", raising=False)
    monkeypatch.delenv("ALPACA_HACKATHON_API_SECRET", raising=False)
    assert mcp_alpaca._server_env() is None
