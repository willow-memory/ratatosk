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


def test_connect_binds_a_sender_that_actually_posts(monkeypatch):
    """The gap this closes: crown bound a sender inside its --mcp branch and
    nowhere else, so every other caller had to reimplement that wiring — and
    a seat probe that did not got "sender not configured" however the box was
    configured (Grove #willow msg 507)."""
    monkeypatch.setenv("RATATOSK_GROVE_CHANNEL", "fleet")
    grove.set_grove_sender(None)
    seen = {}

    bound = grove.connect(lambda name, inputs: seen.update(inputs) or "{}")
    assert bound.ok
    assert "fleet" in bound.detail

    receipt = grove.send("hello")
    assert receipt.ok
    assert seen["channel_name"] == "fleet"


def test_connect_without_a_channel_is_skipped_not_failed(monkeypatch):
    """An unset channel is a normal state, not an error."""
    monkeypatch.delenv("RATATOSK_GROVE_CHANNEL", raising=False)
    bound = grove.connect(lambda name, inputs: "{}")
    assert bound.ok
    assert bound.skipped


def test_connect_reports_a_transport_that_will_not_start(monkeypatch):
    monkeypatch.setenv("RATATOSK_GROVE_CHANNEL", "fleet")
    grove.set_grove_sender(None)

    import ratatosk.mcp_client as mcp_client

    def boom(*a, **k):
        raise RuntimeError("no server here")

    monkeypatch.setattr(mcp_client, "start", boom)
    bound = grove.connect()
    assert not bound.ok
    assert "no server here" in bound.detail
    assert grove.send("hello").ok is False, "an unbound sender must still refuse"


def test_disable_gives_skipped_receipts_not_repeated_failures(monkeypatch):
    """A session posts a start and an end event. An unbound sender makes the
    operator read the same complaint once per event; an explicitly disabled
    one says it at startup and then stays quiet."""
    monkeypatch.setenv("RATATOSK_GROVE_CHANNEL", "willow")
    grove.disable("no MCP transport in this process")

    started = grove.session_started("abcd1234", "test-model")
    ended = grove.session_ended("abcd1234", 3, "/tmp/x.jsonl")

    for receipt in (started, ended):
        assert receipt.ok
        assert receipt.skipped
        assert "no MCP transport" in receipt.detail
