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


def _sender(monkeypatch, result):
    """Wire a grove sender whose transport returns `result`."""
    monkeypatch.setenv("RATATOSK_GROVE_CHANNEL", "fleet")
    calls = []

    def fake_call(name, inputs):
        calls.append((name, inputs))
        return result

    grove.set_grove_sender(grove.make_mcp_sender(fake_call, channel="fleet"))
    return calls


def test_transport_error_string_is_not_a_receipt(monkeypatch):
    """mcp_client.call returns a str, so a dict-only check called this a success."""
    _sender(monkeypatch, "[mcp-error] 'CallToolResult' object has no attribute 'isError'")
    receipt = grove.send("hello")
    assert not receipt.ok
    assert "isError" in receipt.detail


def test_json_error_payload_is_not_a_receipt(monkeypatch):
    """willow-mcp reports a denied or degraded call as a JSON error, not a transport error."""
    _sender(monkeypatch, '{"error": "permission denied: grove_write"}')
    receipt = grove.send("hello")
    assert not receipt.ok
    assert "grove_write" in receipt.detail


def test_empty_result_is_not_a_receipt(monkeypatch):
    _sender(monkeypatch, "")
    receipt = grove.send("hello")
    assert not receipt.ok


def test_dict_error_still_fails(monkeypatch):
    _sender(monkeypatch, {"error": "nope"})
    receipt = grove.send("hello")
    assert not receipt.ok
    assert receipt.detail == "nope"


def test_successful_post_reports_the_channel(monkeypatch):
    calls = _sender(monkeypatch, '{"result": {"id": 249}}')
    receipt = grove.send("hello")
    assert receipt.ok
    assert not receipt.skipped
    assert receipt.detail == "posted to fleet"
    assert calls[0][0] == "grove_send_message"
    assert calls[0][1]["channel_name"] == "fleet"  # willow-mcp names it channel_name


def test_a_sender_built_before_the_channel_is_set_still_posts_to_it(monkeypatch):
    """The defect: grove.py captured _CHANNEL at import while send() re-read the
    variable, so setting the channel after import passed send()'s guard and then
    posted to "" through a sender that had bound the import-time value."""
    from ratatosk import grove

    monkeypatch.delenv("RATATOSK_GROVE_CHANNEL", raising=False)
    seen = {}

    def fake_call(tool, args):
        seen.update(args)
        return {}

    sender = grove.make_mcp_sender(fake_call)          # built with no channel
    monkeypatch.setenv("RATATOSK_GROVE_CHANNEL", "late-channel")   # set afterwards
    receipt = sender("hello")

    assert receipt.ok, receipt.detail
    assert seen["channel_name"] == "late-channel"


def test_an_explicit_channel_still_wins(monkeypatch):
    from ratatosk import grove

    monkeypatch.setenv("RATATOSK_GROVE_CHANNEL", "env-channel")
    seen = {}
    sender = grove.make_mcp_sender(lambda t, a: seen.update(a) or {}, channel="explicit")
    sender("hi")
    assert seen["channel_name"] == "explicit"


def test_a_sender_with_no_channel_anywhere_says_so(monkeypatch):
    from ratatosk import grove

    monkeypatch.delenv("RATATOSK_GROVE_CHANNEL", raising=False)
    called = []
    sender = grove.make_mcp_sender(lambda t, a: called.append(a) or {})
    receipt = sender("hi")
    assert not receipt.ok
    assert "channel unset" in receipt.detail
    assert not called, "must not post to an empty channel"


def test_channel_env_reads_at_call_time(monkeypatch):
    from ratatosk import grove

    monkeypatch.setenv("RATATOSK_GROVE_CHANNEL", "one")
    assert grove.channel_env() == "one"
    monkeypatch.setenv("RATATOSK_GROVE_CHANNEL", "two")
    assert grove.channel_env() == "two"
