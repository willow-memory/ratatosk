import pytest

from ratatosk import mcp_client


def test_server_env_forwards_fleet_config(monkeypatch):
    """The SDK hands a spawned server a stripped env; WILLOW_* must survive."""
    pytest.importorskip("mcp")
    monkeypatch.setenv("WILLOW_PG_DB", "willow_20")
    monkeypatch.setenv("WILLOW_HOME", "/somewhere/.willow")
    monkeypatch.setenv("RATATOSK_APP_ID", "ratatosk")
    monkeypatch.delenv("RATATOSK_MCP_INHERIT_ENV", raising=False)
    env = mcp_client.server_env()
    assert env["WILLOW_PG_DB"] == "willow_20"
    assert env["WILLOW_HOME"] == "/somewhere/.willow"
    assert env["RATATOSK_APP_ID"] == "ratatosk"
    assert "PATH" in env


def test_server_env_can_inherit_everything(monkeypatch):
    pytest.importorskip("mcp")
    monkeypatch.setenv("RATATOSK_MCP_INHERIT_ENV", "1")
    monkeypatch.setenv("SOME_UNRELATED_VAR", "kept")
    assert mcp_client.server_env()["SOME_UNRELATED_VAR"] == "kept"


# ---- reconnect ------------------------------------------------------------


def _reset(monkeypatch, **overrides):
    """Isolate the module globals a reconnect touches."""
    for name, value in {
        "_mcp_argv": None,
        "_last_reconnect": 0.0,
        "_mcp_session": None,
        "_mcp_thread": None,
    }.items():
        monkeypatch.setattr(mcp_client, name, overrides.get(name, value), raising=False)


def test_reconnect_refuses_when_nothing_was_ever_started(monkeypatch):
    """There is no argv to repeat, so there is nothing to reconnect to."""
    _reset(monkeypatch)
    assert mcp_client.reconnect() is False


def test_reconnect_is_rate_limited(monkeypatch):
    """A listener polling every 2s would otherwise respawn a dead server —
    and its Postgres connections — dozens of times a minute."""
    starts = []
    _reset(monkeypatch, _mcp_argv=["python", "-m", "willow_mcp"])
    monkeypatch.setattr(mcp_client, "shutdown", lambda timeout=10.0: True)
    monkeypatch.setattr(
        mcp_client, "start", lambda argv=None: starts.append(argv) or ([], set())
    )
    monkeypatch.setattr(mcp_client, "is_live", lambda: True)

    assert mcp_client.reconnect() is True
    assert mcp_client.reconnect() is False, "the second is inside the cooldown"
    assert len(starts) == 1


def test_a_tool_error_does_not_trigger_a_reconnect(monkeypatch):
    """An answered error is the tool talking; only a transport failure is
    worth respawning a server for."""
    reconnects = []
    monkeypatch.setattr(mcp_client, "reconnect", lambda: reconnects.append(1) or True)
    monkeypatch.setattr(
        mcp_client, "_call_once", lambda name, inputs: "[mcp-error] denied"
    )

    assert mcp_client.call("kb_promote", {}) == "[mcp-error] denied"
    assert reconnects == []


def test_a_transport_failure_reconnects_and_retries_once(monkeypatch):
    attempts = []

    def flaky(name, inputs):
        attempts.append(name)
        if len(attempts) == 1:
            raise RuntimeError("stdio closed")
        return '{"ok": true}'

    monkeypatch.setattr(mcp_client, "_call_once", flaky)
    monkeypatch.setattr(mcp_client, "reconnect", lambda: True)

    assert mcp_client.call("whoami", {}) == '{"ok": true}'
    assert len(attempts) == 2, "one retry, not a loop"


def test_a_failed_reconnect_reports_the_original_error(monkeypatch):
    monkeypatch.setattr(
        mcp_client,
        "_call_once",
        lambda n, i: (_ for _ in ()).throw(RuntimeError("stdio closed")),
    )
    monkeypatch.setattr(mcp_client, "reconnect", lambda: False)

    out = mcp_client.call("whoami", {})
    assert out.startswith("[mcp-error]")
    assert "stdio closed" in out
    assert "after reconnect" not in out, "it never got a second attempt"
