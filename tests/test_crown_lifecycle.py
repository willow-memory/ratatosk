"""The session closes even when the turn does not."""

import argparse
import importlib

from ratatosk import session as _session
from ratatosk.crown import RuntimeState, _resolve_willow_root, _shutdown
from ratatosk.hooks import HookRuntime
from ratatosk.policy import PolicyStore


def _state(tmp_path, monkeypatch, **overrides) -> RuntimeState:
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    monkeypatch.setenv(
        "RATATOSK_SESSION_DIR", str(tmp_path / "ratatosk" / "sessions" / "proj")
    )
    args = argparse.Namespace(
        local=True, trust=False, mcp=False, listen=False, deposit=False
    )
    for key, value in overrides.items():
        setattr(args, key, value)
    writer = _session.SessionWriter(cwd=str(tmp_path))
    return RuntimeState(
        args=args,
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


def test_shutdown_indexes_the_session(tmp_path, monkeypatch, capsys):
    """The defect: an interrupt skipped this, so /sessions could not see the run."""
    state = _state(tmp_path, monkeypatch)
    state.writer.write_user("hello")  # a session with something in it to index
    indexed = {}
    monkeypatch.setattr(
        "ratatosk.crown.index_session",
        lambda **kw: indexed.update(kw),
    )
    _shutdown(state)
    assert indexed["session_id"] == state.writer.session_id
    assert "Session written" in capsys.readouterr().out


def test_shutdown_fires_the_session_end_hook(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch)
    fired = []
    monkeypatch.setattr("ratatosk.crown.index_session", lambda **kw: None)
    monkeypatch.setattr(
        state.hooks, "run_event", lambda name, payload: fired.append(name)
    )
    _shutdown(state)
    assert fired == ["SessionEnd"]


def test_shutdown_survives_a_failing_step(tmp_path, monkeypatch, capsys):
    """One broken step must not strand the rest — it runs from a finally."""
    state = _state(tmp_path, monkeypatch)
    state.writer.write_user("hello")  # a session with something in it to index
    indexed = []

    def boom(*a, **kw):
        raise RuntimeError("grove is down")

    monkeypatch.setattr("ratatosk.crown._grove.session_ended", boom)
    monkeypatch.setattr("ratatosk.crown.index_session", lambda **kw: indexed.append(kw))
    _shutdown(state)
    assert indexed, "index_session must still run after an earlier step raised"
    assert "grove is down" in capsys.readouterr().out


def test_shutdown_reports_an_unfinished_mcp_teardown(tmp_path, monkeypatch, capsys):
    state = _state(tmp_path, monkeypatch, mcp=True)
    monkeypatch.setattr("ratatosk.crown.index_session", lambda **kw: None)
    monkeypatch.setattr("ratatosk.mcp_client.shutdown", lambda *a, **kw: False)
    _shutdown(state)
    assert "teardown did not finish" in capsys.readouterr().out


def test_willow_root_prefers_explicit_env(monkeypatch, tmp_path):
    monkeypatch.setenv("WILLOW_ROOT", str(tmp_path / "explicit"))
    assert _resolve_willow_root() == tmp_path / "explicit"


def test_willow_root_derives_from_willow_home(monkeypatch, tmp_path):
    monkeypatch.delenv("WILLOW_ROOT", raising=False)
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path / "fleet" / ".willow"))
    assert _resolve_willow_root() == tmp_path / "fleet"


def test_willow_root_no_longer_points_at_the_dead_path(monkeypatch):
    """The old default was ~/github/willow-memory/willow, which exists nowhere."""
    monkeypatch.delenv("WILLOW_ROOT", raising=False)
    monkeypatch.delenv("WILLOW_HOME", raising=False)
    monkeypatch.delenv("WILLOW_STORE_ROOT", raising=False)
    assert _resolve_willow_root().name != "willow"


def test_mcp_shutdown_is_a_noop_before_start():
    mcp_client = importlib.import_module("ratatosk.mcp_client")
    assert mcp_client.shutdown() is True


def test_ctrl_c_mid_turn_still_closes_the_session(tmp_path, monkeypatch, capsys):
    """End to end through main(): the interrupt ends the turn, not the session.

    Before the fix KeyboardInterrupt (a BaseException) sailed past the
    `except Exception` and out of the REPL loop, skipping every cleanup path.
    """
    from ratatosk import crown

    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    monkeypatch.setenv(
        "RATATOSK_SESSION_DIR", str(tmp_path / "ratatosk" / "sessions" / "proj")
    )
    monkeypatch.setattr("sys.argv", ["ratatosk", "--local"])

    prompts = iter(["do the thing", EOFError])

    def fake_input(_prompt):
        nxt = next(prompts)
        if nxt is EOFError:
            raise EOFError
        return nxt

    def interrupted(state, user_input):
        # The real `_run_turn` writes the user entry as its first line, so an
        # interrupted turn always leaves a JSONL behind. Raising before that
        # would model a session that never wrote anything — which close-out now
        # reports as such, and correctly does not index.
        state.writer.write_user(user_input)
        raise KeyboardInterrupt

    indexed = []
    fired = []
    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(crown, "_run_turn", interrupted)
    monkeypatch.setattr(crown, "index_session", lambda **kw: indexed.append(kw))
    monkeypatch.setattr(
        crown.HookRuntime, "run_event", lambda self, n, p: fired.append(n)
    )

    crown.main()

    out = capsys.readouterr().out
    assert "interrupted" in out, "the user should be told the turn was abandoned"
    assert indexed, "the session must still be indexed after an interrupt"
    assert "SessionEnd" in fired
