from ratatosk import grove


def test_send_without_channel_skips(monkeypatch):
    monkeypatch.delenv("RATATOSK_GROVE_CHANNEL", raising=False)
    receipt = grove.send("hello")
    assert receipt.skipped
    assert receipt.ok


def test_send_without_sender_fails(monkeypatch):
    monkeypatch.setenv("RATATOSK_GROVE_CHANNEL", "general")
    grove.set_grove_sender(None)
    receipt = grove.send("hello")
    assert not receipt.ok
    assert "not configured" in receipt.detail
