"""One more walk over the rungs that refused transiently (2026-09-30).

B4871ED9 made four good calls, then every audit rung refused in the same
second: openrouter overloaded (a 200 carrying 503), cerebras 402, groq 413.
Only the first of those was weather. A router with a retry pause walks the
ladder once more, after the pause, over the transient refusals only.
"""

from __future__ import annotations

from datetime import date

import pytest

from ratatosk import inference as _inference
from ratatosk.inference import InferenceRouter, LadderRefused
from ratatosk.ladder import parse_ladder
from ratatosk.providers import Completion, ProviderError

TODAY = date(2026, 9, 30)
ENV = {"A_KEY": "a", "B_KEY": "b", "C_KEY": "c"}


def _ladder():
    def rung(name):
        return {
            "provider": name,
            "dialect": "openai",
            "base_url": f"https://{name}.example/v1",
            "key_env": f"{name.upper()}_KEY",
            "models": {"audit": f"{name}-model"},
            "verify_at": "2026-09-30",
        }

    return parse_ladder(
        {
            "version": 1,
            "rotate_after_days": 90,
            "rungs": {"a": rung("a"), "b": rung("b"), "c": rung("c")},
            "classes": {"audit": ["a", "b", "c"]},
        }
    )


class _Fake:
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def complete(self, request):
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _ok():
    return Completion(
        blocks=[{"type": "text", "text": "verdict"}],
        text="verdict",
        tokens_in=1,
        tokens_out=1,
        latency_ms=1,
    )


def _err(kind, status):
    return ProviderError(kind, retryable=True, status=status, kind=kind)


def _router(scripts, **kw):
    fakes = {n: _Fake(s) for n, s in scripts.items()}
    router = InferenceRouter(
        _ladder(),
        "audit",
        env=ENV,
        today=TODAY,
        factory=lambda rung, model: fakes[rung.name],
        **kw,
    )
    return router, fakes


def test_the_b4871ed9_shape_is_rescued_by_one_retry_of_the_overloaded_rung():
    slept = []
    router, fakes = _router(
        {
            "a": [_err("overloaded", 503), _ok()],
            "b": [_err("quota", 402)],
            "c": [_err("too_large", 413)],
        },
        retry_pause=10.0,
        sleep=slept.append,
    )
    done, receipt = router.complete("s", [{"role": "user", "content": "hi"}], [])
    assert done.text == "verdict"
    assert receipt.rung == "a"
    assert slept == [10.0]
    assert (fakes["a"].calls, fakes["b"].calls, fakes["c"].calls) == (2, 1, 1), (
        "quota and size refusals are not retried: waiting does not change them"
    )
    assert [s.kind for s in receipt.trail] == ["overloaded", "quota", "too_large"]


def test_the_retry_is_one_walk_not_a_loop():
    slept = []
    router, fakes = _router(
        {
            "a": [_err("overloaded", 503)] * 2,
            "b": [_err("rate_limited", 429)] * 2,
            "c": [_err("timeout", 408)] * 2,
        },
        retry_pause=1.0,
        sleep=slept.append,
    )
    with pytest.raises(LadderRefused) as info:
        router.complete("s", [{"role": "user", "content": "hi"}], [])
    assert slept == [1.0], "one pause, one retry walk — never a third"
    assert (fakes["a"].calls, fakes["b"].calls, fakes["c"].calls) == (2, 2, 2)
    assert str(info.value).startswith("ladder exhausted")


def test_no_transient_refusal_means_no_pause_and_no_retry():
    slept = []
    router, fakes = _router(
        {
            "a": [_err("quota", 402)],
            "b": [_err("too_large", 413)],
            "c": [_err("quota", 402)],
        },
        retry_pause=10.0,
        sleep=slept.append,
    )
    with pytest.raises(LadderRefused):
        router.complete("s", [{"role": "user", "content": "hi"}], [])
    assert slept == []


def test_a_router_without_a_pause_walks_once_as_before():
    router, fakes = _router(
        {
            "a": [_err("overloaded", 503), _ok()],
            "b": [_err("quota", 402)],
            "c": [_err("quota", 402)],
        },
    )
    with pytest.raises(LadderRefused):
        router.complete("s", [{"role": "user", "content": "hi"}], [])
    assert fakes["a"].calls == 1


def test_from_args_builds_a_router_that_retries():
    router = InferenceRouter.from_args(
        task_class="audit", model=None, local=False, ladder=_ladder(), env=ENV
    )
    assert router._retry_pause == _inference.LADDER_RETRY_PAUSE


def test_the_shipped_audit_class_names_no_rung_that_answered_402():
    """Cerebras answered 402 payment_required on B4871ED9's audit call."""
    from ratatosk.ladder import load_ladder

    shipped = load_ladder()
    assert "cerebras" not in shipped.classes["audit"]
    assert list(shipped.classes["audit"][:2]) == ["openrouter", "openrouter-ultra"]
