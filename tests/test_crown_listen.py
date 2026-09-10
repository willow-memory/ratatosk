"""The --listen path tears the MCP server down on every exit, including refusal.

BusListener refuses an unset channel, and that refusal arrives after
mcp_client.start() has already spawned the server. Built above the try it
tracebacked out of main() and left the child process running.
"""
import pytest

from ratatosk import crown, mcp_client


@pytest.fixture
def fake_mcp(tmp_path, monkeypatch):
    """A willow-mcp stdio server that records whether it was torn down."""
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    torn_down = {"called": False}

    def _shutdown():
        torn_down["called"] = True
        return True

    monkeypatch.setattr(mcp_client, "start", lambda: ([], set()))
    monkeypatch.setattr(mcp_client, "call", lambda name, inputs: {})
    monkeypatch.setattr(mcp_client, "shutdown", _shutdown)
    monkeypatch.setattr("sys.argv", ["ratatosk", "--mcp", "--listen"])
    return torn_down


def test_unset_channel_refuses_without_orphaning_the_server(fake_mcp, monkeypatch, capsys):
    monkeypatch.delenv("RATATOSK_GROVE_CHANNEL", raising=False)

    with pytest.raises(SystemExit) as exit_info:
        crown.main()

    assert exit_info.value.code == 1, "an unconfigured listener is a failure, not a quiet no-op"
    assert fake_mcp["called"], "the MCP server must be torn down, not left to process exit"
    out = capsys.readouterr().out
    assert "grove channel unset" in out
    assert "RATATOSK_GROVE_CHANNEL" in out, "an error states the fault and the remedy"


def test_ctrl_c_still_tears_the_server_down(fake_mcp, monkeypatch, capsys):
    monkeypatch.setenv("RATATOSK_GROVE_CHANNEL", "fleet")

    def _interrupt(self, on_status=None):
        raise KeyboardInterrupt

    monkeypatch.setattr("ratatosk.listener.BusListener.run_forever", _interrupt)

    crown.main()

    assert fake_mcp["called"]
    assert "stopped" in capsys.readouterr().out
