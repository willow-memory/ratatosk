"""A woken seat's broker calls go out as the seat (2026-09-29, C5F04583).

The brief said "close with handoff_write_v4 to willow"; the model sent
``app_id=willow`` and the broker refused it as ``orchestrator_human_required``,
so the verdict had nowhere to land.
"""

from __future__ import annotations

from types import SimpleNamespace

from ratatosk import crown


class _Writer:
    def __init__(self):
        self.notes: list[str] = []

    def write_system(self, text: str) -> None:
        self.notes.append(text)


def _schema(name, *props):
    return {
        "name": name,
        "input_schema": {"type": "object", "properties": {p: {} for p in props}},
    }


def _state(mcp_names=("handoff_write_v4", "task_submit", "whoami", "echo")):
    return SimpleNamespace(
        seat=SimpleNamespace(app_id="loki"),
        mcp_names=set(mcp_names),
        writer=_Writer(),
        all_tools=[
            _schema("handoff_write_v4", "app_id", "dispatch_id"),
            _schema("task_submit", "app_id", "task"),
            _schema("whoami", "app_id"),
            _schema("echo", "word"),
        ],
    )


def _tu(name, inputs):
    return {"id": "t1", "name": name, "input": inputs}


def test_a_wrong_app_id_on_a_broker_call_is_replaced_with_the_seats():
    state = _state()
    tu = _tu("handoff_write_v4", {"app_id": "willow", "dispatch_id": "D1"})
    out = crown._pin_seat_app_id(state, tu)
    assert out["input"] == {"app_id": "loki", "dispatch_id": "D1"}
    assert tu["input"]["app_id"] == "willow", "the model's own block is not mutated"
    assert state.writer.notes and "app_id='willow'" in state.writer.notes[0]


def test_the_seats_own_app_id_is_left_alone():
    state = _state()
    same = _tu("task_submit", {"app_id": "loki", "task": "ls"})
    assert crown._pin_seat_app_id(state, same) is same
    assert state.writer.notes == []


def test_a_missing_app_id_is_filled_when_the_schema_takes_one():
    """E693382F: whoami with no app_id answered no_app_id."""
    state = _state()
    out = crown._pin_seat_app_id(state, _tu("whoami", {}))
    assert out["input"] == {"app_id": "loki"}
    assert "named no app_id" in state.writer.notes[0]


def test_a_missing_app_id_is_not_added_to_a_tool_without_one():
    state = _state()
    tu = _tu("echo", {"word": "x"})
    assert crown._pin_seat_app_id(state, tu) is tu
    assert state.writer.notes == []


def test_a_local_tool_is_never_touched():
    state = _state()
    local = _tu("Read", {"app_id": "willow", "file_path": "/x"})
    assert crown._pin_seat_app_id(state, local) is local


def test_the_wake_tells_the_seat_who_it_is_and_what_it_closes():
    """E693382F spent two calls on whoami and a guessed app_id."""
    entry = SimpleNamespace(
        app_id="loki", dispatch_id="E693382F", closeout_tool="handoff_write_v4"
    )
    line = crown._wake_identity_line(entry)
    assert "app_id=loki" in line
    assert "dispatch_id=E693382F" in line
    assert "handoff_write_v4(" in line
    assert line.endswith("\n\n")


def test_run_wake_sends_the_identity_line_ahead_of_the_brief(tmp_path, monkeypatch):
    from ratatosk.providers import Completion

    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    monkeypatch.setenv("RATATOSK_SESSION_DIR", str(tmp_path / "sessions"))
    monkeypatch.delenv("RATATOSK_GROVE_CHANNEL", raising=False)
    monkeypatch.delenv("WILLOW_HUMAN_ORCHESTRATOR", raising=False)
    seen = []

    def complete(system, history, tools):
        seen.append(history[0]["content"])
        done = Completion(
            blocks=[{"type": "text", "text": "ok"}],
            text="ok",
            tokens_in=1,
            tokens_out=1,
            latency_ms=1,
        )
        receipt = SimpleNamespace(rung="r", line=lambda: "[ratatosk] turn ok")
        receipt.provider, receipt.model, receipt.outcome = "f", "m", "ok"
        receipt.tokens_in = receipt.tokens_out = 1
        return done, receipt

    answers = {
        "session_enter": {
            "entry_mode": "dispatch",
            "app_id": "loki",
            "session_id": "s-1",
            "dispatch_id": "PKT00001",
            "persona": "p",
            "assignment": "# Audit the thing",
            "closeout_tools": ["handoff_write_v4"],
            "blockers": {"count": 0, "items": []},
            "role": "auditor",
            "persona_file": "/fleet/personas/loki.md",
        },
        "dispatch_read": {"meta": {"role": "auditor", "runner": "ratatosk"}},
        "handoff_write_v4": {"dispatch_id": "PKT00001", "status": "complete"},
    }
    crown.run_wake(
        lambda name, inputs: answers.get(name, {"ok": True}),
        app_id="loki",
        dispatch_id="PKT00001",
        trace_id="tr-id",
        inference=SimpleNamespace(
            current=None,
            resolution=SimpleNamespace(usable=[SimpleNamespace(model="m")]),
            complete=complete,
        ),
    )
    assert seen and seen[0].startswith("[wake] You are the loki seat")
    assert seen[0].endswith("# Audit the thing")


def test_no_seat_means_no_pin():
    state = _state()
    state.seat = None
    tu = _tu("handoff_write_v4", {"app_id": "willow"})
    assert crown._pin_seat_app_id(state, tu) is tu
