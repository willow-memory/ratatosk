"""Grove bus wiring — receipted sends, no silent failure."""
from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass

_CHANNEL = os.environ.get("RATATOSK_GROVE_CHANNEL", "")
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


def send(content: str) -> GroveReceipt:
    channel = os.environ.get("RATATOSK_GROVE_CHANNEL", "")
    if not channel:
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


def make_mcp_sender(mcp_call, *, channel: str | None = None, app_id: str | None = None):
    channel = channel or _CHANNEL
    app_id = app_id or os.environ.get("RATATOSK_APP_ID", "ratatosk")

    def _send(content: str) -> GroveReceipt:
        result = mcp_call(
            "grove_send_message",
            {
                "app_id": app_id,
                "channel": channel,
                "content": content,
                "sender": _SENDER,
            },
        )
        if isinstance(result, dict) and result.get("error"):
            return GroveReceipt(ok=False, detail=str(result["error"]))
        return GroveReceipt(ok=True, detail=f"posted to {channel}")

    return _send


def session_started(session_id: str, model: str) -> GroveReceipt:
    return send(f"[ratatosk] session started — {session_id[:8]} model={model}")


def session_ended(session_id: str, turns: int, jsonl_path: str) -> GroveReceipt:
    return send(f"[ratatosk] session ended — {session_id[:8]} turns={turns} jsonl={jsonl_path}")
