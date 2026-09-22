"""SeatDaemon — the heartbeating, stoppable wrapper around BusListener that
makes the fleet wake its own seats instead of the orchestrator hand-spawning
workers.

Grove protocol behavior (channel refusal, validation, gating) is BusListener's
job and is covered in test_listener.py; these tests cover only what SeatDaemon
adds: heartbeat scheduling, wake activation wiring, and a stop signal that
run_forever actually honors.
"""

import json
import time
from types import SimpleNamespace

import pytest

from ratatosk import crown
from ratatosk.daemon import DEFAULT_HEARTBEAT_INTERVAL, SeatDaemon, default_activate
from ratatosk.providers import Completion


def test_daemon_binds_the_configured_channel():
    daemon = SeatDaemon(node="ratatosk", channel="fleet", mcp_call=None)
    assert daemon.channel == "fleet"
    assert daemon.node == "ratatosk"


def test_daemon_refuses_an_unset_channel(monkeypatch):
    """Builds on BusListener's PR-22 refusal (commit 70a994d) rather than
    reimplementing it — SeatDaemon must inherit the same guard, not silently
    default to some channel."""
    monkeypatch.delenv("RATATOSK_GROVE_CHANNEL", raising=False)
    with pytest.raises(ValueError, match="grove channel unset"):
        SeatDaemon(mcp_call=None)


def test_daemon_emits_a_heartbeat():
    calls = []

    def mcp_call(tool, inputs):
        calls.append((tool, inputs))
        return "{}"

    daemon = SeatDaemon(node="ratatosk", channel="fleet", mcp_call=mcp_call)
    assert daemon.emit_heartbeat() is True
    assert calls == [
        (
            "grove_heartbeat",
            {"app_id": "ratatosk", "agent": "ratatosk", "channel": "fleet"},
        )
    ]


def test_run_forever_emits_a_heartbeat_on_start_then_stops_cleanly():
    """Liveness is observable the moment the daemon comes up, not only after
    the first heartbeat interval elapses — and a pre-armed stop is honored
    before any polling happens (clean shutdown)."""
    calls = []

    def mcp_call(tool, inputs):
        calls.append(tool)
        return "{}"

    daemon = SeatDaemon(node="ratatosk", channel="fleet", mcp_call=mcp_call)
    daemon.request_stop()  # armed before the loop starts

    statuses = []
    daemon.run_forever(on_status=statuses.append)

    assert "grove_heartbeat" in calls
    assert "grove_get_history" not in calls, (
        "stop must be honored before polling begins"
    )
    assert statuses[0] == "listening on fleet as ratatosk"
    assert statuses[-1] == "stopped"


def test_restart_is_idempotent():
    """Stop, then run again on the same instance — no accumulated state, no
    crash, no double-registration of anything."""
    calls = []

    def mcp_call(tool, inputs):
        calls.append(tool)
        return "{}"

    daemon = SeatDaemon(node="ratatosk", channel="fleet", mcp_call=mcp_call)

    daemon.request_stop()
    daemon.run_forever()
    first_heartbeats = calls.count("grove_heartbeat")
    assert first_heartbeats == 1

    daemon.request_stop()
    daemon.run_forever()
    assert calls.count("grove_heartbeat") == first_heartbeats + 1


def test_wake_message_activates_the_seat_runtime():
    """End to end through run_once: a wake on the bus reaches the mocked seat
    activation, not a canned acknowledgement."""
    activated = []

    def activate(env):
        activated.append(env.trace_id)
        return f"worked {env.trace_id}"

    history = [
        {
            "id": 1,
            "sender": "willow",
            "content": '{"v":1,"to":"ratatosk","intent":"wake","prompt":"packet-dispatch",'
            '"reply_channel":"fleet","mode":"ollama","capabilities":[],'
            '"nonce":"wk1","trace_id":"tr-daemon-wake","expires_at":"2099-01-01T00:00:00Z",'
            '"requires_confirm":false}',
        }
    ]

    def mcp_call(tool, inputs):
        if tool == "grove_get_history":
            return {"result": list(history)}
        return {}

    daemon = SeatDaemon(
        node="ratatosk", channel="fleet", mcp_call=mcp_call, activate=activate
    )
    outputs = daemon.listener.run_once()

    assert activated == ["tr-daemon-wake"]
    assert outputs == ["worked tr-daemon-wake"]


def test_default_activate_is_honest_about_no_runtime_wired():
    from ratatosk.protocol.envelope import build_envelope

    env = build_envelope(to="ratatosk", prompt="", intent="wake", capabilities=[])
    out = default_activate(env)
    assert "no seat runtime wired" in out


def test_default_heartbeat_interval_is_positive():
    assert DEFAULT_HEARTBEAT_INTERVAL > 0


def _daemon_with_ledger(tmp_path, on_seal=None, mcp_call=None, **kw):
    if mcp_call is None:

        def mcp_call(tool, inputs):
            return "{}"

    return SeatDaemon(
        node="ratatosk",
        channel="fleet",
        mcp_call=mcp_call,
        seal_ledger_path=tmp_path / "ledger.jsonl",
        seal_offset_path=tmp_path / "offset",
        on_seal=on_seal,
        **kw,
    )


def test_no_seal_watcher_is_built_when_no_ledger_configured():
    """Dark-safe default: nothing configured means no watcher object exists,
    not a watcher pointed at an empty path."""

    def mcp_call(tool, inputs):
        return "{}"

    daemon = SeatDaemon(node="ratatosk", channel="fleet", mcp_call=mcp_call)
    assert daemon.seal_watcher is None
    assert daemon.poll_seal_ledger() == 0


def test_poll_seal_ledger_fires_on_seal_callback_once(tmp_path):
    seen = []
    daemon = _daemon_with_ledger(tmp_path, on_seal=seen.append)
    (tmp_path / "ledger.jsonl").write_text(
        json.dumps({"kind": "seal", "nestor_pair_id": "p1"}) + "\n"
    )

    dispatched = daemon.poll_seal_ledger()

    assert dispatched == 1
    assert seen == [{"kind": "seal", "nestor_pair_id": "p1"}]


def test_seal_watcher_restart_does_not_refire(tmp_path):
    """A new SeatDaemon built against the same offset path (the shape of a
    process restart) does not re-emit a seal already dispatched."""
    seen = []
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(json.dumps({"kind": "seal", "nestor_pair_id": "p1"}) + "\n")

    _daemon_with_ledger(tmp_path, on_seal=seen.append).poll_seal_ledger()
    assert seen == [{"kind": "seal", "nestor_pair_id": "p1"}]

    # Fresh daemon instance, same ledger + offset path — the restart.
    dispatched = _daemon_with_ledger(tmp_path, on_seal=seen.append).poll_seal_ledger()
    assert dispatched == 0
    assert seen == [{"kind": "seal", "nestor_pair_id": "p1"}]


def test_default_seal_predicate_matches_the_real_nestor_ledger_shape(tmp_path):
    """The real Nestor ledger keys the record type as ``kind``, not ``op``
    (e.g. ``{"ts","prev","kind":"seal","pair_id","verifier",...}``). The
    default predicate — used whenever no ``seal_predicate`` override is
    given — must fire on that shape without any consumer-side config."""
    seen = []
    daemon = _daemon_with_ledger(tmp_path, on_seal=seen.append)
    record = {
        "ts": "2026-09-11T00:00:00Z",
        "prev": None,
        "kind": "seal",
        "pair_id": "p1",
        "verifier": "sean",
        "source_lang": "decision",
        "target_lang": "en",
        "source_sha": "abc123",
        "origin": "willow-mcp",
        "upgraded_from": None,
    }
    (tmp_path / "ledger.jsonl").write_text(json.dumps(record) + "\n")

    dispatched = daemon.poll_seal_ledger()

    assert dispatched == 1
    assert seen == [record]


def test_default_seal_predicate_does_not_match_legacy_op_only_shape(tmp_path):
    """A record that only has the legacy ``op`` field (no ``kind``) must NOT
    match the default predicate — that field name does not exist on the real
    ledger, and matching it would be the same bug this default replaces."""
    seen = []
    daemon = _daemon_with_ledger(tmp_path, on_seal=seen.append)
    (tmp_path / "ledger.jsonl").write_text(
        json.dumps({"op": "seal", "nestor_pair_id": "p1"}) + "\n"
    )

    dispatched = daemon.poll_seal_ledger()

    assert dispatched == 0
    assert seen == []


def test_caller_supplied_seal_predicate_overrides_the_default(tmp_path):
    """A consumer that wants domain narrowing (e.g. only decision-lane seals)
    supplies its own predicate; SeatDaemon must honor it instead of the
    ``kind == "seal"`` default."""
    seen = []
    daemon = _daemon_with_ledger(
        tmp_path,
        on_seal=seen.append,
        seal_predicate=lambda record: (
            record.get("kind") == "seal" and record.get("source_lang") == "decision"
        ),
    )
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(
        json.dumps({"kind": "seal", "source_lang": "other", "id": 1})
        + "\n"
        + json.dumps({"kind": "seal", "source_lang": "decision", "id": 2})
        + "\n"
    )

    dispatched = daemon.poll_seal_ledger()

    assert dispatched == 1
    assert seen == [{"kind": "seal", "source_lang": "decision", "id": 2}]


def test_a_watcher_ioerror_does_not_stop_the_heartbeat_in_run_forever(
    tmp_path, monkeypatch
):
    """One watcher raising must not kill the heartbeat: force the seal poll
    to explode and confirm run_forever still emits its heartbeat and stops
    cleanly rather than propagating the exception."""
    calls = []

    def mcp_call(tool, inputs):
        calls.append(tool)
        return "{}"

    daemon = _daemon_with_ledger(tmp_path, mcp_call=mcp_call)
    daemon.heartbeat_interval = 0  # heartbeat due on every tick

    def _boom():
        raise OSError("ledger unreadable")

    monkeypatch.setattr(daemon.seal_watcher, "poll", _boom)
    daemon.request_stop()

    statuses = []
    daemon.run_forever(on_status=statuses.append)

    assert "grove_heartbeat" in calls
    assert statuses[-1] == "stopped"
    assert any("seal watch error" in s for s in statuses)


def test_raising_on_seal_does_not_kill_the_heartbeat_and_retries_only_the_bad_record(
    tmp_path,
):
    """A permanently-failing consumer must not take the heartbeat down with
    it, and each retry must surface the failure rather than silently wedging
    or re-firing whatever ran before it in run_forever's own loop."""
    calls = []

    def mcp_call(tool, inputs):
        calls.append(tool)
        return "{}"

    invocations = []

    def on_seal(record):
        invocations.append(record)
        raise ValueError("permanently broken consumer")

    daemon = _daemon_with_ledger(tmp_path, on_seal=on_seal, mcp_call=mcp_call)
    daemon.heartbeat_interval = 0  # heartbeat due on every tick
    (tmp_path / "ledger.jsonl").write_text(json.dumps({"kind": "seal", "id": 1}) + "\n")
    daemon.request_stop()

    statuses = []
    daemon.run_forever(on_status=statuses.append)

    assert "grove_heartbeat" in calls
    assert statuses[-1] == "stopped"
    assert invocations == [{"kind": "seal", "id": 1}]
    assert any("seal watch error" in s for s in statuses)

    # A second, independent poll call still fails on the same record — the
    # failure surfaces every time, not just once, and it's the same record
    # retried, not something new.
    assert daemon.poll_seal_ledger(statuses.append) == 0
    assert invocations == [{"kind": "seal", "id": 1}, {"kind": "seal", "id": 1}]
    assert any("seal watch error" in s for s in statuses[-2:])


def test_seal_ledger_is_polled_on_poll_interval_not_heartbeat_interval(tmp_path):
    """The seal watch must not wait for the (much slower) heartbeat clock —
    across several loop ticks with a long heartbeat interval, the ledger
    should still be polled every tick."""
    ledger = tmp_path / "ledger.jsonl"
    offset = tmp_path / "offset"

    def mcp_call(tool, inputs):
        return "{}"

    daemon = SeatDaemon(
        node="ratatosk",
        channel="fleet",
        mcp_call=mcp_call,
        poll_interval=0.01,
        heartbeat_interval=3600,  # would never come due again in this test
        seal_ledger_path=ledger,
        seal_offset_path=offset,
    )

    poll_calls = []
    real_poll = daemon.poll_seal_ledger

    def counting_poll(on_status=None):
        poll_calls.append(1)
        return real_poll(on_status)

    daemon.poll_seal_ledger = counting_poll

    # Stop the loop after a handful of ticks by flipping the stop event from
    # inside run_once, which run_forever calls once per tick.
    ticks = {"n": 0}
    real_run_once = daemon.listener.run_once

    def run_once_then_stop_after(n=4):
        ticks["n"] += 1
        if ticks["n"] >= n:
            daemon.request_stop()
        return real_run_once()

    daemon.listener.run_once = run_once_then_stop_after

    daemon.run_forever()

    # One poll before the loop starts, plus one per tick — none of them
    # gated on the heartbeat, which is due only once (interval=3600) at the
    # very first call before the loop.
    assert len(poll_calls) >= ticks["n"] + 1


def test_heartbeat_still_fires_when_the_ledger_is_quiet(tmp_path):
    """No seals appended at all — the heartbeat must not depend on the
    ledger having anything to report."""
    calls = []

    def mcp_call(tool, inputs):
        calls.append(tool)
        return "{}"

    daemon = SeatDaemon(
        node="ratatosk",
        channel="fleet",
        mcp_call=mcp_call,
        seal_ledger_path=tmp_path / "ledger.jsonl",  # never created
        seal_offset_path=tmp_path / "offset",
    )
    daemon.request_stop()
    daemon.run_forever()

    assert calls.count("grove_heartbeat") == 1
    assert daemon.poll_seal_ledger() == 0


# -- crown.run_wake: a WAKE becomes a bounded crown run --------------------
#
# Sealed 3613d55e slice 3 (gap 692373e803a1). These drive a fake mcp_call
# (records what was asked, answers from a table — same style as
# test_seat.py's FakeMCP) and a fake inference/ladder (SimpleNamespace or a
# real Ladder with a stub client), never a real provider or a real
# willow-mcp process.
#
# Loki 3564BE3C (rework 45707360, packet 75575CB7's audit) found 8 findings
# against the first cut; each one below is named where it is addressed —
# `tests/test_loki_probe_wake.py`'s probes (which proved the BUGS existed)
# are folded in here under these names, asserting the FIXED behavior instead.

PERSONA = "You are Hanuman — Builder.\n\n*ΔΣ=42*\n"


def _entered(**overrides) -> dict:
    result = {
        "entry_mode": "dispatch",
        "app_id": "hanuman",
        "session_id": "s-1",
        "dispatch_id": "PKT00001",
        "persona": PERSONA,
        "job": "Code, builds, tests, Kart",
        "not_job": "Direct master commits",
        "assignment": "# Build the thing\n\nOne bite.",
        "closeout_tools": ["handoff_write_v4"],
        "blockers": {"count": 0, "items": []},
        # sealed 3566adb5: session_enter's own role, resolved into a wake
        # policy at entry. "build-work-order" matches hanuman's own seat —
        # existing tests that do not care about the wake policy still get
        # one that resolves (Write/Edit allowed, scoped to the worktree).
        "role": "build-work-order",
    }
    result.update(overrides)
    return result


#: The broker's real `handoff_write_v4` answer (willow-mcp handoff.py) — no
#: id/path/continuity_key field at all. Loki F1: a prior test faked a
#: `handoff_id` key the broker never emits; every wake test below uses this
#: shape instead, so a receipt line built against it proves what actually
#: reaches the broker.
_BROKER_HANDOFF_ANSWER = {
    "dispatch_id": "PKT00001",
    "status": "complete",
    "reply_to": "willow",
    "waiting_for": "verify_handoff",
}


class _FakeMCP:
    """Records every call; answers from a table (dict, exception, or a
    callable taking the inputs) keyed by tool name."""

    def __init__(self, answers: dict | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.answers = answers or {}

    def __call__(self, name: str, inputs: dict):
        self.calls.append((name, inputs))
        answer = self.answers.get(name, {"ok": True})
        if isinstance(answer, Exception):
            raise answer
        if callable(answer):
            return answer(inputs)
        return answer

    def named(self, name: str) -> list[dict]:
        return [inputs for n, inputs in self.calls if n == name]


def _fake_inference(complete):
    """A stand-in for InferenceRouter: only ``.complete`` and ``.resolution``
    (read for the informational ``model`` field) are touched by run_wake."""
    return SimpleNamespace(
        current=None,
        resolution=SimpleNamespace(usable=[SimpleNamespace(model="fake-model")]),
        complete=complete,
    )


def _isolate(monkeypatch, tmp_path):
    """WILLOW_HOME too, not only RATATOSK_SESSION_DIR — Loki F8: three tests
    in the first cut wrote `$WILLOW_HOME/ratatosk/traces.jsonl` against the
    live store because only the session dir was isolated. Every wake test
    below isolates both."""
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    monkeypatch.setenv("RATATOSK_SESSION_DIR", str(tmp_path / "sessions"))
    monkeypatch.delenv("RATATOSK_GROVE_CHANNEL", raising=False)
    monkeypatch.delenv("WILLOW_HUMAN_ORCHESTRATOR", raising=False)


def _done(**overrides) -> Completion:
    kw = {
        "blocks": [{"type": "text", "text": "done"}],
        "text": "done",
        "tokens_in": 1,
        "tokens_out": 1,
        "latency_ms": 1,
    }
    kw.update(overrides)
    return Completion(**kw)


def _receipt_ok() -> SimpleNamespace:
    r = SimpleNamespace(rung="rung1", line=lambda: "[ratatosk] turn ok")
    r.provider, r.model, r.tokens_in, r.tokens_out, r.outcome = "f", "m", 1, 1, "ok"
    return r


def test_run_wake_refuses_with_no_dispatch_id_and_never_enters():
    mcp = _FakeMCP()
    line = crown.run_wake(mcp, app_id="hanuman", dispatch_id=None, trace_id="tr-1")
    assert "refused" in line and "no dispatch_id" in line
    assert mcp.calls == [], "acknowledged by name, never worked"


def test_run_wake_refuses_with_no_app_id():
    mcp = _FakeMCP()
    line = crown.run_wake(mcp, app_id=None, dispatch_id="PKT1", trace_id="tr-2")
    assert "refused" in line and "no app_id" in line
    assert mcp.calls == []


def test_run_wake_refuses_app_id_willow(monkeypatch):
    """seat.py rule 2, enforced again here even if a WAKE's extra somehow
    tried to name the orchestrator seat: never app_id=willow from a daemon."""
    monkeypatch.delenv("WILLOW_HUMAN_ORCHESTRATOR", raising=False)
    mcp = _FakeMCP()
    line = crown.run_wake(mcp, app_id="willow", dispatch_id="PKT1", trace_id="tr-3")
    assert "refused" in line and "human orchestrator" in line
    assert mcp.calls == [], "refused before session_enter"


def test_run_wake_names_the_brokers_entry_refusal():
    mcp = _FakeMCP({"session_enter": {"error": "gate denied: expired lease"}})
    line = crown.run_wake(mcp, app_id="hanuman", dispatch_id="PKT1", trace_id="tr-4")
    assert "entry refused" in line and "gate denied" in line
    assert not mcp.named("handoff_write_v4")


def test_run_wake_happy_path_enters_runs_closes_and_inks_a_receipt_line(
    tmp_path, monkeypatch
):
    _isolate(monkeypatch, tmp_path)
    done = _done(tokens_in=100, tokens_out=20, latency_ms=12, raw_model="fake-model")
    receipt = _receipt_ok()
    receipt.line = lambda: "[ratatosk] turn ok class=build rung=rung1 fake/fake-model"
    inference = _fake_inference(lambda *a, **k: (done, receipt))
    mcp = _FakeMCP(
        {
            "session_enter": _entered(),
            "dispatch_read": {"meta": {"role": "build-work-order"}},
            "handoff_write_v4": _BROKER_HANDOFF_ANSWER,
        }
    )

    line = crown.run_wake(
        mcp,
        app_id="hanuman",
        dispatch_id="PKT00001",
        trace_id="tr-5",
        inference=inference,
    )

    assert line.startswith("[ratatosk] wake receipt")
    assert "dispatch=PKT00001" in line
    assert "app=hanuman" in line
    # F1: names the broker's actual "status" answer, not the literal "ok".
    assert "handoff=complete" in line
    assert "handoff=ok " not in line
    assert "class=build" in line
    assert "outcome=ok" in line
    (enter,) = mcp.named("session_enter")
    assert enter["dispatch_id"] == "PKT00001"
    assert mcp.named("dispatch_read") == [
        {"app_id": "hanuman", "dispatch_id": "PKT00001"}
    ]
    (close,) = mcp.named("handoff_write_v4")
    assert close["dispatch_id"] == "PKT00001"


def test_run_wake_refuses_on_the_turn_budget(tmp_path, monkeypatch):
    """A ladder that only ever calls tools (never a final answer) is refused
    by name at the turn cap, not stretched — and still closes and inks."""
    _isolate(monkeypatch, tmp_path)
    # non_interactive routes tool dispatch through _tools.dispatch, not
    # prompt_and_dispatch (see F2 below) — stub the one actually called.
    monkeypatch.setattr(crown._tools, "dispatch", lambda *a, **kw: "tool result")
    tool_use = {"id": "t1", "name": "Read", "input": {"file_path": "/x"}}
    calling = Completion(
        blocks=[{"type": "tool_use", **tool_use}],
        text="",
        tokens_in=1,
        tokens_out=1,
        latency_ms=1,
    )
    inference = _fake_inference(lambda *a, **k: (calling, _receipt_ok()))
    mcp = _FakeMCP(
        {
            "session_enter": _entered(),
            "dispatch_read": {"meta": {"role": "build-work-order"}},
            "handoff_write_v4": _BROKER_HANDOFF_ANSWER,
        }
    )

    line = crown.run_wake(
        mcp,
        app_id="hanuman",
        dispatch_id="PKT00001",
        trace_id="tr-6",
        inference=inference,
        max_turns=2,
    )

    assert "outcome=budget_turns" in line
    assert mcp.named("handoff_write_v4"), "still closes on a budget refusal"
    # F7: the closeout's own narrative/findings must say how it ended, not
    # just the returned receipt line — checked at the seat.py level in
    # test_seat.py; here just confirm the transcript carries the notice
    # summarize() reads.
    (close,) = mcp.named("handoff_write_v4")
    assert "turn cap" in close["narrative"]


def test_run_wake_refuses_on_the_wall_clock_budget_between_calls(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)

    def _never_called(*_a, **_kw):
        raise AssertionError("inference.complete must not run past the deadline")

    inference = _fake_inference(_never_called)
    mcp = _FakeMCP(
        {
            "session_enter": _entered(),
            "dispatch_read": {"meta": {"role": "build-work-order"}},
            "handoff_write_v4": _BROKER_HANDOFF_ANSWER,
        }
    )

    line = crown.run_wake(
        mcp,
        app_id="hanuman",
        dispatch_id="PKT00001",
        trace_id="tr-7",
        inference=inference,
        wall_clock_seconds=-1,
    )

    assert "outcome=budget_seconds" in line
    assert mcp.named("handoff_write_v4")


def test_run_wake_reports_the_wall_clock_budget_even_when_one_call_overruns_it(
    tmp_path, monkeypatch
):
    """F4 (Loki 3564BE3C, folded from test_loki_probe_wake.py's
    test_probe_wall_clock_does_not_bound_a_single_call): the deadline used to
    be checked only BETWEEN calls, so one slow call that itself overran the
    whole budget and still ended in a final answer reported outcome=ok. Now
    it is checked again right after the call returns too."""
    _isolate(monkeypatch, tmp_path)
    calls = []

    def slow_complete(*a, **k):
        calls.append(time.monotonic())
        time.sleep(0.05)
        return _done(), _receipt_ok()

    inference = _fake_inference(slow_complete)
    mcp = _FakeMCP(
        {
            "session_enter": _entered(),
            "dispatch_read": {"meta": {"role": "build-work-order"}},
            "handoff_write_v4": _BROKER_HANDOFF_ANSWER,
        }
    )
    started = time.monotonic()

    line = crown.run_wake(
        mcp,
        app_id="hanuman",
        dispatch_id="PKT00001",
        trace_id="tr-7b",
        inference=inference,
        wall_clock_seconds=0.01,
    )

    elapsed = time.monotonic() - started
    assert len(calls) == 1, (
        "one call still had to run to completion — it cannot be preempted"
    )
    assert elapsed >= 0.05, (
        "the call's own 0.05s sleep ran past the 0.01s wall-clock budget"
    )
    assert "outcome=budget_seconds" in line, "the overrun is reported, not silently ok"
    assert "outcome=ok" not in line


def test_run_wake_unknown_role_falls_back_to_chat_class(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    inference = _fake_inference(lambda *a, **k: (_done(), _receipt_ok()))
    mcp = _FakeMCP(
        {
            "session_enter": _entered(),
            "dispatch_read": {"meta": {"role": "some-unmapped-role"}},
            "handoff_write_v4": _BROKER_HANDOFF_ANSWER,
        }
    )
    line = crown.run_wake(
        mcp,
        app_id="hanuman",
        dispatch_id="PKT00001",
        trace_id="tr-8",
        inference=inference,
    )
    assert "class=chat" in line


def test_run_wake_fires_a_heartbeat_during_a_multi_turn_wake_not_only_after(
    tmp_path, monkeypatch
):
    """F5 (Loki 3564BE3C): on_heartbeat used to fire exactly once, after the
    whole turn loop — up to the wall-clock budget (minutes) dark on a
    30s-interval heartbeat. Drive several tool-calling iterations with a
    tiny heartbeat_interval and prove a heartbeat lands BEFORE the loop
    ends, not only after."""
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(crown._tools, "dispatch", lambda *a, **k: "r")
    tool_use = {"id": "t1", "name": "Read", "input": {"file_path": "/x"}}
    calling = Completion(
        blocks=[{"type": "tool_use", **tool_use}],
        text="",
        tokens_in=1,
        tokens_out=1,
        latency_ms=1,
    )

    def slow_complete(*a, **k):
        time.sleep(0.02)
        return calling, _receipt_ok()

    inference = _fake_inference(slow_complete)
    mcp = _FakeMCP(
        {
            "session_enter": _entered(),
            "dispatch_read": {"meta": {"role": "build-work-order"}},
            "handoff_write_v4": _BROKER_HANDOFF_ANSWER,
        }
    )
    beats = []
    crown.run_wake(
        mcp,
        app_id="hanuman",
        dispatch_id="PKT00001",
        trace_id="tr-9",
        inference=inference,
        max_turns=5,
        on_heartbeat=lambda: beats.append(time.monotonic()),
        heartbeat_interval=0.01,
    )
    # At least one beat from inside the loop (before the final call-after
    # beat run_wake still fires as a courtesy) — more than the single
    # "after the loop" beat the old code produced.
    assert len(beats) >= 2


def test_wake_tool_confirm_verdict_is_refused_not_prompted_on_stdin(
    tmp_path, monkeypatch
):
    """F2 (Loki 3564BE3C, folded from test_loki_probe_wake.py's
    test_probe_wake_tool_call_reaches_interactive_input): PolicyStore's
    default Bash/Write/Edit -> confirm rules survive trust=True; a wake has
    no human at the keyboard by definition, so a CONFIRM verdict must be
    refused by name, never prompted on stdin."""
    _isolate(monkeypatch, tmp_path)
    import builtins

    prompts = []

    def fake_input(prompt=""):
        prompts.append(prompt)
        raise AssertionError("run_wake must never call input()")

    monkeypatch.setattr(builtins, "input", fake_input)
    tool_use = {"id": "t1", "name": "Bash", "input": {"command": "true"}}
    calling = Completion(
        blocks=[{"type": "tool_use", **tool_use}],
        text="",
        tokens_in=1,
        tokens_out=1,
        latency_ms=1,
    )
    inference = _fake_inference(lambda *a, **k: (calling, _receipt_ok()))
    mcp = _FakeMCP(
        {
            "session_enter": _entered(),
            "dispatch_read": {"meta": {"role": "build-work-order"}},
            "handoff_write_v4": _BROKER_HANDOFF_ANSWER,
        }
    )

    line = crown.run_wake(
        mcp,
        app_id="hanuman",
        dispatch_id="PKT00001",
        trace_id="t",
        inference=inference,
        max_turns=2,
    )

    assert prompts == [], "input() was never reached"
    assert "outcome=budget_turns" in line
    # PolicyStore() still wrote its default rules under the isolated
    # WILLOW_HOME — proves the confirm verdict was real, not skipped.
    policy = tmp_path / "ratatosk" / "policy.json"
    assert policy.exists()
    rules = json.loads(policy.read_text())["rules"]
    assert {r["pattern"]: r["action"] for r in rules} == {
        "Bash": "confirm",
        "Write": "confirm",
        "Edit": "confirm",
    }


def test_wake_exception_after_entry_still_closes_and_never_raises(
    tmp_path, monkeypatch
):
    """F3 (Loki 3564BE3C, folded from test_loki_probe_wake.py's
    test_probe_exception_after_enter_never_closes): a defect anywhere
    between seat.enter and the turn loop used to leave the packet `working`
    forever, with no closeout and no receipt. Now it still closes — with a
    'crashed' outcome — and run_wake never raises."""
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(
        crown, "_load_system_prompt", lambda: (_ for _ in ()).throw(OSError("boom"))
    )
    inference = _fake_inference(lambda *a, **k: (_done(), _receipt_ok()))
    mcp = _FakeMCP(
        {
            "session_enter": _entered(),
            "dispatch_read": {"meta": {"role": "build-work-order"}},
            "handoff_write_v4": _BROKER_HANDOFF_ANSWER,
        }
    )

    line = crown.run_wake(  # must not raise
        mcp,
        app_id="hanuman",
        dispatch_id="PKT00001",
        trace_id="t",
        inference=inference,
    )

    assert mcp.named("session_enter"), "entered"
    assert mcp.named("handoff_write_v4"), "closed despite the mid-setup crash"
    assert "outcome=crashed" in line


# -- wake policy enforcement (sealed 3566adb5) ------------------------------


def _confirm_loop_mcp(
    role: str, assignment: str = "# Build the thing\n\nOne bite."
) -> _FakeMCP:
    return _FakeMCP(
        {
            "session_enter": _entered(role=role, assignment=assignment),
            "dispatch_read": {"meta": {"role": "build-work-order"}},
            "handoff_write_v4": _BROKER_HANDOFF_ANSWER,
        }
    )


def _tool_use_inference(name: str, tool_input: dict):
    tool_use = {"id": "t1", "name": name, "input": tool_input}
    calling = Completion(
        blocks=[{"type": "tool_use", **tool_use}],
        text="",
        tokens_in=1,
        tokens_out=1,
        latency_ms=1,
    )
    return _fake_inference(lambda *a, **k: (calling, _receipt_ok()))


def _notice_text(close: dict) -> str:
    """Every notice the closeout carries, joined — narrative only ever
    shows the LAST one (seat.narrative), but build_findings' "how the
    session ended" finding carries all of them; tests that may run past
    the refusal they are checking (a budget note written afterward) read
    this instead of narrative alone."""
    findings = close.get("findings") or []
    ended = [f for f in findings if f.get("title") == "how the session ended"]
    return " | ".join(e for f in ended for e in f.get("evidence", []))


def test_run_wake_bash_is_refused_for_every_role(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    for role in (
        "auditor",
        "build-work-order",
        "architect",
        "librarian",
        "operator",
        "witness",
    ):
        inference = _tool_use_inference("Bash", {"command": "true"})
        mcp = _confirm_loop_mcp(role)
        line = crown.run_wake(
            mcp,
            app_id="hanuman",
            dispatch_id="PKT00001",
            trace_id="t",
            inference=inference,
            max_turns=1,
        )
        assert "outcome=budget_turns" in line, role
        (close,) = mcp.named("handoff_write_v4")
        assert "not in this seat's wake-policy allow set" in _notice_text(close), role


def test_run_wake_auditor_role_write_is_refused_by_policy(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    inference = _tool_use_inference("Write", {"file_path": "/tmp/x", "content": "y"})
    mcp = _confirm_loop_mcp("auditor")

    line = crown.run_wake(
        mcp,
        app_id="hanuman",
        dispatch_id="PKT00001",
        trace_id="t",
        inference=inference,
        max_turns=1,
    )

    assert "outcome=budget_turns" in line
    (close,) = mcp.named("handoff_write_v4")
    assert "not in this seat's wake-policy allow set (role='auditor')" in _notice_text(
        close
    )


def test_run_wake_build_role_write_inside_the_worktree_runs(tmp_path, monkeypatch):
    """No mock of _tools.dispatch here: the point is that the REAL
    permission check raises NeedsConfirmation for Write (default policy),
    the wake policy approves it (build-work-order, in scope), and the
    approved one-shot re-dispatch actually performs the write — proven by
    the file landing on disk, not by a mocked call log (a prior version of
    this test mocked crown._tools.dispatch globally, which also replaced
    the FIRST, confirm-raising call and silently never exercised the wake
    policy check at all)."""
    _isolate(monkeypatch, tmp_path)
    worktree = tmp_path / "worktrees" / "feat-x"
    worktree.mkdir(parents=True)
    target = worktree / "file.txt"
    assignment = f"# Build\n\nSame worktree: `{worktree}`."
    inference = _tool_use_inference(
        "Write", {"file_path": str(target), "content": "hello"}
    )
    mcp = _confirm_loop_mcp("build-work-order", assignment=assignment)

    line = crown.run_wake(
        mcp,
        app_id="hanuman",
        dispatch_id="PKT00001",
        trace_id="t",
        inference=inference,
        max_turns=1,
    )

    assert target.read_text(encoding="utf-8") == "hello"
    assert (
        "outcome=budget_turns" in line
    )  # the fake inference never stops calling tools


def test_run_wake_build_role_write_outside_the_worktree_is_refused(
    tmp_path, monkeypatch
):
    _isolate(monkeypatch, tmp_path)
    worktree = tmp_path / "worktrees" / "feat-x"
    worktree.mkdir(parents=True)
    outside = tmp_path / "elsewhere" / "file.txt"
    assignment = f"# Build\n\nSame worktree: `{worktree}`."
    inference = _tool_use_inference(
        "Write", {"file_path": str(outside), "content": "y"}
    )
    mcp = _confirm_loop_mcp("build-work-order", assignment=assignment)

    line = crown.run_wake(
        mcp,
        app_id="hanuman",
        dispatch_id="PKT00001",
        trace_id="t",
        inference=inference,
        max_turns=1,
    )

    assert not outside.exists(), "never actually written — refused before dispatch"
    (close,) = mcp.named("handoff_write_v4")
    assert "is outside the packet worktree" in _notice_text(close)
    assert "outcome=budget_turns" in line


def test_run_wake_build_role_write_with_no_worktree_named_is_refused(
    tmp_path, monkeypatch
):
    _isolate(monkeypatch, tmp_path)
    target = tmp_path / "x.txt"
    inference = _tool_use_inference("Write", {"file_path": str(target), "content": "y"})
    # default _entered() assignment names no worktree path
    mcp = _confirm_loop_mcp("build-work-order")

    line = crown.run_wake(
        mcp,
        app_id="hanuman",
        dispatch_id="PKT00001",
        trace_id="t",
        inference=inference,
        max_turns=1,
    )

    assert not target.exists()
    (close,) = mcp.named("handoff_write_v4")
    assert "no packet worktree could be named" in _notice_text(close)
    assert "outcome=budget_turns" in line


def test_run_wake_refuses_to_start_for_an_unknown_role(tmp_path, monkeypatch):
    """Sealed 3566adb5: 'a role missing from the table means the wake
    refuses to start.' No turn is spent, but the seat still closes (F3
    symmetry: enter and close stay paired)."""
    _isolate(monkeypatch, tmp_path)

    def _never_called(*_a, **_kw):
        raise AssertionError("no turn should run when the wake policy can't resolve")

    inference = _fake_inference(_never_called)
    mcp = _confirm_loop_mcp("nonexistent-role")

    line = crown.run_wake(
        mcp, app_id="hanuman", dispatch_id="PKT00001", trace_id="t", inference=inference
    )

    assert "outcome=refused:" in line
    assert "no wake policy for role" in line
    (enter,) = mcp.named("session_enter")
    assert enter["dispatch_id"] == "PKT00001"
    assert mcp.named("handoff_write_v4"), "still closes despite refusing to start"


def test_run_wake_ignores_an_envelope_carried_wake_policy_and_notes_it(
    tmp_path, monkeypatch
):
    """Sealed 3566adb5, requirement 4: a WAKE envelope carrying
    policy-shaped fields is ignored, never consulted — the daemon detects
    this (SeatDaemon._crown_activate) and tells run_wake to note it."""
    _isolate(monkeypatch, tmp_path)
    inference = _fake_inference(lambda *a, **k: (_done(), _receipt_ok()))
    mcp = _confirm_loop_mcp("auditor")

    crown.run_wake(
        mcp,
        app_id="hanuman",
        dispatch_id="PKT00001",
        trace_id="t",
        inference=inference,
        envelope_wake_policy_ignored=True,
    )

    (close,) = mcp.named("handoff_write_v4")
    assert (
        "envelope carried wake-policy-shaped field(s) — ignored" in close["narrative"]
    )


def test_crown_activate_flags_an_envelope_carried_wake_policy_for_run_wake(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        crown, "run_wake", lambda mcp_call, **kw: captured.update(kw) or "line"
    )
    daemon = SeatDaemon(
        node="ratatosk",
        channel="fleet",
        mcp_call=lambda n, i: {},
        crown_app_id="hanuman",
    )

    from ratatosk.protocol.envelope import build_envelope

    env = build_envelope(
        to="ratatosk",
        prompt="p",
        intent="wake",
        capabilities=[],
        extra={"dispatch_id": "PKT1", "wake_policy": {"allow": ["Write"]}},
    )
    daemon._crown_activate(env)

    assert captured["envelope_wake_policy_ignored"] is True


def test_crown_activate_does_not_flag_an_ordinary_wake(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        crown, "run_wake", lambda mcp_call, **kw: captured.update(kw) or "line"
    )
    daemon = SeatDaemon(
        node="ratatosk",
        channel="fleet",
        mcp_call=lambda n, i: {},
        crown_app_id="hanuman",
    )

    from ratatosk.protocol.envelope import build_envelope

    env = build_envelope(
        to="ratatosk",
        prompt="p",
        intent="wake",
        capabilities=[],
        extra={"dispatch_id": "PKT1"},
    )
    daemon._crown_activate(env)

    assert captured["envelope_wake_policy_ignored"] is False


def test_wake_envelope_app_id_never_overrides_the_daemons_own_seat(monkeypatch):
    """F6 (Loki 3564BE3C, folded from test_loki_probe_wake.py's
    test_probe_envelope_app_id_overrides_daemon_seat): a WAKE's
    extra.app_id used to pick which seat the daemon entered as, with no
    sender check — any poster on the channel could redirect the daemon's
    identity. The daemon's crown_app_id is fixed at process start and wins
    no matter what the envelope's JSON carries."""
    captured = {}
    monkeypatch.setattr(
        crown, "run_wake", lambda mcp_call, **kw: captured.update(kw) or "line"
    )
    daemon = SeatDaemon(
        node="ratatosk",
        channel="fleet",
        mcp_call=lambda n, i: {},
        crown_app_id="hanuman",
    )

    from ratatosk.protocol.envelope import build_envelope

    env = build_envelope(
        to="ratatosk",
        prompt="p",
        intent="wake",
        capabilities=[],
        extra={"dispatch_id": "PKT1", "app_id": "loki"},
    )
    daemon._crown_activate(env)

    assert captured["app_id"] == "hanuman", "never the envelope's extra.app_id"


# -- SeatDaemon wiring: crown_app_id, busy guard ----------------------------


def test_crown_app_id_wires_the_bounded_crown_activation_by_default():
    daemon = SeatDaemon(
        node="ratatosk",
        channel="fleet",
        mcp_call=lambda n, i: {},
        crown_app_id="hanuman",
    )
    assert daemon.listener.activate == daemon._crown_activate


def test_an_explicit_activate_still_overrides_crown_wiring():
    def custom(env):
        return "custom"

    daemon = SeatDaemon(
        node="ratatosk",
        channel="fleet",
        mcp_call=lambda n, i: {},
        crown_app_id="hanuman",
        activate=custom,
    )
    assert daemon.listener.activate is custom


def test_no_crown_app_id_keeps_the_honest_default_noop():
    daemon = SeatDaemon(node="ratatosk", channel="fleet", mcp_call=lambda n, i: {})
    assert daemon.listener.activate is default_activate


def test_crown_activate_refuses_a_second_wake_as_busy_without_running_it():
    mcp = _FakeMCP({"session_enter": _entered()})
    daemon = SeatDaemon(
        node="ratatosk", channel="fleet", mcp_call=mcp, crown_app_id="hanuman"
    )
    daemon._wake_busy = True

    from ratatosk.protocol.envelope import build_envelope

    env = build_envelope(
        to="ratatosk",
        prompt="p",
        intent="wake",
        capabilities=[],
        extra={"dispatch_id": "PKT1"},
    )
    out = daemon._crown_activate(env)

    assert "busy" in out
    assert mcp.calls == [], "no session_enter while a wake is already running"


def test_crown_activate_clears_the_busy_flag_after_the_wake_finishes(monkeypatch):
    calls = []

    def fake_run_wake(mcp_call, **kw):
        calls.append(kw)
        assert daemon._wake_busy is True, "busy while the wake itself runs"
        return "[ratatosk] wake receipt ..."

    daemon = SeatDaemon(
        node="ratatosk",
        channel="fleet",
        mcp_call=lambda n, i: {},
        crown_app_id="hanuman",
    )
    monkeypatch.setattr(crown, "run_wake", fake_run_wake)

    from ratatosk.protocol.envelope import build_envelope

    env = build_envelope(
        to="ratatosk",
        prompt="p",
        intent="wake",
        capabilities=[],
        extra={"dispatch_id": "PKT1"},
    )
    out = daemon._crown_activate(env)

    assert out == "[ratatosk] wake receipt ..."
    assert daemon._wake_busy is False, "cleared once the wake returns"
    assert calls[0]["dispatch_id"] == "PKT1"
    assert calls[0]["app_id"] == "hanuman"


def test_wake_message_with_a_dispatch_id_routes_through_crown_run_wake(monkeypatch):
    """End to end through run_once, mirroring
    test_wake_message_activates_the_seat_runtime but for the crown-wired
    default rather than a hand-supplied ``activate``."""
    captured = {}

    def fake_run_wake(mcp_call, **kw):
        captured.update(kw)
        return f"[ratatosk] wake receipt trace={kw['trace_id']} dispatch={kw['dispatch_id']}"

    monkeypatch.setattr(crown, "run_wake", fake_run_wake)

    history = [
        {
            "id": 1,
            "sender": "willow",
            "content": json.dumps(
                {
                    "v": 1,
                    "to": "ratatosk",
                    "intent": "wake",
                    "prompt": "packet-dispatch",
                    "reply_channel": "fleet",
                    "mode": "ollama",
                    "capabilities": [],
                    "nonce": "wk9",
                    "trace_id": "tr-x",
                    "expires_at": "2099-01-01T00:00:00Z",
                    "requires_confirm": False,
                    "dispatch_id": "PKT9",
                }
            ),
        }
    ]

    def mcp_call(tool, inputs):
        if tool == "grove_get_history":
            return {"result": list(history)}
        return {}

    daemon = SeatDaemon(
        node="ratatosk", channel="fleet", mcp_call=mcp_call, crown_app_id="hanuman"
    )
    outputs = daemon.listener.run_once()

    assert outputs == ["[ratatosk] wake receipt trace=tr-x dispatch=PKT9"]
    assert captured["dispatch_id"] == "PKT9"
    assert captured["app_id"] == "hanuman"
    assert captured["trace_id"] == "tr-x"


def test_wake_with_no_dispatch_id_is_acknowledged_and_refused_not_worked():
    """No monkeypatch of crown.run_wake here — this drives the real refusal
    path (dispatch_id falls through as None) and checks nothing beyond the
    refusal line was called against the broker."""
    history = [
        {
            "id": 1,
            "sender": "willow",
            "content": json.dumps(
                {
                    "v": 1,
                    "to": "ratatosk",
                    "intent": "wake",
                    "prompt": "packet-dispatch",
                    "reply_channel": "fleet",
                    "mode": "ollama",
                    "capabilities": [],
                    "nonce": "wk10",
                    "trace_id": "tr-y",
                    "expires_at": "2099-01-01T00:00:00Z",
                    "requires_confirm": False,
                }
            ),
        }
    ]

    calls = []

    def mcp_call(tool, inputs):
        calls.append(tool)
        if tool == "grove_get_history":
            return {"result": list(history)}
        return {}

    daemon = SeatDaemon(
        node="ratatosk", channel="fleet", mcp_call=mcp_call, crown_app_id="hanuman"
    )
    outputs = daemon.listener.run_once()

    assert len(outputs) == 1
    assert "refused" in outputs[0] and "no dispatch_id" in outputs[0]
    assert "session_enter" not in calls, "never entered the seat for an unworked wake"


def test_main_wires_app_id_and_wake_flags(monkeypatch, capsys, tmp_path):
    """CLI: ``ratatosk-listen --app-id <seat>`` wires the crown activation;
    ``--wake-turns``/``--wake-seconds`` reach the SeatDaemon it builds."""
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    monkeypatch.setenv("RATATOSK_GROVE_CHANNEL", "hanuman")
    monkeypatch.delenv("WILLOW_HUMAN_ORCHESTRATOR", raising=False)
    from ratatosk import daemon as _daemon_mod
    from ratatosk import mcp_client

    monkeypatch.setattr(mcp_client, "start", lambda: ([{"name": "extra"}], {"extra"}))
    monkeypatch.setattr(mcp_client, "call", lambda n, i: {})
    monkeypatch.setattr(mcp_client, "shutdown", lambda *a, **kw: True)

    built = {}
    real_daemon_cls = _daemon_mod.SeatDaemon

    class Spy(real_daemon_cls):
        def __init__(self, **kw):
            built.update(kw)
            super().__init__(**kw)

    monkeypatch.setattr(_daemon_mod, "SeatDaemon", Spy)
    monkeypatch.setattr(
        Spy,
        "run_forever",
        lambda self, on_status=None: None,
    )

    _daemon_mod.main(
        [
            "--app-id",
            "hanuman",
            "--wake-turns",
            "3",
            "--wake-seconds",
            "45",
        ]
    )

    assert built["crown_app_id"] == "hanuman"
    assert built["wake_turns"] == 3
    assert built["wake_seconds"] == 45.0
    assert built["mcp_extra_tools"] == [{"name": "extra"}]
    assert built["mcp_names"] == {"extra"}
    import os as _os

    assert _os.environ["WILLOW_APP_ID"] == "hanuman"
    assert _os.environ["RATATOSK_APP_ID"] == "hanuman"
    assert _os.environ["WILLOW_AGENT_NAME"] == "hanuman"


def test_main_refuses_app_id_willow_before_connecting(monkeypatch, tmp_path):
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    monkeypatch.delenv("WILLOW_HUMAN_ORCHESTRATOR", raising=False)
    from ratatosk import daemon as _daemon_mod

    connected = []
    monkeypatch.setattr(
        "ratatosk.mcp_client.start", lambda: connected.append(1) or ([], set())
    )

    with pytest.raises(SystemExit) as info:
        _daemon_mod.main(["--app-id", "willow"])
    assert info.value.code == 2
    assert connected == [], "refused before the transport was ever started"
