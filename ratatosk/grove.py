"""Grove bus wiring — receipted sends, no silent failure."""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass

from ratatosk.mcp_client import MCP_ERROR_PREFIX

_SENDER = os.environ.get("WILLOW_AGENT_NAME", "ratatosk")
_last_receipt: GroveReceipt | None = None
_grove_sender: Callable[[str], "GroveReceipt"] | None = None


@dataclass
class GroveReceipt:
    ok: bool
    detail: str
    skipped: bool = False


def last_receipt() -> GroveReceipt | None:
    return _last_receipt


def set_grove_sender(fn: Callable[[str], GroveReceipt] | None) -> None:
    global _grove_sender
    _grove_sender = fn


def _record(receipt: GroveReceipt) -> GroveReceipt:
    global _last_receipt
    _last_receipt = receipt
    return receipt


def channel_env() -> str:
    """The Grove channel, read now rather than at import.

    This module used to capture `_CHANNEL` at import while `send()` re-read the
    variable at call time. Setting the channel after import therefore passed
    `send()`'s guard and then posted to `""` through `make_mcp_sender`, which had
    bound the import-time value. One reader, one answer.
    """
    return os.environ.get("RATATOSK_GROVE_CHANNEL", "")


def send(content: str) -> GroveReceipt:
    chan = channel_env()
    if not chan:
        return _record(GroveReceipt(ok=True, detail="grove disabled (RATATOSK_GROVE_CHANNEL unset)", skipped=True))
    if _grove_sender is None:
        return _record(
            GroveReceipt(
                ok=False,
                detail="grove sender not configured — pass --mcp or call set_grove_sender()",
            )
        )
    try:
        return _record(_grove_sender(content))
    except Exception as exc:
        return _record(GroveReceipt(ok=False, detail=f"grove send failed: {exc}"))


def _failure_detail(result) -> str | None:
    """Return a failure reason for an MCP tool result, or None if it succeeded.

    ``mcp_client.call`` returns a *string*, so a dict-only check reports every
    transport error and every gated denial as a successful post. Both shapes
    are inspected here: the ``[mcp-error]`` transport sentinel, and a JSON
    payload carrying an ``error`` key (how willow-mcp reports a denied or
    degraded call, with no transport error at all).
    """
    if result is None:
        return "grove send returned no result"
    if isinstance(result, dict):
        return str(result["error"]) if result.get("error") else None
    if isinstance(result, str):
        text = result.strip()
        if not text:
            return "grove send returned an empty result"
        if text.startswith(MCP_ERROR_PREFIX):
            return text
        try:
            payload = json.loads(text)
        except ValueError:
            return None
        if isinstance(payload, dict) and payload.get("error"):
            return str(payload["error"])
        return None
    return None


def make_mcp_sender(mcp_call, *, channel: str | None = None, app_id: str | None = None):
    # Resolved per send, not bound at construction: a sender built before the
    # channel is set must not keep posting to "".
    fixed_channel = channel
    app_id = app_id or os.environ.get("RATATOSK_APP_ID", "ratatosk")

    def _send(content: str) -> GroveReceipt:
        chan = fixed_channel or channel_env()
        if not chan:
            return GroveReceipt(
                ok=False,
                detail="grove channel unset — set RATATOSK_GROVE_CHANNEL or pass channel=",
            )
        result = mcp_call(
            "grove_send_message",
            {
                "app_id": app_id,
                "channel_name": chan,
                "content": content,
                "sender": _SENDER,
            },
        )
        detail = _failure_detail(result)
        if detail is not None:
            return GroveReceipt(ok=False, detail=detail)
        return GroveReceipt(ok=True, detail=f"posted to {chan}")

    return _send


def session_started(session_id: str, model: str) -> GroveReceipt:
    return send(f"[ratatosk] session started — {session_id[:8]} model={model}")


def session_ended(session_id: str, turns: int, jsonl_path: str) -> GroveReceipt:
    return send(f"[ratatosk] session ended — {session_id[:8]} turns={turns} jsonl={jsonl_path}")
