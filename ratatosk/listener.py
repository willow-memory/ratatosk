"""Grove bus listener — MCP-backed task receive."""
from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ratatosk import grove, ollama
from ratatosk.capabilities import ActionResult, CapabilityGate
from ratatosk.mcp_client import MCP_ERROR_PREFIX
from ratatosk.protocol.envelope import Envelope, Intent, parse_grove_message, validate_envelope
from ratatosk.traces import log_trace


Handler = Callable[[Envelope], str]


@dataclass
class ListenerState:
    node: str
    channel: str
    cursor: int = 0
    gate: CapabilityGate = field(default_factory=CapabilityGate)
    handlers: dict[str, Handler] = field(default_factory=dict)


def _as_messages(result: Any) -> list[dict[str, Any]]:
    """Normalise a grove_get_history result into a message list.

    ``mcp_client.call`` returns a *string*, so the previous ``isinstance(result,
    str): return []`` guard made every poll return nothing and the listener a
    permanent no-op. Decode the JSON; a transport error or an error payload
    yields no messages, but a real page of history now gets through.
    """
    if isinstance(result, str):
        text = result.strip()
        if not text or text.startswith(MCP_ERROR_PREFIX):
            return []
        try:
            result = json.loads(text)
        except ValueError:
            return []
    if isinstance(result, dict):
        if result.get("error"):
            return []
        result = result.get("result", result.get("messages"))
    return result if isinstance(result, list) else []


class BusListener:
    def __init__(
        self,
        *,
        node: str | None = None,
        channel: str | None = None,
        mcp_call=None,
        poll_interval: float = 2.0,
    ):
        self.node = node or os.environ.get("WILLOW_AGENT_NAME", "ratatosk")
        # Same reader as grove.send(), and the same answer. This used to fall
        # back to "general", so an unconfigured box listened on a channel
        # nobody chose while grove.send() refused to post at all — the node
        # was half-present on a bus it had never been pointed at.
        self.channel = channel or grove.channel_env()
        if not self.channel:
            raise ValueError(
                "grove channel unset — set RATATOSK_GROVE_CHANNEL or pass channel="
            )
        self.mcp_call = mcp_call
        self.poll_interval = poll_interval
        self.state = ListenerState(node=self.node, channel=self.channel)
        self._register_defaults()

    def _register_defaults(self) -> None:
        self.state.handlers[Intent.CHAT.value] = self._handle_chat
        self.state.handlers[Intent.SUMMARIZE.value] = self._handle_chat
        self.state.handlers[Intent.REPLY.value] = self._handle_chat
        self.state.handlers[Intent.OPEN_STATUS.value] = self._handle_status
        self.state.handlers[Intent.RUN_TASK.value] = self._handle_run_task
        self.state.handlers[Intent.SHELL.value] = self._handle_shell_blocked

    def _handle_chat(self, env: Envelope) -> str:
        if not ollama.is_available():
            return "[ratatosk] ollama unavailable — cannot chat"
        return ollama.generate(env.prompt)

    def _handle_status(self, env: Envelope) -> str:
        oll = "up" if ollama.is_available() else "down"
        return f"node={self.node} ollama={oll} channel={self.channel}"

    def _handle_run_task(self, env: Envelope) -> str:
        return f"[ratatosk] task queued for confirmation trace={env.trace_id}"

    def _handle_shell_blocked(self, env: Envelope) -> str:
        return "[ratatosk] shell intent blocked — use run_task with confirmation"

    def process_message(self, msg: dict[str, Any]) -> str | None:
        env = parse_grove_message(msg, default_node=self.node)
        if not env:
            return None

        # Second, independent guard. run_once filters on the raw message; this
        # one catches any caller reaching process_message directly.
        if self.is_own_post(env.sender):
            log_trace(env.trace_id, "skipped_own_post", {"sender": env.sender})
            return None

        log_trace(env.trace_id, "received", {"sender": msg.get("sender"), "intent": env.intent})
        validation = validate_envelope(env, node=self.node)
        if not validation.ok:
            log_trace(env.trace_id, "rejected", {"errors": validation.errors})
            return f"[ratatosk] rejected trace={env.trace_id}: {', '.join(validation.errors)}"

        action = self.state.gate.classify(env)
        log_trace(env.trace_id, "classified", {"action": action.value})
        if action == ActionResult.REJECTED:
            return f"[ratatosk] rejected intent={env.intent}"
        if action == ActionResult.QUEUED_CONFIRM:
            return f"[ratatosk] awaiting confirmation trace={env.trace_id}"

        handler = self.state.handlers.get(env.intent, self._handle_chat)
        try:
            response = handler(env)
            log_trace(env.trace_id, "executed", {"intent": env.intent})
            if self.mcp_call is not None:
                self.mcp_call(
                    "grove_send_message",
                    {
                        "app_id": os.environ.get("RATATOSK_APP_ID", "ratatosk"),
                        "channel_name": env.reply_channel or self.channel,
                        "content": response,
                        "sender": self.node,
                    },
                )
            return response
        except Exception as exc:
            err = f"[{self.node}] error trace={env.trace_id}: {exc}"
            log_trace(env.trace_id, "error", {"error": str(exc)})
            return err

    def fetch_messages(self) -> list[dict[str, Any]]:
        if self.mcp_call is None:
            return []
        result = self.mcp_call(
            "grove_get_history",
            {
                "app_id": os.environ.get("RATATOSK_APP_ID", "ratatosk"),
                "channel_name": self.channel,
                "limit": 20,
            },
        )
        return _as_messages(result)

    def is_own_post(self, sender: Any) -> bool:
        """True when ``sender`` is this seat.

        A seat's own posts never wake it. Without this the loop is closed:
        ``process_message`` replies with ``"sender": self.node``, the reply is
        re-fetched on the next poll, and ``parse_grove_message`` turns any
        unaddressed text into a fresh chat envelope for this node. Replay
        detection cannot break it either — the self-parse mints a new nonce.
        """
        if not isinstance(sender, str):
            return False
        return sender.strip().lower() == self.node.strip().lower()

    def run_once(self) -> list[str]:
        outputs: list[str] = []
        for msg in self.fetch_messages():
            msg_id = int(msg.get("id") or 0)
            if msg_id <= self.state.cursor:
                continue
            # Advance past our own post before doing anything with it. Skipping
            # the cursor advance instead would re-examine it on every poll.
            if self.is_own_post(msg.get("sender")):
                self.state.cursor = max(self.state.cursor, msg_id)
                continue
            self.state.cursor = max(self.state.cursor, msg_id)
            out = self.process_message(msg)
            if out:
                outputs.append(out)
                if self.mcp_call is not None and msg.get("id") is not None:
                    self.mcp_call(
                        "grove_ack",
                        {
                            "app_id": os.environ.get("RATATOSK_APP_ID", "ratatosk"),
                            "message_id": msg_id,
                        },
                    )
        return outputs

    def run_forever(self, on_status: Callable[[str], None] | None = None) -> None:
        if on_status:
            on_status(f"listening on {self.channel} as {self.node}")
        while True:
            try:
                self.run_once()
            except Exception as exc:
                if on_status:
                    on_status(f"poll error: {exc}")
            time.sleep(self.poll_interval)
