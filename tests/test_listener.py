from ratatosk.listener import BusListener
from ratatosk.protocol.envelope import Intent


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
