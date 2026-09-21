"""Walking the ladder: weather steps, defects refuse, every call is inked."""

import argparse
import json
from datetime import date

import pytest

from ratatosk import crown, grove
from ratatosk import session as _session
from ratatosk.crown import RuntimeState, _run_turn
from ratatosk.hooks import HookRuntime
from ratatosk.inference import UNMEASURED, InferenceRouter, LadderRefused
from ratatosk.ladder import parse_ladder
from ratatosk.policy import PolicyStore
from ratatosk.providers import Completion, ProviderError

TODAY = date(2026, 9, 21)
ENV = {"A_KEY": "a", "B_KEY": "b", "C_KEY": "c"}


def _ladder():
    def rung(name, key="X_KEY"):
        return {
            "provider": name,
            "dialect": "openai",
            "base_url": f"https://{name}.example/v1",
            "key_env": key,
            "models": {"build": f"{name}-70b"},
            "verify_at": "2026-09-20",
        }

    return parse_ladder(
        {
            "version": 1,
            "rotate_after_days": 90,
            "rungs": {
                "a": rung("a", "A_KEY"),
                "b": rung("b", "B_KEY"),
                "c": rung("c", "C_KEY"),
            },
            "classes": {"build": ["a", "b", "c"]},
        }
    )


class _Fake:
    """A client whose answers are scripted per rung."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def complete(self, request):
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _ok(text="done", tokens=(10, 2), blocks=None):
    return Completion(
        blocks=blocks if blocks is not None else [{"type": "text", "text": text}],
        text=text,
        tokens_in=tokens[0] if tokens else None,
        tokens_out=tokens[1] if tokens else None,
        latency_ms=5,
        raw_model="m",
    )


def _router(scripts, *, writer=None, env=ENV, **kw):
    fakes = {name: _Fake(script) for name, script in scripts.items()}

    def factory(rung, model):
        return fakes[rung.name]

    router = InferenceRouter(
        _ladder(), "build", env=env, today=TODAY, factory=factory, writer=writer, **kw
    )
    return router, fakes


@pytest.fixture
def writer(tmp_path, monkeypatch):
    monkeypatch.setenv("RATATOSK_SESSION_DIR", str(tmp_path / "sessions"))
    return _session.SessionWriter(cwd=str(tmp_path))


@pytest.fixture
def grove_posts(monkeypatch):
    posts = []
    monkeypatch.setenv("RATATOSK_GROVE_CHANNEL", "willow")
    grove.set_grove_sender(
        lambda content: (posts.append(content), grove.GroveReceipt(True, "ok"))[1]
    )
    yield posts
    grove.set_grove_sender(None)


def _receipts(writer):
    return [e["receipt"] for e in writer.read_entries() if e["type"] == "receipt"]


def test_a_429_steps_to_the_next_rung_and_the_trail_says_so(writer):
    router, fakes = _router(
        {
            "a": [
                ProviderError("busy", retryable=True, status=429, kind="rate_limited")
            ],
            "b": [_ok("from b")],
            "c": [],
        },
        writer=writer,
    )
    done, receipt = router.complete("sys", [{"role": "user", "content": "hi"}], [])
    assert done.text == "from b"
    assert receipt.outcome == "ok"
    assert (receipt.rung, receipt.rung_index, receipt.rung_count) == ("b", 1, 3)
    assert [(s.rung, s.kind) for s in receipt.trail] == [("a", "rate_limited")]
    assert fakes["c"].calls == 0, "the walk stops at the first rung that answers"
    assert receipt.tokens_in == 10 and receipt.tokens_out == 2
    inked = _receipts(writer)
    assert len(inked) == 1
    assert inked[0]["rung"] == "b" and inked[0]["trail"][0]["rung"] == "a"


def test_quota_and_timeout_step_too(writer):
    router, _ = _router(
        {
            "a": [
                ProviderError("no credits", retryable=True, status=402, kind="quota")
            ],
            "b": [ProviderError("slow", retryable=True, kind="timeout")],
            "c": [_ok("from c")],
        },
        writer=writer,
    )
    _, receipt = router.complete("s", [{"role": "user", "content": "hi"}], [])
    assert receipt.rung == "c"
    assert [s.kind for s in receipt.trail] == ["quota", "timeout"]


def test_a_non_quota_4xx_does_not_fall_through_and_names_the_rung(writer):
    router, fakes = _router(
        {
            "a": [ProviderError("bad key", retryable=False, status=401, kind="auth")],
            "b": [_ok("never")],
            "c": [],
        },
        writer=writer,
    )
    with pytest.raises(LadderRefused) as info:
        router.complete("s", [{"role": "user", "content": "hi"}], [])
    assert "a (auth): bad key" in str(info.value)
    assert fakes["b"].calls == 0, "a rung's defect is not the weather; the turn refuses"
    receipt = info.value.receipt
    assert receipt.outcome == "refused" and receipt.rung == "a"
    inked = _receipts(writer)
    assert inked[0]["outcome"] == "refused", "a refusal is inked too"


def test_exhausting_the_ladder_lists_every_rungs_reason(writer):
    router, _ = _router(
        {
            "a": [
                ProviderError("a busy", retryable=True, status=429, kind="rate_limited")
            ],
            "b": [ProviderError("b down", retryable=True, kind="transport")],
            "c": [ProviderError("c slow", retryable=True, kind="timeout")],
        },
        writer=writer,
    )
    with pytest.raises(LadderRefused) as info:
        router.complete("s", [{"role": "user", "content": "hi"}], [])
    text = str(info.value)
    assert text.startswith("ladder exhausted")
    for word in ("a rate_limited: a busy", "b transport: b down", "c timeout: c slow"):
        assert word in text
    assert len(info.value.receipt.trail) == 3


def test_skipped_rungs_ride_in_the_receipt(writer):
    router, _ = _router(
        {"a": [], "b": [_ok()], "c": []},
        writer=writer,
        env={"B_KEY": "b", "C_KEY": "c"},
    )
    _, receipt = router.complete("s", [{"role": "user", "content": "hi"}], [])
    assert [(s.rung, s.kind) for s in receipt.skipped] == [("a", "no-key")]
    assert receipt.rung_index == 0 and receipt.rung_count == 2, (
        "index counts usable rungs"
    )


def test_no_usable_rung_refuses_before_any_call(writer):
    router, fakes = _router(
        {"a": [_ok()], "b": [_ok()], "c": [_ok()]}, writer=writer, env={}
    )
    with pytest.raises(LadderRefused, match="no usable rung"):
        router.complete("s", [], [])
    assert all(f.calls == 0 for f in fakes.values())


def test_missing_usage_is_inked_as_unmeasured(writer):
    router, _ = _router({"a": [_ok(tokens=None)], "b": [], "c": []}, writer=writer)
    _, receipt = router.complete("s", [{"role": "user", "content": "hi"}], [])
    assert receipt.tokens_in == UNMEASURED and receipt.tokens_out == UNMEASURED
    assert "tokens=unmeasured/unmeasured" in receipt.line()


def test_the_receipt_is_posted_to_grove_when_bound(writer, grove_posts):
    router, _ = _router(
        {
            "a": [
                ProviderError("busy", retryable=True, status=429, kind="rate_limited")
            ],
            "b": [_ok()],
            "c": [],
        },
        writer=writer,
    )
    _, receipt = router.complete("s", [{"role": "user", "content": "hi"}], [])
    assert grove_posts == [receipt.line()]
    line = grove_posts[0]
    assert line.startswith("[ratatosk] turn ok class=build rung=2/3 b b/m tokens=10/2")
    assert "trail=a:rate_limited" in line
    assert receipt.grove == "posted"
    assert _receipts(writer)[0]["grove"] == "posted"


def test_a_refused_grove_post_is_recorded_not_reported_as_posted(
    writer, monkeypatch, capsys
):
    """Loki finding 2: grove.send never raises — it answers ok=False. The
    answer is what the receipt records, and the terminal says so."""
    monkeypatch.setenv("RATATOSK_GROVE_CHANNEL", "willow")
    grove.set_grove_sender(
        lambda content: grove.GroveReceipt(
            False, "grove_send_message denied: no grove_write"
        )
    )
    try:
        router, _ = _router({"a": [_ok()], "b": [], "c": []}, writer=writer)
        _, receipt = router.complete("s", [{"role": "user", "content": "hi"}], [])
    finally:
        grove.set_grove_sender(None)
    assert receipt.grove == "refused: grove_send_message denied: no grove_write"
    assert _receipts(writer)[0]["grove"] == receipt.grove
    assert "grove post refused" in capsys.readouterr().out


def test_an_unbound_grove_is_recorded_as_skipped(writer, monkeypatch):
    monkeypatch.delenv("RATATOSK_GROVE_CHANNEL", raising=False)
    router, _ = _router({"a": [_ok()], "b": [], "c": []}, writer=writer)
    _, receipt = router.complete("s", [{"role": "user", "content": "hi"}], [])
    assert receipt.grove == "skipped"


def test_a_client_that_crashes_is_still_inked_and_refuses(writer):
    """Loki finding 1: only ProviderError was caught; any other exception out
    of a client ended the turn with no receipt. The failure that most needs a
    receipt is the one that would skip it."""
    router, fakes = _router(
        {"a": [KeyError("text")], "b": [_ok("never")], "c": []}, writer=writer
    )
    with pytest.raises(LadderRefused) as info:
        router.complete("s", [{"role": "user", "content": "hi"}], [])
    assert "a (crash): KeyError: 'text'" in str(info.value)
    assert fakes["b"].calls == 0, "an unknown fault is not weather; it does not step"
    inked = _receipts(writer)
    assert len(inked) == 1 and inked[0]["outcome"] == "refused"
    assert inked[0]["rung"] == "a"


def test_a_crash_that_echoes_a_key_is_redacted_in_the_receipt(writer):
    leaked = "sk-" + "c" * 40
    router, _ = _router(
        {"a": [RuntimeError(f"boom {leaked}")], "b": [], "c": []}, writer=writer
    )
    with pytest.raises(LadderRefused) as info:
        router.complete("s", [{"role": "user", "content": "hi"}], [])
    assert leaked not in str(info.value)
    assert leaked not in json.dumps(_receipts(writer))


def test_a_provider_error_reason_is_redacted_in_the_trail(writer):
    leaked = "sk-" + "d" * 40
    router, _ = _router(
        {
            "a": [
                ProviderError(
                    f"429 {leaked}", retryable=True, status=429, kind="rate_limited"
                )
            ],
            "b": [_ok()],
            "c": [],
        },
        writer=writer,
    )
    _, receipt = router.complete("s", [{"role": "user", "content": "hi"}], [])
    assert leaked not in receipt.trail[0].reason
    assert leaked not in json.dumps(_receipts(writer))


def test_the_receipt_entry_is_its_own_jsonl_type(writer):
    router, _ = _router({"a": [_ok()], "b": [], "c": []}, writer=writer)
    router.complete("s", [{"role": "user", "content": "hi"}], [])
    lines = writer.path.read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[-1])
    assert entry["type"] == "receipt"
    assert set(entry["receipt"]) >= {
        "task_class",
        "outcome",
        "provider",
        "model",
        "rung",
        "rung_index",
        "rung_count",
        "tokens_in",
        "tokens_out",
        "latency_ms",
        "trail",
        "skipped",
        "at",
    }


def test_a_receipt_that_cannot_be_written_does_not_end_the_turn(writer, capsys):
    class _BadWriter:
        def write_receipt(self, _):
            raise OSError("disk full")

    router, _ = _router({"a": [_ok("still")], "b": [], "c": []}, writer=_BadWriter())
    done, _ = router.complete("s", [{"role": "user", "content": "hi"}], [])
    assert done.text == "still"
    assert "not written" in capsys.readouterr().out


def test_from_args_local_narrows_to_ollama_and_honours_ollama_model(monkeypatch):
    from ratatosk.ladder import load_ladder

    router = InferenceRouter.from_args(
        task_class="chat",
        model=None,
        local=True,
        ladder=load_ladder(),
        env={"OLLAMA_MODEL": "tiny:1b"},
    )
    usable = router.resolution.usable
    assert [v.rung.name for v in usable] == ["ollama"]
    assert usable[0].model == "tiny:1b"


def test_from_args_default_class_chat_starts_on_the_floor():
    from ratatosk.ladder import load_ladder

    router = InferenceRouter.from_args(
        task_class="chat", model=None, local=False, ladder=load_ladder(), env={}
    )
    assert router.resolution.usable[0].rung.name == "ollama"
    assert [v.rung.name for v in router.resolution.skipped] == ["groq"]


# --- crown's loop over the router ---------------------------------------------


def _state(tmp_path, monkeypatch, router) -> RuntimeState:
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    monkeypatch.setenv("RATATOSK_SESSION_DIR", str(tmp_path / "sessions"))
    writer = _session.SessionWriter(cwd=str(tmp_path))
    router._writer = writer
    return RuntimeState(
        args=argparse.Namespace(
            local=False, trust=True, mcp=False, listen=False, deposit=False
        ),
        model="a-70b",
        writer=writer,
        history=[],
        system_prompt="test",
        all_tools=[],
        mcp_names=set(),
        mcp_call=None,
        client=None,
        policy=PolicyStore(),
        hooks=HookRuntime(),
        inference=router,
        task_class="build",
    )


def test_run_turn_dispatches_a_tool_call_from_an_openai_rung(
    tmp_path, monkeypatch, capsys
):
    """tool_use blocks from a non-Anthropic rung reach prompt_and_dispatch
    and their results go back as tool_result blocks keyed by the same id."""
    tool_turn = _ok(
        "",
        blocks=[
            {
                "type": "tool_use",
                "id": "call_9",
                "name": "Read",
                "input": {"file_path": "/x"},
            }
        ],
    )
    router, _ = _router({"a": [tool_turn, _ok("read it")], "b": [], "c": []})
    state = _state(tmp_path, monkeypatch, router)
    dispatched = []
    monkeypatch.setattr(
        crown._tools,
        "prompt_and_dispatch",
        lambda name, inputs, *a, **kw: (dispatched.append((name, inputs)), "contents")[
            1
        ],
    )

    _run_turn(state, "go")

    assert dispatched == [("Read", {"file_path": "/x"})]
    assert state.history[2] == {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "call_9", "content": "contents"}
        ],
    }
    assert state.history[3]["content"] == [{"type": "text", "text": "read it"}]
    assert len(_receipts(state.writer)) == 2, (
        "one receipt per model call, not per user turn"
    )
    out = capsys.readouterr().out
    assert "[ratatosk] turn ok" in out


def test_run_turn_reports_a_refusal_and_keeps_the_session(
    tmp_path, monkeypatch, capsys
):
    router, _ = _router(
        {
            "a": [ProviderError("bad key", retryable=False, status=401, kind="auth")],
            "b": [],
            "c": [],
        }
    )
    state = _state(tmp_path, monkeypatch, router)

    _run_turn(state, "go")  # does not raise

    assert "[ladder] a (auth): bad key" in capsys.readouterr().out
    entries = state.writer.read_entries()
    assert [e["type"] for e in entries] == ["user", "receipt", "system"]
    assert state.history == [{"role": "user", "content": "go"}]


def test_run_turn_without_a_router_refuses_loudly(tmp_path, monkeypatch):
    router, _ = _router({"a": [], "b": [], "c": []})
    state = _state(tmp_path, monkeypatch, router)
    state.inference = None
    with pytest.raises(RuntimeError, match="no inference router"):
        _run_turn(state, "go")


@pytest.mark.parametrize(
    "blocks",
    [[], [{"type": "text", "text": ""}], [{"type": "text", "text": "  \n"}]],
)
def test_an_empty_answer_never_enters_history(tmp_path, monkeypatch, capsys, blocks):
    """Loki finding 6: `{"role": "assistant", "content": []}` in history makes
    the Anthropic rung 400 on every later turn until /clear. An empty answer
    is recorded in the transcript and kept out of the model's view."""
    router, _ = _router({"a": [_ok("", blocks=blocks)], "b": [], "c": []})
    state = _state(tmp_path, monkeypatch, router)

    _run_turn(state, "go")

    assert state.history == [{"role": "user", "content": "go"}]
    assert [e["type"] for e in state.writer.read_entries()] == [
        "user",
        "receipt",
        "system",
    ]
    assert "[empty answer] a returned no content" in capsys.readouterr().out


def test_a_client_crash_inside_run_turn_is_reported_not_raised(
    tmp_path, monkeypatch, capsys
):
    router, _ = _router({"a": [ValueError("no json")], "b": [], "c": []})
    state = _state(tmp_path, monkeypatch, router)

    _run_turn(state, "go")  # does not raise

    assert "[ladder] a (crash): ValueError: no json" in capsys.readouterr().out
    assert [e["type"] for e in state.writer.read_entries()] == [
        "user",
        "receipt",
        "system",
    ]
