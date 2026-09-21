"""Inference clients, one per dialect, behind one shape.

Three dialects the ladder may name:

* ``openai`` — every free-tier rung (groq, cerebras, openrouter, gemini,
  huggingface) speaks ``POST {base_url}/chat/completions``. One client,
  stdlib ``urllib`` only: no new dependency for a JSON POST.
* ``anthropic`` — the SDK path crown always had, wrapped so its errors
  answer the same question as everyone else's.
* ``ollama`` — ``ratatosk.ollama``, the floor.

The one shape: history stays in Anthropic *content-block* form throughout —
``{"type": "text", "text": ...}`` and ``{"type": "tool_use", "id", "name",
"input"}`` on the assistant side, ``{"type": "tool_result", "tool_use_id",
"content"}`` on the user side — and each client translates on the way out
and back. The tool loop in crown therefore never learns which rung answered.

Errors answer one question the ladder needs: *is this the rung's fault or
the weather's?* A 429, a quota/credit refusal, an overloaded 5xx or a timeout
is weather — ``retryable`` — and the ladder steps to the next rung. A bad
request or a bad key is that rung's defect — not retryable — and the turn
refuses naming the rung, because stepping past a misconfigured rung would
hide the misconfiguration behind whichever rung happened to work.
"""

from __future__ import annotations

import contextlib
import json
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ratatosk.redact import redact

DEFAULT_TIMEOUT = 120
MAX_TOKENS = 8192

#: Server-side "not now": the provider is up and refusing everyone.
_OVERLOADED_STATUS = frozenset({500, 502, 503, 504, 529})
#: Words in a 400 body that mean the bill, not the request. Only checked on
#: 400 — a 401/403 is the key's problem whatever the prose says (Gemini
#: answers a key restricted to the wrong API with 403 "insufficient
#: permissions", which must surface, not step). 402 needs no words.
_QUOTA_WORDS = ("quota", "credit")


class ProviderError(Exception):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        status: int | None = None,
        kind: str = "error",
    ):
        super().__init__(message)
        self.retryable = retryable
        self.status = status
        #: ``rate_limited`` / ``quota`` / ``timeout`` / ``overloaded`` /
        #: ``bad_request`` / ``auth`` / ``transport`` — the receipt's word.
        self.kind = kind


@dataclass
class Completion:
    """What a rung answered, rung-agnostic."""

    blocks: list[dict]
    text: str
    tokens_in: int | None
    tokens_out: int | None
    latency_ms: int
    raw_model: str = ""

    @property
    def tool_uses(self) -> list[dict]:
        return [b for b in self.blocks if b.get("type") == "tool_use"]


@dataclass
class Request:
    model: str
    system: str
    messages: Sequence[Mapping[str, Any]]
    tools: Sequence[Mapping[str, Any]] = field(default_factory=list)
    max_tokens: int = MAX_TOKENS


# --- block helpers -----------------------------------------------------------


def block_type(block: Any) -> str | None:
    if isinstance(block, Mapping):
        return block.get("type")
    return getattr(block, "type", None)


def block_field(block: Any, name: str, default: Any = None) -> Any:
    if isinstance(block, Mapping):
        return block.get(name, default)
    return getattr(block, name, default)


def _content_blocks(content: Any) -> list[Any]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return list(content)
    return []


# --- openai dialect ----------------------------------------------------------


def to_openai_messages(
    system: str, messages: Sequence[Mapping[str, Any]]
) -> list[dict]:
    """Anthropic-shaped history -> OpenAI chat messages.

    An assistant turn with tool_use blocks becomes one assistant message
    carrying ``tool_calls``; a user turn of tool_result blocks becomes one
    ``tool`` message per result, in order. The pairing the API checks is by
    id, and the ids are ours (they round-trip untouched), so a compaction
    that keeps the pair keeps it valid here too.
    """
    out: list[dict] = []
    if system:
        out.append({"role": "system", "content": system})
    for m in messages:
        role = m.get("role")
        blocks = _content_blocks(m.get("content"))
        if role == "assistant":
            text = "".join(
                str(block_field(b, "text", ""))
                for b in blocks
                if block_type(b) == "text"
            )
            calls = [
                {
                    "id": block_field(b, "id"),
                    "type": "function",
                    "function": {
                        "name": block_field(b, "name"),
                        "arguments": json.dumps(block_field(b, "input", {}) or {}),
                    },
                }
                for b in blocks
                if block_type(b) == "tool_use"
            ]
            msg: dict = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
            continue
        results = [b for b in blocks if block_type(b) == "tool_result"]
        if results:
            for b in results:
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": block_field(b, "tool_use_id"),
                        "content": str(block_field(b, "content", "")),
                    }
                )
            continue
        text = "".join(
            str(block_field(b, "text", "")) for b in blocks if block_type(b) == "text"
        )
        out.append({"role": "user", "content": text})
    return out


def to_openai_tools(tools: Sequence[Mapping[str, Any]]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object"}),
            },
        }
        for t in tools
    ]


def from_openai_message(message: Mapping[str, Any]) -> list[dict]:
    """OpenAI assistant message -> Anthropic-shaped blocks."""
    blocks: list[dict] = []
    content = message.get("content")
    if isinstance(content, str) and content:
        blocks.append({"type": "text", "text": content})
    elif isinstance(content, list):
        # Some routers return content parts; keep the text ones.
        for part in content:
            if isinstance(part, Mapping) and part.get("type") == "text":
                blocks.append({"type": "text", "text": str(part.get("text", ""))})
    for call in message.get("tool_calls") or []:
        fn = call.get("function", {}) if isinstance(call, Mapping) else {}
        raw_args = fn.get("arguments", "{}")
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
        except (json.JSONDecodeError, TypeError):
            # The model wrote arguments that are not JSON. That is the
            # model's turn, not ours to repair: hand the text through so the
            # tool's own refusal names it.
            args = {"_raw_arguments": raw_args}
        blocks.append(
            {
                "type": "tool_use",
                "id": str(call.get("id") or f"call_{len(blocks)}"),
                "name": str(fn.get("name", "")),
                "input": args if isinstance(args, dict) else {"value": args},
            }
        )
    return blocks


def _classify_http(status: int, body: str) -> tuple[bool, str]:
    """(retryable, kind) for an HTTP status — weather or the rung's defect.

    Order is the point: the key's problems (401/403) are decided before any
    prose in the body is read, so a provider that phrases a permission
    refusal as "insufficient …" cannot talk its way into a quota step.
    """
    if status == 429:
        return True, "rate_limited"
    if status in (401, 403):
        return False, "auth"
    if status == 402:
        return True, "quota"
    if status == 400 and any(w in body.lower() for w in _QUOTA_WORDS):
        return True, "quota"
    if status == 408:
        return True, "timeout"
    if status in _OVERLOADED_STATUS:
        return True, "overloaded"
    return False, "bad_request"


def _malformed(base_url: str, what: str) -> ProviderError:
    """The rung answered, but not in the protocol. Its defect, not weather:
    stepping past it would hide a proxy or an outage page behind whichever
    rung happened to work next."""
    return ProviderError(
        f"{base_url} answered out of shape: {what}",
        retryable=False,
        kind="malformed",
    )


class OpenAICompatibleClient:
    """``/chat/completions`` over urllib. The key is held, never logged."""

    def __init__(
        self, base_url: str, api_key: str, *, timeout: float = DEFAULT_TIMEOUT
    ):
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.timeout = timeout

    def _post(self, payload: dict) -> dict:
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
                "User-Agent": "willow-ratatosk",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            body = ""
            # An error body that cannot be read is still an error with a
            # status; the status classifies and the reason falls back to
            # urllib's own. Nothing to log — the receipt carries the outcome.
            with contextlib.suppress(Exception):
                body = exc.read().decode("utf-8", "replace")[:500]
            retryable, kind = _classify_http(exc.code, body)
            # A provider's error body can echo the key it rejected
            # ("Incorrect API key provided: sk-…"); this string reaches the
            # receipt, the bus and the terminal, so it is masked here, once.
            raise ProviderError(
                f"HTTP {exc.code} from {self.base_url}: {redact(body) or exc.reason}",
                retryable=retryable,
                status=exc.code,
                kind=kind,
            ) from None
        except TimeoutError as exc:  # socket.timeout is this alias since 3.10
            raise ProviderError(
                f"timeout after {self.timeout}s at {self.base_url}",
                retryable=True,
                kind="timeout",
            ) from exc
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, TimeoutError):
                raise ProviderError(
                    f"timeout after {self.timeout}s at {self.base_url}",
                    retryable=True,
                    kind="timeout",
                ) from exc
            raise ProviderError(
                f"cannot reach {self.base_url}: {reason}",
                retryable=True,
                kind="transport",
            ) from exc
        # A 200 that is not JSON — a proxy's HTML, an outage page, an empty
        # body — is the rung out of shape, and it must land as a ProviderError
        # so the walk inks it rather than crashing the turn around it.
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise _malformed(
                self.base_url, f"200 with a non-JSON body: {redact(raw[:120])!r}"
            ) from None
        if not isinstance(data, dict):
            raise _malformed(
                self.base_url, f"200 body is {type(data).__name__}, not an object"
            )
        return data

    def complete(self, request: Request) -> Completion:
        payload: dict = {
            "model": request.model,
            "messages": to_openai_messages(request.system, request.messages),
            "max_tokens": request.max_tokens,
        }
        if request.tools:
            payload["tools"] = to_openai_tools(request.tools)
        started = time.monotonic()
        data = self._post(payload)
        latency = int((time.monotonic() - started) * 1000)
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise _malformed(self.base_url, f"no choices: {redact(str(data)[:200])}")
        first = choices[0]
        if not isinstance(first, Mapping):
            raise _malformed(
                self.base_url, f"choice is {type(first).__name__}, not an object"
            )
        message = first.get("message")
        if not isinstance(message, Mapping):
            raise _malformed(self.base_url, "choice carries no message object")
        blocks = from_openai_message(message)
        usage = data.get("usage")
        if not isinstance(usage, Mapping):
            usage = {}
        return Completion(
            blocks=blocks,
            text="".join(
                str(b.get("text", "")) for b in blocks if b.get("type") == "text"
            ),
            tokens_in=_int_or_none(usage.get("prompt_tokens")),
            tokens_out=_int_or_none(usage.get("completion_tokens")),
            latency_ms=latency,
            raw_model=str(data.get("model", request.model)),
        )


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


# --- anthropic dialect -------------------------------------------------------


class AnthropicClient:
    """The path crown always had, with the same error contract as the rest.

    Streams to stdout as before (the REPL's typing effect) and returns the
    final message's blocks converted to plain dicts, so the history holds
    one shape whichever rung wrote it.
    """

    def __init__(self, api_key: str, *, echo=None):
        try:
            import anthropic
        except ImportError:
            raise ProviderError(
                "anthropic SDK missing — pip install 'willow-ratatosk[cloud]'",
                retryable=False,
                kind="bad_request",
            ) from None
        self._sdk = anthropic
        self._client = anthropic.Anthropic(api_key=api_key)
        self._echo = echo

    def complete(self, request: Request) -> Completion:
        sdk = self._sdk
        started = time.monotonic()
        text = ""
        try:
            with self._client.messages.stream(
                model=request.model,
                max_tokens=request.max_tokens,
                system=request.system,
                messages=list(request.messages),
                tools=list(request.tools),
            ) as stream:
                for chunk in stream.text_stream:
                    if self._echo:
                        self._echo(chunk)
                    text += chunk
                final = stream.get_final_message()
        except sdk.RateLimitError as exc:
            raise ProviderError(
                redact(str(exc)), retryable=True, status=429, kind="rate_limited"
            ) from exc
        except sdk.APITimeoutError as exc:
            raise ProviderError(
                redact(str(exc)), retryable=True, kind="timeout"
            ) from exc
        except sdk.APIConnectionError as exc:
            raise ProviderError(
                redact(str(exc)), retryable=True, kind="transport"
            ) from exc
        except sdk.APIStatusError as exc:
            status = getattr(exc, "status_code", None)
            retryable, kind = _classify_http(status or 0, str(exc))
            raise ProviderError(
                redact(str(exc)), retryable=retryable, status=status, kind=kind
            ) from exc
        latency = int((time.monotonic() - started) * 1000)
        blocks: list[dict] = []
        for b in final.content:
            t = getattr(b, "type", None)
            if t == "text":
                blocks.append({"type": "text", "text": b.text})
            elif t == "tool_use":
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": b.id,
                        "name": b.name,
                        "input": dict(b.input),
                    }
                )
        usage = getattr(final, "usage", None)
        return Completion(
            blocks=blocks,
            text=text,
            tokens_in=_int_or_none(getattr(usage, "input_tokens", None)),
            tokens_out=_int_or_none(getattr(usage, "output_tokens", None)),
            latency_ms=latency,
            raw_model=str(getattr(final, "model", request.model)),
        )


# --- ollama dialect ----------------------------------------------------------


class OllamaClient:
    """``ratatosk.ollama.chat`` — text only; the floor does not call tools."""

    def __init__(self, base_url: str | None = None):
        self.base_url = base_url

    def complete(self, request: Request) -> Completion:
        from ratatosk import ollama

        messages = (
            [{"role": "system", "content": request.system}] if request.system else []
        )
        for m in request.messages:
            blocks = _content_blocks(m.get("content"))
            text = "".join(
                str(block_field(b, "text", ""))
                for b in blocks
                if block_type(b) == "text"
            )
            results = [b for b in blocks if block_type(b) == "tool_result"]
            if results:
                text = "\n".join(str(block_field(b, "content", "")) for b in results)
            messages.append({"role": m.get("role", "user"), "content": text})
        started = time.monotonic()
        try:
            text = ollama.chat(messages, model=request.model)
        except Exception as exc:
            # requests is a lazy import inside ollama; classify by shape, not
            # class, so this module never imports it either.
            name = exc.__class__.__name__
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is not None:
                retryable, kind = _classify_http(int(status), str(exc))
                raise ProviderError(
                    f"ollama HTTP {status}: {exc}",
                    retryable=retryable,
                    status=status,
                    kind=kind,
                ) from exc
            if "Timeout" in name:
                raise ProviderError(
                    f"ollama timeout: {exc}", retryable=True, kind="timeout"
                ) from exc
            raise ProviderError(
                f"ollama unreachable: {exc}", retryable=True, kind="transport"
            ) from exc
        latency = int((time.monotonic() - started) * 1000)
        return Completion(
            blocks=[{"type": "text", "text": text}] if text else [],
            text=text,
            tokens_in=None,
            tokens_out=None,
            latency_ms=latency,
            raw_model=request.model,
        )
