"""The OpenAI-compatible client: wire shape both ways, errors classified."""

import io
import json
import socket
import urllib.error

import pytest

from ratatosk import providers
from ratatosk.providers import (
    OpenAICompatibleClient,
    ProviderError,
    Request,
    from_openai_message,
    to_openai_messages,
    to_openai_tools,
)

TOOL = {
    "name": "Read",
    "description": "Read a file",
    "input_schema": {"type": "object", "properties": {"file_path": {"type": "string"}}},
}


def test_history_round_trips_a_tool_call_into_openai_shape_and_back():
    """Anthropic-shaped history out, OpenAI tool_calls in, and the ids the
    tool loop keys on survive both directions untouched."""
    history = [
        {"role": "user", "content": "read it"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "sure"},
                {
                    "type": "tool_use",
                    "id": "call_1",
                    "name": "Read",
                    "input": {"file_path": "/x"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call_1", "content": "1\tline"}
            ],
        },
    ]
    wire = to_openai_messages("be brief", history)
    assert wire[0] == {"role": "system", "content": "be brief"}
    assert wire[1] == {"role": "user", "content": "read it"}
    assert wire[2]["role"] == "assistant"
    assert wire[2]["content"] == "sure"
    assert wire[2]["tool_calls"] == [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "Read", "arguments": json.dumps({"file_path": "/x"})},
        }
    ]
    assert wire[3] == {"role": "tool", "tool_call_id": "call_1", "content": "1\tline"}

    back = from_openai_message(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_2",
                    "type": "function",
                    "function": {"name": "Read", "arguments": '{"file_path": "/y"}'},
                }
            ],
        }
    )
    assert back == [
        {
            "type": "tool_use",
            "id": "call_2",
            "name": "Read",
            "input": {"file_path": "/y"},
        }
    ]


def test_tools_map_to_function_declarations():
    assert to_openai_tools([TOOL]) == [
        {
            "type": "function",
            "function": {
                "name": "Read",
                "description": "Read a file",
                "parameters": TOOL["input_schema"],
            },
        }
    ]


def test_unparseable_tool_arguments_are_handed_through_not_repaired():
    back = from_openai_message(
        {
            "tool_calls": [
                {"id": "c", "function": {"name": "Read", "arguments": "{oops"}}
            ]
        }
    )
    assert back[0]["input"] == {"_raw_arguments": "{oops"}


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _serve(monkeypatch, body: dict):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["headers"] = dict(req.header_items())
        seen["payload"] = json.loads(req.data.decode("utf-8"))
        seen["timeout"] = timeout
        return _Resp(json.dumps(body).encode("utf-8"))

    monkeypatch.setattr(providers.urllib.request, "urlopen", fake_urlopen)
    return seen


def test_complete_posts_chat_completions_and_reads_usage(monkeypatch):
    seen = _serve(
        monkeypatch,
        {
            "model": "free-70b-0921",
            "choices": [{"message": {"role": "assistant", "content": "hello"}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3},
        },
    )
    client = OpenAICompatibleClient("https://free.example/v1/", "sk-test", timeout=7)
    done = client.complete(
        Request(
            model="free-70b",
            system="s",
            messages=[{"role": "user", "content": "hi"}],
            tools=[TOOL],
        )
    )
    assert seen["url"] == "https://free.example/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer sk-test"
    assert seen["timeout"] == 7
    assert seen["payload"]["model"] == "free-70b"
    assert seen["payload"]["tools"][0]["function"]["name"] == "Read"
    assert done.text == "hello"
    assert done.tokens_in == 12 and done.tokens_out == 3
    assert done.raw_model == "free-70b-0921"
    assert done.latency_ms >= 0


def test_missing_usage_reads_as_none_not_zero(monkeypatch):
    _serve(monkeypatch, {"choices": [{"message": {"content": "x"}}]})
    done = OpenAICompatibleClient("https://f.example", "k").complete(
        Request(model="m", system="", messages=[{"role": "user", "content": "hi"}])
    )
    assert done.tokens_in is None and done.tokens_out is None


def _raise_http(monkeypatch, code: int, body: str = ""):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, code, "reason", {}, io.BytesIO(body.encode("utf-8"))
        )

    monkeypatch.setattr(providers.urllib.request, "urlopen", fake_urlopen)


@pytest.mark.parametrize(
    ("code", "body", "retryable", "kind"),
    [
        (429, "", True, "rate_limited"),
        (402, "", True, "quota"),
        (403, '{"error":"insufficient credits"}', True, "quota"),
        (503, "", True, "overloaded"),
        (401, "", False, "auth"),
        (403, "forbidden", False, "auth"),
        (400, '{"error":"bad schema"}', False, "bad_request"),
        (404, "", False, "bad_request"),
    ],
)
def test_http_errors_are_classified_weather_or_defect(
    monkeypatch, code, body, retryable, kind
):
    _raise_http(monkeypatch, code, body)
    client = OpenAICompatibleClient("https://f.example", "k")
    with pytest.raises(ProviderError) as info:
        client.complete(
            Request(model="m", system="", messages=[{"role": "user", "content": "hi"}])
        )
    assert info.value.retryable is retryable
    assert info.value.kind == kind
    assert info.value.status == code
    assert "Bearer" not in str(info.value), "the error names the host, never the header"


def test_a_timeout_is_retryable(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise socket.timeout("timed out")

    monkeypatch.setattr(providers.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(ProviderError) as info:
        OpenAICompatibleClient("https://f.example", "k", timeout=3).complete(
            Request(model="m", system="", messages=[{"role": "user", "content": "hi"}])
        )
    assert info.value.retryable and info.value.kind == "timeout"
    assert "3s" in str(info.value)


def test_a_dead_host_is_retryable_transport(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError(ConnectionRefusedError(111, "refused"))

    monkeypatch.setattr(providers.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(ProviderError) as info:
        OpenAICompatibleClient("https://f.example", "k").complete(
            Request(model="m", system="", messages=[{"role": "user", "content": "hi"}])
        )
    assert info.value.retryable and info.value.kind == "transport"


def test_an_answer_without_choices_is_the_rungs_defect(monkeypatch):
    _serve(monkeypatch, {"object": "list", "data": []})
    with pytest.raises(ProviderError) as info:
        OpenAICompatibleClient("https://f.example", "k").complete(
            Request(model="m", system="", messages=[{"role": "user", "content": "hi"}])
        )
    assert info.value.retryable is False
