"""SeatDaemon — the heartbeating, stoppable wrapper around BusListener that
makes the fleet wake its own seats instead of the orchestrator hand-spawning
workers.

Grove protocol behavior (channel refusal, validation, gating) is BusListener's
job and is covered in test_listener.py; these tests cover only what SeatDaemon
adds: heartbeat scheduling, wake activation wiring, and a stop signal that
run_forever actually honors.
"""

import json
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
    }
    result.update(overrides)
    return result


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
    monkeypatch.setenv("RATATOSK_SESSION_DIR", str(tmp_path))
    monkeypatch.delenv("RATATOSK_GROVE_CHANNEL", raising=False)
    done = Completion(
        blocks=[{"type": "text", "text": "done"}],
        text="done",
        tokens_in=100,
        tokens_out=20,
        latency_ms=12,
        raw_model="fake-model",
    )
    receipt = SimpleNamespace(
        rung="rung1",
        line=lambda: "[ratatosk] turn ok class=build rung=rung1 fake/fake-model",
    )
    receipt.provider = "fake"
    receipt.model = "fake-model"
    receipt.tokens_in = 100
    receipt.tokens_out = 20
    receipt.outcome = "ok"
    inference = _fake_inference(lambda *a, **k: (done, receipt))
    mcp = _FakeMCP(
        {
            "session_enter": _entered(),
            "dispatch_read": {"meta": {"role": "build-work-order"}},
            "handoff_write_v4": {"handoff_id": "H1"},
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
    assert "handoff=H1" in line
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
    monkeypatch.setenv("RATATOSK_SESSION_DIR", str(tmp_path))
    monkeypatch.delenv("RATATOSK_GROVE_CHANNEL", raising=False)
    monkeypatch.setattr(
        crown._tools, "prompt_and_dispatch", lambda *a, **kw: "tool result"
    )
    tool_use = {"id": "t1", "name": "Read", "input": {"file_path": "/x"}}
    calling = Completion(
        blocks=[{"type": "tool_use", **tool_use}],
        text="",
        tokens_in=1,
        tokens_out=1,
        latency_ms=1,
    )
    receipt = SimpleNamespace(rung="rung1", line=lambda: "[ratatosk] turn ok")
    inference = _fake_inference(lambda *a, **k: (calling, receipt))
    mcp = _FakeMCP(
        {
            "session_enter": _entered(),
            "dispatch_read": {"meta": {"role": "build-work-order"}},
            "handoff_write_v4": {"handoff_id": "H2"},
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


def test_run_wake_refuses_on_the_wall_clock_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("RATATOSK_SESSION_DIR", str(tmp_path))
    monkeypatch.delenv("RATATOSK_GROVE_CHANNEL", raising=False)

    def _never_called(*_a, **_kw):
        raise AssertionError("inference.complete must not run past the deadline")

    inference = _fake_inference(_never_called)
    mcp = _FakeMCP(
        {
            "session_enter": _entered(),
            "dispatch_read": {"meta": {"role": "build-work-order"}},
            "handoff_write_v4": {"handoff_id": "H3"},
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


def test_run_wake_unknown_role_falls_back_to_chat_class(tmp_path, monkeypatch):
    monkeypatch.setenv("RATATOSK_SESSION_DIR", str(tmp_path))
    monkeypatch.delenv("RATATOSK_GROVE_CHANNEL", raising=False)
    done = Completion(
        blocks=[{"type": "text", "text": "done"}],
        text="done",
        tokens_in=1,
        tokens_out=1,
        latency_ms=1,
    )
    receipt = SimpleNamespace(rung="rung1", line=lambda: "[ratatosk] turn ok")
    inference = _fake_inference(lambda *a, **k: (done, receipt))
    mcp = _FakeMCP(
        {
            "session_enter": _entered(),
            "dispatch_read": {"meta": {"role": "some-unmapped-role"}},
            "handoff_write_v4": {"handoff_id": "H4"},
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


def test_run_wake_calls_on_heartbeat_once_after_the_turn_loop(tmp_path, monkeypatch):
    monkeypatch.setenv("RATATOSK_SESSION_DIR", str(tmp_path))
    monkeypatch.delenv("RATATOSK_GROVE_CHANNEL", raising=False)
    done = Completion(
        blocks=[{"type": "text", "text": "done"}],
        text="done",
        tokens_in=1,
        tokens_out=1,
        latency_ms=1,
    )
    receipt = SimpleNamespace(rung="rung1", line=lambda: "[ratatosk] turn ok")
    inference = _fake_inference(lambda *a, **k: (done, receipt))
    mcp = _FakeMCP(
        {
            "session_enter": _entered(),
            "dispatch_read": {"meta": {"role": "build-work-order"}},
            "handoff_write_v4": {"handoff_id": "H5"},
        }
    )
    beats = []
    crown.run_wake(
        mcp,
        app_id="hanuman",
        dispatch_id="PKT00001",
        trace_id="tr-9",
        inference=inference,
        on_heartbeat=lambda: beats.append(1),
    )
    assert beats == [1]


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
