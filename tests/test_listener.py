import pytest

from ratatosk.listener import BusListener
from ratatosk.protocol.envelope import Intent


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
