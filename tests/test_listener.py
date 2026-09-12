import pytest

from ratatosk.listener import BusListener


def test_listener_refuses_unset_channel(monkeypatch):
    """No implicit "general". grove.send() treats an unset channel as disabled;
    the listener used to fall back to "general", so an unconfigured node
    listened on a channel nobody chose while refusing to post to it."""
    monkeypatch.delenv("RATATOSK_GROVE_CHANNEL", raising=False)
    with pytest.raises(ValueError, match="grove channel unset"):
        BusListener(mcp_call=None)


def test_listener_reads_channel_from_env(monkeypatch):
    monkeypatch.setenv("RATATOSK_GROVE_CHANNEL", "env-channel")
    listener = BusListener(mcp_call=None)
    assert listener.channel == "env-channel"


def test_listener_explicit_channel_beats_env(monkeypatch):
    monkeypatch.setenv("RATATOSK_GROVE_CHANNEL", "env-channel")
    listener = BusListener(channel="explicit", mcp_call=None)
    assert listener.channel == "explicit"


def test_listener_rejects_wrong_node():
    listener = BusListener(node="ratatosk", channel="general", mcp_call=None)
    out = listener.process_message(
        {
            "sender": "willow",
            "content": '{"v":1,"to":"other","intent":"chat","prompt":"hi","reply_channel":"general","mode":"ollama","capabilities":["chat"],"nonce":"abc123","trace_id":"tr-1","expires_at":"2099-01-01T00:00:00Z","requires_confirm":false}',
        }
    )
    assert out is not None
    assert "rejected" in out


def test_listener_accepts_chat(monkeypatch):
    monkeypatch.setattr("ratatosk.ollama.is_available", lambda: True)
    monkeypatch.setattr("ratatosk.ollama.generate", lambda prompt: "pong")
    listener = BusListener(node="ratatosk", channel="general", mcp_call=None)
    out = listener.process_message(
        {
            "sender": "willow",
            "content": '{"v":1,"to":"ratatosk","intent":"chat","prompt":"ping","reply_channel":"general","mode":"ollama","capabilities":["chat"],"nonce":"def456","trace_id":"tr-2","expires_at":"2099-01-01T00:00:00Z","requires_confirm":false}',
        }
    )
    assert out == "pong"


def test_as_messages_decodes_the_json_string_transport():
    """mcp_client.call returns a str; the old guard dropped every poll on the floor."""
    from ratatosk.listener import _as_messages

    payload = '{"result": [{"id": 1, "sender": "willow", "content": "hi"}]}'
    assert _as_messages(payload) == [{"id": 1, "sender": "willow", "content": "hi"}]


def test_as_messages_swallows_errors_not_history():
    from ratatosk.listener import _as_messages

    assert _as_messages("[mcp-error] boom") == []
    assert _as_messages('{"error": "postgres_unavailable"}') == []
    assert _as_messages("") == []
    assert _as_messages("not json") == []


def test_listener_ignores_its_own_post(monkeypatch):
    """A seat's own posts never wake it."""
    monkeypatch.setattr("ratatosk.ollama.is_available", lambda: True)
    monkeypatch.setattr("ratatosk.ollama.generate", lambda prompt: "pong")
    listener = BusListener(node="ratatosk", channel="general", mcp_call=None)
    assert listener.process_message({"sender": "ratatosk", "content": "pong"}) is None


def test_own_post_match_ignores_case_and_padding():
    listener = BusListener(node="Ratatosk", channel="general", mcp_call=None)
    assert listener.is_own_post("ratatosk")
    assert listener.is_own_post("  RATATOSK ")
    assert not listener.is_own_post("willow")
    assert not listener.is_own_post(None)


def test_run_once_advances_the_cursor_past_an_own_post():
    """Skipping the advance would re-examine the same post on every poll."""
    calls = []

    def mcp_call(tool, args):
        calls.append((tool, args))
        if tool == "grove_get_history":
            return {"result": [{"id": 7, "sender": "ratatosk", "content": "pong"}]}
        return {}

    listener = BusListener(node="ratatosk", channel="general", mcp_call=mcp_call)
    assert listener.run_once() == []
    assert listener.state.cursor == 7
    assert [t for t, _ in calls] == ["grove_get_history"]


def test_run_once_still_answers_a_foreign_sender(monkeypatch):
    monkeypatch.setattr("ratatosk.ollama.is_available", lambda: True)
    monkeypatch.setattr("ratatosk.ollama.generate", lambda prompt: "pong")
    sent = []

    def mcp_call(tool, args):
        if tool == "grove_get_history":
            return {"result": [{"id": 3, "sender": "willow", "content": "ping"}]}
        sent.append((tool, args))
        return {}

    listener = BusListener(node="ratatosk", channel="general", mcp_call=mcp_call)
    assert listener.run_once() == ["pong"]
    assert "grove_send_message" in [t for t, _ in sent]


def test_the_reply_loop_is_closed(monkeypatch):
    """Regression: feed every send back into history, as a live channel does.

    Before the sender guard this ran away — the reply parsed as a fresh chat
    envelope for this node, with a brand-new nonce each pass.
    """
    monkeypatch.setattr("ratatosk.ollama.is_available", lambda: True)
    monkeypatch.setattr("ratatosk.ollama.generate", lambda prompt: "pong")
    history = [{"id": 1, "sender": "willow", "content": "ping"}]

    def mcp_call(tool, args):
        if tool == "grove_get_history":
            return {"result": list(history)}
        if tool == "grove_send_message":
            history.append(
                {
                    "id": len(history) + 1,
                    "sender": args["sender"],
                    "content": args["content"],
                }
            )
        return {}

    listener = BusListener(node="ratatosk", channel="general", mcp_call=mcp_call)
    assert listener.run_once() == ["pong"]
    assert listener.run_once() == []
    assert listener.run_once() == []
    assert len(history) == 2


def test_a_refused_reply_is_reported_not_swallowed(monkeypatch):
    """Driven against a live bus, the listener produced two answers, posted
    neither — the gate refused the sender — and reported success both times,
    because the send result was discarded."""
    monkeypatch.setenv("RATATOSK_GROVE_CHANNEL", "ratatosk-smoke")
    refusals = []

    def refusing_call(tool, inputs):
        refusals.append(tool)
        return '{"error": "sender_forbidden", "detail": "needs grove_relay"}'

    listener = BusListener(
        node="ratatosk", channel="ratatosk-smoke", mcp_call=refusing_call
    )
    out = listener.process_message(
        {
            "sender": "willow",
            "content": '{"v":1,"to":"ratatosk","intent":"open_status","prompt":"status",'
            '"reply_channel":"ratatosk-smoke","mode":"ollama","capabilities":["open_status"],'
            '"nonce":"n1","trace_id":"tr-refused","expires_at":"2099-01-01T00:00:00Z",'
            '"requires_confirm":false}',
        }
    )
    assert refusals == ["grove_send_message"], "it must have tried"
    assert "not delivered" in out
    assert "sender_forbidden" in out


def test_wake_dispatches_to_the_seat_runtime(monkeypatch):
    """A WAKE envelope activates the seat runtime rather than getting a canned
    reply — the daemon's `activate` callback stands in for that runtime."""
    calls = []

    def activate(env):
        calls.append(env.trace_id)
        return f"[seat] worked packet {env.trace_id}"

    listener = BusListener(
        node="ratatosk", channel="general", mcp_call=None, activate=activate
    )
    out = listener.process_message(
        {
            "sender": "willow",
            "content": '{"v":1,"to":"ratatosk","intent":"wake","prompt":"packet-dispatch",'
            '"reply_channel":"general","mode":"ollama","capabilities":[],'
            '"nonce":"wake1","trace_id":"tr-wake-1","expires_at":"2099-01-01T00:00:00Z",'
            '"requires_confirm":false}',
        }
    )
    assert calls == ["tr-wake-1"]
    assert out == "[seat] worked packet tr-wake-1"


def test_wake_without_activate_acknowledges_but_does_nothing():
    listener = BusListener(node="ratatosk", channel="general", mcp_call=None)
    out = listener.process_message(
        {
            "sender": "willow",
            "content": '{"v":1,"to":"ratatosk","intent":"wake","prompt":"packet-dispatch",'
            '"reply_channel":"general","mode":"ollama","capabilities":[],'
            '"nonce":"wake2","trace_id":"tr-wake-2","expires_at":"2099-01-01T00:00:00Z",'
            '"requires_confirm":false}',
        }
    )
    assert "tr-wake-2" in out
    assert "no activation wired" in out


def test_emit_heartbeat_posts_grove_heartbeat():
    calls = []

    def mcp_call(tool, inputs):
        calls.append((tool, inputs))
        return "{}"

    listener = BusListener(node="ratatosk", channel="fleet", mcp_call=mcp_call)
    assert listener.emit_heartbeat() is True
    assert calls[0][0] == "grove_heartbeat"
    assert calls[0][1]["agent"] == "ratatosk"
    assert calls[0][1]["channel"] == "fleet"


def test_emit_heartbeat_is_a_noop_without_transport():
    listener = BusListener(node="ratatosk", channel="fleet", mcp_call=None)
    assert listener.emit_heartbeat() is False


def test_a_delivered_reply_returns_the_answer(monkeypatch):
    monkeypatch.setenv("RATATOSK_GROVE_CHANNEL", "ratatosk-smoke")
    listener = BusListener(
        node="ratatosk",
        channel="ratatosk-smoke",
        mcp_call=lambda tool, inputs: (
            '{"id": 1, "channel": "ratatosk-smoke", "sent": true}'
        ),
    )
    out = listener.process_message(
        {
            "sender": "willow",
            "content": '{"v":1,"to":"ratatosk","intent":"open_status","prompt":"status",'
            '"reply_channel":"ratatosk-smoke","mode":"ollama","capabilities":["open_status"],'
            '"nonce":"n2","trace_id":"tr-ok","expires_at":"2099-01-01T00:00:00Z",'
            '"requires_confirm":false}',
        }
    )
    assert out.startswith("node=ratatosk")
    assert "not delivered" not in out
