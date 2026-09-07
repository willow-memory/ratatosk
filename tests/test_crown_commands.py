import argparse

from ratatosk import session as _session
from ratatosk.crown import CommandRouter, RuntimeState
from ratatosk.history import index_session
from ratatosk.hooks import HookRuntime
from ratatosk.policy import PolicyStore


def _state(tmp_path, monkeypatch) -> RuntimeState:
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    monkeypatch.setenv("RATATOSK_SESSION_DIR", str(tmp_path / "ratatosk" / "sessions" / "proj"))
    writer = _session.SessionWriter(cwd=str(tmp_path))
    return RuntimeState(
        args=argparse.Namespace(local=True, trust=False, mcp=False, listen=False, deposit=False),
        model="test-model",
        writer=writer,
        history=[],
        system_prompt="test",
        all_tools=[],
        mcp_names=set(),
        mcp_call=None,
        client=None,
        policy=PolicyStore(),
        hooks=HookRuntime(),
    )


def test_permissions_command_roundtrip(tmp_path, monkeypatch, capsys):
    state = _state(tmp_path, monkeypatch)
    router = CommandRouter(state)
    assert router.run("/permissions set Read allow") is False
    assert state.policy.decide("Read") == "allow"
    assert router.run("/permissions list") is False
    out = capsys.readouterr().out
    assert "Read" in out


def test_resume_command_loads_history(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch)
    state.writer.write_user("first prompt")
    state.writer.write_assistant("first answer")
    index_session(
        session_id=state.writer.session_id,
        cwd=state.writer.cwd,
        model=state.model,
        jsonl_path=state.writer.path,
    )
    fresh_state = _state(tmp_path, monkeypatch)
    router = CommandRouter(fresh_state)
    prefix = state.writer.session_id[:8]
    assert router.run(f"/resume {prefix}") is False
    assert fresh_state.history
    assert "resumed context" in str(fresh_state.history[0]["content"])
