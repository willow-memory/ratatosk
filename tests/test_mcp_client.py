import os

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
