"""SeatDaemon — the heartbeating, stoppable wrapper around BusListener that
makes the fleet wake its own seats instead of the orchestrator hand-spawning
workers.

Grove protocol behavior (channel refusal, validation, gating) is BusListener's
job and is covered in test_listener.py; these tests cover only what SeatDaemon
adds: heartbeat scheduling, wake activation wiring, and a stop signal that
run_forever actually honors.
"""
import pytest

from ratatosk.daemon import DEFAULT_HEARTBEAT_INTERVAL, SeatDaemon, default_activate


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
    assert calls == [("grove_heartbeat", {"app_id": "ratatosk", "agent": "ratatosk", "channel": "fleet"})]


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
    assert "grove_get_history" not in calls, "stop must be honored before polling begins"
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

    daemon = SeatDaemon(node="ratatosk", channel="fleet", mcp_call=mcp_call, activate=activate)
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
