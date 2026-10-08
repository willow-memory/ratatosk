"""One-script part 3 wiring: ``_run_turn_bounded`` frames every turn.

turn_open(n) before the work, turn_close(n) after it (in a ``finally``), rows in
between stamped ``turn == n``; a genuine crash leaves exactly one open turn.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import textwrap
from itertools import pairwise
from pathlib import Path

import pytest

from ratatosk import crown
from ratatosk import session as _session
from ratatosk.crown import RuntimeState, _run_turn_bounded
from ratatosk.hooks import HookRuntime
from ratatosk.policy import PolicyStore
from ratatosk.providers import Completion

REPO = Path(__file__).resolve().parent.parent


class _Receipt:
    rung = "stub"

    def line(self):
        return "[stub receipt]"


class _Inference:
    def __init__(self, action):
        self.action = action

    def complete(self, system, history, tools, **forced):
        return self.action(), _Receipt()


def _ok():
    return Completion(
        blocks=[{"type": "text", "text": "done"}],
        text="done",
        tokens_in=1,
        tokens_out=1,
        latency_ms=1,
        raw_model="m",
    )


def _state(tmp_path, monkeypatch, action) -> RuntimeState:
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    monkeypatch.setenv("RATATOSK_SESSION_DIR", str(tmp_path / "sessions"))
    return RuntimeState(
        args=argparse.Namespace(trust=True),
        model="stub",
        writer=_session.SessionWriter(cwd=str(tmp_path), who="hanuman"),
        history=[],
        system_prompt="t",
        all_tools=[],
        mcp_names=set(),
        mcp_call=None,
        client=None,
        policy=PolicyStore(),
        hooks=HookRuntime(),
        inference=_Inference(action),
    )


def _frames(writer):
    return [
        (row["type"], row["n"])
        for row in writer.read_entries()
        if row["type"] in ("turn_open", "turn_close")
    ]


def test_each_turn_is_opened_then_closed_with_its_stamp(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch, _ok)
    _run_turn_bounded(state, "one")
    _run_turn_bounded(state, "two")
    w = state.writer
    assert _frames(w) == [
        ("turn_open", 1),
        ("turn_close", 1),
        ("turn_open", 2),
        ("turn_close", 2),
    ]
    assert w.last_unclosed_turn() is None
    rows = w.read_entries()
    work = [r for r in rows if r["type"] in ("user", "assistant")]
    assert [(r["type"], r["turn"]) for r in work] == [
        ("user", 1),
        ("assistant", 1),
        ("user", 2),
        ("assistant", 2),
    ]
    # No change to the chain: each row's parent is the row before it.
    assert rows[0]["parentUuid"] is None
    for prev, row in pairwise(rows):
        assert row["parentUuid"] == prev["uuid"]
    # Closed means unstamped afterwards.
    assert w._turn is None


@pytest.mark.parametrize("exc", [RuntimeError("boom"), KeyboardInterrupt()])
def test_error_and_interrupt_paths_still_close_the_turn(tmp_path, monkeypatch, exc):
    def boom():
        raise exc

    state = _state(tmp_path, monkeypatch, boom)
    with pytest.raises(type(exc)):
        _run_turn_bounded(state, "go")
    assert _frames(state.writer) == [("turn_open", 1), ("turn_close", 1)]
    assert state.writer.last_unclosed_turn() is None


def test_a_failed_close_write_does_not_replace_the_turn_error(tmp_path, monkeypatch):
    def boom():
        raise RuntimeError("the turn's own error")

    state = _state(tmp_path, monkeypatch, boom)

    def bad_close(n):
        raise OSError("disk")

    state.writer.write_turn_close = bad_close
    with pytest.raises(RuntimeError, match="the turn's own error"):
        _run_turn_bounded(state, "go")


def test_a_hard_crash_leaves_exactly_one_open_turn(tmp_path):
    """os._exit runs no ``finally``: the process-death case. The transcript
    must end with one turn_open and no close."""
    sessions = tmp_path / "sessions"
    script = textwrap.dedent(
        f"""
        import argparse, os
        from ratatosk import session as S
        from ratatosk.crown import RuntimeState, _run_turn_bounded
        from ratatosk.hooks import HookRuntime
        from ratatosk.policy import PolicyStore

        class R:
            rung = "stub"
            def line(self): return ""

        class I:
            def complete(self, *a, **k):
                os._exit(7)

        w = S.SessionWriter(cwd={str(tmp_path)!r}, who="hanuman")
        print(w.path)
        state = RuntimeState(
            args=argparse.Namespace(trust=True), model="s", writer=w, history=[],
            system_prompt="t", all_tools=[], mcp_names=set(), mcp_call=None,
            client=None, policy=PolicyStore(), hooks=HookRuntime(), inference=I(),
        )
        _run_turn_bounded(state, "go")
        """
    )
    env = {
        **os.environ,
        "WILLOW_HOME": str(tmp_path),
        "RATATOSK_SESSION_DIR": str(sessions),
        "PYTHONPATH": str(REPO),
    }
    proc = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 7, proc.stderr
    path = Path(proc.stdout.strip().splitlines()[0])
    rows = [json.loads(line) for line in path.read_text().splitlines() if line]
    opens = [r for r in rows if r["type"] == "turn_open"]
    closes = [r for r in rows if r["type"] == "turn_close"]
    assert len(opens) == 1 and not closes
    assert opens[0]["n"] == 1
    # The reader can find it: exactly one open turn.
    reader = _session.SessionWriter.__new__(_session.SessionWriter)
    reader.path = path
    assert reader.last_unclosed_turn() == 1


def test_run_turn_still_frames(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch, _ok)
    crown._run_turn(state, "hi")
    assert _frames(state.writer) == [("turn_open", 1), ("turn_close", 1)]
