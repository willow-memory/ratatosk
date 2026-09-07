"""Grove bus listener — MCP-backed task receive."""
from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ratatosk import ollama
from ratatosk.capabilities import ActionResult, CapabilityGate
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
        self.channel = channel or os.environ.get("RATATOSK_GROVE_CHANNEL", "general")
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
                        "channel": env.reply_channel or self.channel,
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
                "channel": self.channel,
                "limit": 20,
            },
        )
        if isinstance(result, str):
            return []
        messages = result.get("messages") if isinstance(result, dict) else result
        return messages or []

    def run_once(self) -> list[str]:
        outputs: list[str] = []
        for msg in self.fetch_messages():
            msg_id = int(msg.get("id") or 0)
            if msg_id <= self.state.cursor:
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
