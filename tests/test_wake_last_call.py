"""A wake's last model call is kept for the verdict (2026-09-30, D9148C42).

The seat spent all eight calls reading — the stat, four files, three more
reads — and the loop ended ``budget_turns`` with nothing written. The last
call under the cap now offers the closeout tool alone, and a closeout the
broker accepts ends the wake there.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from ratatosk import crown
from ratatosk.providers import Completion

HANDOFF = "handoff_write_v4"


def _schema(name, *props):
    return {
        "name": name,
        "input_schema": {"type": "object", "properties": {p: {} for p in props}},
    }


MCP_TOOLS = [
    _schema(HANDOFF, "app_id", "dispatch_id", "findings"),
    _schema("task_submit", "app_id", "task"),
]


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    monkeypatch.setenv("RATATOSK_SESSION_DIR", str(tmp_path / "sessions"))
    monkeypatch.delenv("RATATOSK_GROVE_CHANNEL", raising=False)
    monkeypatch.delenv("WILLOW_HUMAN_ORCHESTRATOR", raising=False)


def _receipt():
    r = SimpleNamespace(rung="r", line=lambda: "[ratatosk] turn ok")
    r.provider, r.model, r.outcome, r.tokens_in, r.tokens_out = "f", "m", "ok", 1, 1
    return r


def _tool_call(name, inputs, n):
    return Completion(
        blocks=[{"type": "tool_use", "id": f"t{n}", "name": name, "input": inputs}],
        text="",
        tokens_in=1,
        tokens_out=1,
        latency_ms=1,
    )


class _Reader:
    """A model that reads for as long as it is offered anything to read with —
    the D9148C42 shape — and closes only when the closeout is all it has."""

    def __init__(self):
        self.calls: list[tuple[str, list[str]]] = []

    def complete(self, system, history, tools):
        names = [t["name"] for t in tools]
        self.calls.append((system, names))
        n = len(self.calls)
        if "task_submit" in names:
            return _tool_call("task_submit", {"task": f"git show {n}"}, n), _receipt()
        return _tool_call(HANDOFF, {"dispatch_id": "PKT00001", "findings": []}, n), (
            _receipt()
        )


def _wake(monkeypatch, tmp_path, model, handoff_answer, max_turns=3, extra_tools=None):
    _isolate(monkeypatch, tmp_path)
    dispatched: list[str] = []

    def fake_dispatch(name, inputs, *a, **kw):
        dispatched.append(name)
        if name == HANDOFF:
            return json.dumps(handoff_answer)
        return json.dumps({"ok": True})

    monkeypatch.setattr(crown._tools, "dispatch", fake_dispatch)
    answers = {
        "session_enter": {
            "entry_mode": "dispatch",
            "app_id": "loki",
            "session_id": "s-1",
            "dispatch_id": "PKT00001",
            "persona": "p",
            "assignment": "# Audit the thing",
            "closeout_tools": [HANDOFF],
            "blockers": {"count": 0, "items": []},
            "role": "auditor",
            "persona_file": "/fleet/personas/loki.md",
        },
        "dispatch_read": {"meta": {"role": "auditor", "runner": "ratatosk"}},
    }
    crown_closeouts: list[dict] = []

    def mcp(name, inputs):
        if name == HANDOFF:
            crown_closeouts.append(inputs)
            return {"dispatch_id": "PKT00001", "status": "complete"}
        return answers.get(name, {"ok": True})

    line = crown.run_wake(
        mcp,
        app_id="loki",
        dispatch_id="PKT00001",
        trace_id="tr-last",
        inference=SimpleNamespace(
            current=None,
            resolution=SimpleNamespace(usable=[SimpleNamespace(model="m")]),
            complete=model.complete,
        ),
        mcp_extra_tools=MCP_TOOLS if extra_tools is None else extra_tools,
        mcp_names={HANDOFF, "task_submit"},
        max_turns=max_turns,
    )
    return line, dispatched, crown_closeouts


def test_the_last_call_offers_only_the_closeout_and_says_so(tmp_path, monkeypatch):
    model = _Reader()
    line, dispatched, crown_closeouts = _wake(
        monkeypatch, tmp_path, model, {"dispatch_id": "PKT00001", "status": "complete"}
    )
    assert len(model.calls) == 3
    for system, names in model.calls[:2]:
        assert "task_submit" in names
        assert "last model call" not in system
    last_system, last_names = model.calls[2]
    assert last_names == [HANDOFF]
    assert "This is your last model call" in last_system
    assert dispatched == ["task_submit", "task_submit", HANDOFF]
    assert "outcome=ok" in line, line
    assert "handoff=seat_called_closeout" in line
    assert crown_closeouts == [], "crown does not write over the seat's own closeout"


def test_a_closeout_the_broker_accepts_ends_the_wake_early(tmp_path, monkeypatch):
    """A seat that closes on its first call is not called again."""
    calls = []

    def closes_at_once(system, history, tools):
        calls.append(1)
        return _tool_call(HANDOFF, {"dispatch_id": "PKT00001"}, len(calls)), _receipt()

    line, _, _ = _wake(
        monkeypatch,
        tmp_path,
        SimpleNamespace(complete=closes_at_once),
        {"dispatch_id": "PKT00001", "status": "complete"},
        max_turns=8,
    )
    assert calls == [1]
    assert "outcome=ok" in line and "handoff=seat_called_closeout" in line


def test_a_refused_closeout_does_not_end_the_wake(tmp_path, monkeypatch):
    """19841D91: the broker refused the handoff EINVAL; the seat must still be
    able to correct it, so the loop runs on — and a refused last call leaves
    the packet working rather than claiming a close."""
    model = _Reader()
    line, dispatched, crown_closeouts = _wake(
        monkeypatch, tmp_path, model, {"error": "EINVAL", "message": "no evidence"}
    )
    assert dispatched[-1] == HANDOFF
    assert "outcome=budget_turns" in line, line
    assert "handoff=left_working" in line
    assert crown_closeouts == []


def test_no_closeout_tool_in_the_definitions_means_no_restriction(
    tmp_path, monkeypatch
):
    """A tool the model was never sent cannot be the only one offered: the
    last call goes out as before rather than with an empty tool list."""
    model = _Reader()
    line, _, _ = _wake(
        monkeypatch,
        tmp_path,
        model,
        {"dispatch_id": "PKT00001", "status": "complete"},
        extra_tools=[_schema("task_submit", "app_id", "task")],
    )
    last_system, last_names = model.calls[-1]
    assert "task_submit" in last_names
    assert "last model call" not in last_system
    assert "outcome=budget_turns" in line
