"""Cassettes, and the defect they found.

`tests/cassettes/willow_mcp.json` was recorded against a live willow-mcp. The
history entry in it is three concatenated JSON objects, which is what the
server actually sends and what every hand-written fake in this suite got wrong.
"""

import json
from pathlib import Path

import pytest

from ratatosk import grove
from ratatosk.cassette import SCHEMA, Recorder, UnrecordedCall, replay, scrub_result
from ratatosk.listener import _as_messages
from ratatosk.mcp_client import decode_payloads

CASSETTE = Path(__file__).parent / "cassettes" / "willow_mcp.json"

HISTORY_CALL = (
    "grove_get_history",
    {"app_id": "ratatosk", "channel_name": "willow", "limit": 3},
)


# ---- decode_payloads ------------------------------------------------------


def test_one_document_decodes_to_one_value():
    assert decode_payloads('{"a": 1}') == [{"a": 1}]


def test_concatenated_objects_all_decode():
    """The shape willow-mcp actually sends: one content chunk per row."""
    assert decode_payloads('{"id": 1}\n{"id": 2}\n{"id": 3}') == [
        {"id": 1},
        {"id": 2},
        {"id": 3},
    ]


def test_nothing_json_decodes_to_nothing():
    assert decode_payloads("not json at all") == []
    assert decode_payloads("") == []


def test_a_malformed_tail_keeps_what_parsed():
    """Losing a real page over a broken last row would be the worse failure."""
    assert decode_payloads('{"id": 1}\n{"id": 2}\n{"id": ') == [{"id": 1}, {"id": 2}]


# ---- the defect the cassette found ----------------------------------------


def test_a_real_page_of_history_parses_as_messages():
    """The regression. `json.loads` over the whole string raises "Extra data"
    on three concatenated objects, and _as_messages returned zero — so the
    listener polled a live bus, received real messages, and answered none."""
    raw = replay(CASSETTE)(*HISTORY_CALL)

    with pytest.raises(ValueError):
        json.loads(raw)  # the old implementation's one and only attempt

    messages = _as_messages(raw)
    assert len(messages) == 3
    assert all("id" in m for m in messages)


def test_a_single_document_history_still_parses():
    """The wrapped shape stays tolerated — it is an accepted input, not a
    claim that any server sends it."""
    assert len(_as_messages('{"result": [{"id": 1}, {"id": 2}]}')) == 2


def test_an_error_among_concatenated_rows_is_not_a_page():
    assert _as_messages('{"id": 1}\n{"error": "postgres_unavailable"}') == []


def test_a_concatenated_error_payload_is_not_a_successful_send():
    """grove._failure_detail had the same assumption: json.loads over the whole
    string, and its except-branch read the failure as a success."""
    detail = grove._failure_detail(
        '{"ok": false}\n{"error": "gate denied: grove_send_message"}'
    )
    assert detail is not None
    assert "gate denied" in detail


# ---- replay ---------------------------------------------------------------


def test_the_cassette_is_the_schema_it_claims():
    doc = json.loads(CASSETTE.read_text(encoding="utf-8"))
    assert doc["schema"] == SCHEMA
    assert doc["interactions"], "an empty cassette proves nothing"


def test_an_unrecorded_call_is_refused_loudly():
    """A test that silently gets nothing back is testing the fake."""
    with pytest.raises(UnrecordedCall) as caught:
        replay(CASSETTE)("grove_send_message", {"app_id": "ratatosk"})
    assert "not in" in str(caught.value)
    assert "python -m ratatosk.cassette" in str(caught.value), "say how to record it"


def test_repeated_calls_hold_on_the_last_answer():
    call = replay(CASSETTE)
    first = call(*HISTORY_CALL)
    assert call(*HISTORY_CALL) == first


def test_a_foreign_schema_is_refused(tmp_path):
    bogus = tmp_path / "bogus.json"
    bogus.write_text(
        json.dumps({"schema": "vcr/2", "interactions": []}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="not a ratatosk-cassette"):
        replay(bogus)


# ---- scrubbing ------------------------------------------------------------


def test_scrubbing_keeps_the_layout_and_drops_the_payload():
    raw = '{"id": 1, "content": "secret fleet chatter", "sender": "willow"}\n{"id": 2, "content": "more"}'
    out = scrub_result(raw)

    assert len(decode_payloads(out)) == 2, "two objects in, two objects out"
    assert "secret fleet chatter" not in out
    assert "scrubbed" in out
    first = decode_payloads(out)[0]
    assert first["id"] == 1, "structure and ids survive — they are shape"


def test_scrubbing_leaves_a_transport_error_readable():
    raw = "[mcp-error] [TextContent(type='text', text='Unknown tool: no_such_tool')]"
    assert scrub_result(raw).startswith("[mcp-error]")


def test_the_committed_cassette_carries_no_fleet_prose():
    """This package is published; a cassette must prove shape, never content."""
    text = CASSETTE.read_text(encoding="utf-8")
    for row in json.loads(text)["interactions"]:
        for value in decode_payloads(row["result"]):
            if isinstance(value, dict) and isinstance(value.get("content"), str):
                assert value["content"].startswith("<scrubbed"), value["content"][:60]


def _stored_cassette_carries(path: Path, secret: str) -> bool:
    """The grep this file runs over a saved cassette: is `secret` anywhere in
    the bytes on disk? Factored out of the test below so it can be planted —
    a check written inline in a test body can never be shown to fire
    (`tests/test_scans_fire.py`)."""
    return secret in path.read_text(encoding="utf-8")


def test_recorder_scrubs_before_it_stores(tmp_path):
    recorder = Recorder(inner=lambda tool, inputs: '{"content": "load-bearing secret"}')
    recorder.call("grove_get_history", {"app_id": "ratatosk"})
    path = recorder.save(tmp_path / "c.json", server="python -m willow_mcp")

    assert not _stored_cassette_carries(path, "load-bearing secret")


def test_the_leak_check_catches_a_planted_unscrubbed_cassette(tmp_path):
    """Planted: the same recorder with scrubbing switched off. The secret
    reaches disk, and the check must say so — otherwise the test above
    proves nothing about the scrub, only about the check's silence."""
    recorder = Recorder(
        inner=lambda tool, inputs: '{"content": "load-bearing secret"}',
        scrub_keys=frozenset(),
    )
    recorder.call("grove_get_history", {"app_id": "ratatosk"})
    path = recorder.save(tmp_path / "leaky.json", server="python -m willow_mcp")

    assert _stored_cassette_carries(path, "load-bearing secret")


SMOKE_HISTORY = (
    "grove_get_history",
    {"app_id": "ratatosk", "channel_name": "ratatosk-smoke", "limit": 1},
)
SEND_OK = (
    "grove_send_message",
    {
        "app_id": "ratatosk",
        "channel_name": "ratatosk-smoke",
        "content": "[ratatosk] cassette refresh",
        "sender": "ratatosk",
    },
)
SEND_REFUSED = (
    "grove_send_message",
    {
        "app_id": "ratatosk",
        "channel_name": "ratatosk-smoke",
        "content": "[ratatosk] refusal probe",
        "sender": "not-ratatosk",
    },
)


def test_a_one_message_page_is_one_message():
    """A single bare row is indistinguishable by length from the wrapped
    `{"result": [...]}` shape. Taking the wrapper path on it asked for a
    "result" key it does not have, and a real one-message page parsed as
    zero — found by driving a live channel that held exactly one message."""
    assert len(_as_messages(replay(CASSETTE)(*SMOKE_HISTORY))) == 1


def test_a_real_send_success_is_not_read_as_a_failure():
    """The recorded shape is {"id", "channel", "sent"} — no "result" wrapper,
    which is what the hand-written fakes assumed."""
    assert grove._failure_detail(replay(CASSETTE)(*SEND_OK)) is None


def test_a_real_send_refusal_is_read_as_one():
    detail = grove._failure_detail(replay(CASSETTE)(*SEND_REFUSED))
    assert detail is not None and "sender_forbidden" in detail


def test_an_unknown_tool_is_a_transport_error():
    assert grove._failure_detail(
        replay(CASSETTE)("no_such_tool", {"app_id": "ratatosk"})
    ).startswith("[mcp-error]")
