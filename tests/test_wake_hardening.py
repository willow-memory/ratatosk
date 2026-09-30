"""What the first live audit wakes on the free ladder broke (2026-09-29).

39397B98 and B5B2D017 were Loki's first two audits after the ladder moved to
free rungs. Each one ended in a way the wake path handled worse than it
should have: a transient refusal spent the packet, the seat's own answer had
no way into a dispatch closeout, a Kart read needed a second model call to
collect, and oversized tool results rode along on every later call. One test
per fix, each against the shape that was measured.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from ratatosk import crown
from ratatosk import seat as _seat
from ratatosk import wake_policy as _wake_policy
from ratatosk.inference import LadderRefused
from ratatosk.providers import Completion

PERSONA = "You are a test seat."

_BROKER_HANDOFF_ANSWER = {
    "dispatch_id": "PKT00001",
    "status": "complete",
    "reply_to": "willow",
    "waiting_for": "verify_handoff",
}


def _entered(**overrides) -> dict:
    result = {
        "entry_mode": "dispatch",
        "app_id": "loki",
        "session_id": "s-1",
        "dispatch_id": "PKT00001",
        "persona": PERSONA,
        "job": "Audit",
        "not_job": "Build",
        "assignment": "# Audit the thing\n\nOne bite.",
        "closeout_tools": ["handoff_write_v4"],
        "blockers": {"count": 0, "items": []},
        "role": "auditor",
        "persona_file": "/fleet/personas/loki.md",
    }
    result.update(overrides)
    return result


class _FakeMCP:
    def __init__(self, answers: dict | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.answers = answers or {}

    def __call__(self, name: str, inputs: dict):
        self.calls.append((name, inputs))
        answer = self.answers.get(name, {"ok": True})
        if callable(answer):
            return answer(inputs)
        return answer

    def named(self, name: str) -> list[dict]:
        return [inputs for n, inputs in self.calls if n == name]


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    monkeypatch.setenv("RATATOSK_SESSION_DIR", str(tmp_path / "sessions"))
    monkeypatch.delenv("RATATOSK_GROVE_CHANNEL", raising=False)
    monkeypatch.delenv("WILLOW_HUMAN_ORCHESTRATOR", raising=False)


def _fake_inference(complete):
    return SimpleNamespace(
        current=None,
        resolution=SimpleNamespace(usable=[SimpleNamespace(model="fake-model")]),
        complete=complete,
    )


def _receipt(outcome: str = "ok") -> SimpleNamespace:
    r = SimpleNamespace(rung="rung1", line=lambda: f"[ratatosk] turn {outcome}")
    r.provider, r.model, r.tokens_in, r.tokens_out, r.outcome = "f", "m", 1, 1, outcome
    return r


def _wake(mcp, inference, **kw):
    return crown.run_wake(
        mcp,
        app_id="loki",
        dispatch_id="PKT00001",
        trace_id="tr-h",
        inference=inference,
        **kw,
    )


def _mcp():
    return _FakeMCP(
        {
            "session_enter": _entered(),
            "dispatch_read": {"meta": {"role": "auditor", "runner": "ratatosk"}},
            "handoff_write_v4": _BROKER_HANDOFF_ANSWER,
        }
    )


# -- 1: a wake with no answer to stand on leaves the packet working ----------


def test_a_ladder_refusal_leaves_the_packet_working_not_complete(tmp_path, monkeypatch):
    """39397B98 and B5B2D017 both closed `complete` on a ladder refusal: one
    transient provider answer spent the packet with no audit in it."""
    _isolate(monkeypatch, tmp_path)

    def refuse(*_a, **_k):
        raise LadderRefused("every rung refused", _receipt("refused"))

    mcp = _mcp()
    line = _wake(mcp, _fake_inference(refuse))
    assert "outcome=ladder_refused" in line
    assert not mcp.named("handoff_write_v4"), "a refusal is not a completion"
    assert "handoff=left_working" in line


def test_an_empty_answer_leaves_the_packet_working(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    blank = Completion(blocks=[], text="", tokens_in=1, tokens_out=0, latency_ms=1)
    mcp = _mcp()
    line = _wake(mcp, _fake_inference(lambda *a, **k: (blank, _receipt())))
    assert "outcome=empty_answer" in line
    assert not mcp.named("handoff_write_v4")
    assert "handoff=left_working" in line


def test_a_final_answer_still_closes(tmp_path, monkeypatch):
    """The fix above must not keep a finished wake open."""
    _isolate(monkeypatch, tmp_path)
    done = Completion(
        blocks=[{"type": "text", "text": "verdict"}],
        text="verdict",
        tokens_in=1,
        tokens_out=1,
        latency_ms=1,
    )
    mcp = _mcp()
    line = _wake(mcp, _fake_inference(lambda *a, **k: (done, _receipt())))
    assert "outcome=ok" in line
    assert len(mcp.named("handoff_write_v4")) == 1


# -- 2: the seat's own final answer reaches the dispatch closeout ------------


def test_the_seats_final_answer_is_carried_into_the_dispatch_handoff(
    tmp_path, monkeypatch
):
    _isolate(monkeypatch, tmp_path)
    verdict = "F1 (high): the pin reads the wrong project at handoff.py:88."
    done = Completion(
        blocks=[{"type": "text", "text": verdict}],
        text=verdict,
        tokens_in=1,
        tokens_out=1,
        latency_ms=1,
    )
    mcp = _mcp()
    _wake(mcp, _fake_inference(lambda *a, **k: (done, _receipt())))
    (close,) = mcp.named("handoff_write_v4")
    final = [f for f in close["findings"] if f["title"] == "the seat's final answer"]
    assert final and final[0]["evidence"] == [verdict]


def test_the_final_answer_is_capped():
    entry = _seat.SeatEntry(
        app_id="loki",
        session_id="s",
        dispatch_id="D1",
        entry_mode="dispatch",
        persona="",
        job="",
        not_job="",
        assignment="",
        closeout_tool="handoff_write_v4",
        blockers=[],
        raw={},
    )
    summary = {
        "turns": 1,
        "tools": {},
        "calls_ok": 1,
        "calls_refused": 0,
        "rungs": {},
        "tokens_in": 0,
        "tokens_out": 0,
        "next_bite": "x" * (_seat.FINAL_ANSWER_CAP + 50),
        "notices": [],
    }
    (final,) = [
        f
        for f in _seat.build_findings(summary, entry, "")
        if f["title"] == "the seat's final answer"
    ]
    assert final["evidence"][0].endswith("[… cut at the closeout's cap]")
    assert len(final["evidence"][0]) < _seat.FINAL_ANSWER_CAP + 100


# -- 3: a seat that closed itself is not closed a second time ----------------


def test_a_seat_that_called_its_own_closeout_is_not_closed_again(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(crown._tools, "dispatch", lambda *a, **kw: '{"status": "ok"}')
    closing = Completion(
        blocks=[
            {
                "type": "tool_use",
                "id": "t1",
                "name": "handoff_write_v4",
                "input": {"dispatch_id": "PKT00001", "findings": []},
            }
        ],
        text="",
        tokens_in=1,
        tokens_out=1,
        latency_ms=1,
    )
    done = Completion(
        blocks=[{"type": "text", "text": "closed"}],
        text="closed",
        tokens_in=1,
        tokens_out=1,
        latency_ms=1,
    )
    answers = iter([(closing, _receipt()), (done, _receipt())])
    mcp = _mcp()
    line = _wake(mcp, _fake_inference(lambda *a, **k: next(answers)))
    assert not mcp.named("handoff_write_v4"), (
        "crown must not write a second closeout over the seat's own"
    )
    assert "handoff=seat_called_closeout" in line


# -- 4: a wake waits for its own Kart task -----------------------------------


def _kart_state(mcp_names=("task_status",)):
    return SimpleNamespace(
        seat=SimpleNamespace(app_id="loki"),
        mcp_names=set(mcp_names),
        mcp_call=None,
    )


def test_a_submitted_kart_task_is_awaited_and_its_output_returned(monkeypatch):
    monkeypatch.setattr(crown, "KART_POLL_SECONDS", 0)
    polls = iter(
        [
            json.dumps({"task_id": "K1", "status": "running"}),
            json.dumps(
                {"task_id": "K1", "status": "completed", "result": "diff --git"}
            ),
        ]
    )
    seen = []

    def fake_dispatch(name, inputs, *a, **kw):
        seen.append((name, inputs))
        return next(polls)

    monkeypatch.setattr(crown._tools, "dispatch", fake_dispatch)
    submitted = json.dumps({"task_id": "K1", "status": "pending"})
    out = crown._await_kart(_kart_state(), submitted, deadline=None)
    assert "diff --git" in out and out.startswith(submitted)
    assert seen[0] == ("task_status", {"app_id": "loki", "task_id": "K1"})


def test_a_held_or_foreign_answer_is_returned_untouched(monkeypatch):
    def never(*a, **kw):
        raise AssertionError("no poll for an answer that is not an open task")

    monkeypatch.setattr(crown._tools, "dispatch", never)
    for answer in (
        json.dumps({"task_id": "K1", "status": "held_net_authorization"}),
        json.dumps({"error": "EPERM"}),
        "not json",
    ):
        assert crown._await_kart(_kart_state(), answer, deadline=None) == answer
    pending = json.dumps({"task_id": "K1", "status": "pending"})
    assert crown._await_kart(_kart_state(mcp_names=()), pending, None) == pending


def test_the_wait_stops_at_the_deadline_with_the_pending_answer(monkeypatch):
    monkeypatch.setattr(crown, "KART_POLL_SECONDS", 0)
    monkeypatch.setattr(
        crown._tools,
        "dispatch",
        lambda *a, **kw: json.dumps({"task_id": "K1", "status": "running"}),
    )
    pending = json.dumps({"task_id": "K1", "status": "pending"})
    assert crown._await_kart(_kart_state(), pending, deadline=0) == pending


# -- 5: tool results are capped in a wake ------------------------------------


def test_a_long_tool_result_is_cut_with_a_note():
    long = "y" * (crown.WAKE_RESULT_CAP + 1000)
    out = crown._cap_wake_result(long)
    assert out.startswith("y" * crown.WAKE_RESULT_CAP)
    assert "1000 more chars cut" in out
    assert crown._cap_wake_result("short") == "short"


# -- 6: a wake does not send the schema of a tool it would refuse ------------


def _names(tools):
    return {t["name"] for t in tools}


def test_an_audit_wake_sends_no_bash_write_or_edit():
    tools = [{"name": n} for n in ("Read", "Bash", "Write", "Edit", "task_submit")]
    table = _wake_policy.load()
    audit = _wake_policy.resolve_for_role(table, "auditor")
    assert _names(crown._wake_tools(tools, audit)) == {"Read", "task_submit"}


def test_a_build_wake_keeps_its_scoped_write_tools_but_never_bash():
    tools = [{"name": n} for n in ("Read", "Bash", "Write", "Edit", "task_submit")]
    table = _wake_policy.load()
    build = _wake_policy.resolve_for_role(table, "build-work-order")
    assert _names(crown._wake_tools(tools, build)) == {
        "Read",
        "Write",
        "Edit",
        "task_submit",
    }
